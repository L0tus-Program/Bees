"""Respostas inválidas não devem persistir identidade nem derrubar o pareamento."""

import json
import sys
from http.server import BaseHTTPRequestHandler
from uuid import uuid4

import pytest
from test_exchange_redirect import endpoint

from bees_executor.client import CredentialRejected, exchange

VALID = {"executor_id": str(uuid4()), "credential": "be_" + "A" * 43, "kind": "personal"}
INVALID = [
    b"",
    b"{",
    b"\xff",
    b"null",
    b"[]",
    pytest.param(b"[" * 100000 + b'"private-response-secret"' + b"]" * 100000, id="deep"),
    b'"secret-response"',
    *[json.dumps({k: v for k, v in VALID.items() if k != missing}).encode() for missing in VALID],
    *[
        json.dumps(VALID | {field: value}).encode()
        for field, values in {
            "executor_id": [None, 42, "", "not-a-uuid"],
            "credential": [None, 42, "", "bh_" + "A" * 43, "be_short", "be_" + "\n" * 43],
            "kind": [None, 42, [], "", "unknown"],
        }.items()
        for value in values
    ],
]


@pytest.mark.parametrize("body", INVALID)
@pytest.mark.parametrize("existing", [False, True])
def test_exchange_rejects_invalid_response_without_writing_state(tmp_path, body, existing):
    requests = []
    path = tmp_path / "credential.json"
    if existing:
        path.write_text("existing-private-state", encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    with endpoint(Handler) as api:
        with pytest.raises(CredentialRejected, match="^exchange_invalid_response$"):
            exchange(api, "private-invite", tmp_path)
    assert len(requests) == 1
    if existing:
        assert path.read_text() == "existing-private-state"
    else:
        assert not path.exists()
    assert not path.with_suffix(".tmp").exists()


@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
def test_exchange_persists_valid_response(tmp_path, kind):
    payload = VALID | {"kind": kind, "future_metadata": True}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    with endpoint(Handler) as api:
        result = exchange(api, "private-invite", tmp_path)
    assert result == {k: payload[k] for k in VALID} | {"api": api}
    assert json.loads((tmp_path / "credential.json").read_text()) == result


@pytest.mark.skipif(sys.platform == "win32", reason="Processo POSIX simples.")
@pytest.mark.parametrize("kind", ["personal", "computer_runtime"])
@pytest.mark.parametrize("response_mode", ["invalid", "truncated", "timeout", "wrong_kind", "deep"])
def test_executor_waits_for_new_invite_after_invalid_response(
    tmp_path, monkeypatch, kind, response_mode
):
    import os
    import subprocess
    import time

    from starlette.responses import Response, StreamingResponse
    from test_executor_gateway import Server, create_app

    from bees_core.executors import ExecutorService

    attempts = []

    def broken_app(settings):
        app = create_app(settings)

        @app.middleware("http")
        async def corrupt_first_exchange(request, call_next):
            if request.url.path != "/api/v1/executors/exchange":
                return await call_next(request)
            attempts.append((await request.json())["token"])
            response = await call_next(request)
            if len(attempts) == 1 and response_mode != "wrong_kind":
                # A API consumiu o convite, mas a resposta recebida é inválida.
                if response_mode in ("truncated", "timeout"):

                    async def interrupted_body():
                        yield b'{"credential":"private-response-secret"'
                        if response_mode == "timeout":
                            import asyncio

                            # A CLI mantém seu timeout padrão de 20 s. A API já
                            # consumiu o convite antes de bloquear esta leitura.
                            await asyncio.sleep(25)
                            return
                        raise RuntimeError("controlled transport interruption")

                    return StreamingResponse(interrupted_body(), media_type="application/json")
                if response_mode == "deep":
                    return Response(
                        b"[" * 100000 + b'"private-response-secret"' + b"]" * 100000,
                        status_code=200,
                    )
                return Response(b'{"credential":"private-response-secret"}', status_code=200)
            return response

        return app

    monkeypatch.setattr("test_executor_gateway.create_app", broken_app)
    server = Server(tmp_path)
    process = None
    try:
        service = ExecutorService(server.database)
        other_kind = "computer_runtime" if kind == "personal" else "personal"
        first_kind = other_kind if response_mode == "wrong_kind" else kind
        first = service.invite({"kind": first_kind, "name": "Resposta perdida"})
        invite_file = tmp_path / "invite.json"
        invite_file.write_text(json.dumps(first), encoding="utf-8")
        state = tmp_path / "executor"
        arguments = (
            ["--root", f"Docs={tmp_path}"]
            if kind == "personal"
            else ["--computers", str(tmp_path / "computers"), "--mode", "single"]
        )
        log_path = tmp_path / "executor.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "bees_executor.cli",
                    "personal" if kind == "personal" else "computer",
                    "--api",
                    server.url,
                    "--state",
                    str(state),
                    "--invite-file",
                    str(invite_file),
                    *arguments,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env=os.environ
                | {
                    "BEES_EXECUTOR_INVITE": "",
                    "PYTHONPATH": os.pathsep.join(p for p in sys.path if p),
                },
            )
            deadline = time.monotonic() + (40 if response_mode == "timeout" else 15)
            while time.monotonic() < deadline and process.poll() is None:
                if "Aguardando convite" in log_path.read_text(encoding="utf-8"):
                    break
                time.sleep(0.1)
            diagnostic = log_path.read_text(encoding="utf-8")
            assert process.poll() is None, diagnostic
            code = (
                "exchange_kind_mismatch"
                if response_mode == "wrong_kind"
                else "exchange_invalid_response"
            )
            assert code in diagnostic
            assert "Aguardando convite" in diagnostic
            assert "private-response-secret" not in diagnostic
            assert first["token"] not in diagnostic
            assert attempts == [first["token"]]
            assert not (state / "credential.json").exists()
            assert not (state / "credential.tmp").exists()
            assert len(service.list(first_kind)) == 1
            assert not any(x["online"] for x in service.list(first_kind))
            second = service.invite({"kind": kind, "name": "Novo convite"})
            temporary = invite_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(second), encoding="utf-8")
            temporary.replace(invite_file)
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not any(x["online"] for x in service.list(kind)):
                time.sleep(0.1)
            assert any(x["online"] for x in service.list(kind)), log_path.read_text(
                encoding="utf-8"
            )
            assert process.poll() is None
            assert attempts == [first["token"], second["token"]]
            assert len(service.list(kind)) == (1 if response_mode == "wrong_kind" else 2)
            if response_mode == "wrong_kind":
                assert len(service.list(other_kind)) == 1
                assert not any(x["online"] for x in service.list(other_kind))
            saved = json.loads((state / "credential.json").read_text())
            assert saved["kind"] == kind
            assert service.authenticate(saved["credential"])["id"] == saved["executor_id"]
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
        server.stop()
