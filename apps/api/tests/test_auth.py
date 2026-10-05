import time
from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from bees_api.app import create_app
from bees_api.auth import COOKIE_NAME, install_auth, require_session
from bees_api.config import Settings
from bees_core.security.identity import IdentityService, Session
from bees_core.storage.database import Database

ORIGIN = "http://127.0.0.1:8000"
PASSWORD = "uma senha apenas para teste 123"


def build_app(path, *, origin=ORIGIN, secure=False, clock=time.time) -> FastAPI:
    database = Database(path)
    database.initialize()
    app = FastAPI()
    app.state.identity = IdentityService(database, clock=clock)
    install_auth(
        app,
        allowed_origins=(origin,),
        secure_cookie=secure,
        health_authorities=("127.0.0.1:8000",),
    )

    @app.get("/api/v1/private")
    def private(session: Annotated[Session, Depends(require_session)]):
        return {"name": session.user.name}

    @app.post("/api/v1/private")
    def write(session: Annotated[Session, Depends(require_session)]):
        return {"saved": True}

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    return app


@pytest.fixture
def client(tmp_path):
    app = build_app(tmp_path / "auth.sqlite3")
    with TestClient(app, base_url=ORIGIN) as result:
        yield result


def configure(client, *, origin=ORIGIN):
    token = client.app.state.identity.issue_bootstrap()
    response = client.post(
        "/api/v1/auth/setup",
        json={"bootstrap_token": token, "name": "Pessoa de teste", "password": PASSWORD},
        headers={"Origin": origin},
    )
    assert response.status_code == 200, response.text
    return response


def test_status_does_not_disclose_user_or_csrf_to_unauthenticated_client(client) -> None:
    assert client.get("/api/v1/auth/status").json() == {
        "configured": False,
        "authenticated": False,
    }
    configured = configure(client)
    status = configured.json()
    assert status["user"] == {"name": "Pessoa de teste"}
    assert len(status["csrf_token"]) == 43
    assert COOKIE_NAME not in status
    assert PASSWORD not in configured.text
    assert client.get("/api/v1/auth/status").json() == status
    client.cookies.clear()
    response = client.get("/api/v1/auth/status")
    assert response.json() == {"configured": True, "authenticated": False}
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/private").status_code == 401


def test_cookie_http_only_same_site_fixed_expiration_and_no_domain(client) -> None:
    response = configure(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "Max-Age=43200" in cookie
    assert "expires=" in cookie
    assert "Path=/" in cookie
    assert "Domain=" not in cookie
    assert "Secure" not in cookie
    assert client.get("/api/v1/private").status_code == 200


@pytest.mark.parametrize("origin", [None, "null", "https://attacker.example", ORIGIN + "/"])
@pytest.mark.parametrize("endpoint", ["setup", "login"])
def test_unauthenticated_mutations_require_exact_origin(client, origin, endpoint) -> None:
    response = client.post(
        f"/api/v1/auth/{endpoint}",
        json={"password": PASSWORD},
        headers={"Origin": origin} if origin is not None else {},
    )
    assert response.status_code == 403
    assert not client.app.state.identity.configured()


def test_form_login_and_cross_site_status_are_rejected(client) -> None:
    response = client.post(
        "/api/v1/auth/login", data={"password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 415
    configure(client)
    assert (
        client.get("/api/v1/auth/status", headers={"Sec-Fetch-Site": "cross-site"}).status_code
        == 403
    )


@pytest.mark.parametrize("host", ["attacker.example:8000", "127.0.0.1:9999", "localhost:8000"])
def test_host_authority_is_exact_and_ignores_forwarded_host(client, host) -> None:
    response = client.get(
        "/api/v1/auth/status", headers={"Host": host, "X-Forwarded-Host": "127.0.0.1:8000"}
    )
    assert response.status_code == 400


def test_private_writes_and_logout_require_current_csrf(client) -> None:
    csrf = configure(client).json()["csrf_token"]
    for nonce in (None, "wrong", "x" * 43):
        headers = {"Origin": ORIGIN}
        if nonce is not None:
            headers["X-Bees-CSRF"] = nonce
        assert client.post("/api/v1/private", json={}, headers=headers).status_code == 403
        assert client.post("/api/v1/auth/logout", json={}, headers=headers).status_code == 403
    headers = {"Origin": ORIGIN, "X-Bees-CSRF": csrf}
    assert client.post("/api/v1/private", json={}, headers=headers).status_code == 200
    previous = client.cookies.get(COOKIE_NAME)
    assert client.post("/api/v1/auth/logout", json={}, headers=headers).status_code == 204
    assert COOKIE_NAME not in client.cookies
    replay = client.get("/api/v1/private", headers={"Cookie": f"{COOKIE_NAME}={previous}"})
    assert replay.status_code == 401


def test_duplicate_or_non_ascii_csrf_headers_are_rejected(client) -> None:
    csrf = configure(client).json()["csrf_token"]
    response = client.post(
        "/api/v1/private",
        json={},
        headers=[("Origin", ORIGIN), ("X-Bees-CSRF", csrf), ("X-Bees-CSRF", csrf)],
    )
    assert response.status_code == 403
    response = client.post(
        "/api/v1/private", json={}, headers={"Origin": ORIGIN, "X-Bees-CSRF": b"\xff"}
    )
    assert response.status_code == 403


def test_login_rotates_cookie_and_previous_csrf_is_invalid(client) -> None:
    initial = configure(client)
    old_token = client.cookies.get(COOKIE_NAME)
    old_csrf = initial.json()["csrf_token"]
    response = client.post(
        "/api/v1/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 200
    assert client.cookies.get(COOKIE_NAME) != old_token
    assert response.json()["csrf_token"] != old_csrf
    assert (
        client.post(
            "/api/v1/private", json={}, headers={"Origin": ORIGIN, "X-Bees-CSRF": old_csrf}
        ).status_code
        == 403
    )
    assert (
        client.get("/api/v1/private", headers={"Cookie": f"{COOKIE_NAME}={old_token}"}).status_code
        == 401
    )


def test_bootstrap_invalid_replay_and_authenticated_reload(tmp_path) -> None:
    path = tmp_path / "restart.sqlite3"
    with TestClient(build_app(path), base_url=ORIGIN) as client:
        response = client.post(
            "/api/v1/auth/setup",
            json={"bootstrap_token": "A" * 43, "name": "Teste", "password": PASSWORD},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401
        token = client.app.state.identity.issue_bootstrap()
        body = {"bootstrap_token": token, "name": "Teste", "password": PASSWORD}
        assert (
            client.post("/api/v1/auth/setup", json=body, headers={"Origin": ORIGIN}).status_code
            == 200
        )
        cookie = client.cookies.get(COOKIE_NAME)
        csrf = client.get("/api/v1/auth/status").json()["csrf_token"]
        assert (
            client.post("/api/v1/auth/setup", json=body, headers={"Origin": ORIGIN}).status_code
            == 409
        )
    with TestClient(build_app(path), base_url=ORIGIN) as reopened:
        reopened.cookies.set(COOKIE_NAME, cookie)
        assert reopened.get("/api/v1/auth/status").json()["csrf_token"] == csrf
        assert reopened.get("/api/v1/private").status_code == 200


def test_login_rate_limit_is_persistent_and_returns_retry_after(client) -> None:
    configure(client)
    client.cookies.clear()
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login",
            json={"password": "incorrect password"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401
        assert "incorrect password" not in response.text
    old_identity = client.app.state.identity
    client.app.state.identity = IdentityService(Database(old_identity.database.path))
    response = client.post(
        "/api/v1/auth/login", json={"password": PASSWORD}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"
    assert COOKIE_NAME not in client.cookies


def test_expired_cookie_cannot_read_or_mutate(tmp_path) -> None:
    now = [time.time()]
    app = build_app(tmp_path / "expiration.sqlite3", clock=lambda: now[0])
    with TestClient(app, base_url=ORIGIN) as client:
        csrf = configure(client).json()["csrf_token"]
        now[0] += 43200
        assert client.get("/api/v1/auth/status").json() == {
            "configured": True,
            "authenticated": False,
        }
        assert client.get("/api/v1/private").status_code == 401
        assert (
            client.post(
                "/api/v1/private", json={}, headers={"Origin": ORIGIN, "X-Bees-CSRF": csrf}
            ).status_code
            == 401
        )


def test_https_cookie_and_forwarded_header_cannot_fake_secure_transport(tmp_path) -> None:
    origin = "https://bees.example"
    app = build_app(tmp_path / "https.sqlite3", origin=origin, secure=True)
    with TestClient(app, base_url=origin) as client:
        response = configure(client, origin=origin)
        assert "Secure" in response.headers["set-cookie"]
        assert client.get("/api/v1/private").status_code == 200
        assert client.get("http://bees.example/api/v1/auth/status").status_code == 403
        response = client.post(
            "http://bees.example/api/v1/auth/login",
            json={"password": PASSWORD},
            headers={"Origin": origin, "X-Forwarded-Proto": "https"},
        )
        assert response.status_code == 403
        assert client.get("http://127.0.0.1:8000/api/v1/health").status_code == 200
        assert client.get("http://127.0.0.1:8000/api/v1/private").status_code == 400


@pytest.mark.parametrize(
    "origin", ["*", "http://127.0.0.1:8000/path", "https://user:pw@example.org"]
)
def test_invalid_allowed_origin_fails_configuration(origin) -> None:
    with pytest.raises(ValueError):
        install_auth(FastAPI(), allowed_origins=(origin,))


def test_real_api_composition_and_validation_do_not_echo_secrets(tmp_path) -> None:
    app = create_app(Settings(data_dir=tmp_path, web_dist=tmp_path / "missing"))
    with TestClient(app, base_url=ORIGIN) as client:
        invalid = "PRIVATE-INVALID-PASSWORD" * 30
        response = client.post(
            "/api/v1/auth/login", json={"password": invalid}, headers={"Origin": ORIGIN}
        )
        assert response.status_code == 422
        assert "PRIVATE-INVALID-PASSWORD" not in response.text
        assert response.json()["error"]["code"] == "validation_failed"
        configure(client)
        assert client.get("/api/v1/auth/status").json()["authenticated"] is True
        assert client.get("/api/v1/state/status").json()["schema_version"] == 2
    # Um novo lifespan de produção abre o mesmo estado sem reemitir bootstrap.
    app = create_app(Settings(data_dir=tmp_path, web_dist=tmp_path / "missing"))
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/v1/auth/status").json() == {
            "configured": True,
            "authenticated": False,
        }
