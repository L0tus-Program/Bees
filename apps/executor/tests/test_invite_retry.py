"""Executor iniciado antes da API: o convite não se perde por erro de rede."""

import os
import signal
import socket
import subprocess
import sys
import time

import pytest
from test_executor_gateway import Server

from bees_core.executors import ExecutorService

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")


def test_executor_pairs_once_the_api_comes_up(tmp_path, monkeypatch):
    # Porta reservada (bind sem listen): conexões são recusadas e outro teste paralelo
    # não a ocupa enquanto a API ainda não subiu.
    reserved = socket.socket()
    reserved.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    reserved.bind(("127.0.0.1", 0))
    port = reserved.getsockname()[1]
    monkeypatch.setattr("test_executor_gateway.free_port", lambda: port)
    # O convite é emitido num banco preparado antes; a API só sobe depois do executor.
    from bees_core.storage.database import Database

    (tmp_path / "state").mkdir()
    database = Database(tmp_path / "state" / "bees.sqlite3")
    database.initialize()
    invite = ExecutorService(database).invite({"kind": "personal", "name": "Notebook"})
    log = open(tmp_path / "executor.log", "wb")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "bees_executor.cli",
            "personal",
            "--api",
            f"http://127.0.0.1:{port}",
            "--state",
            str(tmp_path / "personal"),
            "--invite",
            invite["token"],
            "--root",
            f"Docs={tmp_path}",
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=os.environ | {"PYTHONPATH": os.pathsep.join(p for p in sys.path if p)},
    )
    try:
        time.sleep(4)  # pelo menos uma tentativa falha com conexão recusada
        assert process.poll() is None, (tmp_path / "executor.log").read_text()
        reserved.close()
        server = Server(tmp_path)
        try:
            service = ExecutorService(server.database)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline and not any(
                item["online"] for item in service.list("personal")
            ):
                time.sleep(0.3)
            assert any(item["online"] for item in service.list("personal")), (
                tmp_path / "executor.log"
            ).read_text()
        finally:
            server.stop()
    finally:
        reserved.close()
        process.send_signal(signal.SIGKILL)
        process.wait(timeout=10)
