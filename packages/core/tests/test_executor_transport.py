"""BEES-010.1: processo/IPC/journal próprios reais; não comprova VM ou sandbox."""

import os
from dataclasses import dataclass
from uuid import uuid4

import pytest
from test_tools import invocation, prepare
from test_tools import setup as _builtin_setup

from bees_core.executor_transport.authority import CoreToolAuthority
from bees_core.executor_transport.client import ProcessTextResource, ProcessTextTransport
from bees_core.executor_transport.journal import Journal
from bees_core.security.hosts import HostService
from bees_core.security.identity import IdentityService

builtin_setup = _builtin_setup


@dataclass
class System:
    builtin: tuple
    resource: ProcessTextResource
    transport: ProcessTextTransport

    def prepared(self, **changes):
        action, value = prepare(self.builtin, invocation(self.builtin, **changes))
        return self.authority(action, value)

    def authority(self, action, value):
        _, _, service, agent, *_ = self.builtin
        return CoreToolAuthority(
            service,
            self.resource.identity,
            agent.id,
            action.id,
            value["arguments"],
            expected_revision=action.revision,
            claim=service.test_claim,
        )


@pytest.fixture
def system(builtin_setup, tmp_path):
    if os.name != "posix":
        pytest.skip("Adaptador inicial exige POSIX; não relaxa ACL/handles no Windows.")
    database = builtin_setup[0]
    identity = IdentityService(database)
    identity.setup(identity.issue_bootstrap(), "Pessoa do executor", "senha de teste 123456")
    invite = HostService(database).issue_invite(uuid4())
    resource = ProcessTextResource.initialize(
        tmp_path / "executor",
        str(invite.installation_id),
        str(builtin_setup[3].id),
    )
    return System(builtin_setup, resource, ProcessTextTransport(resource))


def test_own_process_text_ipc_receipt_replay_and_private_journal(system):
    authority = system.prepared()
    action = system.transport.execute(authority)
    assert action.status == "confirmed" and action.result == {"text": "Olá Bees"}
    request = action.metadata["executor_transport"]
    receipt = system.transport.inspect(request)
    assert receipt["status"] == "receipt" and receipt["cached"] is True
    assert receipt["result"] == action.result
    assert system.transport.execute(authority) == action
    journal = Journal(system.resource.directory, system.resource.identity)
    try:
        assert journal.connection.execute(
            "SELECT status FROM events ORDER BY sequence"
        ).fetchall() == [("intent",), ("started",), ("receipt",)]
        assert journal.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        journal.close()
    assert ProcessTextResource.open(system.resource.directory).identity == system.resource.identity


def states(system):
    journal = Journal(system.resource.directory, system.resource.identity)
    try:
        return journal.connection.execute("SELECT status FROM events ORDER BY sequence").fetchall()
    finally:
        journal.close()


def change_authority(system, change):
    database, store, service, agent, _, task, _, plugin, grant = system.builtin
    if change == "grant":
        service.set_grant(
            agent.id,
            plugin.id,
            "text.normalize",
            {"enabled": False, "expected_revision": grant.revision},
        )
    elif change == "plugin":
        service.update_plugin(plugin.id, {"enabled": False, "expected_revision": plugin.revision})
    elif change in ("deny", "ask"):
        from bees_core.policies import PolicyService

        PolicyService(database).create(
            agent.id,
            {
                "name": "Política corrente",
                "effect": change,
                "scope": {"tool_name": "text.normalize", "action": "transform"},
            },
        )
    elif change in ("stop", "stop_resume"):
        from bees_core.safety import SafetyCommand, SafetyService

        safety = SafetyService(database)
        safety.command(SafetyCommand(client_request_id=uuid4(), kind="stop", expected_revision=1))
        if change == "stop_resume":
            safety.command(
                SafetyCommand(client_request_id=uuid4(), kind="resume", expected_revision=2)
            )
    elif change == "lease":
        with database.transaction() as connection:
            connection.execute("UPDATE execution_leases SET expires_at='2000-01-01T00:00:00+00:00'")
    else:
        with store.transaction() as unit:
            current = unit.tasks.get(task.id)
            update = {"control_revision": 1} if change == "control" else {"desired_state": "paused"}
            unit.tasks.update(current.model_copy(update=update), current.revision)


@pytest.mark.parametrize(
    "change",
    [
        "grant",
        "plugin",
        "deny",
        "ask",
        "stop",
        "stop_resume",
        "lease",
        "control",
        "pause",
    ],
)
def test_online_authority_changes_before_go_start_no_effect(system, monkeypatch, change):
    from bees_core.storage.store import RevisionConflict
    from bees_core.tools import ToolError

    authority = system.prepared()
    actual_begin = authority.begin

    def changed(request):
        # Alterações reais no SQLite entre PREPARED do filho e GO da autoridade.
        change_authority(system, change)
        return actual_begin(request)

    monkeypatch.setattr(authority, "begin", changed)
    with pytest.raises((ToolError, RevisionConflict)):
        system.transport.execute(authority)
    current = authority.service.get_invocation(authority.agent_id, authority.action_id)
    assert current.status == "ready" and current.result is None
    assert states(system) == [("intent",), ("stopped",)]


@pytest.mark.parametrize("change", ["grant", "deny", "stop_resume", "lease", "control", "pause"])
def test_authority_lost_after_effect_preserves_receipt_without_stale_publication(
    system, monkeypatch, change
):
    authority = system.prepared()
    actual_complete = authority.complete

    def changed(request, observed):
        change_authority(system, change)
        return actual_complete(request, observed)

    monkeypatch.setattr(authority, "complete", changed)
    action = system.transport.execute(authority)
    assert action.status == "outcome_unknown" and action.result is None
    assert action.metadata["error_code"] == "executor_publication_fenced"
    assert system.transport.inspect(action.metadata["executor_transport"])["status"] == "receipt"
    assert states(system) == [("intent",), ("started",), ("receipt",)]
    assert system.transport.execute(authority) == action
    with authority.service.store.transaction(write=False) as unit:
        assert unit.tasks.get(authority.claim.task.id).status == "paused"


@pytest.mark.parametrize(
    "change",
    [
        "credential",
        "provisioner",
        "wrong_bx",
        "agent_id",
        "resource_id",
        "environment_id",
        "identity",
        "input",
    ],
)
def test_private_ipc_refuses_wrong_credentials_identity_and_input_before_intent(system, change):
    from bees_core.executor_transport.contracts import TransportError, write_frame

    authority = system.prepared()
    _, request = authority.seal()
    with system.transport._session() as (stream, credential, deadline):
        command = {
            "type": "invoke",
            "credential": credential,
            "envelope": request,
            "arguments": authority.arguments,
        }
        if change in ("credential", "provisioner", "wrong_bx"):
            command["credential"] = (
                "bx_" + "A" * 43
                if change == "wrong_bx"
                else ("bp_" if change == "provisioner" else "bh_") + credential[3:]
            )
        elif change == "input":
            command["arguments"] = authority.arguments | {"text": "Entrada trocada"}
        elif change == "identity":
            command["envelope"] = request | {"identity": "admin"}
        else:
            command["envelope"] = request | {change: str(uuid4())}
        write_frame(stream, command, deadline)
        with pytest.raises(TransportError):
            system.transport._receive(stream, deadline)
    assert states(system) == []


def test_resource_and_ids_are_not_selected_by_arguments_or_other_instance(system, tmp_path):
    from bees_core.executor_transport.contracts import TransportError

    authority = system.prepared()
    authority.seal()
    another = ProcessTextResource.initialize(
        tmp_path / "another-resource",
        system.resource.identity.installation_id,
        system.resource.identity.agent_id,
    )
    with pytest.raises(TransportError, match="binding_mismatch"):
        ProcessTextTransport(another).execute(authority)
    assert states(system) == []


def test_real_resource_lock_rejects_concurrent_process_without_go(system):
    from bees_core.executor_transport.contracts import TransportError

    authority = system.prepared()
    journal = Journal(system.resource.directory, system.resource.identity)
    try:
        with pytest.raises(TransportError, match="resource_busy"):
            system.transport.execute(authority)
    finally:
        journal.close()
    assert states(system) == []
    assert (
        authority.service.get_invocation(authority.agent_id, authority.action_id).status == "ready"
    )


def fault_command(monkeypatch, transport, body):
    """Apenas harness injeta queda no código confiável; não é opção do produto."""
    original = transport._command

    def command(descriptor):
        arguments = original(descriptor)
        code = arguments[-2]
        code = code.replace(
            "from bees_core.executor_transport.runtime import main;main()",
            "import bees_core.executor_transport.runtime as runtime\n" + body + "\nruntime.main()",
        )
        return arguments[:-2] + [code, arguments[-1]]

    monkeypatch.setattr(transport, "_command", command)


@pytest.mark.parametrize("point", ["after_effect", "after_receipt", "invalid_result"])
def test_real_process_crash_or_invalid_result_never_repeats_effect(system, monkeypatch, point):
    authority = system.prepared()
    if point in ("after_effect", "invalid_result"):
        end = (
            "os._exit(73)"
            if point == "after_effect"
            else "return {'unexpected': 'private-invalid'}"
        )
        body = (
            "import os,pathlib\noriginal=runtime.normalize\n"
            "def fail(value):\n"
            " output=original(value)\n"
            " pathlib.Path('effect-count').write_text('1')\n " + end + "\n"
            "runtime.normalize=fail"
        )
    else:
        body = (
            "import os,pathlib\noriginal=runtime.write_frame\n"
            "def fail(stream,value,deadline):\n"
            " if value.get('type')=='state' and value.get('status')=='receipt':\n"
            "  pathlib.Path('effect-count').write_text('1')\n"
            "  os._exit(73)\n"
            " return original(stream,value,deadline)\n"
            "runtime.write_frame=fail"
        )
    with monkeypatch.context() as fault:
        fault_command(fault, system.transport, body)
        action = system.transport.execute(authority)
    assert action.status == "outcome_unknown" and action.result is None
    assert (system.resource.directory / "effect-count").read_text() == "1"
    private = system.transport.inspect(action.metadata["executor_transport"])
    assert private["status"] == ("receipt" if point == "after_receipt" else "unknown")
    before = states(system)
    assert system.transport.execute(authority) == action
    assert states(system) == before
    assert sum(status == ("started",) for status in before) == 1


def test_child_has_clean_environment_fixed_command_and_no_core_database(system, monkeypatch):
    import json
    import subprocess

    observed = []
    popen = subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        observed.append((args, kwargs, process.pid))
        return process

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-secret-never-in-child")
    monkeypatch.setattr("bees_core.executor_transport.client.subprocess.Popen", launch)
    authority = system.prepared()
    body = (
        "import os,pathlib,sys,json\noriginal=runtime.normalize\n"
        "def checked(value):\n"
        " assert set(os.environ) <= {'LC_CTYPE'}\n"
        " assert 'apsw' not in sys.modules and 'sqlite3' in sys.modules\n"
        " assert not any(name.startswith('bees_core.storage') or "
        "name.startswith('bees_core.providers') for name in sys.modules)\n"
        " pathlib.Path('child-proof').write_text(str(os.getpid()))\n"
        " return original(value)\n"
        "runtime.normalize=checked"
    )
    fault_command(monkeypatch, system.transport, body)
    action = system.transport.execute(authority)
    assert action.status == "confirmed"
    [(arguments, kwargs, pid)] = observed
    assert pid != os.getpid() and (system.resource.directory / "child-proof").read_text() == str(
        pid
    )
    assert kwargs["env"] == {} and kwargs["shell"] is False and kwargs["close_fds"] is True
    assert not any(str(system.builtin[0].path) in value for value in arguments[0])
    assert "test-secret-never-in-child" not in repr(arguments)
    assert "bx_" not in repr(arguments)
    assert "OPENROUTER_API_KEY" not in json.dumps(action.model_dump(mode="json"))


@pytest.mark.parametrize(
    "data",
    [
        b"{} {}",
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b"[]",
        b"null",
        b"\xff",
        b"",
        b'{"x":1',
        b"{" + b" " * 32768 + b"}",
    ],
)
def test_closed_utf8_json_rejects_duplicates_trailing_partial_and_large_frames(data):
    from bees_core.executor_transport.contracts import TransportError, decode

    with pytest.raises(TransportError):
        decode(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("protocol", True),
        ("protocol", 1.0),
        ("protocol", 2),
        ("tool_name", "shell"),
        ("tool_version", "2"),
        ("generation", True),
        ("generation", 0),
        ("safety_generation", -1),
        ("control_revision", False),
        ("deadline_ms", 0),
        ("binding_hash", "unknown"),
        ("input_hash", "A" * 64),
        ("owner_id", "0" * 32),
        ("agent_id", str(uuid4())),
        ("identity", "root"),
        ("extra", "unexpected"),
    ],
)
def test_envelope_is_closed_strict_and_bound_to_own_resource(system, field, value):
    from bees_core.executor_transport.contracts import TransportError, envelope

    _, request = system.prepared().seal()
    with pytest.raises(TransportError):
        envelope(request | {field: value}, system.resource.identity)


@pytest.mark.parametrize(
    "value",
    [
        {"text": "x" * 4097, "operation": "trim"},
        {"text": "😀" * 3000, "operation": "upper"},
        {"text": "\x00", "operation": "trim"},
        {"text": "\ud800", "operation": "trim"},
        {"text": 1, "operation": "trim"},
        {"text": "x", "operation": "shell"},
        {"text": "x", "operation": "trim", "environment_id": "personal"},
    ],
)
def test_argument_contract_never_accepts_capability_or_scope_injection(value):
    from bees_core.executor_transport.contracts import TransportError, arguments

    with pytest.raises(TransportError):
        arguments(value, "a" * 64)


@pytest.mark.parametrize(
    "value",
    [
        {"text": "x" * 8193},
        {"text": "😀" * 5000},
        {"text": "\ud800"},
        {"text": 0},
        {},
        {"text": "x", "stdout": "private"},
    ],
)
def test_result_contract_is_bounded_complete_and_closed(value):
    from bees_core.executor_transport.contracts import TransportError, result

    with pytest.raises(TransportError):
        result(value)


def test_framing_checks_size_before_body_and_uses_one_total_deadline():
    import socket
    import struct
    import time

    from bees_core.executor_transport.contracts import MAX_FRAME, TransportError, read_frame

    left, right = socket.socketpair()
    try:
        right.sendall(struct.pack("!I", MAX_FRAME + 1))
        with pytest.raises(TransportError, match="frame_invalid"):
            read_frame(left, time.monotonic() + 1)
    finally:
        left.close()
        right.close()
    left, right = socket.socketpair()
    try:
        right.sendall(struct.pack("!I", 100) + b"{")
        started = time.monotonic()
        with pytest.raises(TransportError, match="executor_timeout"):
            read_frame(left, started + 0.05)
        assert time.monotonic() - started < 1
    finally:
        left.close()
        right.close()


def test_local_journal_conflict_replay_fencing_and_sql_integrity(system):
    import sqlite3

    from bees_core.executor_transport.contracts import TransportError

    _, request = system.prepared().seal()
    journal = Journal(system.resource.directory, system.resource.identity)
    try:
        journal.intent(request)
        with pytest.raises(TransportError, match="request_conflict"):
            journal.intent(request | {"input_hash": "0" * 64})
        with pytest.raises(TransportError, match="request_conflict"):
            journal.intent(request | {"action_id": str(uuid4())})
        with pytest.raises(sqlite3.IntegrityError, match="transition"):
            journal.connection.execute("UPDATE actions SET status='receipt',response_json='{}'")
        journal.start(request)
        journal.finish(request, {"text": "Registro privado"})
        with pytest.raises(TransportError, match="request_replay"):
            journal.start(request)
        for sql in (
            "UPDATE actions SET status='unknown'",
            "DELETE FROM actions",
            "UPDATE events SET status='unknown'",
            "DELETE FROM events",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                journal.connection.execute(sql)
        # Mesmo fence com outro dono/run não recebe acesso ao recurso.
        with pytest.raises(TransportError, match="executor_fenced"):
            journal.intent(
                request
                | {
                    "action_id": str(uuid4()),
                    "request_id": str(uuid4()),
                    "owner_id": str(uuid4()),
                    "run_id": str(uuid4()),
                }
            )
    finally:
        journal.close()


def test_private_storage_symlinks_and_hardlinks_fail_closed(system, tmp_path):
    from bees_core.executor_transport.contracts import TransportError
    from bees_core.executor_transport.journal import private

    file = system.resource.directory / "identity.json"
    linked = tmp_path / "identity-link"
    linked.symlink_to(file)
    with pytest.raises(TransportError, match="private_storage"):
        private(linked)
    os.link(file, tmp_path / "hardlink")
    with pytest.raises(TransportError, match="private_storage"):
        ProcessTextResource.open(system.resource.directory)


def test_mismatched_or_partial_local_journal_is_not_repaired(system):
    from bees_core.executor_transport.contracts import TransportError

    file = system.resource.directory / "identity.json"
    before = file.read_bytes()
    file.write_bytes(b'{"format":1}')
    with pytest.raises(TransportError):
        Journal(system.resource.directory, system.resource.identity)
    assert file.read_bytes() == b'{"format":1}'
    file.write_bytes(before)
    with pytest.raises(TransportError, match="already_present"):
        ProcessTextResource.initialize(
            system.resource.directory,
            system.resource.identity.installation_id,
            system.resource.identity.agent_id,
        )
    assert file.read_bytes() == before


def test_sealed_request_cannot_change_deadline_or_target_and_never_publishes_raw_text(system):
    import json

    from bees_core.activity import ActivityService
    from bees_core.executor_transport.contracts import TransportError

    secret = "entrada-privada-deste-teste"
    authority = system.prepared(arguments={"text": secret, "operation": "trim"})
    sealed, request = authority.seal()
    assert secret not in json.dumps(request)
    for change in (
        {"deadline_ms": request["deadline_ms"] + 1000},
        {"environment_id": str(uuid4())},
        {"owner_id": str(uuid4())},
    ):
        with pytest.raises(TransportError):
            authority.begin(request | change)
    authority.expected_revision = sealed.revision
    action = system.transport.execute(authority)
    assert action.status == "confirmed" and action.result == {"text": secret}
    with authority.service.store.transaction(write=False) as unit:
        assert secret not in json.dumps(
            [event.model_dump(mode="json") for event in unit.events.list()]
        )
    assert (
        secret not in ActivityService(system.builtin[0]).list(authority.agent_id).model_dump_json()
    )


def test_concurrent_same_action_has_single_real_process_effect(system):
    from concurrent.futures import ThreadPoolExecutor

    from bees_core.storage.store import RevisionConflict
    from bees_core.tools import ToolError

    authority = system.prepared()

    def execute(_):
        try:
            return system.transport.execute(authority).status
        except RevisionConflict, ToolError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(execute, range(2)))
    assert "confirmed" in outcomes
    assert all(item in ("confirmed", "conflict") for item in outcomes)
    assert states(system) == [("intent",), ("started",), ("receipt",)]


def test_sealed_process_action_never_falls_back_to_legacy_builtin(system, monkeypatch):
    from bees_core import tools
    from bees_core.tools import ToolError

    authority = system.prepared()
    action, _ = authority.seal()
    monkeypatch.setattr(tools, "_execute_builtin", lambda value: pytest.fail("Fallback executou"))
    with pytest.raises(ToolError) as failure:
        authority.service.execute(
            authority.agent_id,
            action.id,
            authority.arguments,
            expected_revision=action.revision,
            claim=authority.claim,
        )
    assert failure.value.code == "executor_transport_required"
    assert states(system) == []
    authority.expected_revision = action.revision
    completed = system.transport.execute(authority)
    assert completed.status == "confirmed"
    # Consultar o resultado terminal pela API interna antiga permanece somente leitura.
    assert (
        authority.service.execute(
            authority.agent_id,
            action.id,
            authority.arguments,
            expected_revision=completed.revision,
            claim=authority.claim,
        )
        == completed
    )


@pytest.mark.parametrize("change", ["nonce", "request_hash", "credential", "deadline"])
def test_private_go_is_single_request_bound_and_deadline_checked_before_start(system, change):
    import time

    from bees_core.executor_transport.contracts import TransportError, digest, write_frame

    authority = system.prepared()
    _, request = authority.seal()
    with system.transport._session() as (stream, credential, deadline):
        if change == "deadline":
            request = request | {"deadline_ms": int(time.time() * 1000) + 50}
        write_frame(
            stream,
            {
                "type": "invoke",
                "credential": credential,
                "envelope": request,
                "arguments": authority.arguments,
            },
            deadline,
        )
        prepared = system.transport._receive(stream, deadline)
        assert prepared["type"] == "prepared"
        permit = {
            "protocol": 1,
            "type": "go",
            "credential": credential,
            "request_hash": digest(request),
            "nonce": prepared["nonce"],
        }
        if change == "deadline":
            time.sleep(0.07)
        elif change == "credential":
            permit["credential"] = "bx_" + "A" * 43
        elif change == "nonce":
            permit["nonce"] = str(uuid4())
        else:
            permit["request_hash"] = "0" * 64
        write_frame(stream, permit, deadline)
        with pytest.raises(TransportError):
            system.transport._receive(stream, deadline)
    assert states(system) == [("intent",)]
    assert (
        authority.service.get_invocation(authority.agent_id, authority.action_id).status == "ready"
    )


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_initialization_preserves_existing_sidecar_evidence(tmp_path, suffix):
    if os.name != "posix":
        pytest.skip("Privacidade POSIX do adaptador inicial.")
    from bees_core.executor_transport.contracts import TransportError

    directory = tmp_path / "partial"
    directory.mkdir(mode=0o700)
    sidecar = directory / ("journal.sqlite3" + suffix)
    sidecar.write_bytes(b"private-existing-evidence")
    with pytest.raises(TransportError, match="already_present"):
        ProcessTextResource.initialize(directory, str(uuid4()), str(uuid4()))
    assert sidecar.read_bytes() == b"private-existing-evidence"
    assert sorted(path.name for path in directory.iterdir()) == [sidecar.name]


@pytest.mark.parametrize("operation", ["initialize", "reopen"])
def test_ancestor_symlink_is_rejected_before_creating_or_opening_journal(tmp_path, operation):
    if os.name != "posix":
        pytest.skip("Privacidade POSIX do adaptador inicial.")
    from bees_core.executor_transport.contracts import TransportError

    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    installation_id, environment_id = str(uuid4()), str(uuid4())
    if operation == "initialize":
        with pytest.raises(TransportError, match="private_storage"):
            ProcessTextResource.initialize(alias / "new", installation_id, environment_id)
        assert list(real.iterdir()) == []
    else:
        resource = ProcessTextResource.initialize(
            real / "existing", installation_id, environment_id
        )
        before = {p.name: p.read_bytes() for p in resource.directory.iterdir()}
        with pytest.raises(TransportError, match="private_storage"):
            Journal(alias / "existing", resource.identity)
        assert {p.name: p.read_bytes() for p in resource.directory.iterdir()} == before
