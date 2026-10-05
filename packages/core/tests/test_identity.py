from concurrent.futures import ThreadPoolExecutor

import apsw
import pytest
from argon2 import extract_parameters
from argon2.low_level import Type

from bees_core.security.identity import AuthError, IdentityService
from bees_core.storage.database import Database

PASSWORD = "somente senha de teste 123"


@pytest.fixture
def database(tmp_path) -> Database:
    result = Database(tmp_path / "identity.sqlite3")
    result.initialize()
    return result


def test_password_session_and_bootstrap_are_private_and_survive_reload(database) -> None:
    service = IdentityService(database)
    bootstrap = service.issue_bootstrap()
    session = service.setup(bootstrap, "Pessoa de teste", PASSWORD)
    assert service.configured()
    with database.transaction(write=False) as connection:
        password_hash = connection.execute("SELECT password_hash FROM identity_users").get
        parameters = extract_parameters(password_hash)
        assert parameters.type == Type.ID
        assert parameters.memory_cost == 65536
        assert parameters.time_cost == 3
        assert parameters.parallelism == 4
        dumped = repr(
            list(connection.execute("SELECT * FROM identity_bootstrap"))
            + list(connection.execute("SELECT * FROM identity_sessions"))
            + list(connection.execute("SELECT * FROM identity_users"))
        )
    assert PASSWORD not in dumped
    assert bootstrap not in dumped
    assert session.token not in dumped
    assert session.token not in repr(session)
    assert session.csrf_token not in repr(session)
    reopened = IdentityService(Database(database.path))
    restored = reopened.authenticate(session.token)
    assert restored.user.name == "Pessoa de teste"
    assert restored.csrf_token == session.csrf_token
    assert restored.expires_at == session.expires_at
    with pytest.raises(AuthError, match="já possui"):
        reopened.issue_bootstrap()


def test_bootstrap_renewal_expiry_and_replay(database) -> None:
    now = [1000.0]
    service = IdentityService(database, clock=lambda: now[0])
    previous = service.issue_bootstrap()
    token = service.issue_bootstrap()
    with pytest.raises(AuthError) as error:
        service.setup(previous, "Teste", PASSWORD)
    assert error.value.code == "bootstrap_invalid"
    now[0] += 900
    with pytest.raises(AuthError) as error:
        service.setup(token, "Teste", PASSWORD)
    assert error.value.code == "bootstrap_invalid"
    token = service.issue_bootstrap()
    service.setup(token, "Teste", PASSWORD)
    with pytest.raises(AuthError) as error:
        service.setup(token, "Outro nome", PASSWORD)
    assert error.value.code == "already_configured"
    assert IdentityService(database).configured()


def test_setup_confirm_rechecks_renewed_bootstrap_after_hash(database, monkeypatch) -> None:
    service = IdentityService(database)
    bootstrap = service.issue_bootstrap()
    hasher_type = type(service._hasher)
    original_hash = hasher_type.hash

    def renew_during_hash(hasher, password):
        service.issue_bootstrap()
        return original_hash(hasher, password)

    monkeypatch.setattr(hasher_type, "hash", renew_during_hash)
    with pytest.raises(AuthError) as error:
        service.setup(bootstrap, "Teste", PASSWORD)
    assert error.value.code == "bootstrap_invalid"
    assert not service.configured()
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM identity_sessions").get == 0


def test_concurrent_setup_creates_one_identity_and_one_session(database) -> None:
    token = IdentityService(database).issue_bootstrap()

    def configure(name):
        try:
            return IdentityService(Database(database.path)).setup(token, name, PASSWORD)
        except AuthError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(configure, ["Primeiro", "Segundo"]))
    assert sum(not isinstance(result, AuthError) for result in results) == 1
    assert [result.code for result in results if isinstance(result, AuthError)] == [
        "already_configured"
    ]
    with database.transaction(write=False) as connection:
        assert connection.execute("SELECT count(*) FROM identity_users").get == 1
        assert connection.execute("SELECT count(*) FROM identity_sessions").get == 1


def test_login_rotates_previous_session_and_logout_revokes_on_reload(database) -> None:
    service = IdentityService(database)
    first = service.setup(service.issue_bootstrap(), "Teste", PASSWORD)
    second = service.login(PASSWORD, first.token)
    assert second.token != first.token
    assert second.csrf_token != first.csrf_token
    assert service.authenticate(first.token) is None
    assert service.authenticate(second.token) is not None
    IdentityService(Database(database.path)).logout(second.token)
    assert service.authenticate(second.token) is None
    service.logout(second.token)
    assert service.authenticate("made-up-token") is None


def test_session_has_fixed_expiration_not_sliding(database) -> None:
    now = [1000.0]
    service = IdentityService(database, clock=lambda: now[0], session_ttl_seconds=60)
    session = service.setup(service.issue_bootstrap(), "Teste", PASSWORD)
    now[0] += 59
    assert service.authenticate(session.token).expires_at == 1060
    now[0] += 1
    assert service.authenticate(session.token) is None


@pytest.mark.parametrize("operation,maximum", [("login", 5), ("setup", 10)])
def test_attempt_limit_is_persistent_atomic_and_expires(database, operation, maximum) -> None:
    now = [1000.0]
    service = IdentityService(database, clock=lambda: now[0])
    if operation == "login":
        service.setup(service.issue_bootstrap(), "Teste", PASSWORD)
    else:
        service.issue_bootstrap()
    for _ in range(maximum):
        with pytest.raises(AuthError) as error:
            if operation == "login":
                service.login("senha errada de teste")
            else:
                service.setup("A" * 43, "Teste", PASSWORD)
        assert error.value.code in ("credentials_invalid", "bootstrap_invalid")
    reloaded = IdentityService(Database(database.path), clock=lambda: now[0])
    with pytest.raises(AuthError) as error:
        if operation == "login":
            reloaded.login(PASSWORD)
        else:
            reloaded.setup("A" * 43, "Teste", PASSWORD)
    assert error.value.code == "rate_limited"
    now[0] -= 1
    with pytest.raises(AuthError) as error:
        reloaded._reserve_attempt(operation)
    assert error.value.code == "rate_limited"
    now[0] = 1300
    if operation == "login":
        assert reloaded.login(PASSWORD).user.name == "Teste"
    else:
        assert reloaded.setup(reloaded.issue_bootstrap(), "Teste", PASSWORD).user.name == "Teste"


def test_concurrent_attempt_reservation_cannot_exceed_limit(database) -> None:
    def reserve(_):
        try:
            IdentityService(Database(database.path))._reserve_attempt("login")
            return "reserved"
        except AuthError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(reserve, range(10)))
    assert results.count("reserved") == 5
    assert results.count("rate_limited") == 5


@pytest.mark.parametrize("password", ["short", "x" * 257, "x" * 12 + "\ud800"])
def test_invalid_password_cannot_create_identity(database, password) -> None:
    service = IdentityService(database)
    with pytest.raises(AuthError) as error:
        service.setup(service.issue_bootstrap(), "Teste", password)
    assert error.value.code == "invalid_identity"
    assert password not in str(error.value)
    assert not service.configured()


def test_sql_constraint_prevents_second_user(database) -> None:
    service = IdentityService(database)
    service.setup(service.issue_bootstrap(), "Teste", PASSWORD)
    with pytest.raises(apsw.ConstraintError), database.transaction() as connection:
        connection.execute(
            "INSERT INTO identity_users(id,name,password_hash,created_at) VALUES(2,'x','x',0)"
        )
