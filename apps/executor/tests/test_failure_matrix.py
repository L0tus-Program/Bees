"""Matriz de falhas com processos reais: worker e executor pessoal mortos com SIGKILL.

API real (uvicorn em thread do teste), worker (`bees_worker`, com scheduler) e conector
pessoal (`bees_executor`) em subprocessos próprios; modelo simulado por HTTP. Cada cenário
conta o efeito no disco e os comandos no banco depois de reiniciar os processos.
Inclui tarefas avulsas e rotinas agendadas/manuais, sem frontend. Lento (espera o lease
de 30 s): rode com BEES_FAILURE_MATRIX=1. Grava a evidência sanitizada em
BEES_FAILURE_MATRIX_OUT, se definido.
"""

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
from test_executor_gateway import Server, agent_with_tools, free_port

from bees_core.agent_tools.cycle import AgentTaskInput, AgentTaskService
from bees_core.agent_tools.service import AgentToolService
from bees_core.executors import ExecutorService
from bees_core.models import Agent, Conversation
from bees_core.providers.contracts import ProviderConfig
from bees_core.routine_manual import ManualRoutineInput, ManualRoutineService
from bees_core.routines import CreateRoutine, RoutineDefinition, RoutineService
from bees_core.skills import CreateSkill, EnableSkill, SkillDefinition, SkillService
from bees_core.storage.store import StateStore

pytestmark = [
    pytest.mark.skipif(sys.platform == "win32", reason="Sinais POSIX e shell bash."),
    pytest.mark.skipif(
        os.environ.get("BEES_FAILURE_MATRIX") != "1", reason="Matriz lenta: BEES_FAILURE_MATRIX=1"
    ),
]


class Model:
    """Servidor OpenAI-compatível com roteiro; conta cada geração recebida."""

    def __init__(self):
        self.script: list[dict] = []
        self.requests = 0
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length", 0)))
                model.requests += 1
                message = (
                    model.script.pop(0)
                    if model.script
                    else {"role": "assistant", "content": "Concluído."}
                )
                body = json.dumps(
                    {
                        "choices": [
                            {
                                "message": message,
                                "finish_reason": "tool_calls"
                                if message.get("tool_calls")
                                else "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                    }
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, identity, name, arguments):
        self.script.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": identity,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }
                ],
            }
        )


class Processes:
    def __init__(self, tmp_path, api_url, invite, docs):
        self.tmp_path, self.api_url, self.invite, self.docs = tmp_path, api_url, invite, docs
        self.worker = self.executor = None
        self.logs = tmp_path / "logs"
        self.logs.mkdir()

    def _spawn(self, name, argv, env=None):
        log = open(self.logs / f"{name}-{time.monotonic_ns()}.log", "wb")
        # Mesmos pacotes do processo de teste (pythonpath do pytest), sem instalar nada.
        paths = os.pathsep.join(p for p in sys.path if p)
        return subprocess.Popen(
            argv,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=os.environ | {"PYTHONPATH": paths} | (env or {}),
        )

    def start_worker(self):
        self.worker = self._spawn(
            "worker",
            [sys.executable, "-c", "from bees_worker.cli import main; main()"],
            {"BEES_DATA_DIR": str(self.tmp_path / "state")},
        )

    def start_executor(self):
        self.executor = self._spawn(
            "executor",
            [
                sys.executable,
                "-m",
                "bees_executor.cli",
                "personal",
                "--api",
                self.api_url,
                "--state",
                str(self.tmp_path / "personal"),
                "--invite",
                self.invite,
                "--root",
                f"Docs={self.docs}",
            ],
        )

    @staticmethod
    def kill(process):
        if process and process.poll() is None:
            process.send_signal(signal.SIGCONT)
            process.kill()
            process.wait(timeout=10)

    def stop(self):
        self.kill(self.worker)
        self.kill(self.executor)


def wait_for(predicate, timeout=60, what="condição"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError(f"{what} não atingida em {timeout}s")


@pytest.fixture
def matrix(tmp_path):
    server = Server(tmp_path)
    model = Model()
    database = server.database
    service = ExecutorService(database)
    docs = tmp_path / "Docs"
    docs.mkdir()
    invite = service.invite({"kind": "personal", "name": "Notebook de teste"})
    processes = Processes(tmp_path, server.url, invite["token"], docs)
    processes.start_executor()
    wait_for(lambda: service.list("personal") and service.list("personal")[0]["online"])
    executor = service.list("personal")[0]
    agent = agent_with_tools(database, "bees.personal")
    config = ProviderConfig(
        kind="openai_compatible",
        endpoint=model.url,
        model="fixture",
        capabilities={"text": True, "tool_calls": True},
    )
    with StateStore(database).transaction() as unit:
        current = unit.agents.get(agent.id)
        unit.agents.update(
            Agent.model_validate(
                current.model_dump() | {"provider_config": config.model_dump(mode="json")}
            ),
            current.revision,
        )
    service.set_grant(
        executor["id"],
        agent.id,
        {"expected_revision": 0, "enabled": True, "roots": ["Docs"], "write": True, "shell": True},
    )
    processes.start_worker()
    yield database, service, agent, model, processes, docs
    processes.stop()
    model.server.shutdown()
    server.stop()


def commands(database, tool=None, *, task_id=None):
    with StateStore(database).transaction(write=False) as unit:
        rows = unit.agents._context.connection.execute(
            "SELECT c.tool_name,c.status,c.error_code FROM executor_commands c "
            "JOIN actions a ON a.id=c.id JOIN runs r ON r.id=a.run_id "
            + ("WHERE r.task_id=? " if task_id else "")
            + "ORDER BY c.created_at,c.id",
            (str(task_id),) if task_id else (),
        ).fetchall()
    return [row for row in rows if tool is None or row[0] == tool]


def action_status(database, action_id):
    with StateStore(database).transaction(write=False) as unit:
        return unit.actions.get(action_id).status


def start_task(database, agent, title):
    return AgentTaskService(database).create(
        agent.id, AgentTaskInput(client_request_id=uuid4(), title=title, objective=title)
    )


def approve_pending(database, agent, task):
    approvals = AgentToolService(database)

    def pending():
        return approvals.list(agent.id, task_id=task.id, status="pending")

    (item,) = wait_for(pending, what="pedido de aprovação")
    approvals.decide(
        agent.id,
        item["id"],
        {
            "client_request_id": uuid4(),
            "expected_revision": item["revision"],
            "decision": "allow_once",
        },
    )


def lines(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


def create_routine(database, agent, name, *, active):
    skills = SkillService(database)
    skill = skills.create(
        agent.id,
        CreateSkill(
            client_request_id=uuid4(),
            definition=SkillDefinition(
                name=name,
                purpose="Escrever uma linha no diretório próprio de teste.",
                steps=[
                    {"title": "Escrever", "instruction": "Execute o passo e confira o arquivo."}
                ],
                resources={
                    "environment": "personal",
                    "tools": ["personal.files.write", "personal.shell.run"],
                },
                output="Linha gravada no arquivo próprio.",
                completion="Efeito e recibo conferidos.",
            ),
        ),
    )
    skills.enable(
        agent.id,
        skill.id,
        EnableSkill(client_request_id=uuid4(), expected_revision=1, version=1, enabled=True),
    )
    with StateStore(database).transaction() as unit:
        destination = unit.conversations.create(Conversation(agent_id=agent.id))
    # Agenda vencida dentro da tolerância; quem materializa e despacha é o scheduler real.
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    routines = RoutineService(database, clock=lambda: now - timedelta(minutes=1))
    return routines.create(
        agent.id,
        CreateRoutine(
            client_request_id=uuid4(),
            active=active,
            definition=RoutineDefinition(
                name=name,
                instructions="Execute uma vez o procedimento no diretório autorizado.",
                schedule={"frequency": "daily", "time": now.strftime("%H:%M")},
                timezone="UTC",
                skill_id=skill.id,
                skill_version=1,
                environment="personal",
                destination={"conversation_id": destination.id},
                notification="always",
                grace_seconds=3600,
                execution_mode="agent",
            ),
        ),
    )


def routine_occurrence(database, agent, routine):
    rows = RoutineService(database).occurrences(agent.id, routine.id)
    return next((row for row in rows if row.task_id is not None), None)


def routine_counts(database, agent, routine):
    occurrences = RoutineService(database).occurrences(agent.id, routine.id)
    with StateStore(database).transaction(write=False) as unit:
        tasks = unit.agents._context.connection.execute(
            "SELECT count(*) FROM tasks WHERE routine_id=?", (str(routine.id),)
        ).get
    return {"ocorrencias": len(occurrences), "tarefas": tasks}


def test_real_process_failure_matrix(matrix):
    database, service, agent, model, processes, docs = matrix
    evidence = []

    # 1. Efeito aplicado sem recibo: shell grava e segue rodando; executor e worker morrem.
    marker = docs / "efeito.txt"
    model.call(
        "s1",
        "personal_shell",
        {"command": "echo efeito >> efeito.txt; sleep 60", "cwd": "Docs"},
    )
    task = start_task(database, agent, "Efeito sem recibo")
    approve_pending(database, agent, task)
    wait_for(lambda: lines(marker) == 1, what="efeito no disco")
    wait_for(lambda: commands(database, "personal.shell.run")[-1][1] == "sent", what="enviado")
    processes.kill(processes.executor)
    processes.kill(processes.worker)
    processes.start_worker()
    processes.start_executor()
    wait_for(lambda: service.list("personal")[0]["online"], what="executor de volta")
    # Lease de 30 s expira; a recuperação declara desconhecido, nunca repete.
    wait_for(
        lambda: (
            AgentTaskService(database).detail(agent.id, task.id).task.status
            not in ("running", "queued")
        ),
        timeout=90,
        what="recuperação do lease",
    )
    time.sleep(3)
    detail = AgentTaskService(database).detail(agent.id, task.id)
    shell_rows = commands(database, "personal.shell.run")
    assert lines(marker) == 1
    assert shell_rows == [("personal.shell.run", "unknown", "worker_restart")]
    assert detail.task.status != "completed"
    evidence.append(
        {
            "cenario": "efeito aplicado, executor e worker mortos antes do recibo",
            "efeitos": lines(marker),
            "comandos": len(shell_rows),
            "comando": shell_rows[0][1],
            "tarefa": detail.task.status,
        }
    )

    # 2. Executor congelado antes de receber: comando sai do banco, mas nada é aplicado.
    frozen = docs / "congelado.txt"
    os.kill(processes.executor.pid, signal.SIGSTOP)
    model.call(
        "w1",
        "personal_write_file",
        {"path": "Docs/congelado.txt", "content": "linha\n", "append": True},
    )
    task2 = start_task(database, agent, "Executor congelado")
    approve_pending(database, agent, task2)
    wait_for(
        lambda: [r for r in commands(database, "personal.files.write") if r[1] == "sent"],
        what="comando enviado ao executor congelado",
    )
    processes.kill(processes.executor)
    processes.kill(processes.worker)
    processes.start_worker()
    processes.start_executor()
    wait_for(lambda: service.list("personal")[0]["online"], what="executor de volta")
    wait_for(
        lambda: (
            AgentTaskService(database).detail(agent.id, task2.id).task.status
            not in ("running", "queued")
        ),
        timeout=90,
        what="recuperação do lease",
    )
    time.sleep(3)
    write_rows = commands(database, "personal.files.write")
    detail2 = AgentTaskService(database).detail(agent.id, task2.id)
    assert not frozen.exists()
    assert write_rows == [("personal.files.write", "unknown", "worker_restart")]
    evidence.append(
        {
            "cenario": "executor congelado recebe o comando e morre antes de aplicar",
            "efeitos": lines(frozen),
            "comandos": len(write_rows),
            "comando": write_rows[0][1],
            "tarefa": detail2.task.status,
        }
    )

    # 3. Confirmado não repete após reiniciar tudo.
    done = docs / "confirmado.txt"
    model.call(
        "w2",
        "personal_write_file",
        {"path": "Docs/confirmado.txt", "content": "linha\n", "append": True},
    )
    model.script.append({"role": "assistant", "content": "Arquivo escrito."})
    task3 = start_task(database, agent, "Confirmado")
    approve_pending(database, agent, task3)
    wait_for(
        lambda: AgentTaskService(database).detail(agent.id, task3.id).task.status == "completed",
        what="tarefa confirmada",
    )
    processes.kill(processes.executor)
    processes.kill(processes.worker)
    processes.start_worker()
    processes.start_executor()
    wait_for(lambda: service.list("personal")[0]["online"], what="executor de volta")
    time.sleep(5)
    confirmed = [r for r in commands(database, "personal.files.write") if r[1] == "confirmed"]
    assert lines(done) == 1 and len(confirmed) == 1
    assert AgentTaskService(database).detail(agent.id, task3.id).task.status == "completed"
    evidence.append(
        {
            "cenario": "efeito confirmado, executor e worker reiniciados",
            "efeitos": lines(done),
            "comandos": len(confirmed),
            "comando": "confirmed",
            "tarefa": "completed",
        }
    )
    # 4. O scheduler em processo cria a rotina, sem frontend; confirmado não redispara.
    scheduled = docs / "rotina-confirmada.txt"
    generations = model.requests
    model.call(
        "r1",
        "personal_write_file",
        {"path": "Docs/rotina-confirmada.txt", "content": "rotina\n", "append": True},
    )
    model.script.append({"role": "assistant", "content": "Rotina concluída."})
    routine = create_routine(database, agent, "Rotina confirmada", active=True)
    occurrence = wait_for(
        lambda: routine_occurrence(database, agent, routine), what="despacho da rotina pelo worker"
    )
    task4 = AgentTaskService(database).detail(agent.id, occurrence.task_id).task
    approve_pending(database, agent, task4)
    wait_for(
        lambda: routine_occurrence(database, agent, routine).status == "completed",
        what="ocorrência concluída",
    )
    processes.stop()
    processes.start_worker()
    processes.start_executor()
    wait_for(lambda: service.list("personal")[0]["online"], what="executor da rotina de volta")
    time.sleep(6)  # Janela de observação após o reinício dos processos reais.
    assert routine_counts(database, agent, routine) == {"ocorrencias": 1, "tarefas": 1}
    assert routine_occurrence(database, agent, routine).id == occurrence.id
    assert routine_occurrence(database, agent, routine).status == "completed"
    assert commands(database, task_id=task4.id) == [("personal.files.write", "confirmed", None)]
    assert lines(scheduled) == 1 and model.requests - generations == 2
    evidence.append(
        {
            "cenario": "rotina agendada confirmada, worker e executor reiniciados",
            **routine_counts(database, agent, routine),
            "efeitos": lines(scheduled),
            "comandos": len(commands(database, task_id=task4.id)),
            "comando": "confirmed",
            "ocorrencia": "completed",
            "geracoes": model.requests - generations,
        }
    )

    # 5. Efeito manual sem recibo: o mesmo UUID reconcilia sem criar tarefa ou repetir.
    manual_marker = docs / "rotina-incerta.txt"
    generations = model.requests
    model.call(
        "r2",
        "personal_shell",
        {"command": "echo rotina >> rotina-incerta.txt; sleep 60", "cwd": "Docs"},
    )
    manual_routine = create_routine(database, agent, "Rotina manual incerta", active=False)
    manual_input = ManualRoutineInput(
        client_request_id=uuid4(), expected_revision=manual_routine.revision
    )
    receipt = ManualRoutineService(database).execute(agent.id, manual_routine.id, manual_input)
    task5 = AgentTaskService(database).detail(agent.id, receipt.occurrence.task_id).task
    approve_pending(database, agent, task5)
    wait_for(lambda: lines(manual_marker) == 1, what="efeito manual no disco")
    processes.stop()
    processes.start_worker()
    processes.start_executor()
    wait_for(
        lambda: routine_occurrence(database, agent, manual_routine).status == "outcome_unknown",
        timeout=90,
        what="recuperação da ocorrência manual incerta",
    )
    repeated = ManualRoutineService(database).execute(agent.id, manual_routine.id, manual_input)
    assert repeated.occurrence.id == receipt.occurrence.id
    assert repeated.occurrence.task_id == task5.id
    time.sleep(6)
    assert routine_counts(database, agent, manual_routine) == {"ocorrencias": 1, "tarefas": 1}
    assert commands(database, task_id=task5.id) == [
        ("personal.shell.run", "unknown", "worker_restart")
    ]
    assert AgentTaskService(database).detail(agent.id, task5.id).task.status == "paused"
    assert lines(manual_marker) == 1 and model.requests - generations == 1
    evidence.append(
        {
            "cenario": "rotina manual com efeito sem recibo, restart e replay do UUID",
            **routine_counts(database, agent, manual_routine),
            "efeitos": lines(manual_marker),
            "comandos": len(commands(database, task_id=task5.id)),
            "comando": "unknown",
            "ocorrencia": "outcome_unknown",
            "tarefa": "paused",
            "geracoes": model.requests - generations,
        }
    )

    # 6. Uma decisão pendente sobrevive ao restart; só a mesma aprovação libera o efeito.
    decision_marker = docs / "rotina-decisao.txt"
    generations = model.requests
    model.call(
        "r3",
        "personal_write_file",
        {"path": "Docs/rotina-decisao.txt", "content": "rotina\n", "append": True},
    )
    model.script.append({"role": "assistant", "content": "Rotina aprovada e concluída."})
    decision_routine = create_routine(database, agent, "Rotina com decisão", active=True)
    pending_occurrence = wait_for(
        lambda: routine_occurrence(database, agent, decision_routine), what="rotina para decisão"
    )
    task6 = AgentTaskService(database).detail(agent.id, pending_occurrence.task_id).task
    approvals = AgentToolService(database)
    (decision,) = wait_for(
        lambda: approvals.list(agent.id, task_id=task6.id, status="pending"),
        what="decisão antes do restart",
    )
    assert model.requests - generations == 1
    assert not decision_marker.exists() and not commands(database, task_id=task6.id)
    processes.stop()
    processes.start_worker()
    processes.start_executor()
    wait_for(lambda: service.list("personal")[0]["online"], what="executor antes da aprovação")
    time.sleep(6)
    (same_decision,) = approvals.list(agent.id, task_id=task6.id, status="pending")
    assert same_decision["id"] == decision["id"]
    assert routine_counts(database, agent, decision_routine) == {"ocorrencias": 1, "tarefas": 1}
    assert not decision_marker.exists() and not commands(database, task_id=task6.id)
    assert model.requests - generations == 1
    approve_pending(database, agent, task6)
    wait_for(
        lambda: routine_occurrence(database, agent, decision_routine).status == "completed",
        what="rotina concluída depois da aprovação",
    )
    assert lines(decision_marker) == 1 and model.requests - generations == 2
    assert commands(database, task_id=task6.id) == [("personal.files.write", "confirmed", None)]
    evidence.append(
        {
            "cenario": "rotina com aprovação pendente, restart e decisão humana",
            **routine_counts(database, agent, decision_routine),
            "efeitos_antes_da_decisao": 0,
            "efeitos": lines(decision_marker),
            "comandos": len(commands(database, task_id=task6.id)),
            "comando": "confirmed",
            "ocorrencia": "completed",
            "geracoes": model.requests - generations,
        }
    )
    # Nenhum comando novo nasceu dos reinícios: um por cenário.
    assert len(commands(database)) == 6
    if output := os.environ.get("BEES_FAILURE_MATRIX_OUT"):
        Path(output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
