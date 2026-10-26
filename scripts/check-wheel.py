"""Verifica a wheel do executor: reprodutível, conteúdo restrito e instalável offline.

Uso: python scripts/check-wheel.py <wheel-a> <wheel-b> [--out SHA256SUMS]
As duas wheels devem vir de builds independentes do mesmo checkout.
"""

import argparse
import hashlib
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

ALLOWED_PREFIXES = ("bees_executor/", "bees_executor-")
FORBIDDEN = (".env", "credential", "secret", ".pem", ".key", "tests/", "__pycache__", ".pyc")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def problems(path: Path) -> list[str]:
    found = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if not any(name == "bees_executor/cli.py" for name in names):
            found.append("cli ausente")
        for name in names:
            lowered = name.lower()
            if not name.startswith(ALLOWED_PREFIXES):
                found.append(f"arquivo fora do pacote: {name}")
            if any(marker in lowered for marker in FORBIDDEN):
                found.append(f"arquivo proibido: {name}")
        metadata = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if metadata is None:
            found.append("METADATA ausente")
        else:
            text = archive.read(metadata).decode("utf-8")
            if "Name: bees-executor" not in text:
                found.append("nome inesperado")
            headers = Parser().parsestr(text)
            if headers.get_all("License-Expression") != ["Apache-2.0"]:
                found.append("licença SPDX ausente ou divergente")
            if sorted(headers.get_all("License-File", [])) != ["LICENSE", "NOTICE"]:
                found.append("inventário de avisos ausente ou divergente")
            prefix = metadata.removesuffix("METADATA") + "licenses/"
            root = Path(__file__).resolve().parent.parent
            for filename in ("LICENSE", "NOTICE"):
                member = prefix + filename
                if member not in names or archive.read(member) != (root / filename).read_bytes():
                    found.append(f"aviso ausente ou divergente: {filename}")
        entry = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        if entry is None or "bees-executor = bees_executor.cli:main" not in archive.read(
            entry
        ).decode("utf-8"):
            found.append("entry point bees-executor ausente")
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    errors = problems(args.first)
    if digest(args.first) != digest(args.second):
        errors.append("builds diferentes: wheel não é reprodutível")
    for error in errors:
        print(f"erro: {error}", file=sys.stderr)
    if errors:
        return 1
    line = f"{digest(args.first)}  {args.first.name}\n"
    if args.out:
        args.out.write_text(line, encoding="utf-8")
    print(line, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
