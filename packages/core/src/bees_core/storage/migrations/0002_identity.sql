-- Identidade individual. Nenhum token de sessão/bootstrap é armazenado em claro.
CREATE TABLE identity_users (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
) STRICT;

CREATE TABLE identity_bootstrap (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    token_hash TEXT NOT NULL CHECK(length(token_hash) = 64),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL CHECK(expires_at > created_at),
    consumed_at REAL
) STRICT;

CREATE TABLE identity_sessions (
    token_hash TEXT PRIMARY KEY CHECK(length(token_hash) = 64),
    user_id INTEGER NOT NULL REFERENCES identity_users(id),
    csrf_token TEXT NOT NULL CHECK(length(csrf_token) = 43),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL CHECK(expires_at > created_at),
    revoked_at REAL
) STRICT;
CREATE INDEX identity_sessions_expiry ON identity_sessions(expires_at);

CREATE TABLE identity_rate_limits (
    operation TEXT PRIMARY KEY CHECK(operation IN ('login', 'setup')),
    window_started_at REAL NOT NULL,
    attempts INTEGER NOT NULL CHECK(attempts > 0)
) STRICT;
