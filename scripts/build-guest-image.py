"""Kit local: ISO Debian intacta e payload separado; nunca inicia/instala uma VM."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dist/guest-image"
PACKAGE = OUTPUT / "linux-desktop-v1"
LOCK = ROOT / "scripts/packaging/guest-image.json"
KEY = ROOT / "scripts/packaging/debian-cd.asc"
PAYLOAD = "bees-linux-desktop-v1-payload.tar.gz"
INVENTORY = "payload-inventory.json"
MAX_PAYLOAD_SIZE = 2 * 1024**3
MAX_ARCHIVE_SIZE = 4 * 1024**3
MAX_MEMBER_SIZE = 512 * 1024**2
MAX_JSON_SIZE = 4 * 1024**2
RECIPE_FILES = {"Dockerfile", "snapshot.sources", "install.sh", "prepare.py", "smoke.py"}
PRIVATE_NAMES = {
    ".git",
    ".env",
    "data",
    "secrets",
    "credentials.bin",
    "credentials.json",
    "bootstrap.json",
    "state.json",
    "vault",
    "bees.sqlite3",
    ".venv",
    "node_modules",
}


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def regular_path(path: Path, *, directory: bool = False) -> Path:
    """Conferir antes de resolver: não seguir symlinks/junctions/reparse points."""
    absolute = path.absolute()
    for current in [absolute, *absolute.parents]:
        if current.is_symlink() or (
            current.exists() and getattr(current.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Link ou reparse point recusado")
    if absolute.exists() and not (absolute.is_dir() if directory else absolute.is_file()):
        raise ValueError("Tipo de arquivo inválido")
    return absolute.resolve()


def checked_output(path: Path) -> Path:
    path = regular_path(path, directory=True)
    if not path.is_relative_to(ROOT.absolute() / "dist"):
        raise ValueError("Destino fora do dist do checkout")
    return path


def public_name(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or "\x00" in name
        or path.is_absolute()
        or any(part in (".", "..") for part in name.split("/"))
        or len(path.parts) > 12
        or any(
            part.lower() in PRIVATE_NAMES or part.lower().startswith(".env") for part in path.parts
        )
    ):
        raise ValueError("Caminho privado ou inválido no kit")
    return path.as_posix()


def bounded_json(path: Path) -> dict:
    regular_path(path)
    if path.stat().st_size > MAX_JSON_SIZE:
        raise ValueError("Manifesto excede o limite")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Manifesto inválido")
    return value


def load_lock() -> dict:
    lock = bounded_json(LOCK)
    iso = lock.get("iso", {})
    if not isinstance(iso, dict):
        raise ValueError("Pins da ISO inválidos")
    version = lock.get("debian_version", "")
    if (
        lock.get("format") != 1
        or lock.get("template_id") != "linux-desktop-v1"
        or lock.get("installation_mode") != "human-confirmation"
        or lock.get("architecture") != "amd64"
        or not re.fullmatch(r"\d+\.\d+\.\d+", version)
        or iso.get("filename") != f"debian-{version}-amd64-netinst.iso"
        or iso.get("url")
        != (f"https://cdimage.debian.org/debian-cd/{version}/amd64/iso-cd/" + iso["filename"])
        or not re.fullmatch(r"[0-9a-f]{64}", iso.get("sha256", ""))
        or type(iso.get("size")) is not int
        or not 1 <= iso["size"] <= 1024**3
        or not re.fullmatch(r"[0-9A-F]{40}", lock.get("signing_fingerprint", ""))
        or not re.fullmatch(r"[0-9a-f]{64}", lock.get("key_sha256", ""))
        or not re.fullmatch(
            r"debian:\d+\.\d+-slim@sha256:[0-9a-f]{64}", lock.get("builder_image", "")
        )
        or not re.fullmatch(r"\d{8}T\d{6}Z", lock.get("snapshot", ""))
    ):
        raise ValueError("Pins de origem inválidos")
    return lock


class DebianRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 2
    max_repeats = 1

    def redirect_request(self, request, fp, code, message, headers, new_url):
        previous, target = urlsplit(request.full_url), urlsplit(new_url)
        if (
            target.scheme != "https"
            or target.path != previous.path
            or target.query
            or target.fragment
            or target.username
            or target.password
            or target.port not in (None, 443)
            or not re.fullmatch(r"(?:[a-z0-9-]+\.)?ftp\.acc\.umu\.se", target.hostname or "")
        ):
            raise ValueError("Redirecionamento fora do mirror Debian registrado")
        return super().redirect_request(request, fp, code, message, headers, new_url)


def download(url: str, target: Path, *, limit: int, expected_size: int | None = None) -> None:
    """Origem HTTPS fixa, mirror registrado restrito e resposta limitada."""
    if not re.fullmatch(
        r"https://cdimage\.debian\.org/debian-cd/\d+\.\d+\.\d+/amd64/iso-cd/"
        r"(?:debian-\d+\.\d+\.\d+-amd64-netinst\.iso|SHA256SUMS(?:\.sign)?)",
        url,
    ):
        raise ValueError("Origem de download recusada")
    regular_path(target)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), DebianRedirect())
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    started = time.monotonic()
    created = False
    try:
        with opener.open(request, timeout=20) as response, target.open("xb") as output:
            created = True
            length = response.headers.get("Content-Length")
            if response.status != 200 or (
                length is not None and (not length.isdecimal() or int(length) > limit)
            ):
                raise ValueError("Resposta de download inválida")
            if expected_size is not None and length is not None and int(length) != expected_size:
                raise ValueError("Tamanho remoto diverge do pin")
            total = 0
            while chunk := response.read(65536):
                total += len(chunk)
                if total > limit or time.monotonic() - started > 900:
                    raise ValueError("Download excedeu o limite")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if expected_size is not None and total != expected_size:
            raise ValueError("Download incompleto")
    except BaseException:
        # Este arquivo foi criado por esta operação, nunca é estado existente.
        if created and target.exists():
            regular_path(target)
            target.unlink()
        raise


def verify_iso(path: Path, lock: dict) -> None:
    regular_path(path)
    if path.stat().st_size != lock["iso"]["size"] or digest(path) != lock["iso"]["sha256"]:
        raise ValueError("ISO incompleta ou diferente da versão fixada")


def key_blob(lock: dict) -> bytes:
    regular_path(KEY)
    if digest(KEY) != lock["key_sha256"]:
        raise ValueError("Âncora pública de confiança alterada")
    text = KEY.read_text(encoding="ascii")
    match = re.fullmatch(
        r"-----BEGIN PGP PUBLIC KEY BLOCK-----\s+(.+?)\s*"
        r"-----END PGP PUBLIC KEY BLOCK-----\s*",
        text,
        re.S,
    )
    if match is None:
        raise ValueError("Chave pública inválida")
    data = "".join(
        line.strip() for line in match[1].splitlines() if line.strip() and not line.startswith("=")
    )
    return base64.b64decode(data, validate=True)


def signed_manifest(text: str, status: str, lock: dict) -> None:
    valid = []
    for line in status.splitlines():
        fields = line.split()
        if fields[:2] == ["[GNUPG:]", "VALIDSIG"] and len(fields) >= 11:
            # Quando há subchave, o último campo é o fingerprint primário.
            valid.append(fields[-1] if len(fields) >= 12 else fields[2])
        if fields[:2] == ["[GNUPG:]", "BADSIG"]:
            raise ValueError("Assinatura Debian recusada")
    if valid != [lock["signing_fingerprint"]]:
        raise ValueError("Assinante Debian fora da âncora fixada")
    entries = []
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64}) [ *](\S+)", line)
        if match is None:
            raise ValueError("Manifesto Debian inválido")
        if match[2] == lock["iso"]["filename"]:
            entries.append(match[1])
    if entries != [lock["iso"]["sha256"]]:
        raise ValueError("Manifesto assinado difere do hash fixado")


def command(argv: list[str], *, timeout: int = 900) -> str:
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, stdout=output, stderr=output, cwd=ROOT, shell=False)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise ValueError("Operação do builder excedeu o prazo") from None
        output.seek(0)
        text = output.read(8 * 1024**2 + 1)
        if code != 0 or len(text) > 8 * 1024**2:
            raise ValueError("Operação do builder falhou")
        return text.decode("utf-8", errors="replace")


def docker_build(context: Path, target: str, tag: str) -> None:
    command(
        [
            "docker",
            "build",
            "--platform",
            "linux/amd64",
            "--target",
            target,
            "--tag",
            tag,
            "--file",
            str(context / "runtime/linux-desktop/Dockerfile"),
            str(context),
        ],
        timeout=1800,
    )


def verify_signature(inputs: Path, context: Path, lock: dict, suffix: str) -> None:
    tag, container = f"bees-guest-verifier:{suffix}", f"bees-guest-verify-{suffix}"
    docker_build(context, "verification-image", tag)
    try:
        command(
            [
                "docker",
                "create",
                "--name",
                container,
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "256m",
                "--cpus",
                "1",
                "--pids-limit",
                "64",
                "--entrypoint",
                "/usr/bin/gpgv",
                tag,
                "--homedir",
                "/tmp",
                "--keyring",
                "/input/debian-cd.gpg",
                "--status-fd",
                "1",
                "/input/SHA256SUMS.sign",
                "/input/SHA256SUMS",
            ]
        )
        command(["docker", "cp", str(inputs), f"{container}:/input"])
        status = command(["docker", "start", "--attach", container], timeout=30)
        exit_code = command(["docker", "inspect", "--format", "{{.State.ExitCode}}", container])
        if exit_code.strip() != "0":
            raise ValueError("Assinatura Debian inválida")
        signed_manifest((inputs / "SHA256SUMS").read_text(encoding="ascii"), status, lock)
    finally:
        subprocess.run(["docker", "rm", "--force", container], capture_output=True, check=False)
        subprocess.run(["docker", "image", "rm", tag], capture_output=True, check=False)


def source_context(stage: Path) -> tuple[Path, dict]:
    source = regular_path(ROOT / "runtime/linux-desktop", directory=True)
    context = stage / "context"
    target = context / "runtime/linux-desktop"
    target.mkdir(parents=True)
    hashes = {}
    for path in sorted(source.rglob("*")):
        regular_path(path, directory=path.is_dir())
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        relative = public_name(path.relative_to(source).as_posix())
        if relative not in RECIPE_FILES:
            raise ValueError("Arquivo fora da receita pública do payload")
        destination = target / relative
        if path.is_dir():
            destination.mkdir(exist_ok=True)
        elif path.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            hashes["runtime/linux-desktop/" + relative] = digest(path)
    if not (target / "Dockerfile").is_file():
        raise ValueError("Receita Linux ausente")
    validate_recipe(target, load_lock())
    return context, hashes


def validate_recipe(source: Path, lock: dict) -> None:
    """Pins de auditoria devem representar a receita realmente executada."""
    dockerfile = (source / "Dockerfile").read_text(encoding="utf-8")
    bases = re.findall(r"^FROM (\S+)", dockerfile, re.M)
    external = [base for base in bases if base not in ("packages", "scratch")]
    if not external or any(base != lock["builder_image"] for base in external):
        raise ValueError("Imagem da receita diverge do pin")
    sources = (source / "snapshot.sources").read_text(encoding="utf-8")
    snapshot = lock["snapshot"]
    uris = re.findall(r"^URIs: (\S+)", sources, re.M)
    if uris != [
        f"http://snapshot.debian.org/archive/debian/{snapshot}/",
        f"http://snapshot.debian.org/archive/debian-security/{snapshot}/",
    ]:
        raise ValueError("Snapshot da receita diverge do pin")
    actual = re.findall(
        r"echo '([0-9a-f]{64})  /var/lib/apt/lists/snapshot.debian.org_archive_"
        r"(debian|debian-security)_([0-9TZ]+)_dists_(trixie(?:-security)?)_InRelease'",
        dockerfile,
    )
    expected = [
        (lock["snapshot_inrelease_sha256"]["trixie"], "debian", snapshot, "trixie"),
        (
            lock["snapshot_inrelease_sha256"]["trixie-security"],
            "debian-security",
            snapshot,
            "trixie-security",
        ),
    ]
    if actual != expected:
        raise ValueError("Metadados APT da receita divergem do pin")
    prepare = (source / "prepare.py").read_text(encoding="utf-8")
    if re.findall(r'"snapshot": "([0-9TZ]+)"', prepare) != [snapshot]:
        raise ValueError("Inventário da receita diverge do snapshot")


def verify_payload(archive: Path, inventory_path: Path) -> dict:
    regular_path(archive)
    if archive.stat().st_size > MAX_PAYLOAD_SIZE:
        raise ValueError("Payload excede o limite")
    inventory = bounded_json(inventory_path)
    expected = inventory.get("files")
    if inventory.get("format") != 1 or inventory.get("template_id") != "linux-desktop-v1":
        raise ValueError("Inventário de outro template")
    if not isinstance(expected, dict) or not expected or len(expected) > 10000:
        raise ValueError("Inventário de payload inválido")
    for name, info in expected.items():
        public_name(name)
        if (
            not isinstance(info, dict)
            or type(info.get("size")) is not int
            or not 0 <= info["size"] <= MAX_MEMBER_SIZE
            or not re.fullmatch(r"[0-9a-f]{64}", info.get("sha256", ""))
        ):
            raise ValueError("Entrada de inventário inválida")
    found, names, total = {}, set(), 0
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            name = public_name(member.name.removeprefix("./").rstrip("/"))
            if name in names or len(names) >= 12000:
                raise ValueError("Entrada duplicada ou arquivo excessivo no payload")
            names.add(name)
            if member.mode & 0o7022 or member.uid != 0 or member.gid != 0:
                raise ValueError("Permissão especial ou gravável no payload")
            if member.isdir():
                continue
            if not member.isfile() or member.size > MAX_MEMBER_SIZE:
                raise ValueError("Arquivo especial, link ou permissão insegura no payload")
            total += member.size
            if total > MAX_ARCHIVE_SIZE:
                raise ValueError("Payload expandido excede o limite")
            handle = tar.extractfile(member)
            hashed = hashlib.sha256()
            length = 0
            script_text = bytearray()
            while chunk := handle.read(65536):
                length += len(chunk)
                hashed.update(chunk)
                if name == "install.sh":
                    if length > 65536:
                        raise ValueError("Instalador excede o limite")
                    script_text.extend(chunk)
            found[name] = {"sha256": hashed.hexdigest(), "size": length}
            if name == "install.sh":
                if not script_text.startswith(b"#!/bin/sh\n") or b"\r" in script_text:
                    raise ValueError("Instalador inválido ou com finais de linha Windows")
                if member.mode != 0o755:
                    raise ValueError("Permissões do instalador inválidas")
    if found != expected:
        raise ValueError("Payload incompleto ou diferente do inventário")
    if not {"install.sh", "packages.lock", "packages.json", "SHA256SUMS"} <= found.keys():
        raise ValueError("Payload sem contrato de instalação completo")
    return inventory


def export_payload(context: Path, package: Path, suffix: str) -> None:
    tag, container = f"bees-guest-kit:{suffix}", f"bees-guest-export-{suffix}"
    docker_build(context, "kit-export", tag)
    try:
        command(["docker", "create", "--name", container, "--entrypoint", "/bin/true", tag])
        for filename in (PAYLOAD, INVENTORY):
            command(["docker", "cp", f"{container}:/out/{filename}", str(package / filename)])
        verify_payload(package / PAYLOAD, package / INVENTORY)
    finally:
        subprocess.run(["docker", "rm", "--force", container], capture_output=True, check=False)
        subprocess.run(["docker", "image", "rm", tag], capture_output=True, check=False)


def write_manifest(package: Path, lock: dict, sources: dict) -> None:
    info = {
        "format": 1,
        "template_id": "linux-desktop-v1",
        "installation_mode": "human-confirmation",
        "base_iso": lock["iso"],
        "signing_fingerprint": lock["signing_fingerprint"],
        "builder_image": lock["builder_image"],
        "snapshot": lock["snapshot"],
        "source_sha256": sources,
        "vm_boot_verified": False,
        "vm_isolation_verified": False,
        "guest_bridge_ready": False,
        "usable": False,
    }
    (package / "kit-info.json").write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    (package / "README.txt").write_text(
        "Bees — kit Linux desktop v1\n\n"
        "A ISO Debian original permanece intacta: instalação humana com confirmação de disco.\n"
        "O payload separado só instala apps após uma decisão explícita dentro do guest.\n"
        "Este kit não particiona discos, cria VM, inicia serviços do host ou conecta o Bees.\n"
        "Não contém conta/chave do Bees ou senha compartilhada.\n"
        "Instalar Debian netinst pode exigir rede; o payload de apps é separado.\n"
        "Laboratório Docker não comprova boot, isolamento de VM ou sandbox do navegador.\n"
        "Assinatura Debian autentica somente a ISO original, não o payload local Bees.\n"
        "Não publicar sem revisão das licenças e validação real restante.\n",
        encoding="utf-8",
    )
    files = {
        path.name: {"sha256": digest(path), "size": path.stat().st_size}
        for path in sorted(package.iterdir())
    }
    (package / "SHA256SUMS.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")


def verify(package: Path, lock: dict | None = None) -> None:
    lock = load_lock() if lock is None else lock
    regular_path(package, directory=True)
    files = bounded_json(package / "SHA256SUMS.json")
    required = {
        lock["iso"]["filename"],
        PAYLOAD,
        INVENTORY,
        "kit-info.json",
        "README.txt",
        "SHA256SUMS",
        "SHA256SUMS.sign",
        "debian-cd.gpg",
    }
    if set(files) != required or {path.name for path in package.iterdir()} != (
        required | {"SHA256SUMS.json"}
    ):
        raise ValueError("Kit parcial ou contendo arquivos não previstos")
    for name, expected in files.items():
        public_name(name)
        if (
            not isinstance(expected, dict)
            or type(expected.get("size")) is not int
            or not re.fullmatch(r"[0-9a-f]{64}", expected.get("sha256", ""))
        ):
            raise ValueError("Inventário do kit inválido")
        path = regular_path(package / name)
        if path.stat().st_size != expected["size"] or digest(path) != expected["sha256"]:
            raise ValueError("Kit incompleto ou alterado")
    verify_iso(package / lock["iso"]["filename"], lock)
    if (package / "debian-cd.gpg").read_bytes() != key_blob(lock):
        raise ValueError("Âncora pública do kit difere do checkout")
    verify_payload(package / PAYLOAD, package / INVENTORY)
    if bounded_json(package / INVENTORY).get("snapshot") != lock["snapshot"]:
        raise ValueError("Snapshot do payload diverge do pin")
    info = bounded_json(package / "kit-info.json")
    if (
        info.get("base_iso") != lock["iso"]
        or info.get("snapshot") != lock["snapshot"]
        or info.get("builder_image") != lock["builder_image"]
        or info.get("signing_fingerprint") != lock["signing_fingerprint"]
        or info.get("installation_mode") != "human-confirmation"
        or any(
            info.get(flag) is not False
            for flag in (
                "vm_boot_verified",
                "vm_isolation_verified",
                "guest_bridge_ready",
                "usable",
            )
        )
    ):
        raise ValueError("Kit não comprovou as capacidades declaradas")


def cleanup_stage(stage: Path) -> None:
    checked_output(stage)
    if stage.parent != OUTPUT or not re.fullmatch(r"\.build-[0-9a-f]{32}", stage.name):
        raise ValueError("Não remover diretório fora do staging próprio")
    for path in stage.rglob("*"):
        regular_path(path, directory=path.is_dir())
    shutil.rmtree(stage)


def build(iso_file: Path | None = None) -> None:
    lock = load_lock()
    checked_output(PACKAGE)
    if PACKAGE.exists():
        raise ValueError("Kit já existe; preservar artefatos e estado de uso")
    checked_output(OUTPUT).mkdir(parents=True, exist_ok=True)
    lock_file = OUTPUT / ".builder.lock"
    regular_path(lock_file)
    descriptor = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    stage = checked_output(OUTPUT / (".build-" + uuid4().hex))
    try:
        stage.mkdir()
        package, inputs = stage / "package", stage / "signature"
        package.mkdir()
        inputs.mkdir()
        context, sources = source_context(stage)
        sources[LOCK.relative_to(ROOT).as_posix()] = digest(LOCK)
        sources[KEY.relative_to(ROOT).as_posix()] = digest(KEY)
        (inputs / "debian-cd.gpg").write_bytes(key_blob(lock))
        base = lock["iso"]["url"].rsplit("/", 1)[0] + "/"
        for name in ("SHA256SUMS", "SHA256SUMS.sign"):
            download(base + name, inputs / name, limit=32768)
        verify_signature(inputs, context, lock, stage.name.removeprefix(".build-"))
        target = package / lock["iso"]["filename"]
        if iso_file is not None:
            verify_iso(iso_file, lock)
            shutil.copyfile(iso_file, target)
        else:
            download(
                lock["iso"]["url"],
                target,
                limit=lock["iso"]["size"],
                expected_size=lock["iso"]["size"],
            )
        verify_iso(target, lock)
        for path in inputs.iterdir():
            shutil.copyfile(path, package / path.name)
        export_payload(context, package, stage.name.removeprefix(".build-"))
        write_manifest(package, lock, sources)
        verify(package, lock)
        checked_output(PACKAGE)
        if PACKAGE.exists():
            raise ValueError("Destino apareceu durante o build; preservar seu conteúdo")
        package.rename(PACKAGE)
    finally:
        os.close(descriptor)
        regular_path(lock_file)
        lock_file.unlink()
        if stage.exists():
            cleanup_stage(stage)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preparar kit Linux sem iniciar/instalar VM")
    parser.add_argument("--iso-file", type=Path, help="Reutilizar ISO local, verificando os pins")
    parser.add_argument(
        "--verify-only", action="store_true", help="Conferir kit existente localmente"
    )
    args = parser.parse_args(argv)
    try:
        if args.verify_only:
            if args.iso_file is not None:
                raise ValueError("Opções incompatíveis")
            verify(PACKAGE)
        else:
            build(args.iso_file)
    except OSError, ValueError, json.JSONDecodeError, tarfile.TarError:
        print(
            "Bees: kit não preparado/verificado; preserve estado existente e confira os pins.",
            file=sys.stderr,
        )
        return 1
    print("Bees: kit local verificado; boot e isolamento de VM continuam pendentes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
