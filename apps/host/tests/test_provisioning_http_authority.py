"""Transporte limitado: protocolos falsos explícitos e slow-drip HTTP loopback real."""

import asyncio
import gzip
import json
import logging
import socket
import threading
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from bees_host.provisioning import http_authority as module
from bees_host.provisioning.contracts import ProvisionError
from bees_host.provisioning.http_authority import MAX_BYTES, HTTPAuthority
from pydantic import SecretStr
from test_provisioning_runner import claim_fixture

TOKEN = "bp_" + "a" * 43


def raw(body: bytes, *, status=200, headers=None):
    return httpx.Response(
        status,
        headers=headers if headers is not None else {"Content-Type": "application/json"},
        stream=httpx.ByteStream(body),
    )


def response(value, *, status=200, headers=None):
    return raw(json.dumps(value).encode(), status=status, headers=headers)


def context(claim, revision=1):
    return {
        "installation_id": str(claim.installation_id),
        "host_id": str(claim.host_id),
        "provisioner_id": str(claim.provisioner_id),
        "revision": revision,
        "status": "active",
    }


def adapter(claim, handler, *, origin="http://127.0.0.1:8080", credential=None):
    return HTTPAuthority(
        origin,
        credential if credential is not None else SecretStr(TOKEN),
        installation_id=claim.installation_id,
        host_id=claim.host_id,
        provisioner_id=claim.provisioner_id,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.com",
        "https://api.openai.com",
        "http://127.0.0.2",
        "http://127.1",
        "http://127.0.0.1.evil",
        "http://localhost.evil",
        "http://user:secret@127.0.0.1",
        "http://127.0.0.1/api",
        "http://127.0.0.1?x=y",
        "http://127.0.0.1#secret",
        "http://127.0.0.1:0",
        "http://127.0.0.1:65536",
        "http://LOCALHOST",
        "http://127.0.0.1:080",
        "http://127.0.0.1\n",
        "file:///data",
    ],
)
def test_unsafe_origin_has_no_request_or_secret_in_error(origin):
    with pytest.raises(ProvisionError, match="provision_origin_invalid") as error:
        adapter(claim_fixture(), lambda request: pytest.fail("network"), origin=origin)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("token", ["bh_" + "a" * 43, "bp_short", "bp_" + "a" * 43 + "\n", TOKEN])
def test_credential_namespace_and_secret_type_fail_closed(token):
    credential = token if token == TOKEN else SecretStr(token)
    with pytest.raises(ProvisionError, match="provision_credentials_invalid"):
        adapter(claim_fixture(), lambda request: pytest.fail("network"), credential=credential)


def test_localhost_is_resolved_once_then_pinned_with_original_origin_and_tls_name(monkeypatch):
    claim = claim_fixture()
    resolved = []

    def resolve(*args, **kwargs):
        resolved.append(args)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8080))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.headers["Host"] == "localhost:8080"
        assert request.headers["Origin"] == "https://localhost:8080"
        assert request.extensions["sni_hostname"] == "localhost"
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        return response(context(claim))

    with adapter(claim, handler, origin="https://localhost:8080/") as authority:
        authority.session()
        authority.session()
    assert len(resolved) == 1 and len(requests) == 2


def test_localhost_resolving_remote_refuses_before_bearer_request(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.2", 8080)),
        ],
    )
    with pytest.raises(ProvisionError, match="provision_origin_invalid"):
        adapter(claim_fixture(), lambda request: pytest.fail("network"), origin="http://localhost")


def test_headers_are_fixed_and_cookie_or_proxy_environment_is_not_inherited(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://secret@remote.invalid:1234")
    claim = claim_fixture()
    calls = []

    def handler(request):
        calls.append(request)
        assert "cookie" not in request.headers and "proxy-authorization" not in request.headers
        assert request.headers["Accept-Encoding"] == "identity"
        return response(
            context(claim),
            headers={"Content-Type": "application/json", "Set-Cookie": "human=secret"},
        )

    with adapter(claim, handler) as authority:
        authority.session()
        authority.session()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "status,code",
    [
        (301, "provision_protocol_invalid"),
        (307, "provision_protocol_invalid"),
        (401, "provision_credentials_invalid"),
        (403, "provision_authority_rejected"),
        (409, "provision_authority_rejected"),
        (422, "provision_protocol_invalid"),
        (429, "provision_connection_unavailable"),
        (500, "provision_connection_unavailable"),
    ],
)
def test_errors_do_not_echo_body_or_retry(status, code, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        return raw(TOKEN.encode(), status=status, headers={"Location": "https://evil.invalid"})

    with caplog.at_level(logging.DEBUG), adapter(claim_fixture(), handler) as authority:
        with pytest.raises(ProvisionError, match=code) as error:
            authority.session()
    assert len(calls) == 1 and TOKEN not in str(error.value) and TOKEN not in caplog.text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Content-Type": "text/html"},
        {"Content-Type": "application/json;charset=latin1"},
        {"Content-Type": "application/json", "Content-Encoding": "gzip"},
        {"Content-Type": "application/json", "Content-Encoding": "br"},
        {"Content-Type": "application/json", "Content-Length": str(MAX_BYTES + 1)},
        {"Content-Type": "application/json", "Content-Length": "-1"},
        [("Content-Type", "application/json"), ("Content-Type", "application/json")],
        [("Content-Type", "application/json"), ("Content-Length", "1"), ("Content-Length", "1")],
    ],
)
def test_untrusted_headers_or_compression_fail_before_body(headers):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("must not consume or decompress invalid response")
            yield b""

    def handler(request):
        return httpx.Response(200, headers=headers, stream=Stream())

    with adapter(claim_fixture(), handler) as authority:
        with pytest.raises(ProvisionError, match="provision_protocol_invalid"):
            authority.session()


@pytest.mark.parametrize(
    "body",
    [
        b'{"revision":1,"revision":2}',
        b'{"revision":NaN}',
        b'{"revision":Infinity}',
        b'{"revision":-Infinity}',
        b'{"nested":{"x":1,"x":2}}',
        b"\xff",
        b'{"extra":"private"}',
        b"[]",
        b'"string"',
        b"{" + b" " * MAX_BYTES + b"}",
    ],
)
def test_json_is_strict_closed_and_size_bounded(body):
    with adapter(claim_fixture(), lambda request: raw(body)) as authority:
        with pytest.raises(ProvisionError, match="provision_protocol_invalid"):
            authority.session()


def test_gzip_bomb_is_not_decompressed():
    compressed = gzip.compress(b"x" * (MAX_BYTES * 100))
    with adapter(
        claim_fixture(),
        lambda request: raw(
            compressed, headers={"Content-Type": "application/json", "Content-Encoding": "gzip"}
        ),
    ) as authority:
        with pytest.raises(ProvisionError, match="provision_protocol_invalid"):
            authority.session()


@pytest.mark.parametrize("field", ["installation_id", "host_id", "provisioner_id"])
def test_session_identity_does_not_use_trust_on_first_response(field):
    claim = claim_fixture()
    value = context(claim)
    value[field] = str(uuid4())
    with adapter(claim, lambda request: response(value)) as authority:
        with pytest.raises(ProvisionError, match="provision_protocol_invalid"):
            authority.session()


def test_session_revision_cannot_go_back():
    claim = claim_fixture()
    values = iter([context(claim, 2), context(claim, 1)])
    with adapter(claim, lambda request: response(next(values))) as authority:
        assert authority.session().revision == 2
        with pytest.raises(ProvisionError, match="provision_protocol_invalid"):
            authority.session()


@pytest.mark.parametrize(
    "change", ["owner", "generation", "revision", "snapshot", "expired", "status", "same_revision"]
)
def test_claim_cannot_change_binding_or_rewind_snapshot(change):
    claim = claim_fixture().model_copy(update={"revision": 2, "status": "dispatch_started"})
    value = claim.model_dump(mode="json")
    if change == "owner":
        value["owner_id"] = str(uuid4())
    elif change == "generation":
        value["generation"] += 1
    elif change == "revision":
        value["revision"] -= 1
    elif change == "snapshot":
        value["plan"]["cpu_count"] = 4
    elif change == "expired":
        value["lease_expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif change == "status":
        value["status"] = "claimed"
        value["revision"] += 1
    else:
        value["lease_expires_at"] = (claim.lease_expires_at + timedelta(seconds=1)).isoformat()
    with adapter(claim, lambda request: response(value)) as authority:
        with pytest.raises(ProvisionError):
            authority.assert_current(claim)


def test_claim_request_uuid_and_snapshot_hash_are_preserved():
    claim = claim_fixture()
    request_id = uuid4()
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return response(claim.model_dump(mode="json"))

    with adapter(claim, handler) as authority:
        assert authority.claim(claim.plan_id, claim.plan_hash, claim.owner_id, request_id) == claim
    assert requests == [
        {
            "plan_id": str(claim.plan_id),
            "plan_hash": claim.plan_hash,
            "owner_id": str(claim.owner_id),
            "client_request_id": str(request_id),
        }
    ]


@pytest.mark.parametrize("failure", ["wrong_effect", "cached_allowed", "wrong_operation"])
def test_dispatch_response_cannot_invent_effect_or_enable_replay(failure):
    claim, request_id = claim_fixture(), uuid4()
    value = {
        "claim": claim.model_copy(update={"revision": 2, "status": "dispatch_started"}).model_dump(
            mode="json"
        ),
        "effect_request_id": str(request_id),
        "operation": "create_vhd",
        "status": "dispatch_started",
        "cached": False,
        "dispatch_allowed": True,
    }
    if failure == "wrong_effect":
        value["effect_request_id"] = str(uuid4())
    elif failure == "cached_allowed":
        value["cached"] = True
    else:
        value["operation"] = "create_vm"
    with adapter(claim, lambda request: response(value)) as authority:
        with pytest.raises(ProvisionError, match="provision_protocol_invalid"):
            authority.begin_dispatch(claim, "create_vhd", request_id)


def test_cached_begin_never_returns_dispatch_permission():
    claim, request_id = claim_fixture(), uuid4()
    value = {
        "claim": claim.model_copy(update={"revision": 2, "status": "dispatch_started"}).model_dump(
            mode="json"
        ),
        "effect_request_id": str(request_id),
        "operation": "create_vhd",
        "status": "dispatch_started",
        "cached": True,
        "dispatch_allowed": False,
    }
    with adapter(claim, lambda request: response(value)) as authority:
        permit = authority.begin_dispatch(claim, "create_vhd", request_id)
    assert permit.cached and not permit.dispatch_allowed


def test_timeout_has_no_automatic_retry_or_credential_in_error():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(TOKEN, request=request)

    with adapter(claim_fixture(), handler) as authority:
        with pytest.raises(ProvisionError, match="provision_connection_unavailable") as error:
            authority.session()
    assert len(calls) == 1 and TOKEN not in str(error.value)


def test_sync_interface_rejects_active_async_loop_without_coroutine_warning():
    with adapter(claim_fixture(), lambda request: pytest.fail("network")) as authority:

        async def call():
            with pytest.raises(ProvisionError, match="provision_async_context_unsupported"):
                authority.session()

        asyncio.run(call())


def test_real_loopback_requests_reuse_one_strict_tls_context(monkeypatch):
    """Cliente por request, sem recriar o contexto TLS padrão (certifi/hostname/required)."""
    import ssl
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from httpx._transports import default as transports

    claim = claim_fixture()
    body = json.dumps(context(claim)).encode()

    class Session(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    contexts = []
    original = transports.create_ssl_context

    def observed(*args, **kwargs):
        contexts.append(original(*args, **kwargs))
        return contexts[-1]

    monkeypatch.setattr(transports, "create_ssl_context", observed)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Session)
    worker = threading.Thread(target=server.serve_forever, name="own-session-server")
    worker.start()
    try:
        with HTTPAuthority(
            f"http://127.0.0.1:{server.server_address[1]}",
            SecretStr(TOKEN),
            installation_id=claim.installation_id,
            host_id=claim.host_id,
            provisioner_id=claim.provisioner_id,
        ) as authority:
            assert authority.session().revision == authority.session().revision == 1
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
    assert len(contexts) == 2 and contexts[0] is contexts[1]
    assert contexts[0].verify_mode == ssl.CERT_REQUIRED and contexts[0].check_hostname
    assert contexts[0].cert_store_stats()["x509_ca"] > 0


def test_closed_adapter_does_not_open_network():
    authority = adapter(claim_fixture(), lambda request: pytest.fail("network"))
    authority.close()
    with pytest.raises(ProvisionError, match="provision_authority_closed"):
        authority.session()


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_total_deadline_cancels_real_loopback_slow_drip_without_request_threads(phase, monkeypatch):
    """Servidor próprio faz progresso a cada30ms; timeout de inatividade não bastaria."""
    monkeypatch.setattr(module, "REQUEST_BUDGET", 0.25)
    claim = claim_fixture()
    stop = threading.Event()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    port = listener.getsockname()[1]

    def serve():
        try:
            connection, _ = listener.accept()
            with connection:
                connection.settimeout(1)
                request = bytearray()
                while b"\r\n\r\n" not in request and not stop.is_set():
                    chunk = connection.recv(1024)
                    if not chunk:
                        # Cancelamento antes dos headers pode fechar o socket.
                        # EOF não é progresso: não deixar o servidor de teste
                        # preso num loop que também impede o processo de sair.
                        return
                    request.extend(chunk)
                    if len(request) > MAX_BYTES:
                        return
                if stop.is_set():
                    return
                head = (
                    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                    b"Content-Length: 9999\r\n\r\n"
                )
                if phase == "headers":
                    output = head
                else:
                    connection.sendall(head)
                    output = b" " * 100
                for byte in output:
                    if stop.wait(0.03):
                        return
                    connection.sendall(bytes([byte]))
        except OSError:
            pass

    worker = threading.Thread(target=serve, name="own-slow-drip-server")
    worker.start()
    started = time.monotonic()
    try:
        with HTTPAuthority(
            f"http://127.0.0.1:{port}",
            SecretStr(TOKEN),
            installation_id=claim.installation_id,
            host_id=claim.host_id,
            provisioner_id=claim.provisioner_id,
        ) as authority:
            with pytest.raises(ProvisionError, match="provision_connection_unavailable"):
                authority.session()
        assert time.monotonic() - started < 1
    finally:
        stop.set()
        listener.close()
        worker.join(timeout=2)
    assert not worker.is_alive()
