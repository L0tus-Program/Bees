"""Build local Windows do diagnóstico. Não instala nem publica o pacote gerado."""

import argparse
import hashlib
import importlib.metadata as metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "dist/host-windows"
PACKAGE = OUTPUT / "bees-host-windows"
FORBIDDEN = {
    "bees-api",
    "bees-core",
    "bees-worker",
    "apsw",
    "fastapi",
    "uvicorn",
    "pytest",
    "pyinstaller",
}


def checked_output(path: Path) -> Path:
    """Não seguir links/reparse points nem operar fora do dist deste checkout."""
    for ancestor in [path, *path.parents]:
        if ancestor.exists() and (
            ancestor.is_symlink() or getattr(ancestor.stat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Destino de build inválido")
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT / "dist"):
        raise ValueError("Destino de build fora do checkout")
    return resolved


def runtime_distributions(additional: set[str] | None = None) -> dict[str, metadata.Distribution]:
    result = {}
    pending = ["bees-host", *(additional or [])]
    while pending:
        name = canonicalize_name(pending.pop())
        if name in FORBIDDEN:
            raise ValueError("Plano de controle presente nas dependências do helper")
        if name in result:
            continue
        dist = metadata.distribution(name)
        result[name] = dist
        for raw in dist.requires or []:
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return dict(sorted(result.items()))


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def package_files(folder: Path):
    for ancestor in [folder, *folder.parents]:
        if ancestor.is_symlink() or getattr(ancestor.stat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Link/reparse point no pacote")
    pending = [folder]
    while pending:
        for file in sorted(pending.pop().iterdir()):
            if file.is_symlink() or getattr(file.stat(), "st_file_attributes", 0) & 0x400:
                raise ValueError("Link/reparse point no pacote")
            if file.is_dir():
                pending.append(file)
            elif file.is_file():
                yield file
            else:
                raise ValueError("Arquivo não regular no pacote")


def write_inventory(folder: Path) -> None:
    modules = json.loads((folder / "module-inventory.json").read_text(encoding="utf-8"))
    distributions = metadata.packages_distributions()
    additional = {
        dist for module in modules for dist in distributions.get(module.split(".")[0], [])
    }
    packages = runtime_distributions(additional)
    licenses = folder / "licenses"
    licenses.mkdir()
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    for name, dist in {**packages, "pyinstaller": metadata.distribution("pyinstaller")}.items():
        for file in dist.files or []:
            if "license" in str(file).lower() or "copying" in str(file).lower():
                source = dist.locate_file(file)
                if source.is_file():
                    target = licenses / name / Path(str(file)).name
                    target.parent.mkdir(exist_ok=True)
                    shutil.copy2(source, target)
    (folder / "package-info.json").write_text(
        json.dumps(
            {
                "format": 1,
                "purpose": "diagnostics-only",
                "platform": "windows-x64",
                "python": platform.python_version(),
                "pyinstaller": metadata.version("pyinstaller"),
                "hooks": metadata.version("pyinstaller-hooks-contrib"),
                "runtime_dependencies": {name: dist.version for name, dist in packages.items()},
                "source_sha256": {
                    file.relative_to(ROOT).as_posix(): digest(file)
                    for file in sorted((ROOT / "apps/host/src").rglob("*.py"))
                },
                "native_license_review": "Publication requires reviewing bundled native notices.",
                "signed": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (folder / "README.txt").write_text(
        "Bees — diagnóstico Windows x64\n\n"
        "Com o Bees já aberto pelo Docker e acesso configurado, abra Conectar host Bees.vbs.\n"
        "Na aba Computador, compare o código mostrado e confirme somente o diagnóstico.\n"
        "Python, .venv e terminal não são necessários para este helper.\n"
        "Docker Desktop é necessário.\n"
        "O pacote não cria VM, habilita Hyper-V, acessa arquivos pessoais nem instala serviço.\n"
        "Estado privado: data/host-links, na pasta do pacote; DPAPI da conta atual.\n"
        "Não mover estado cifrado para outra instalação/conta como forma de restaurar o vínculo.\n"
        "Revogue pela interface. Abrir novamente reutiliza o vínculo; não inicia após boot.\n"
        "Este build local não é um instalador assinado nem uma distribuição publicada.\n"
        "package-info.json registra versões; SHA256SUMS.json detecta cópia incompleta.\n"
        "Hashes locais não autenticam origem se pacote e manifesto forem alterados juntos.\n",
        encoding="utf-8-sig",
    )
    files = {
        file.relative_to(folder).as_posix(): {"sha256": digest(file), "size": file.stat().st_size}
        for file in package_files(folder)
    }
    (folder / "SHA256SUMS.json").write_text(json.dumps(files, indent=2) + "\n", encoding="utf-8")


def verify(folder: Path) -> None:
    hashes = json.loads((folder / "SHA256SUMS.json").read_text(encoding="utf-8"))
    actual = {
        file.relative_to(folder).as_posix()
        for file in package_files(folder)
        if file.name != "SHA256SUMS.json"
    }
    if actual != set(hashes):
        raise ValueError("Inventário de arquivos incompleto")
    for name, expected in hashes.items():
        file = folder / name
        if not file.resolve().is_relative_to(folder.resolve()) or file.is_symlink():
            raise ValueError("Caminho de inventário inválido")
        if file.stat().st_size != expected["size"] or digest(file) != expected["sha256"]:
            raise ValueError("Hash de arquivo inválido")
    info = json.loads((folder / "package-info.json").read_text(encoding="utf-8"))
    if FORBIDDEN.intersection(info["runtime_dependencies"]):
        raise ValueError("Dependência do plano de controle encontrada")


def reject_runtime_state(folder: Path) -> None:
    if (folder / "data").exists():
        raise ValueError("Pacote contém estado de uso; preserve data e atualize somente o runtime")


def build() -> None:
    if os.name != "nt" or platform.machine().lower() not in {"amd64", "x86_64"}:
        raise ValueError("Este build exige Windows x64")
    if metadata.version("pyinstaller") != "6.22.3":
        raise ValueError("Use o grupo packaging fixado no uv.lock")
    checked_output(PACKAGE)
    reject_runtime_state(PACKAGE)
    checked_output(OUTPUT).mkdir(parents=True, exist_ok=True)
    stage = checked_output(OUTPUT / (".build-" + uuid4().hex))
    stage.mkdir()
    package = stage / "bees-host-windows"
    package.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(package / "runtime"),
            "--workpath",
            str(stage / "work"),
            str(ROOT / "scripts/packaging/host.spec"),
        ],
        cwd=ROOT,
        check=True,
    )
    (package / "scripts").mkdir()
    for name in ["compose.yaml", "Conectar host Bees.vbs", "scripts/connect-host.ps1"]:
        shutil.copy2(ROOT / name, package / name)
    write_inventory(package)
    verify(package)
    # Preserva o build anterior, sem apagar estado privado ou matar helper em execução.
    checked_output(PACKAGE)
    if PACKAGE.exists():
        PACKAGE.rename(checked_output(OUTPUT / (".previous-" + uuid4().hex)))
    package.rename(PACKAGE)
    archive = OUTPUT / "bees-host-windows.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for file in sorted(PACKAGE.rglob("*")):
            if file.is_file():
                zipped.write(file, "bees-host-windows/" + file.relative_to(PACKAGE).as_posix())
    print("Pacote local pronto:", archive)


def main() -> int:
    parser = argparse.ArgumentParser(description="Empacotar diagnóstico Bees Windows")
    parser.add_argument("--verify", type=Path, help="Conferir inventário sem construir")
    args = parser.parse_args()
    try:
        if args.verify:
            verify(args.verify.absolute())
            print("Inventário e hashes conferidos.")
        else:
            build()
        return 0
    except (
        OSError,
        ValueError,
        metadata.PackageNotFoundError,
        subprocess.CalledProcessError,
    ) as error:
        print("Falha no empacotamento:", str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
