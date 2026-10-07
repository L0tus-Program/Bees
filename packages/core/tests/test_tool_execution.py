"""Integração do efeito builtin com leases reais e quarentena durável."""

import json
import traceback
from datetime import timedelta
from importlib import resources
from types import SimpleNamespace
from uuid import uuid4

import apsw
import pytest

from bees_core import tools
from bees_core.models import Action, Agent, ModelCall, Run, Task, utc_now
from bees_core.providers.contracts import ProviderConfig
from bees_core.storage.database import Database
from bees_core.storage.store import IntegrityError, InvalidTransition, RevisionConflict, StateStore
from bees_core.tasks import TaskCommandInput, TaskError, TaskInput, TaskService
from bees_core.tools import ToolError, ToolService


@pytest.fixture
def state(tmp_path):
    db = Database(tmp_path / "tool.sqlite3")
    db.initialize()
    store = StateStore(db)
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint="https://fixture.invalid/v1",
        model="fixture",
        capabilities={"text": True, "tool_calls": False},
    ).model_dump(mode="json")
    with store.transaction() as unit:
        agent = unit.agents.create(Agent(name="Teste de execução", provider_config=config))
    tasks = TaskService(db)
    task = tasks.create(
        agent.id, TaskInput(client_request_id=uuid4(), title="Teste", objective="Texto")
    )
    clock = [utc_now()]
    with store.transaction() as unit:
        claim = unit.execution.claim_next(uuid4(), now=clock[0], ttl_seconds=5)
    service = ToolService(db, clock=lambda: clock[0])
    plugin = service.install({"manifest": service.catalog()[0], "client_request_id": uuid4()})
    service.update_plugin(plugin.id, {"enabled": True, "expected_revision": 1})
    service.set_grant(
        agent.id, plugin.id, "text.normalize", {"enabled": True, "expected_revision": 0}
    )
    value = {
        "plugin_id": plugin.id,
        "tool_name": "text.normalize",
        "version": "1",
        "arguments": {"text": "  Resultado privado  ", "operation": "trim"},
        "client_request_id": uuid4(),
    }
    return SimpleNamespace(
        db=db,
        store=store,
        agent=agent,
        task=task,
        tasks=tasks,
        clock=clock,
        claim=claim,
        service=service,
        value=value,
    )


def prepare(state):
    return state.service.prepare(state.agent.id, state.claim.run.id, state.value, claim=state.claim)


def execute(state, action, *, claim=None):
    return state.service.execute(
        state.agent.id,
        action.id,
        state.value["arguments"],
        expected_revision=action.revision,
        claim=claim or state.claim,
    )


def control(state, kind, **kwargs):
    task = state.tasks.detail(state.agent.id, state.task.id).task
    value = TaskCommandInput(
        client_request_id=uuid4(), expected_revision=task.revision, kind=kind, **kwargs
    )
    return state.tasks.command(state.agent.id, task.id, value)


def model_intention(unit, state):
    task = unit.tasks.get(state.task.id)
    config = state.claim.run.provider_config
    request = {"messages": [{"role": "user", "content": "Teste"}]}
    return ModelCall(
        run_id=state.claim.run.id,
        ordinal=1,
        task_control_revision=task.control_revision,
        agent_revision=state.agent.revision,
        lease_generation=state.claim.generation,
        provider_config=config,
        request=request,
        request_hash=unit.model_calls.request_digest(config, request, {}),
    )


def test_binding_is_atomic_complete_immutable_and_private(state):
    action = prepare(state)
    binding = action.execution_binding
    assert binding["task_id"] == str(state.task.id)
    assert binding["owner_id"] == str(state.claim.owner_id)
    assert len(binding["leases"]) == 2
    assert {entry["resource_key"] for entry in binding["leases"]} == {
        "worker_slot:0",
        f"agent:{state.agent.id}",
    }
    assert "Resultado privado" not in json.dumps(binding)
    with state.store.transaction() as unit:
        with pytest.raises(IntegrityError):
            unit.actions.update(
                action.model_copy(
                    update={"execution_binding": binding | {"owner_id": str(uuid4())}}
                ),
                action.revision,
            )
    with state.db.transaction() as connection:
        with pytest.raises(apsw.ConstraintError):
            connection.execute(
                "UPDATE actions SET execution_binding_json=NULL WHERE id=?", (str(action.id),)
            )
    with state.store.transaction(write=False) as unit:
        assert all("execution_binding" not in event.payload for event in unit.events.list())


@pytest.mark.parametrize(
    "part", ["owner", "generation_global", "generation_agent", "leases", "task", "run"]
)
def test_forged_claim_never_prepares_or_dispatches(state, monkeypatch, part):
    action = prepare(state)
    claim = state.claim
    if part == "owner":
        claim = claim.model_copy(update={"owner_id": uuid4()})
    elif part.startswith("generation"):
        index = 0 if part.endswith("global") else 1
        leases = list(claim.leases)
        leases[index] = leases[index].model_copy(
            update={"generation": leases[index].generation + 1}
        )
        claim = claim.model_copy(update={"leases": tuple(leases)})
    elif part == "leases":
        claim = claim.model_copy(update={"leases": claim.leases[:1]})
    else:
        with state.store.transaction() as unit:
            other = unit.tasks.create(
                Task(agent_id=state.agent.id, title="Outra", objective="Outro")
            )
        if part == "task":
            claim = claim.model_copy(update={"task": other})
        else:
            claim = claim.model_copy(
                update={"run": claim.run.model_copy(update={"task_id": other.id})}
            )
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda _: pytest.fail("Claim falso iniciou efeito")
    )
    with pytest.raises(RevisionConflict):
        execute(state, action, claim=claim)
    with pytest.raises(RevisionConflict):
        state.service.prepare(
            state.agent.id,
            state.claim.run.id,
            state.value | {"client_request_id": uuid4()},
            claim=claim,
        )
    assert state.service.get_invocation(state.agent.id, action.id).status == "ready"


def test_expired_prepared_action_is_cancelled_and_cannot_transfer_to_new_owner(state, monkeypatch):
    action = prepare(state)
    state.clock[0] += timedelta(seconds=6)
    with state.store.transaction() as unit:
        second = unit.execution.claim_next(uuid4(), now=state.clock[0], ttl_seconds=5)
    assert second is not None and second.owner_id != state.claim.owner_id
    assert second.generation > state.claim.generation
    assert state.service.get_invocation(state.agent.id, action.id).status == "cancelled"
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda _: pytest.fail("Ação antiga repetiu efeito")
    )
    with pytest.raises((RevisionConflict, ToolError)):
        execute(state, action)
    # Replay é consulta, não transfere nem recria o vínculo ao dono novo.
    assert (
        state.service.prepare(state.agent.id, second.run.id, state.value, claim=second).status
        == "cancelled"
    )


@pytest.mark.parametrize("kind", ["pause", "cancel", "redirect", "agent_revoke"])
def test_effect_result_after_control_change_is_preserved_but_never_task_result(
    state, monkeypatch, kind
):
    action = prepare(state)

    def effect(_):
        if kind == "agent_revoke":
            with state.store.transaction() as unit:
                unit.agents.update(state.agent.model_copy(update={"status": "paused"}), 1)
        elif kind == "redirect":
            control(state, kind, instruction="Outra intenção")
        else:
            control(state, kind)
        return {"text": "Efeito confirmado em memória"}

    monkeypatch.setattr(tools, "_execute_builtin", effect)
    result = execute(state, action)
    assert result.status == "confirmed" and result.metadata["obsolete"] is True
    detail = state.tasks.detail(state.agent.id, state.task.id)
    assert not detail.messages and "result_message_id" not in detail.runs[-1].checkpoint
    if kind == "cancel":
        assert detail.task.status == "cancelled"
    assert detail.actions[0].obsolete is True
    assert "Efeito confirmado" not in detail.actions[0].model_dump_json()


@pytest.mark.parametrize("fails", [False, True])
def test_expired_owner_cannot_confirm_or_mark_unknown_even_after_local_success(
    state, monkeypatch, fails
):
    action = prepare(state)

    def effect(_):
        state.clock[0] += timedelta(seconds=6)
        if fails:
            raise RuntimeError("texto privado que não pode vazar")
        return {"text": "Resultado tarde demais"}

    monkeypatch.setattr(tools, "_execute_builtin", effect)
    with pytest.raises(RevisionConflict) as exc:
        execute(state, action)
    assert "texto privado" not in "".join(traceback.format_exception(exc.value))
    assert state.service.get_invocation(state.agent.id, action.id).status == "dispatch_started"
    with state.store.transaction() as unit:
        assert unit.execution.recover_expired(now=state.clock[0]) == 1
    recovered = state.service.get_invocation(state.agent.id, action.id)
    assert recovered.status == "outcome_unknown" and recovered.result is None
    detail = state.tasks.detail(state.agent.id, state.task.id)
    assert detail.task.status == "paused" and detail.unknown_tool_actions == 1
    assert detail.runs[-1].checkpoint["progress"] == "paused"
    assert detail.runs[-1].checkpoint["attention_required"] is True


def test_unknown_quarantines_task_calls_queue_and_requires_durable_human_ack(state, monkeypatch):
    action = prepare(state)
    monkeypatch.setattr(tools, "_execute_builtin", lambda _: {"invalid": "output"})
    result = execute(state, action)
    assert result.status == "outcome_unknown"
    detail = state.tasks.detail(state.agent.id, state.task.id)
    assert detail.task.revision > state.claim.task.revision
    assert detail.runs[-1].checkpoint["progress"] == "paused"
    assert detail.runs[-1].checkpoint["attention_required"] is True
    with pytest.raises(ToolError):
        state.service.prepare(
            state.agent.id,
            state.claim.run.id,
            state.value | {"client_request_id": uuid4()},
            claim=state.claim,
        )
    with state.store.transaction() as unit:
        with pytest.raises(RevisionConflict):
            unit.execution.begin_call(state.claim, model_intention(unit, state), now=state.clock[0])
        task = unit.tasks.get(state.task.id)
        unit.tasks.update(
            task.model_copy(update={"status": "queued", "desired_state": "running"}), task.revision
        )
        unit.execution.release(state.claim)
        assert unit.execution.claim_next(uuid4(), now=state.clock[0], ttl_seconds=5) is None
        task = unit.tasks.get(state.task.id)
        unit.tasks.update(
            task.model_copy(update={"status": "paused", "desired_state": "paused"}), task.revision
        )
    with pytest.raises(TaskError, match="unknown_requires_ack"):
        control(state, "resume")
    with pytest.raises(TaskError, match="unknown_requires_ack"):
        control(state, "redirect", instruction="Não é reconhecimento")
    task = state.tasks.detail(state.agent.id, state.task.id).task
    command = TaskCommandInput(
        client_request_id=uuid4(),
        expected_revision=task.revision,
        kind="resume",
        acknowledge_unknown=True,
    )
    queued = state.tasks.command(state.agent.id, task.id, command)
    reopened = TaskService(Database(state.db.path))
    assert reopened.command(state.agent.id, task.id, command) == queued
    detail = reopened.detail(state.agent.id, task.id)
    assert detail.unknown_tool_actions == 0 and detail.actions[0].acknowledged
    assert detail.actions[0].status == "outcome_unknown" and detail.task.calls_started == 0
    with state.store.transaction() as unit:
        old = unit.actions.get(action.id)
        reconciled = unit.actions.reconcile(
            action.id,
            "failed_no_effect",
            expected_revision=old.revision,
            evidence_ref="proof:local_no_effect",
        )
        assert reconciled.unknown_acknowledged_at == old.unknown_acknowledged_at
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda _: pytest.fail("Ack/replay repetiu efeito")
    )
    assert execute(state, action).status == "failed_no_effect"


def test_inflight_action_guards_other_dispatch_and_resume_and_redirect(state, monkeypatch):
    action = prepare(state)

    def effect(_):
        with state.store.transaction() as unit:
            with pytest.raises(InvalidTransition):
                unit.execution.begin_call(
                    state.claim, model_intention(unit, state), now=state.clock[0]
                )
        with pytest.raises(ToolError):
            state.service.prepare(
                state.agent.id,
                state.claim.run.id,
                state.value | {"client_request_id": uuid4()},
                claim=state.claim,
            )
        control(state, "pause")
        with state.store.transaction() as unit:
            task = unit.tasks.get(state.task.id)
            unit.tasks.update(task.model_copy(update={"status": "paused"}), task.revision)
        with pytest.raises(TaskError, match="action_in_flight"):
            control(state, "resume")
        control(state, "redirect", instruction="Nova intenção fica registrada")
        return {"text": "Confirmado"}

    monkeypatch.setattr(tools, "_execute_builtin", effect)
    assert execute(state, action).status == "confirmed"


def test_unknown_after_cancel_preserves_terminal_progress_and_private_journal(state, monkeypatch):
    action = prepare(state)

    def effect(_):
        control(state, "cancel")
        return {"invalid": "não registrar conteúdo"}

    monkeypatch.setattr(tools, "_execute_builtin", effect)
    assert execute(state, action).status == "outcome_unknown"
    detail = state.tasks.detail(state.agent.id, state.task.id)
    assert detail.task.status == detail.task.desired_state == "cancelled"
    assert detail.runs[-1].status == "cancelled"
    assert detail.runs[-1].checkpoint["progress"] == "cancelled"
    assert detail.runs[-1].checkpoint["attention_required"] is True
    assert not detail.messages and detail.unknown_tool_actions == 1


def test_unknown_transition_invalidates_previously_selected_ack_cas(state, monkeypatch):
    action = prepare(state)
    task = state.tasks.detail(state.agent.id, state.task.id).task
    stale = TaskCommandInput(
        client_request_id=uuid4(),
        expected_revision=task.revision,
        kind="resume",
        acknowledge_unknown=True,
    )
    monkeypatch.setattr(tools, "_execute_builtin", lambda _: {"text": 123})
    execute(state, action)
    with pytest.raises(RevisionConflict):
        state.tasks.command(state.agent.id, task.id, stale)
    assert state.tasks.detail(state.agent.id, task.id).unknown_tool_actions == 1


def test_supervisor_requires_old_owner_stopped_opaque_evidence_and_cas(state):
    action = prepare(state)
    with state.store.transaction() as unit:
        action = unit.actions.update(
            action.model_copy(update={"status": "dispatch_started"}), action.revision
        )
    kwargs = dict(
        expected_revision=action.revision,
        evidence_ref="process:stopped",
        owner_id=state.claim.owner_id,
    )
    with pytest.raises(ToolError, match="lease"):
        state.service.mark_unknown(state.agent.id, action.id, **kwargs)
    with pytest.raises(RevisionConflict):
        state.service.mark_unknown(state.agent.id, action.id, **(kwargs | {"owner_id": uuid4()}))
    with state.store.transaction() as unit:
        unit.execution.release(state.claim)
    result = state.service.mark_unknown(state.agent.id, action.id, **kwargs)
    assert result.status == "outcome_unknown"
    assert state.tasks.detail(state.agent.id, state.task.id).task.status == "paused"


def test_active_time_limit_blocks_prepare_and_dispatch_without_model_charge(state, monkeypatch):
    action = prepare(state)
    with state.store.transaction() as unit:
        task = unit.tasks.get(state.task.id)
        unit.tasks.update(
            task.model_copy(update={"active_milliseconds": task.max_active_seconds * 1000}),
            task.revision,
        )
    monkeypatch.setattr(
        tools, "_execute_builtin", lambda _: pytest.fail("Orçamento esgotado iniciou efeito")
    )
    with pytest.raises(ToolError, match="Tempo ativo"):
        execute(state, action)
    with pytest.raises(ToolError, match="Tempo ativo"):
        state.service.prepare(
            state.agent.id,
            state.claim.run.id,
            state.value | {"client_request_id": uuid4()},
            claim=state.claim,
        )


def test_independent_unknown_guards_begin_call_even_when_task_state_was_left_running(state):
    action = prepare(state)
    with state.store.transaction() as unit:
        dispatched = unit.actions.update(
            action.model_copy(update={"status": "dispatch_started"}), action.revision
        )
        unit.actions.update(
            dispatched.model_copy(update={"status": "outcome_unknown"}), dispatched.revision
        )
        with pytest.raises(InvalidTransition, match="Ação"):
            unit.execution.begin_call(state.claim, model_intention(unit, state), now=state.clock[0])
        assert unit.tasks.get(state.task.id).calls_started == 0


def test_renewed_claim_can_finish_and_new_generation_cannot_take_ready_binding(state):
    action = prepare(state)
    state.clock[0] += timedelta(seconds=2)
    with state.store.transaction() as unit:
        renewed = unit.execution.renew(state.claim, now=state.clock[0], ttl_seconds=5)
    assert execute(state, action, claim=renewed).status == "confirmed"
    state.value = state.value | {"client_request_id": uuid4()}
    next_action = prepare(state)
    # A liberação é um ato confiável do supervisor; novo dono não recebe bindings antigos.
    with state.store.transaction() as unit:
        unit.execution.release(renewed)
        task = unit.tasks.get(state.task.id)
        unit.tasks.update(task.model_copy(update={"status": "queued"}), task.revision)
        second = unit.execution.claim_next(uuid4(), now=state.clock[0], ttl_seconds=5)
    assert second is not None
    with pytest.raises(RevisionConflict, match="outro dono"):
        execute(state, next_action, claim=second)


def test_guards_and_ack_cover_unknown_in_runs_beyond_display_page(state):
    with state.store.transaction() as unit:
        for _ in range(1001):
            run = unit.runs.create(
                Run(task_id=state.task.id, provider_config=state.claim.run.provider_config)
            )
        action = unit.actions.create(
            Action(run_id=state.claim.run.id, tool_name="text.normalize", status="outcome_unknown")
        )
        task = unit.tasks.get(state.task.id)
        unit.tasks.update(
            task.model_copy(update={"status": "paused", "desired_state": "paused"}), task.revision
        )
        unit.execution.release(state.claim)
    detail = state.tasks.detail(state.agent.id, state.task.id)
    assert len(detail.runs) == 1000 and detail.unknown_tool_actions == 1
    assert detail.runs[-1].id == run.id and detail.has_more
    assert action.run_id not in {entry.id for entry in detail.runs}
    assert detail.actions[0].id == action.id
    with pytest.raises(TaskError, match="unknown_requires_ack"):
        control(state, "resume")
    control(state, "resume", acknowledge_unknown=True)
    with state.store.transaction(write=False) as unit:
        assert unit.actions.get(action.id).unknown_acknowledged_at is not None
    detail = state.tasks.detail(state.agent.id, state.task.id)
    assert detail.unknown_tool_actions == 0
    with state.store.transaction() as unit:
        latest = unit.runs.latest(state.task.id)
        assert detail.runs[-1].id == latest.id
        claimed = unit.execution.claim_next(uuid4(), now=state.clock[0], ttl_seconds=5)
    assert claimed.run.id == latest.id


@pytest.mark.parametrize("last_status", ["queued", "completed"])
def test_claim_picks_latest_after_page_limit_and_never_old_unfinished(state, last_status):
    with state.store.transaction() as unit:
        for _ in range(1001):
            latest = unit.runs.create(
                Run(task_id=state.task.id, provider_config=state.claim.run.provider_config)
            )
        if last_status == "completed":
            latest = unit.runs.update(
                latest.model_copy(update={"status": "completed"}), latest.revision
            )
        unit.execution.release(state.claim)
        task = unit.tasks.get(state.task.id)
        unit.tasks.update(task.model_copy(update={"status": "queued"}), task.revision)
        claimed = unit.execution.claim_next(uuid4(), now=state.clock[0], ttl_seconds=5)
        assert claimed.run.id == unit.runs.latest(state.task.id).id
    if last_status == "queued":
        assert claimed.run.id == latest.id
    else:
        assert claimed.run.id != latest.id and claimed.run.id != state.claim.run.id


def test_upgrade_six_to_seven_preserves_legacy_unknown_fail_closed(tmp_path, monkeypatch):
    from bees_core.storage import database as module

    original = resources.files("bees_core.storage.migrations")
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for file in original.iterdir():
        if file.name.endswith(".sql") and int(file.name[:4]) < 7:
            (migrations / file.name).write_bytes(file.read_bytes())
    monkeypatch.setattr(module.resources, "files", lambda _: migrations)
    db = Database(tmp_path / "upgrade.sqlite3")
    db.initialize()
    # O software schema6 não conhece as colunas novas: SQL representa esse escritor antigo.
    with StateStore(db).transaction() as unit:
        agent = unit.agents.create(Agent(name="Legado"))
        task = unit.tasks.create(Task(agent_id=agent.id, title="Legado", objective="Texto"))
        run = unit.runs.create(Run(task_id=task.id))
    action_id = uuid4()
    with db.transaction() as connection:
        connection.execute(
            "INSERT INTO actions(id,run_id,tool_name,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                str(action_id),
                str(run.id),
                "text.normalize",
                "outcome_unknown",
                utc_now().isoformat(),
                utc_now().isoformat(),
            ),
        )
    (migrations / "0007_action_execution.sql").write_bytes(
        (original / "0007_action_execution.sql").read_bytes()
    )
    db.initialize()
    assert db.schema_version() == 7 and len(list(tmp_path.glob("*.backup-*.sqlite3"))) == 1
    detail = TaskService(db).detail(agent.id, task.id)
    assert detail.unknown_tool_actions == 1
    with StateStore(db).transaction() as unit:
        assert unit.execution.claim_next(uuid4(), now=utc_now(), ttl_seconds=5) is None
        assert unit.actions.get(action_id).execution_binding is None
