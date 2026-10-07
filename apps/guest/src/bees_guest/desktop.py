"""Somente leitura de arquivos/processos; nenhum programa do desktop é iniciado."""

import os
import stat
from pathlib import Path

DESKTOP_UID = 10001


def _executable(path):
    try:
        entry = Path(path).stat()
        return (
            stat.S_ISREG(entry.st_mode)
            and entry.st_uid == 0
            and not entry.st_mode & 0o022
            and bool(entry.st_mode & 0o111)
        )
    except OSError:
        return False


def _desktop_session():
    try:
        for index, entry in enumerate(Path("/proc").iterdir()):
            if index >= 4096:
                return False
            if not entry.name.isdigit():
                continue
            try:
                if (entry / "comm").read_text()[:100].strip() != "xfce4-session":
                    continue
                for line in (entry / "status").read_text()[:8192].splitlines():
                    if line.startswith("Uid:") and int(line.split()[2]) == DESKTOP_UID:
                        return True
            except (OSError, ValueError, IndexError):
                continue
    except OSError:
        pass
    return False


def probe():
    try:
        entry = Path("/workspace").lstat()
        workspace = (
            stat.S_ISDIR(entry.st_mode)
            and entry.st_uid == entry.st_gid == DESKTOP_UID
            and not entry.st_mode & 0o002
        )
    except OSError:
        workspace = False
    return {
        "desktop_session": os.name == "posix" and _desktop_session(),
        "chromium": _executable("/usr/bin/chromium"),
        "writer": _executable("/usr/bin/libreoffice"),
        "workspace": workspace,
    }
