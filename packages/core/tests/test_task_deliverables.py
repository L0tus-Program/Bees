"""BEES-018.3: relatório e tabela gerados por tarefa, validados antes da publicação."""

import asyncio
import csv
import io
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError
from test_tasks_execution import answer, command, fixture_state, task_input

from bees_core import deliverables
from bees_core.artifacts import ArtifactStore
from bees_core.deliverables import END_MARKER, MARKERS, artifact_id, artifact_name, extract
from bees_core.execution import TaskWorker
from bees_core.models import utc_now
from bees_core.storage.database import Database
from bees_core.tasks import TaskInput, TaskService, request_digest, submission_digest

REPORT = MARKERS["report"]
TABLE = MARKERS["table"]
MISSING = MARKERS["missing"]


def section(marker, body):
    return f"{marker}\n{body}\n{END_MARKER}"


def output(report=None, table=None, missing=None, intro="Resumo da entrega."):
    parts = [intro]
    if report is not None:
        parts.append(section(REPORT, report))
    if table is not None:
        parts.append(section(TABLE, table))
    if missing is not None:
        parts.append(section(MISSING, missing))
    return "\n\n".join(parts)


TABLE_TEXT = 'Região,Ação,Valor\nNorte,"Ampliar, priorizar",10\nSul,"Diz ""sim""",ñandú\n'


def setup(tmp_path, kinds=("report", "table"), **updates):
    database, store, agent, chat, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input(deliverables=kinds, **updates))
    artifacts = ArtifactStore(database, tmp_path / "artifacts")
    return database, store, agent, service, task, artifacts


def worker(database, contents, requests, artifacts):
    def handler(request):
        requests.append(json.loads(request.content))
        return answer(contents[min(len(requests), len(contents)) - 1])

    return TaskWorker(database, transport=httpx.MockTransport(handler), artifacts=artifacts)


def items(detail):
    return {item["kind"]: item for item in detail.runs[-1].checkpoint["deliverables"]["items"]}


# --- Extração e validação independentes -----------------------------------------


def test_extracts_valid_report_and_canonical_table_preserving_values():
    result = extract(
        output("# Relatório\n\nÇedilha, acentuação e niño.", TABLE_TEXT), ("report", "table")
    )
    report, table = result.deliverables["report"], result.deliverables["table"]
    assert report.status == table.status == "valid"
    assert report.content.decode("utf-8") == "# Relatório\n\nÇedilha, acentuação e niño.\n"
    assert table.content.endswith(b"\r\n") and b"\r\n" in table.content
    rows = list(csv.reader(io.StringIO(table.content.decode("utf-8"), newline="")))
    assert rows == [
        ["Região", "Ação", "Valor"],
        ["Norte", "Ampliar, priorizar", "10"],
        ["Sul", 'Diz "sim"', "ñandú"],
    ]
    assert (table.rows, table.columns, table.formula_like_cells) == (2, 3, 0)
    assert result.missing_data == [] and not result.partial


def test_unwraps_whole_fence_but_keeps_inner_code_blocks():
    fenced = extract(output(table="```csv\na,b\n1,2\n```"), ("table",))
    assert fenced.deliverables["table"].content == b"a,b\r\n1,2\r\n"
    report = "Texto\n\n```python\nprint(1)\n```\n\nFim"
    kept = extract(output(report=report), ("report",))
    assert kept.deliverables["report"].content.decode() == report + "\n"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Sem seções", "deliverable_missing"),
        (output(report="a") + "\n" + section(REPORT, "b"), "deliverable_duplicated"),
        (f"{REPORT}\nsem fim", "deliverable_unterminated"),
        (f"{REPORT}\n{TABLE}\nx\n{END_MARKER}", "deliverable_unterminated"),
        (f"texto\n{END_MARKER}", "deliverable_unexpected_end"),
        (output(report="   \n  "), "deliverable_empty"),
    ],
)
def test_structure_errors_are_explicit(text, code):
    assert extract(text, ("report",)).deliverables["report"].error_code == code


@pytest.mark.parametrize(
    ("table", "code"),
    [
        ("", "deliverable_empty"),
        ("a,,c\n1,2,3", "table_header_invalid"),
        ("a,a\n1,2", "table_header_invalid"),
        ("a,b\n1,2,3", "table_columns_inconsistent"),
        ('a,b\n"1"x,2', "table_parse_failed"),
        ("a,b\n\x00,2", "artifact_content_invalid"),
    ],
)
def test_invalid_tables_are_not_published(table, code):
    item = extract(output(table=table), ("table",)).deliverables["table"]
    assert (item.status, item.error_code, item.content) == ("invalid", code, None)


def test_empty_data_large_table_formulas_and_limits(monkeypatch):
    only_header = extract(output(table="coluna"), ("table",)).deliverables["table"]
    assert (only_header.status, only_header.rows) == ("valid", 0)
    rows = "\n".join(f"{index},valor {index},=SOMA(A{index})" for index in range(5000))
    large = extract(output(table="n,texto,f\n" + rows), ("table",)).deliverables["table"]
    assert (large.rows, large.formula_like_cells) == (5000, 5000)
    assert b"=SOMA(A4999)" in large.content
    monkeypatch.setattr(deliverables, "MAX_ROWS", 10)
    limited = extract(output(table="n\n" + "\n".join(map(str, range(11)))), ("table",))
    assert limited.deliverables["table"].error_code == "table_too_large"
    oversized = extract(output(report="x" * 64), ("report",), max_bytes=32)
    assert oversized.deliverables["report"].error_code == "artifact_too_large"


def test_missing_data_unrequested_sections_and_names():
    result = extract(
        output(report="r", table="a\n1", missing="- População de 2025\n* Fonte oficial\nnenhum"),
        ("report",),
    )
    assert set(result.deliverables) == {"report"}
    assert result.missing_data == ["População de 2025", "Fonte oficial"]
    assert result.partial
    assert artifact_name("Plano/Q3\\ ‮ anual\n", "table") == "Plano Q3 anual - tabela.csv"
    assert artifact_name("...", "report") == "tarefa - relatorio.md"
    call = uuid4()
    assert artifact_id(call, "report") == artifact_id(call, "report") != artifact_id(call, "table")


def test_task_input_deliverables_are_closed_and_keep_legacy_digest():
    value = task_input(deliverables=("table", "report"))
    assert value.deliverables == ("report", "table")
    with pytest.raises(ValidationError):
        task_input(deliverables=("report", "report"))
    with pytest.raises(ValidationError):
        task_input(deliverables=("docx",))
    legacy = task_input()
    assert submission_digest(legacy) == request_digest(legacy, exclude={"deliverables"})
    assert submission_digest(value) != request_digest(value, exclude={"deliverables"})


# --- Execução real pelo worker ---------------------------------------------------


def test_worker_publishes_validated_files_linked_to_run(tmp_path):
    database, store, agent, service, task, artifacts = setup(tmp_path)
    text = output("# Relatório\n\nFonte: pedido da tarefa.", TABLE_TEXT)
    requests = []
    assert asyncio.run(worker(database, [text], requests, artifacts).run_once())
    detail = TaskService(Database(database.path)).detail(agent.id, task.id)
    run = detail.runs[-1]
    assert detail.task.status == "completed" and run.checkpoint["result_ready"] is True
    assert run.checkpoint["publication"]["status"] == "done"
    assert len(requests) == 1 and detail.task.calls_started == 1
    prompt = requests[0]["messages"][-1]["content"]
    assert REPORT in prompt and TABLE in prompt and END_MARKER in prompt
    published = {item.media_type: item for item in artifacts.list(agent.id, task.id)}
    assert set(published) == {"text/markdown", "text/csv"}
    for artifact in published.values():
        assert artifact.status == "ready" and artifact.source == "system_generated"
        assert (artifact.task_id, artifact.run_id) == (task.id, run.id)
        assert artifact.conversation_id == task.conversation_id
        assert artifact.metadata["model_call_id"] == str(detail.calls[0].id)
        assert artifact.metadata["result_message_id"] == run.checkpoint["result_message_id"]
    summary = items(detail)
    assert summary["table"]["artifact_id"] == str(published["text/csv"].id)
    assert summary["table"]["sha256"] == published["text/csv"].sha256
    assert (summary["table"]["rows"], summary["table"]["columns"]) == (2, 3)
    # Leitura independente direto do disco, fora do ArtifactStore.
    blob = (tmp_path / "artifacts" / "blobs" / str(published["text/csv"].id)).read_bytes()
    assert list(csv.DictReader(io.StringIO(blob.decode("utf-8"), newline="")))[1] == {
        "Região": "Sul",
        "Ação": 'Diz "sim"',
        "Valor": "ñandú",
    }
    report = (tmp_path / "artifacts" / "blobs" / str(published["text/markdown"].id)).read_text(
        encoding="utf-8"
    )
    assert report.startswith("# Relatório")
    # Nova execução do worker não republica nem chama o modelo outra vez.
    assert not asyncio.run(worker(database, [text], requests, artifacts).run_once())
    assert len(requests) == 1 and len(artifacts.list(agent.id, task.id)) == 2


def test_tasks_without_deliverables_keep_previous_prompt_and_no_files(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    task = service.create(agent.id, task_input())
    artifacts = ArtifactStore(database, tmp_path / "artifacts")
    requests = []
    assert asyncio.run(worker(database, [output(report="x")], requests, artifacts).run_once())
    assert REPORT not in requests[0]["messages"][-1]["content"]
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "completed"
    assert "publication" not in detail.runs[-1].checkpoint
    assert artifacts.list(agent.id, task.id) == []


def test_invalid_table_pauses_with_partial_publication_and_resume_regenerates(tmp_path):
    database, _, agent, service, task, artifacts = setup(tmp_path)
    requests = []
    contents = [output("Relatório", "a,b\n1"), output("Relatório revisto", "a,b\n1,2")]
    assert asyncio.run(worker(database, contents, requests, artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.runs[-1].error == "deliverable_invalid"
    assert detail.runs[-1].checkpoint["attention_required"] is True
    assert detail.runs[-1].checkpoint["result_ready"] is False
    summary = items(detail)
    assert summary["report"]["status"] == "ready"
    assert (summary["table"]["status"], summary["table"]["error_code"]) == (
        "invalid",
        "table_columns_inconsistent",
    )
    assert [item.media_type for item in artifacts.list(agent.id, task.id)] == ["text/markdown"]
    command(service, agent, task, "resume")
    assert asyncio.run(worker(database, contents, requests, artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "completed" and len(requests) == 2
    assert len(detail.runs) == 2
    assert sorted(item.run_id == detail.runs[-1].id for item in artifacts.list(agent.id, task.id))
    assert len(artifacts.list(agent.id, task.id)) == 3


def test_missing_section_and_declared_missing_data_are_explicit(tmp_path):
    database, _, agent, service, task, artifacts = setup(tmp_path)
    requests = []
    text = output(report="Parcial", missing="- Valores de 2026")
    assert asyncio.run(worker(database, [text], requests, artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    summary = detail.runs[-1].checkpoint["deliverables"]
    assert summary["partial"] is True and summary["missing_data"] == ["Valores de 2026"]
    assert items(detail)["table"]["error_code"] == "deliverable_missing"
    assert detail.runs[-1].error == "deliverable_invalid"
    (report,) = artifacts.list(agent.id, task.id)
    assert report.metadata["partial"] is True
    assert report.metadata["missing_data"] == ["Valores de 2026"]


def test_partial_but_valid_files_complete_with_marking(tmp_path):
    database, _, agent, service, task, artifacts = setup(tmp_path, kinds=("report",))
    text = output(report="Sem dados de 2026.", missing="Série 2026")
    assert asyncio.run(worker(database, [text], [], artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "completed"
    assert detail.runs[-1].checkpoint["deliverables"]["partial"] is True


def test_storage_unavailable_keeps_publication_pending_without_new_generation(tmp_path):
    database, _, agent, service, task, artifacts = setup(tmp_path, max_calls=1)
    requests = []
    text = output("Relatório", TABLE_TEXT)
    assert asyncio.run(worker(database, [text], requests, None).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.runs[-1].error == "artifact_storage_unavailable"
    assert detail.runs[-1].checkpoint["publication"]["status"] == "pending"
    assert {item["status"] for item in items(detail).values()} == {"pending"}
    # Sem retomada humana o worker não tenta de novo.
    assert not asyncio.run(worker(database, [text], requests, artifacts).run_once())
    command(service, agent, task, "resume")
    assert asyncio.run(worker(database, [text], requests, artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "completed"
    assert len(requests) == 1 and detail.task.calls_started == 1 and len(detail.runs) == 1
    assert len(artifacts.list(agent.id, task.id)) == 2


def test_crash_during_publication_requires_resume_and_readopts_same_ids(tmp_path, monkeypatch):
    database, store, agent, service, task, artifacts = setup(tmp_path)
    requests = []
    text = output("Relatório", TABLE_TEXT)
    original = ArtifactStore.create
    calls = []

    def crash_after_first(self, *args, **kwargs):
        calls.append(kwargs["artifact_id"])
        result = original(self, *args, **kwargs)
        if len(calls) == 1:
            raise KeyboardInterrupt  # processo encerrado entre as duas gravações
        return result

    monkeypatch.setattr(ArtifactStore, "create", crash_after_first)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(worker(database, [text], requests, artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "running"
    assert detail.runs[-1].checkpoint["publication"]["status"] == "started"
    with store.transaction() as uow:
        assert uow.execution.recover_expired(now=utc_now() + timedelta(seconds=31)) == 1
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.runs[-1].error == "artifact_publication_interrupted"
    assert detail.runs[-1].checkpoint["attention_required"] is True
    assert not asyncio.run(worker(database, [text], requests, artifacts).run_once())
    command(service, agent, task, "resume")
    assert asyncio.run(worker(database, [text], requests, artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "completed" and len(requests) == 1
    published = artifacts.list(agent.id, task.id)
    # O relatório interrompido é readotado pelo mesmo id; nenhum cartão duplicado.
    assert len(published) == 2 and len(calls) == 3 and calls[0] == calls[1]
    assert {item.id for item in published} == set(calls)
    assert detail.runs[-1].checkpoint["publication"]["attempts"] == 2


def test_pause_before_publication_and_redirect_supersedes_pending_files(tmp_path):
    database, store, agent, service, task, artifacts = setup(tmp_path)
    requests = []
    text = output("Relatório", TABLE_TEXT)
    pausing = worker(database, [text], requests, None)
    original = TaskWorker._finish

    def pause_then_finish(self, claim, *args, **kwargs):
        command(service, agent, task, "pause")
        return original(self, claim, *args, **kwargs)

    TaskWorker._finish = pause_then_finish
    try:
        assert asyncio.run(pausing.run_once())
    finally:
        TaskWorker._finish = original
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "paused"
    assert detail.runs[-1].checkpoint["publication"]["status"] == "pending"
    command(service, agent, task, "redirect", instruction="Use outra região.")
    assert detail.runs[-1].checkpoint["publication"]["status"] == "pending"
    redirected = service.detail(agent.id, task.id).runs[-1]
    assert redirected.checkpoint["publication"]["status"] == "superseded"
    command(service, agent, task, "resume")
    second = output("Relatório novo", "a\n1")
    assert asyncio.run(worker(database, [text, second], requests, artifacts).run_once())
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "completed" and len(requests) == 2
    assert "Use outra região." in requests[-1]["messages"][-1]["content"]
    published = artifacts.list(agent.id, task.id)
    assert len(published) == 2
    assert {item.run_id for item in published} == {detail.runs[-1].id}


def test_global_stop_before_publication_requeues_without_writing(tmp_path, monkeypatch):
    database, store, agent, service, task, artifacts = setup(tmp_path)
    requests = []
    text = output("Relatório", TABLE_TEXT)
    current = worker(database, [text], requests, artifacts)
    monkeypatch.setattr("bees_core.execution.blocked", lambda state, generation: True)
    with store.transaction() as uow:
        claim = uow.execution.claim_next(current.owner_id, now=utc_now(), ttl_seconds=30)
        generation = uow.safety.state().generation
    with store.transaction() as uow:
        run = uow.runs.get(claim.run.id)
        uow.runs.update(
            run.model_copy(
                update={"checkpoint": run.checkpoint | {"publication": {"status": "pending"}}}
            ),
            run.revision,
        )
    current._publish(claim, generation)
    detail = service.detail(agent.id, task.id)
    assert detail.task.status == "queued"
    assert detail.runs[-1].checkpoint["publication"]["status"] == "pending"
    assert artifacts.list(agent.id, task.id) == [] and requests == []


def test_api_style_input_round_trip_rejects_changed_deliverables(tmp_path):
    database, _, agent, _, service = fixture_state(tmp_path)
    value = task_input(deliverables=("report",))
    task = service.create(agent.id, value)
    assert task.metadata["deliverables"] == ["report"]
    assert service.create(agent.id, value).id == task.id
    with pytest.raises(Exception, match="idempotency_conflict"):
        service.create(agent.id, value.model_copy(update={"deliverables": ("report", "table")}))
    assert TaskInput.model_validate(value.model_dump(mode="json")) == value
