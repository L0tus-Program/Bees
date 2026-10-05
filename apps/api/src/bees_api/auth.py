"""Transporte de identidade: cookies opacos, fronteira de origem e CSRF."""

import secrets
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from bees_core.security.identity import AuthError, IdentityService, IssuedSession, Session

COOKIE_NAME = "bees_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
AUTH_HTTP_CODES = {
    "already_configured": 409,
    "bootstrap_invalid": 401,
    "credentials_invalid": 401,
    "invalid_identity": 422,
    "rate_limited": 429,
}


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SetupInput(Input):
    bootstrap_token: SecretStr = Field(min_length=43, max_length=43)
    name: str = Field(min_length=1, max_length=80)
    password: SecretStr = Field(min_length=12, max_length=256)


class LoginInput(Input):
    password: SecretStr = Field(min_length=1, max_length=256)


class UserResponse(BaseModel):
    name: str


class AuthStatus(BaseModel):
    configured: bool
    authenticated: bool
    user: UserResponse | None = None
    csrf_token: str | None = Field(default=None, repr=False)


def _error(code: str, message: str, status: int) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 429:
        headers["Retry-After"] = str(IdentityService.RATE_WINDOW_SECONDS)
    return JSONResponse({"error": {"code": code, "message": message}}, status, headers=headers)


class AuthBoundaryMiddleware:
    """Valida Host/Origin originais; não interpreta headers de encaminhamento."""

    def __init__(
        self,
        app: ASGIApp,
        allowed_origins: tuple[str, ...],
        secure_cookie: bool,
        health_authorities: tuple[str, ...],
    ) -> None:
        self.app = app
        self.origins = frozenset(allowed_origins)
        self.authorities = frozenset(urlsplit(origin).netloc.lower() for origin in allowed_origins)
        self.secure_cookie = secure_cookie
        self.health_authorities = frozenset(health_authorities)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        hosts = request.headers.getlist("host")
        path = scope.get("path", "")
        health_read = path in ("/api/v1/health", "/api/v1/state/status") and scope["method"] in (
            "GET",
            "HEAD",
        )
        valid_authorities = (
            self.authorities | self.health_authorities if health_read else self.authorities
        )
        if len(hosts) != 1 or hosts[0].lower() not in valid_authorities:
            await _error("host_rejected", "Host não permitido.", 400)(scope, receive, send)
            return
        if path == "/api" or path.startswith("/api/"):
            origins = request.headers.getlist("origin")
            origin = origins[0] if len(origins) == 1 else None
            unsafe = scope["method"] not in SAFE_METHODS
            if (
                len(origins) > 1
                or (origins and origin not in self.origins)
                or (unsafe and origin not in self.origins)
                or request.headers.get("sec-fetch-site") == "cross-site"
            ):
                await _error("origin_rejected", "Origem não permitida.", 403)(scope, receive, send)
                return
            if unsafe:
                content_types = request.headers.getlist("content-type")
                if len(content_types) != 1 or content_types[0].split(";", 1)[0].strip().lower() != (
                    "application/json"
                ):
                    await _error("json_required", "Envie conteúdo JSON.", 415)(scope, receive, send)
                    return
            if self.secure_cookie and not health_read and scope["scheme"] != "https":
                await _error("https_required", "HTTPS é obrigatório.", 403)(scope, receive, send)
                return

        async def private_send(message):
            if message["type"] == "http.response.start" and path.startswith("/api/"):
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key != b"cache-control"
                ]
                headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, private_send)


def _identity(request: Request) -> IdentityService:
    return request.app.state.identity


def require_session(request: Request) -> Session:
    """Dependência de toda rota privada; mutações exigem nonce da sessão atual."""
    session = _identity(request).authenticate(request.cookies.get(COOKIE_NAME))
    if session is None:
        raise HTTPException(
            401, detail={"code": "authentication_required", "message": "Entre para continuar."}
        )
    if request.method not in SAFE_METHODS:
        csrf_values = request.headers.getlist("x-bees-csrf")
        csrf = csrf_values[0] if len(csrf_values) == 1 else ""
        if not csrf.isascii() or not secrets.compare_digest(csrf, session.csrf_token):
            raise HTTPException(
                403, detail={"code": "csrf_rejected", "message": "Sessão ou token CSRF inválido."}
            )
    return session


def _status(session: Session | None, configured: bool = True) -> AuthStatus:
    return AuthStatus(
        configured=configured,
        authenticated=session is not None,
        user=UserResponse(name=session.user.name) if session else None,
        csrf_token=session.csrf_token if session else None,
    )


def _set_cookie(request: Request, response: Response, session: IssuedSession) -> None:
    response.set_cookie(
        COOKIE_NAME,
        session.token,
        max_age=_identity(request).session_ttl_seconds,
        expires=datetime.fromtimestamp(session.expires_at, UTC),
        path="/",
        secure=request.app.state.auth_secure_cookie,
        httponly=True,
        samesite="strict",
    )


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.get("/status", response_model=AuthStatus, response_model_exclude_none=True)
def status(request: Request) -> AuthStatus:
    identity = _identity(request)
    return _status(identity.authenticate(request.cookies.get(COOKIE_NAME)), identity.configured())


@router.post("/setup", response_model=AuthStatus, response_model_exclude_none=True)
def setup(body: SetupInput, request: Request, response: Response) -> AuthStatus:
    session = _identity(request).setup(
        body.bootstrap_token.get_secret_value(), body.name, body.password.get_secret_value()
    )
    _set_cookie(request, response, session)
    return _status(session)


@router.post("/login", response_model=AuthStatus, response_model_exclude_none=True)
def login(body: LoginInput, request: Request, response: Response) -> AuthStatus:
    session = _identity(request).login(
        body.password.get_secret_value(), request.cookies.get(COOKIE_NAME)
    )
    _set_cookie(request, response, session)
    return _status(session)


@router.post("/logout", status_code=204)
def logout(request: Request, session: Annotated[Session, Depends(require_session)]) -> Response:
    _identity(request).logout(request.cookies.get(COOKIE_NAME))
    response = Response(status_code=204)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=request.app.state.auth_secure_cookie,
        httponly=True,
        samesite="strict",
    )
    return response


def install_auth(
    app: FastAPI,
    *,
    allowed_origins: tuple[str, ...],
    secure_cookie: bool = False,
    health_authorities: tuple[str, ...] = (),
) -> None:
    if not allowed_origins:
        raise ValueError("Origens explícitas são obrigatórias.")
    for origin in allowed_origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or "*" in origin
        ):
            raise ValueError("Origem de autenticação inválida.")
    app.state.auth_secure_cookie = secure_cookie
    app.add_middleware(
        AuthBoundaryMiddleware,
        allowed_origins=allowed_origins,
        secure_cookie=secure_cookie,
        health_authorities=health_authorities,
    )
    app.include_router(router)

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, error: AuthError) -> JSONResponse:
        return _error(error.code, str(error), AUTH_HTTP_CODES.get(error.code, 400))
