"""Empacotar dependências oficiais já verificadas pelo APT, sem dados do hospedeiro."""

import gzip
import hashlib
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path("/out/payload")


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main():
    packages = {}
    licenses = {}
    (ROOT / "licenses").mkdir()
    for path in sorted((ROOT / "packages").glob("*.deb")):
        metadata = subprocess.check_output(
            ["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"], text=True
        )
        fields = dict(line.split(": ", 1) for line in metadata.splitlines())
        name = fields["Package"]
        if name in packages or fields["Architecture"] not in ("all", "amd64"):
            raise ValueError("Pacote duplicado ou arquitetura inesperada")
        packages[name] = fields | {"file": path.name, "sha256": digest(path)}
        process = subprocess.Popen(
            ["dpkg-deb", "--fsys-tarfile", str(path)], stdout=subprocess.PIPE
        )
        notices = []
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if member.name.endswith("/copyright"):
                    if member.isfile():
                        if member.size > 4 * 1024**2:
                            raise ValueError("Aviso de licença excessivo")
                        target = ROOT / "licenses" / f"{name}-{len(notices)}.txt"
                        target.write_bytes(archive.extractfile(member).read())
                        notices.append({"file": target.relative_to(ROOT).as_posix()})
                    elif member.issym() or member.islnk():
                        notices.append({"upstream_reference": member.linkname})
        process.stdout.close()
        if process.wait() != 0:
            raise ValueError("Arquivo Debian inválido")
        licenses[name] = notices
    if len(packages) < 20:
        raise ValueError("Closure incompleta")
    shutil.copyfile("/recipe/install.sh", ROOT / "install.sh")
    (ROOT / "install.sh").chmod(0o755)
    shutil.copytree("/usr/share/common-licenses", ROOT / "licenses/common", symlinks=False)
    (ROOT / "packages.lock").write_text(
        "".join(f"{name} {value['Version']}\n" for name, value in sorted(packages.items())),
        encoding="utf-8",
    )
    (ROOT / "packages.json").write_text(json.dumps(packages, sort_keys=True, indent=2) + "\n")
    (ROOT / "licenses.json").write_text(json.dumps(licenses, sort_keys=True, indent=2) + "\n")
    for source in sorted(Path("/var/lib/apt/lists").glob("*_InRelease")):
        shutil.copyfile(source, ROOT / source.name)
    paths = sorted(p for p in ROOT.rglob("*") if p.is_file())
    (ROOT / "SHA256SUMS").write_text(
        "".join(f"{digest(path)}  {path.relative_to(ROOT).as_posix()}\n" for path in paths)
    )
    files = {
        path.relative_to(ROOT).as_posix(): {"sha256": digest(path), "size": path.stat().st_size}
        for path in sorted(ROOT.rglob("*"))
        if path.is_file()
    }
    inventory = {
        "format": 1,
        "template_id": "linux-desktop-v1",
        "files": files,
        "packages": packages,
        "licenses": licenses,
        "snapshot": "20261005T000000Z",
        "publication_license_review_required": True,
    }
    Path("/out/payload-inventory.json").write_text(json.dumps(inventory, sort_keys=True, indent=2))
    with Path("/out/bees-linux-desktop-v1-payload.tar.gz").open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0, filename="") as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in sorted(ROOT.rglob("*")):
                    entry = archive.gettarinfo(str(path), arcname=path.relative_to(ROOT).as_posix())
                    if not (entry.isfile() or entry.isdir()):
                        raise ValueError("Link no payload")
                    entry.uid = entry.gid = entry.mtime = 0
                    entry.uname = entry.gname = ""
                    entry.mode = 0o755 if entry.isdir() or path.name == "install.sh" else 0o644
                    if entry.isfile():
                        with path.open("rb") as handle:
                            archive.addfile(entry, handle)
                    else:
                        archive.addfile(entry)


if __name__ == "__main__":
    main()
