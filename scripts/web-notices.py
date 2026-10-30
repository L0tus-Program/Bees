"""Gera apps/web/public/third-party-notices.txt a partir do package-lock e de node_modules.

Inclui pacotes de produção/peer (não dev) com os textos integrais de LICENSE/NOTICE/AUTHORS.
Uso: python scripts/web-notices.py [--check]
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
TARGET = WEB / "public" / "third-party-notices.txt"
HEADER = (
    "Third-party notices for the Bees web distribution\n\n"
    "Generated from production/peer entries in package-lock.json.\n"
    "Bees itself is licensed under Apache-2.0; these components retain their licenses.\n"
    "noVNC unmodified source: https://github.com/novnc/noVNC/tree/v{novnc}\n"
)
RULE = "=" * 72
PREFIXES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")


def notice_files(directory: Path) -> list[tuple[str, Path]]:
    """Arquivos de licença do pacote, inclusive em subpastas (vendor/, docs/), sem dependências."""
    found = []
    for item in directory.rglob("*"):
        relative = item.relative_to(directory)
        if "node_modules" in relative.parts or len(relative.parts) > 3 or not item.is_file():
            continue
        upper = item.name.upper()
        if upper.startswith(PREFIXES) or upper.startswith("THIRDPARTYNOTICE"):
            found.append((relative.as_posix(), item))
    return sorted(found, key=lambda pair: (pair[0].count("/"), pair[0]))


def render() -> str:
    lock = json.loads((WEB / "package-lock.json").read_text(encoding="utf-8"))
    entries = sorted(
        (path.removeprefix("node_modules/"), details)
        for path, details in lock["packages"].items()
        if path and not details.get("dev")
    )
    novnc = next(details["version"] for name, details in entries if name == "@novnc/novnc")
    parts = [HEADER.format(novnc=novnc)]
    for name, details in entries:
        directory = WEB / "node_modules" / name
        files = notice_files(directory) if directory.is_dir() else []
        block = [RULE, f"{name} {details['version']}", ""]
        if not files:
            # Alguns pacotes só declaram a licença no package.json; registra o que publicaram.
            meta = json.loads((directory / "package.json").read_text(encoding="utf-8"))
            if not meta.get("license"):
                raise SystemExit(f"Sem licença declarada para {name}; confira node_modules.")
            author = meta.get("author")
            author = author.get("name") if isinstance(author, dict) else author
            repository = meta.get("repository")
            repository = repository.get("url") if isinstance(repository, dict) else repository
            block += [
                "--- package.json ---",
                f"License: {meta['license']} (no license file distributed in the package)",
                *([f"Author: {author}"] if author else []),
                *([f"Source: {repository}"] if repository else []),
                "",
            ]
        for label, item in files:
            text = item.read_text(encoding="utf-8", errors="replace").rstrip()
            block += [f"--- {label} ---", text, ""]
        parts.append("\n".join(block))
    return "\n".join(parts).rstrip() + "\n"


if __name__ == "__main__":
    content = render()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8")
        sys.exit(0 if current == content else 1)
    TARGET.write_text(content, encoding="utf-8", newline="\n")
