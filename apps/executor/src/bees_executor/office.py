"""Conversão de documentos com LibreOffice headless (e pandoc para Markdown)."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from bees_executor.files import ToolFailure

LIBREOFFICE_FILTERS = {
    "pdf": "pdf",
    "docx": "docx:MS Word 2007 XML",
    "odt": "odt",
    "xlsx": "xlsx:Calc MS Excel 2007 XML",
    "ods": "ods",
    "csv": "csv",
    "html": "html",
    "txt": "txt:Text",
}


def _soffice() -> str:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(candidate).exists():
            return candidate
    raise ToolFailure("office_unavailable", "LibreOffice não instalado neste executor.")


def convert(source: Path, fmt: str, env: dict[str, str]) -> Path:
    if fmt not in LIBREOFFICE_FILTERS:
        raise ToolFailure("format_unsupported")
    target = source.with_suffix("." + fmt)
    if source.suffix.lower() in (".md", ".markdown") and fmt in ("docx", "odt", "html"):
        pandoc = shutil.which("pandoc")
        if pandoc:
            completed = subprocess.run(
                [pandoc, str(source), "-o", str(target)],
                env=env,
                capture_output=True,
                timeout=120,
                check=False,
            )
            if completed.returncode == 0 and target.exists():
                return target
    working = source
    if source.suffix.lower() in (".md", ".markdown"):
        # LibreOffice não lê Markdown: converter primeiro para HTML simples.
        working = source.with_suffix(".bees.html")
        working.write_text(markdown_to_html(source.read_text("utf-8", "replace")), "utf-8")
    with tempfile.TemporaryDirectory(prefix="bees-office-") as profile:
        completed = subprocess.run(
            [
                _soffice(),
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--norestore",
                "--convert-to",
                LIBREOFFICE_FILTERS[fmt],
                "--outdir",
                str(source.parent),
                str(working),
            ],
            env=env,
            capture_output=True,
            timeout=170,
            check=False,
        )
    produced = working.with_suffix("." + fmt)
    if working != source:
        working.unlink(missing_ok=True)
        if produced.exists() and produced != target:
            produced.replace(target)
            produced = target
    if completed.returncode != 0 or not produced.exists():
        raise ToolFailure(
            "conversion_failed", completed.stderr.decode("utf-8", "replace")[-400:] or None
        )
    return produced


def markdown_to_html(text: str) -> str:
    """Conversor mínimo: títulos, listas, ênfase, código e parágrafos."""
    import html
    import re

    def inline(value):
        value = html.escape(value)
        value = re.sub(r"`([^`]+)`", r"<code>\1</code>", value)
        value = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", value)
        value = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", value)
        return re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', value)

    out, in_list, in_code, paragraph = [], False, False, []

    def flush():
        nonlocal paragraph
        if paragraph:
            out.append("<p>" + inline(" ".join(paragraph)) + "</p>")
            paragraph = []

    for line in text.splitlines():
        if line.strip().startswith("```"):
            flush()
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(line))
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        item = re.match(r"^\s*[-*+]\s+(.*)$", line) or re.match(r"^\s*\d+[.)]\s+(.*)$", line)
        if heading:
            flush()
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(heading.group(1))
            out.append(f"<h{level}>{inline(heading.group(2))}</h{level}>")
        elif item:
            flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline(item.group(1))}</li>")
        elif not line.strip():
            flush()
            if in_list:
                out.append("</ul>")
                in_list = False
        else:
            paragraph.append(line.strip())
    flush()
    if in_list:
        out.append("</ul>")
    return '<html><head><meta charset="utf-8"></head><body>' + "\n".join(out) + "</body></html>"
