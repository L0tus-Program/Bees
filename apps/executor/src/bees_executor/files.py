"""Arquivos dentro de raízes explícitas; caminhos são resolvidos e conferidos no SO."""

import base64
import mimetypes
import os
import shutil
import stat
import zipfile
from pathlib import Path
from xml.etree import ElementTree

MAX_PUBLISH = 25 * 1024 * 1024
MAX_WRITE = 2 * 1024 * 1024
MEDIA = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
}


class ToolFailure(Exception):
    """Falha observada antes de qualquer efeito; ``code`` é fechado."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class Roots:
    """Mapeia caminhos do modelo para raízes locais; nunca segue links para fora delas.

    ``named`` usa o primeiro segmento como alias (conector pessoal: ``Documentos/x``).
    """

    def __init__(self, roots: dict[str, Path], *, named: bool):
        if not roots:
            raise ToolFailure("no_roots")
        self.roots = {alias: Path(path).resolve() for alias, path in roots.items()}
        self.named = named

    def resolve(self, value: str, *, must_exist=False) -> Path:
        if "\x00" in value:
            raise ToolFailure("path_invalid")
        raw = value.replace("\\", "/").strip()
        if self.named:
            parts = [part for part in raw.split("/") if part not in ("", ".")]
            if not parts:
                raise ToolFailure("path_outside_roots", "Informe Raiz/caminho.")
            alias, rest = parts[0], parts[1:]
            if alias not in self.roots:
                raise ToolFailure("path_outside_roots", f"Raízes: {', '.join(self.roots)}")
            root = self.roots[alias]
            candidate = root.joinpath(*rest)
        else:
            root = next(iter(self.roots.values()))
            if raw in ("", "."):
                candidate = root
            elif os.path.isabs(raw) or raw.startswith("~"):
                expanded = Path(os.path.expanduser(raw))
                candidate = expanded
            else:
                candidate = root / raw
        resolved = candidate.resolve()
        if not (resolved == root or resolved.is_relative_to(root)):
            raise ToolFailure("path_outside_roots")
        if must_exist and not resolved.exists():
            raise ToolFailure("not_found")
        return resolved

    def display(self, path: Path) -> str:
        for alias, root in self.roots.items():
            if path == root or path.is_relative_to(root):
                relative = path.relative_to(root).as_posix()
                if self.named:
                    return alias if relative == "." else f"{alias}/{relative}"
                return relative or "."
        return path.name

    def listing(self) -> list[dict]:
        return [{"name": alias, "type": "root"} for alias in self.roots] if self.named else []


def list_files(roots: Roots, path: str) -> dict:
    if roots.named and path.strip() in ("", "."):
        return {"path": ".", "entries": roots.listing()}
    target = roots.resolve(path, must_exist=True)
    if not target.is_dir():
        raise ToolFailure("not_a_directory")
    entries = []
    for entry in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        try:
            info = entry.lstat()
        except OSError:
            continue
        kind = (
            "link"
            if stat.S_ISLNK(info.st_mode)
            else "dir"
            if stat.S_ISDIR(info.st_mode)
            else "file"
        )
        entries.append(
            {"name": entry.name, "type": kind, "size": info.st_size if kind == "file" else None}
        )
        if len(entries) >= 500:
            break
    return {"path": roots.display(target), "entries": entries, "truncated": len(entries) >= 500}


def _docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        paragraphs.append("".join(node.text or "" for node in paragraph.iter(f"{namespace}t")))
    return "\n".join(paragraphs)


def _xlsx_text(path: Path) -> str:
    namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            for item in ElementTree.fromstring(archive.read("xl/sharedStrings.xml")).iter(
                f"{namespace}si"
            ):
                shared.append("".join(node.text or "" for node in item.iter(f"{namespace}t")))
        lines = []
        sheets = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/"))
        for sheet in sheets:
            lines.append(f"# {Path(sheet).stem}")
            for row in ElementTree.fromstring(archive.read(sheet)).iter(f"{namespace}row"):
                cells = []
                for cell in row.iter(f"{namespace}c"):
                    value = cell.find(f"{namespace}v")
                    text = value.text if value is not None else ""
                    if cell.get("t") == "s" and text:
                        text = shared[int(text)] if int(text) < len(shared) else ""
                    cells.append(text or "")
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ToolFailure("pdf_reader_unavailable") from None
    reader = PdfReader(str(path))
    return "\n\n".join(
        f"[página {index + 1}]\n{page.extract_text() or ''}"
        for index, page in enumerate(reader.pages[:300])
    )


def read_file(roots: Roots, path: str, offset: int = 0, max_chars: int = 20000) -> dict:
    target = roots.resolve(path, must_exist=True)
    if target.is_dir():
        raise ToolFailure("is_a_directory")
    suffix = target.suffix.lower()
    try:
        if suffix == ".docx":
            text = _docx_text(target)
        elif suffix == ".xlsx":
            text = _xlsx_text(target)
        elif suffix == ".pdf":
            text = _pdf_text(target)
        else:
            raw = target.read_bytes()[: 20 * 1024 * 1024]
            if b"\x00" in raw[:8192]:
                return {
                    "path": roots.display(target),
                    "binary": True,
                    "size": target.stat().st_size,
                    "media_type": MEDIA.get(suffix) or mimetypes.guess_type(target.name)[0],
                }
            text = raw.decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ToolFailure("document_unreadable") from None
    chunk = text[offset : offset + max_chars]
    return {
        "path": roots.display(target),
        "content": chunk,
        "offset": offset,
        "total_chars": len(text),
        "truncated": offset + len(chunk) < len(text),
    }


def write_file(roots: Roots, path: str, content: str, append: bool = False) -> dict:
    data = content.encode("utf-8")
    if len(data) > MAX_WRITE:
        raise ToolFailure("content_too_large")
    target = roots.resolve(path)
    if target.is_dir():
        raise ToolFailure("is_a_directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "ab" if append else "wb") as handle:
        handle.write(data)
    return {"path": roots.display(target), "bytes": len(data), "appended": append}


def delete_file(roots: Roots, path: str) -> dict:
    target = roots.resolve(path, must_exist=True)
    if any(target == root for root in roots.roots.values()):
        raise ToolFailure("cannot_delete_root")
    if target.is_dir() and not target.is_symlink():
        try:
            target.rmdir()
        except OSError:
            raise ToolFailure("directory_not_empty") from None
    else:
        target.unlink()
    return {"path": roots.display(target), "deleted": True}


def publish_file(roots: Roots, path: str, name: str | None = None) -> dict:
    target = roots.resolve(path, must_exist=True)
    if not target.is_file():
        raise ToolFailure("not_a_file")
    size = target.stat().st_size
    if size > MAX_PUBLISH:
        raise ToolFailure("file_too_large")
    media = MEDIA.get(target.suffix.lower())
    if media is None:
        raise ToolFailure("media_type_unsupported", target.suffix)
    return {
        "path": roots.display(target),
        "name": name or target.name,
        "media_type": media,
        "size": size,
        "content_b64": base64.b64encode(target.read_bytes()).decode("ascii"),
    }


def ensure_directory(path: Path, *, owner: tuple[int, int] | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if owner is not None and hasattr(os, "chown"):
        os.chown(path, *owner)


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True)
