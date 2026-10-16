"""Entregáveis de tarefas textuais: relatório editável e tabela interoperável.

O modelo devolve seções delimitadas na resposta; o Bees extrai e valida cada uma
de forma independente antes de publicar pelo ``ArtifactStore``. A validação é pura
(sem rede, banco ou arquivos) e não confia no texto do modelo para escolher formato,
nome, vínculo ou permissão. Conteúdo inválido não é publicado nem corrigido em
silêncio: o resultado registra o motivo para decisão humana.
"""

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid5

from bees_core.artifacts import DEFAULT_MAX_BYTES, ArtifactError, validate_content

DeliverableKind = Literal["report", "table"]
KINDS: tuple[DeliverableKind, ...] = ("report", "table")
MEDIA_TYPES: dict[DeliverableKind, str] = {"report": "text/markdown", "table": "text/csv"}
EXTENSIONS: dict[DeliverableKind, str] = {"report": "md", "table": "csv"}
LABELS: dict[DeliverableKind, str] = {"report": "relatorio", "table": "tabela"}

MARKERS = {
    "report": "<<<BEES:REPORT>>>",
    "table": "<<<BEES:TABLE>>>",
    "missing": "<<<BEES:MISSING>>>",
}
END_MARKER = "<<<BEES:END>>>"
_SECTIONS = {value: key for key, value in MARKERS.items()}

MAX_COLUMNS = 200
MAX_ROWS = 100_000
MAX_MISSING_ITEMS = 20
MAX_MISSING_LENGTH = 300
# Células iniciadas por estes caracteres podem virar fórmulas em planilhas.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
_FENCE = re.compile(r"^```[A-Za-z0-9_-]*$")
_NAMESPACE = UUID("8f0a4d52-1c3e-4b6f-9a57-0e18f3c2b7d4")


@dataclass(frozen=True)
class Deliverable:
    """Resultado da validação de um formato solicitado; nunca contém caminho."""

    kind: DeliverableKind
    status: Literal["valid", "missing", "invalid"]
    error_code: str | None = None
    content: bytes | None = None
    rows: int | None = None
    columns: int | None = None
    formula_like_cells: int = 0


@dataclass(frozen=True)
class Extraction:
    deliverables: dict[DeliverableKind, Deliverable]
    missing_data: list[str] = field(default_factory=list)
    error_code: str | None = None

    @property
    def partial(self) -> bool:
        return bool(self.missing_data)


def requested(metadata: dict) -> tuple[DeliverableKind, ...]:
    """Formatos registrados na criação da tarefa, em ordem canônica."""
    values = metadata.get("deliverables") or []
    if not isinstance(values, list) or any(value not in KINDS for value in values):
        raise ValueError("Entregáveis registrados inválidos.")
    return tuple(kind for kind in KINDS if kind in values)


def instruction(kinds: tuple[DeliverableKind, ...]) -> str:
    """Contrato textual enviado ao modelo; não concede ferramentas nem ambiente."""
    parts = [
        "Além da resposta, entregue os arquivos abaixo em seções delimitadas. Cada seção "
        "começa com o marcador sozinho numa linha e termina com "
        f"{END_MARKER} sozinho numa linha. Use cada marcador uma única vez e somente "
        "para essa finalidade."
    ]
    if "report" in kinds:
        parts.append(
            f"- Relatório editável em Markdown: {MARKERS['report']} … {END_MARKER}. "
            "Cite as entradas e fontes usadas a partir do pedido e da conversa."
        )
    if "table" in kinds:
        parts.append(
            f"- Tabela CSV: {MARKERS['table']} … {END_MARKER}. Use vírgula como "
            "separador, primeira linha com nomes de colunas únicos e não vazios, o mesmo "
            "número de colunas em todas as linhas e aspas duplas quando um valor tiver "
            "vírgula, aspas ou quebra de linha."
        )
    parts.append(
        f"- Se faltarem dados, liste cada dado ausente numa linha em {MARKERS['missing']} "
        f"… {END_MARKER}. Não invente valores para completar a entrega."
    )
    return "\n".join(parts)


def artifact_id(call_id: UUID, kind: DeliverableKind) -> UUID:
    """Id determinístico: repetir a mesma intenção readota o rascunho existente."""
    return uuid5(_NAMESPACE, f"deliverable:{call_id}:{kind}")


def artifact_name(title: str, kind: DeliverableKind) -> str:
    """Nome de exibição derivado do título; caracteres de caminho/controle são removidos."""
    cleaned = "".join(
        " " if char in "/\\" or unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") else char
        for char in title
    )
    cleaned = " ".join(cleaned.split())[:150].strip(" .") or "tarefa"
    return f"{cleaned} - {LABELS[kind]}.{EXTENSIONS[kind]}"


def _sections(text: str) -> tuple[dict[str, list[str]], str | None]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        marker = line.strip()
        if marker in _SECTIONS:
            if current is not None:
                return sections, "deliverable_unterminated"
            current = _SECTIONS[marker]
            if current in sections:
                return sections, "deliverable_duplicated"
            sections[current] = []
        elif marker == END_MARKER:
            if current is None:
                return sections, "deliverable_unexpected_end"
            current = None
        elif current is not None:
            sections[current].append(line)
    if current is not None:
        return sections, "deliverable_unterminated"
    return sections, None


def _body(lines: list[str]) -> str:
    while lines and not lines[0].strip():
        lines = lines[1:]
    while lines and not lines[-1].strip():
        lines = lines[:-1]
    # Um bloco cercado inteiro (```csv … ```) é desembrulhado; cercas internas ficam.
    if (
        len(lines) >= 2
        and _FENCE.match(lines[0].strip())
        and lines[-1].strip() == "```"
        and not any(line.strip().startswith("```") for line in lines[1:-1])
    ):
        lines = lines[1:-1]
    return "\n".join(lines)


def _report(text: str, max_bytes: int) -> Deliverable:
    if not text.strip():
        return Deliverable("report", "invalid", "deliverable_empty")
    content = (text + "\n").encode("utf-8")
    try:
        validate_content(MEDIA_TYPES["report"], content, max_bytes)
    except ArtifactError as error:
        return Deliverable("report", "invalid", error.code)
    return Deliverable("report", "valid", content=content)


def _table(text: str, max_bytes: int) -> Deliverable:
    if not text.strip():
        return Deliverable("table", "invalid", "deliverable_empty")
    try:
        rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        return Deliverable("table", "invalid", "table_parse_failed")
    rows = [row for row in rows if row]
    header = rows[0] if rows else []
    if (
        not header
        or len(header) > MAX_COLUMNS
        or any(not name.strip() for name in header)
        or len({name.strip() for name in header}) != len(header)
    ):
        return Deliverable("table", "invalid", "table_header_invalid")
    if len(rows) - 1 > MAX_ROWS:
        return Deliverable("table", "invalid", "table_too_large")
    if any(len(row) != len(header) for row in rows[1:]):
        return Deliverable("table", "invalid", "table_columns_inconsistent")
    # Reescrita canônica (RFC 4180, CRLF) preservando exatamente os valores lidos.
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\r\n").writerows(rows)
    content = output.getvalue().encode("utf-8")
    try:
        validate_content(MEDIA_TYPES["table"], content, max_bytes)
    except ArtifactError as error:
        return Deliverable("table", "invalid", error.code)
    formulas = sum(1 for row in rows[1:] for cell in row if cell.startswith(_FORMULA_PREFIXES))
    return Deliverable(
        "table",
        "valid",
        content=content,
        rows=len(rows) - 1,
        columns=len(header),
        formula_like_cells=formulas,
    )


def _missing(lines: list[str]) -> list[str]:
    items = []
    for line in lines:
        value = line.strip()
        if value.startswith(("- ", "* ", "• ")):
            value = value[2:].strip()
        value = "".join(char for char in value if unicodedata.category(char) not in ("Cc", "Cf"))
        if value and value.casefold() not in ("nenhum", "nenhuma", "none", "ninguno"):
            items.append(value[:MAX_MISSING_LENGTH])
    return items[:MAX_MISSING_ITEMS]


def extract(
    text: str, kinds: tuple[DeliverableKind, ...], *, max_bytes: int = DEFAULT_MAX_BYTES
) -> Extraction:
    """Extrai e valida os formatos solicitados; seções não pedidas são ignoradas."""
    sections, error = _sections(text)
    if error is not None:
        return Extraction(
            {kind: Deliverable(kind, "invalid", error) for kind in kinds}, error_code=error
        )
    results: dict[DeliverableKind, Deliverable] = {}
    for kind in kinds:
        if kind not in sections:
            results[kind] = Deliverable(kind, "missing", "deliverable_missing")
            continue
        body = _body(sections[kind])
        results[kind] = _report(body, max_bytes) if kind == "report" else _table(body, max_bytes)
    return Extraction(results, _missing(sections.get("missing", [])))
