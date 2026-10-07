"""Laboratório de apps; não afirma boot, sandbox Chromium ou isolamento de VM."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

INSTALL = ["/bin/sh", "/out/payload/install.sh", "--confirm-guest-install"]


def run(argv):
    return subprocess.run(argv, check=True, timeout=180, text=True, capture_output=True)


def main():
    if "--installed" not in sys.argv:
        # Falhas reais controladas apenas neste rootfs descartável.
        state_path = Path("/var/lib/bees-desktop")
        assert not state_path.exists()

        def refused(command=INSTALL):
            outcome = subprocess.run(command, capture_output=True, timeout=180)
            assert outcome.returncode != 0
            assert not (state_path / "complete").exists()

        refused(INSTALL[:-1])
        refused(["runuser", "-u", "nobody", "--", *INSTALL])
        extra = Path("/out/payload/packages/unverified.deb")
        extra.write_bytes(b"never-install")
        try:
            refused()
        finally:
            extra.unlink()
        installer = Path("/out/payload/install.sh")
        original = installer.read_bytes()
        try:
            installer.write_bytes(original + b"\n# checksum mismatch\n")
            refused()
        finally:
            installer.write_bytes(original)
        installer.chmod(0o775)
        try:
            refused()
        finally:
            installer.chmod(0o755)
        os.chown(installer, 10001, 10001)
        try:
            refused()
        finally:
            os.chown(installer, 0, 0)
        state_path.mkdir()
        try:
            refused()
            assert list(state_path.iterdir()) == []
        finally:
            state_path.rmdir()
        run(["groupadd", "--gid", "10001", "collision-fixture"])
        try:
            refused()
        finally:
            run(["groupdel", "collision-fixture"])
        run(["groupadd", "--gid", "10002", "collision-fixture"])
        run(["useradd", "--uid", "10001", "--gid", "10002", "collision-user"])
        try:
            refused()
        finally:
            run(["userdel", "collision-user"])
            run(["groupdel", "collision-fixture"])
        fixture_bin = Path("/apt-failure-fixture")
        fixture_bin.mkdir(mode=0o700)
        fake_apt = fixture_bin / "apt-get"
        fake_apt.write_text("#!/bin/sh\nexit 71\n")
        fake_apt.chmod(0o755)
        failure = subprocess.run(
            INSTALL,
            capture_output=True,
            timeout=180,
            env=os.environ | {"PATH": str(fixture_bin) + ":" + os.environ["PATH"]},
        )
        assert failure.returncode != 0
        assert (state_path / "installing").exists() and not (state_path / "complete").exists()
        refused()  # O estado parcial impede uma segunda tentativa implícita.
        assert state_path == Path("/var/lib/bees-desktop")
        shutil.rmtree(state_path)  # Apenas estado criado nesta falha de laboratório.
        fake_apt.unlink()
        fixture_bin.rmdir()
        run(["groupadd", "--gid", "1000", "operador-fixture"])
        run(["useradd", "--uid", "1000", "--gid", "1000", "operador-fixture"])
        print("10 falhas controladas recusadas; conta humana1000 preservada.")
    run(INSTALL)
    state = Path("/var/lib/bees-desktop/complete").read_bytes()
    run(INSTALL)
    assert Path("/var/lib/bees-desktop/complete").read_bytes() == state
    if "--installed" not in sys.argv:
        assert run(["id", "-u", "operador-fixture"]).stdout.strip() == "1000"
    workspace = Path("/workspace")
    source = workspace / "documento.txt"
    run(
        [
            "runuser",
            "-u",
            "abelha",
            "--",
            "/bin/sh",
            "-c",
            'printf "Documento produzido pela abelha.\\n" > /workspace/documento.txt',
        ]
    )
    run(
        [
            "runuser",
            "-u",
            "abelha",
            "--",
            "env",
            "HOME=/home/abelha",
            "libreoffice",
            "--headless",
            "-env:UserInstallation=file:///home/abelha/writer-profile",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workspace),
            str(source),
        ]
    )
    result = workspace / "documento.pdf"
    assert result.read_bytes().startswith(b"%PDF-")
    assert os.stat(source).st_uid == os.stat(result).st_uid == 10001
    before_replay = result.read_bytes()
    run(INSTALL)
    assert result.read_bytes() == before_replay
    version = run(["runuser", "-u", "abelha", "--", "chromium", "--version"]).stdout.strip()
    assert version.startswith("Chromium ")
    print("Writer real, usuário restrito, replay e documento PDF: OK; " + version)
    print("PDF SHA256=" + hashlib.sha256(result.read_bytes()).hexdigest())


if __name__ == "__main__":
    main()
