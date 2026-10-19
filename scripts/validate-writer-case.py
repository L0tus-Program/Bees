"""Caso público Writer: valida arquivos próprios, sem abrir apps ou publicar entregas.

PDF usa parsers Poppler em subprocessos POSIX limitados. Não é endpoint de upload,
adapter do guest, sandbox de documentos ou avaliação automática de qualidade.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

MAX_FILE = 16 * 1024**2
MAX_XML = 2 * 1024**2
MAX_TEXT = 1024**2
MAX_MEMBERS = 128
MAX_EXPANDED = 32 * 1024**2
MAX_PAGES = 8
WRITER_VERSION = "25.2.3"
CASE_ID = "bees-writer-001"
REQUIRED = {
    "identity": "BEES-WRITER-001",
    "title": "Plano de horta didática",
    "objective": "Objetivo",
    "budget": "Orçamento",
    "comparison": "Comparação",
    "recommendation": "Recomendação",
    "limits": "Limites e fontes",
    "kits": "300,00",
    "plants": "200,00",
    "soil": "450,00",
    "total": "950,00",
    "ceiling": "1.200,00",
    "reserve": "250,00",
    "alternative_b": "1.120,00",
    "alternative_c": "1.040,00",
    "decision": "Escolher a alternativa A",
    "fictional": "Valores fictícios, sem cotação de mercado",
    "source": "https://example.org/bees-writer-001",
}
BUDGET_ROWS = (
    ("Kit de ferramentas", "6", "R$ 50,00", "R$ 300,00"),
    ("Mudas", "40", "R$ 5,00", "R$ 200,00"),
    ("Sacos de terra", "15", "R$ 30,00", "R$ 450,00"),
    ("Total alternativa A", "", "", "R$ 950,00"),
)
NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}


class CaseError(ValueError):
    """Código fechado; não inclui texto, metadados, paths ou erros do parser."""


def read_file(path: Path) -> bytes:
    path = path.absolute()
    for ancestor in [*path.parents, path]:
        if ancestor.is_symlink() or (getattr(ancestor.lstat(), "st_file_attributes", 0) & 0x400):
            raise CaseError("writer_link_refused")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE:
            raise CaseError("writer_file_limit")
        raw = stream.read(MAX_FILE + 1)
    if not 0 < len(raw) <= MAX_FILE:
        raise CaseError("writer_file_limit")
    return raw


def normalize(text):
    return " ".join(unicodedata.normalize("NFC", text).split()).casefold()


def content_result(text: str) -> dict:
    if len(text.encode("utf-8")) > MAX_TEXT:
        raise CaseError("writer_text_limit")
    normalized = normalize(text)
    missing = [key for key, value in REQUIRED.items() if normalize(value) not in normalized]
    return {"status": "partial" if missing else "content_candidate", "missing": missing}


def xml(raw: bytes):
    if len(raw) > MAX_XML:
        raise CaseError("writer_xml_limit")
    try:
        text = raw.decode("utf-8-sig")
        if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.IGNORECASE):
            raise CaseError("writer_xml_declaration_refused")
        root = ET.fromstring(text)
        stack = [(root, 1)]
        count = 0
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > 30000 or depth > 40:
                raise CaseError("writer_xml_limit")
            stack.extend((child, depth + 1) for child in node)
        return root
    except UnicodeError, ET.ParseError:
        raise CaseError("writer_invalid_xml") from None


def odt(raw: bytes) -> dict:
    if not 22 <= len(raw) <= MAX_FILE or raw[-22:-18] != b"PK\x05\x06":
        raise CaseError("writer_invalid_odt")
    _, disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from(
        "<4s4H2LH", raw, len(raw) - 22
    )
    if (
        disk
        or start_disk
        or disk_count != count
        or comment
        or not 3 <= count <= MAX_MEMBERS
        or offset + size != len(raw) - 22
    ):
        raise CaseError("writer_odt_inventory_limit")
    central = offset
    for _ in range(count):
        if central + 46 > len(raw) - 22 or raw[central : central + 4] != b"PK\x01\x02":
            raise CaseError("writer_invalid_odt")
        name_size, extra_size, comment_size = struct.unpack_from("<3H", raw, central + 28)
        central += 46 + name_size + extra_size + comment_size
    if central != len(raw) - 22:
        raise CaseError("writer_invalid_odt")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            names = [member.orig_filename for member in members]
            if len(members) != count or len(set(names)) != len(names):
                raise CaseError("writer_invalid_odt")
            if (
                names[0] != "mimetype"
                or members[0].compress_type != zipfile.ZIP_STORED
                or not {"content.xml", "styles.xml", "META-INF/manifest.xml"} <= set(names)
                or sum(member.file_size for member in members) > MAX_EXPANDED
            ):
                raise CaseError("writer_invalid_odt")
            for member in members:
                name = member.orig_filename
                if (
                    member.filename != name
                    or any(ord(character) < 32 for character in name)
                    or name.startswith("/")
                    or "\\" in name
                    or ":" in name
                    or any(part in {".", ".."} for part in name.rstrip("/").split("/"))
                    or any(
                        part.casefold() in {"basic", "scripts", "objectreplacements"}
                        or part.casefold().startswith("object ")
                        for part in name.split("/")
                    )
                    or member.flag_bits & 1
                    or stat.S_ISLNK(member.external_attr >> 16)
                    or member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or member.file_size > MAX_XML
                ):
                    raise CaseError("writer_odt_member_refused")
                # CRC and declared sizes are checked even for members not interpreted.
                with archive.open(member) as stream:
                    if len(stream.read(MAX_XML + 1)) != member.file_size:
                        raise CaseError("writer_invalid_odt")
            if archive.read("mimetype") != b"application/vnd.oasis.opendocument.text":
                raise CaseError("writer_wrong_format")
            manifest = xml(archive.read("META-INF/manifest.xml"))
            if manifest.tag != "{" + NS["manifest"] + "}manifest":
                raise CaseError("writer_invalid_manifest")
            entries = {
                node.get("{" + NS["manifest"] + "}full-path"): node.get(
                    "{" + NS["manifest"] + "}media-type"
                )
                for node in manifest.findall("manifest:file-entry", NS)
            }
            if (
                entries.get("/") != "application/vnd.oasis.opendocument.text"
                or entries.get("content.xml") != "text/xml"
                or manifest.find(".//manifest:encryption-data", NS) is not None
            ):
                raise CaseError("writer_invalid_manifest")
            root = xml(archive.read("content.xml"))
            xml(archive.read("styles.xml"))
            body = root.find("office:body/office:text", NS)
            if root.tag != "{" + NS["office"] + "}document-content" or body is None:
                raise CaseError("writer_wrong_format")
            for scripts in root.findall("office:scripts", NS):
                if len(scripts) or scripts.attrib or scripts.text and scripts.text.strip():
                    raise CaseError("writer_active_content_refused")
            paragraphs = body.findall(".//text:p", NS) + body.findall(".//text:h", NS)
            tables = body.findall(".//table:table", NS)
            if len(paragraphs) < 6 or not tables:
                raise CaseError("writer_missing_structure")
            result = content_result(" ".join("".join(node.itertext()) for node in paragraphs))
            actual_rows = set()
            for table in tables:
                for row in table.findall("table:table-row", NS):
                    cells = []
                    for cell in row.findall("table:table-cell", NS):
                        repeats = cell.get("{" + NS["table"] + "}number-columns-repeated", "1")
                        if not repeats.isdecimal() or not 1 <= int(repeats) <= 4:
                            raise CaseError("writer_table_limit")
                        cells.extend([normalize(" ".join(cell.itertext()))] * int(repeats))
                    actual_rows.add(tuple(cells))
            if any(tuple(map(normalize, row)) not in actual_rows for row in BUDGET_ROWS):
                result["missing"].append("budget_rows")
                result["status"] = "partial"
            return result | {
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "paragraphs": len(paragraphs),
                "tables": len(tables),
            }
    except zipfile.BadZipFile, RuntimeError, OSError, OverflowError:
        raise CaseError("writer_invalid_odt") from None


def _parser_limits():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024**2, 512 * 1024**2))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_TEXT, MAX_TEXT))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def parser(executable: str, arguments: list[str], directory: Path, output: Path) -> str:
    # Offline lab only. No shell, URL, user profile, stdin or raw stderr in evidence.
    if os.name != "posix" or not shutil.which(executable):
        raise CaseError("writer_pdf_parser_unavailable")
    try:
        with output.open("xb") as stream:
            completed = subprocess.run(
                [shutil.which(executable), *arguments],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
                preexec_fn=_parser_limits,
                env={"LC_ALL": "C", "LANG": "C"},
            )
        if completed.returncode != 0 or output.stat().st_size > MAX_TEXT:
            raise CaseError("writer_pdf_parser_rejected")
        return output.read_text(encoding="utf-8")
    except subprocess.SubprocessError, UnicodeError, OSError:
        raise CaseError("writer_pdf_parser_rejected") from None


def pdf(raw: bytes) -> dict:
    if (
        not 20 <= len(raw) <= MAX_FILE
        or not raw.startswith(b"%PDF-")
        or not raw.rstrip().endswith(b"%%EOF")
    ):
        raise CaseError("writer_invalid_pdf")
    with tempfile.TemporaryDirectory(prefix="bees-writer-pdf-") as temporary:
        directory = Path(temporary)
        source = directory / "document.pdf"
        source.write_bytes(raw)
        source.chmod(0o600)
        info = parser("pdfinfo", [str(source)], directory, directory / "info.txt")
        pages = re.search(r"^Pages:\s+(\d+)\s*$", info, re.MULTILINE)
        if not pages or not 1 <= int(pages[1]) <= MAX_PAGES:
            raise CaseError("writer_pdf_page_limit")
        text = parser(
            "pdftotext",
            ["-enc", "UTF-8", "-nopgbrk", "-layout", str(source), "-"],
            directory,
            directory / "text.txt",
        )
        result = content_result(text)
        lines = {normalize(line) for line in text.splitlines()}
        if any(normalize(" ".join(row)) not in lines for row in BUDGET_ROWS):
            result["missing"].append("budget_rows")
            result["status"] = "partial"
        return result | {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "pages": int(pages[1]),
        }


def evaluate(
    odt_bytes: bytes, pdf_bytes: bytes, *, observed_writer_version: str | None = None
) -> dict:
    outputs = {"odt": odt(odt_bytes), "pdf": pdf(pdf_bytes)}
    complete = all(item["status"] == "content_candidate" for item in outputs.values())
    version_matches = observed_writer_version == WRITER_VERSION
    return {
        "format": "bees.writer-case-evidence/v1",
        "case_id": CASE_ID,
        "status": "supported_lab_candidate" if complete and version_matches else "partial",
        "outputs": outputs,
        "expected_writer_version": WRITER_VERSION,
        "observed_writer_version": observed_writer_version,
        "application_version_matches": version_matches,
        "human_visual_review": "pending",
        "bees_task_execution": "not_demonstrated",
    }


def main() -> int:
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--odt", type=Path, required=True)
    arguments.add_argument("--pdf", type=Path, required=True)
    options = arguments.parse_args()
    try:
        # Version evidence comes from the operator, never from document metadata.
        report = evaluate(read_file(options.odt), read_file(options.pdf))
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return (
            0
            if all(item["status"] == "content_candidate" for item in report["outputs"].values())
            else 3
        )
    except CaseError as error:
        print(json.dumps({"status": "invalid", "code": str(error)}))
        return 2
    except OSError, ValueError, KeyError, TypeError:
        print(json.dumps({"status": "invalid", "code": "writer_validation_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
