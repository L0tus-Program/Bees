"""Bootstrap atômico, senha Argon2id e sessões opacas revogáveis."""

import hashlib
import math
import re
import secrets
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import BoundedSemaphore

import apsw
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from bees_core.storage.database import Database

_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_ARGON_SLOTS = BoundedSemaphore(2)
_MESSAGES = {
    "already_configured": "Esta instalação já possui uma identidade.",
    "bootstrap_invalid": "Código de configuração inválido ou expirado.",
    "credentials_invalid": "Senha inválida ou identidade indisponível.",
    "invalid_identity": "Nome ou senha fora dos limites permitidos.",
    "rate_limited": "Limite de tentativas atingido. Aguarde antes de tentar novamente.",
}


class AuthError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in _MESSAGES else "credentials_invalid"
        super().__init__(_MESSAGES[self.code])


@contextmanager
def _argon_slot():
    # Limite por processo; não prender dezenas de threads aguardando memória.
    if not _ARGON_SLOTS.acquire(blocking=False):
        raise AuthError("rate_limited")
    try:
        yield
    finally:
        _ARGON_SLOTS.release()


@dataclass(frozen=True)
class User:
    name: str


@dataclass(frozen=True)
class Session:
    user: User
    csrf_token: str = field(repr=False)
    expires_at: float


@dataclass(frozen=True)
class IssuedSession(Session):
    token: str = field(repr=False)


def _hash_token(token: str | None) -> str | None:
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def validate_password(password: str) -> None:
    # Limite antes de Argon2 e sem normalização silenciosa da senha.
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise AuthError("invalid_identity")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError:
        raise AuthError("invalid_identity") from None


class IdentityService:
    """Uma identidade por banco; o relógio injetável retorna epoch UTC.

    Reservar tentativas em transação própria mantém proteção após erro/restart.
    Argon2 acontece fora do lock de escrita e a condição é reavaliada ao confirmar.
    O limite global por operação não confia em IP/headers de proxy e permite DoS
    temporário por bloqueio da conta; não é proteção contra host comprometido.
    """

    RATE_WINDOW_SECONDS = 300
    LOGIN_ATTEMPTS = 5
    SETUP_ATTEMPTS = 10

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], float] = time.time,
        session_ttl_seconds: int = 43200,
        bootstrap_ttl_seconds: int = 900,
    ) -> None:
        if not 60 <= session_ttl_seconds <= 86400 or not 60 <= bootstrap_ttl_seconds <= 900:
            raise ValueError("TTL de identidade fora dos limites permitidos.")
        self.database = database
        self.clock = clock
        self.session_ttl_seconds = session_ttl_seconds
        self.bootstrap_ttl_seconds = bootstrap_ttl_seconds
        # RFC 9106 low-memory profile: Argon2id, 64 MiB, 3 passes, 4 lanes.
        self._hasher = PasswordHasher(
            time_cost=3, memory_cost=65536, parallelism=4, hash_len=32, salt_len=16, type=Type.ID
        )

    def _now(self) -> float:
        now = float(self.clock())
        if not math.isfinite(now):
            raise ValueError("Relógio inválido.")
        return now

    def configured(self) -> bool:
        with self.database.transaction(write=False) as connection:
            return connection.execute("SELECT EXISTS(SELECT 1 FROM identity_users)").get == 1

    def issue_bootstrap(self) -> str:
        token = secrets.token_urlsafe(32)
        now = self._now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT EXISTS(SELECT 1 FROM identity_users)").get:
                raise AuthError("already_configured")
            connection.execute(
                "INSERT INTO identity_bootstrap(id,token_hash,created_at,expires_at,consumed_at) "
                "VALUES(1,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
                "token_hash=excluded.token_hash,created_at=excluded.created_at,"
                "expires_at=excluded.expires_at,consumed_at=NULL",
                (_hash_token(token), now, now + self.bootstrap_ttl_seconds),
            )
        return token

    def _reserve_attempt(self, operation: str) -> None:
        now = self._now()
        maximum = self.LOGIN_ATTEMPTS if operation == "login" else self.SETUP_ATTEMPTS
        with self.database.transaction() as connection:
            previous = connection.execute(
                "SELECT window_started_at,attempts FROM identity_rate_limits WHERE operation=?",
                (operation,),
            ).get
            if previous is not None:
                started, attempts = previous
                # Uma regressão do relógio não limpa tentativas existentes.
                if now < started + self.RATE_WINDOW_SECONDS and attempts >= maximum:
                    raise AuthError("rate_limited")
                if now < started + self.RATE_WINDOW_SECONDS:
                    connection.execute(
                        "UPDATE identity_rate_limits SET attempts=attempts+1 WHERE operation=?",
                        (operation,),
                    )
                    return
            connection.execute(
                "INSERT INTO identity_rate_limits(operation,window_started_at,attempts) "
                "VALUES(?,?,1) ON CONFLICT(operation) DO UPDATE SET "
                "window_started_at=excluded.window_started_at,attempts=1",
                (operation, now),
            )

    @staticmethod
    def _valid_bootstrap(connection: apsw.Connection, token_hash: str | None, now: float) -> bool:
        if token_hash is None:
            return False
        return (
            connection.execute(
                "SELECT EXISTS(SELECT 1 FROM identity_bootstrap "
                "WHERE id=1 AND token_hash=? AND consumed_at IS NULL AND expires_at>?)",
                (token_hash, now),
            ).get
            == 1
        )

    def _create_session(self, connection: apsw.Connection, name: str, now: float) -> IssuedSession:
        session = IssuedSession(
            user=User(name),
            csrf_token=secrets.token_urlsafe(32),
            expires_at=now + self.session_ttl_seconds,
            token=secrets.token_urlsafe(32),
        )
        connection.execute(
            "INSERT INTO identity_sessions(token_hash,user_id,csrf_token,created_at,expires_at) "
            "VALUES(?,1,?,?,?)",
            (_hash_token(session.token), session.csrf_token, now, session.expires_at),
        )
        return session

    def setup(self, bootstrap_token: str, name: str, password: str) -> IssuedSession:
        validate_password(password)
        if (
            not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 80
            or any(ord(char) < 32 or ord(char) == 127 for char in name)
        ):
            raise AuthError("invalid_identity")
        name = name.strip()
        self._reserve_attempt("setup")
        token_hash = _hash_token(bootstrap_token)
        with self.database.transaction(write=False) as connection:
            if connection.execute("SELECT EXISTS(SELECT 1 FROM identity_users)").get:
                raise AuthError("already_configured")
            if not self._valid_bootstrap(connection, token_hash, self._now()):
                raise AuthError("bootstrap_invalid")
        with _argon_slot():
            password_hash = self._hasher.hash(password)
        now = self._now()
        with self.database.transaction() as connection:
            if connection.execute("SELECT EXISTS(SELECT 1 FROM identity_users)").get:
                raise AuthError("already_configured")
            if not self._valid_bootstrap(connection, token_hash, now):
                raise AuthError("bootstrap_invalid")
            connection.execute(
                "INSERT INTO identity_users(id,name,password_hash,created_at) VALUES(1,?,?,?)",
                (name, password_hash, now),
            )
            connection.execute("UPDATE identity_bootstrap SET consumed_at=? WHERE id=1", (now,))
            connection.execute("DELETE FROM identity_rate_limits WHERE operation='setup'")
            return self._create_session(connection, name, now)

    def login(self, password: str, previous_token: str | None = None) -> IssuedSession:
        self._reserve_attempt("login")
        try:
            validate_password(password)
        except AuthError:
            raise AuthError("credentials_invalid") from None
        with self.database.transaction(write=False) as connection:
            user = connection.execute(
                "SELECT name,password_hash FROM identity_users WHERE id=1"
            ).get
        if user is None:
            raise AuthError("credentials_invalid")
        name, password_hash = user
        try:
            with _argon_slot():
                self._hasher.verify(password_hash, password)
        except VerificationError, InvalidHashError:
            raise AuthError("credentials_invalid") from None
        now = self._now()
        with self.database.transaction() as connection:
            # Verificação otimista caso senha mude em manutenção concorrente.
            current = connection.execute("SELECT password_hash FROM identity_users WHERE id=1").get
            if current != password_hash:
                raise AuthError("credentials_invalid")
            previous_hash = _hash_token(previous_token)
            if previous_hash is not None:
                connection.execute(
                    "UPDATE identity_sessions SET revoked_at=? "
                    "WHERE token_hash=? AND revoked_at IS NULL",
                    (now, previous_hash),
                )
            connection.execute("DELETE FROM identity_rate_limits WHERE operation='login'")
            return self._create_session(connection, name, now)

    def authenticate(self, token: str | None) -> Session | None:
        token_hash = _hash_token(token)
        if token_hash is None:
            return None
        with self.database.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT u.name,s.csrf_token,s.expires_at FROM identity_sessions s "
                "JOIN identity_users u ON u.id=s.user_id "
                "WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?",
                (token_hash, self._now()),
            ).get
        return None if row is None else Session(User(row[0]), row[1], row[2])

    def logout(self, token: str | None) -> None:
        token_hash = _hash_token(token)
        if token_hash is None:
            return
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE identity_sessions SET revoked_at=? "
                "WHERE token_hash=? AND revoked_at IS NULL",
                (self._now(), token_hash),
            )
