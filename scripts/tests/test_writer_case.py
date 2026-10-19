"""Casos próprios e corrupção controlada, com parsers PDF reais quando disponíveis."""

import importlib.util
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

SCRIPT = Path(__file__).parents[1] / "validate-writer-case.py"
SPEC = importlib.util.spec_from_file_location("bees_writer_case", SCRIPT)
CASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CASE)
PDF_AVAILABLE = os.name == "posix" and shutil.which("pdfinfo") and shutil.which("pdftotext")


def document(
    *, text=None, extra=None, mimetype=None, declaration="", scripts="", wrong_amount=False
):
    values = list(CASE.REQUIRED.values()) if text is None else text
    paragraphs = "".join(f"<text:p>{escape(value)}</text:p>" for value in values)
    ns = " ".join(f'xmlns:{name}="{value}"' for name, value in CASE.NS.items())
    rows = [
        ["Kit de ferramentas", "6", "R$ 50,00", "R$ 301,00" if wrong_amount else "R$ 300,00"],
        ["Mudas", "40", "R$ 5,00", "R$ 200,00"],
        ["Sacos de terra", "15", "R$ 30,00", "R$ 450,00"],
        ["Total alternativa A", "", "", "R$ 950,00"],
    ]
    if text == []:
        rows = []
    table = "".join(
        "<table:table-row>"
        + "".join(
            f"<table:table-cell><text:p>{escape(cell)}</text:p></table:table-cell>" for cell in row
        )
        + "</table:table-row>"
        for row in rows
    )
    content = (
        declaration
        + f"<office:document-content {ns}>"
        + scripts
        + "<office:body><office:text>"
        + paragraphs
        + "<table:table>"
        + table
        + "</table:table>"
        + "</office:text></office:body></office:document-content>"
    ).encode("utf-8")
    manifest = (
        f"<manifest:manifest {ns}>"
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        "</manifest:manifest>"
    ).encode()
    members = {
        "mimetype": mimetype or b"application/vnd.oasis.opendocument.text",
        "content.xml": content,
        "styles.xml": f"<office:document-styles {ns}/>".encode(),
        "META-INF/manifest.xml": manifest,
    } | (extra or {})
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        for name, value in members.items():
            # ZipInfo normaliza os.sep no Windows; preserve o nome hostil no wire.
            info = zipfile.ZipInfo(name)
            info.filename = name
            info.orig_filename = name
            archive.writestr(info, value)
    return raw.getvalue()


def pdf_document(*, text=None, pages=1):
    # PDF 1.4 próprio: xref real e WinAnsi para conferir acentos com Poppler.
    text = (
        (list(CASE.REQUIRED.values()) + [" ".join(row) for row in CASE.BUDGET_ROWS])
        if text is None
        else text
    )
    commands = [b"BT /F1 10 Tf 40 800 Td"]
    for line in text:
        escaped = b"".join(
            bytes([byte]) if 32 <= byte < 127 and byte not in b"()\\" else f"\\{byte:03o}".encode()
            for byte in line.encode("cp1252")
        )
        commands.append(b"(" + escaped + b") Tj 0 -14 Td")
    stream = b"\n".join(commands) + b"\nET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Count {pages} /Kids [".encode() + b"3 0 R " * pages + b"] >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream",
    ]
    result = bytearray(b"%PDF-1.4\n")
    offsets = []
    for ordinal, value in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{ordinal} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        result.extend(f"{offset:010d} 00000 n \n".encode())
    result.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(result)


def test_valid_odt_checks_structure_text_and_missing_content():
    result = CASE.odt(document())
    assert result["status"] == "content_candidate" and result["missing"] == []
    assert result["tables"] == 1 and len(result["sha256"]) == 64
    partial = CASE.odt(document(text=["Texto parcial"] * 8))
    assert partial["status"] == "partial" and "title" in partial["missing"]
    assert "Texto parcial" not in json.dumps(partial)
    wrong = CASE.odt(document(wrong_amount=True))
    assert wrong["status"] == "partial" and "budget_rows" in wrong["missing"]


@pytest.mark.parametrize("raw", [b"", b"not odt", b"%PDF-1.4\n%%EOF", document()[:100]])
def test_empty_truncated_or_wrong_format_is_rejected(raw):
    with pytest.raises(CASE.CaseError):
        CASE.odt(raw)


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "a\\b",
        "a\x00hidden",
        "Scripts/macro.py",
        "Basic/main.xml",
        "Object 1/data",
    ],
)
def test_unsupported_archive_members_are_rejected(name):
    raw = document(extra={name: b"untrusted"})
    assert name.encode("utf-8") in raw
    with pytest.raises(CASE.CaseError, match="member_refused"):
        CASE.odt(raw)


def test_mimetype_empty_structure_xml_entities_and_active_content_are_rejected():
    for raw in (
        document(mimetype=b"application/zip"),
        document(text=[]),
        document(declaration='<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///private">]>'),
        document(
            scripts="<office:scripts><office:script>untrusted</office:script></office:scripts>"
        ),
    ):
        with pytest.raises(CASE.CaseError):
            CASE.odt(raw)


def test_lied_central_directory_count_does_not_trigger_unbounded_zip_parsing():
    raw = bytearray(document(extra={f"extra-{i}": b"" for i in range(100)}))
    struct.pack_into("<HH", raw, len(raw) - 22 + 8, 4, 4)
    with pytest.raises(CASE.CaseError, match="invalid_odt"):
        CASE.odt(bytes(raw))


def test_writer_web_with_odt_extension_is_not_a_writer_text_document():
    with pytest.raises(CASE.CaseError, match="writer_wrong_format"):
        CASE.odt(document(mimetype=b"application/vnd.oasis.opendocument.text-web"))


def test_expansion_and_xml_limits_are_checked(monkeypatch):
    monkeypatch.setattr(CASE, "MAX_EXPANDED", 40)
    with pytest.raises(CASE.CaseError):
        CASE.odt(document())
    monkeypatch.setattr(CASE, "MAX_XML", 10)
    with pytest.raises(CASE.CaseError, match="xml_limit"):
        CASE.xml(b"<root>" + b"a" * 20 + b"</root>")


def test_symlink_source_is_never_read(tmp_path):
    source = tmp_path / "source.odt"
    source.write_bytes(document())
    alias = tmp_path / "alias.odt"
    try:
        alias.symlink_to(source)
    except OSError:
        pytest.skip("Criação de symlink não disponível nesta conta")
    with pytest.raises(CASE.CaseError, match="link_refused"):
        CASE.read_file(alias)


@pytest.mark.skipif(not PDF_AVAILABLE, reason="Parsers Poppler POSIX limitados indisponíveis")
def test_real_pdf_parser_checks_content_wrong_format_partial_and_page_count():
    result = CASE.pdf(pdf_document())
    assert result["status"] == "content_candidate" and result["pages"] == 1
    partial = CASE.pdf(pdf_document(text=["Resultado parcial"]))
    assert partial["status"] == "partial" and "total" in partial["missing"]
    for raw in (
        pdf_document()[:150],
        document(),
        b"%PDF-1.4\ngarbage\n%%EOF",
        pdf_document(pages=9),
    ):
        with pytest.raises(CASE.CaseError):
            CASE.pdf(raw)


@pytest.mark.skipif(not PDF_AVAILABLE, reason="Parsers Poppler POSIX limitados indisponíveis")
def test_app_version_and_task_proof_are_separate_from_valid_files():
    result = CASE.evaluate(document(), pdf_document(), observed_writer_version="26.8.0")
    assert result["status"] == "partial" and not result["application_version_matches"]
    assert result["human_visual_review"] == "pending"
    assert result["bees_task_execution"] == "not_demonstrated"
    candidate = CASE.evaluate(document(), pdf_document(), observed_writer_version="25.2.3")
    assert candidate["status"] == "supported_lab_candidate"
    assert candidate["human_visual_review"] == "pending"


def test_parser_timeout_has_no_raw_error_or_private_path(tmp_path, monkeypatch):
    monkeypatch.setattr(CASE.os, "name", "posix")
    monkeypatch.setattr(CASE.shutil, "which", lambda _: "/trusted/parser")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE-CONTENT", 8)

    monkeypatch.setattr(CASE.subprocess, "run", timeout)
    with pytest.raises(CASE.CaseError, match="^writer_pdf_parser_rejected$"):
        CASE.parser("pdfinfo", ["private-path"], tmp_path, tmp_path / "output")


def test_cli_validation_error_never_echoes_document_or_private_filename(tmp_path):
    source = tmp_path / "private-human-name.odt"
    source.write_bytes(b"PRIVATE-TEXT bh_" + b"A" * 43)
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--odt", str(source), "--pdf", str(source)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert process.returncode == 2
    assert json.loads(process.stdout)["status"] == "invalid"
    assert "PRIVATE" not in process.stdout and str(tmp_path) not in process.stdout
    assert process.stderr == ""
