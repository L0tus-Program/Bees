"""API inicial e distribuição opcional do frontend na mesma origem."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from bees_api import __version__
from bees_api.approvals import router as approvals_router
from bees_api.auth import install_auth
from bees_api.config import Settings
from bees_api.configuration import router as configuration_router
from bees_api.managed_key import managed_vault_key, prepare_managed_directories
from bees_api.onboarding import Receipts
from bees_api.onboarding import router as onboarding_router
from bees_api.policies import router as policies_router
from bees_api.profiles import router as profiles_router
from bees_api.runtime import validate_sqlite_runtime
from bees_api.safety import RequestSafetyMiddleware
from bees_api.tasks import router as tasks_router
from bees_api.tools import router as tools_router
from bees_core.approvals import ApprovalError
from bees_core.policies import PolicyError
from bees_core.providers.errors import ProviderError
from bees_core.providers.secrets import build_secret_resolver
from bees_core.providers.service import ProviderService
from bees_core.providers.vault import FernetBackend, FileSecretVault
from bees_core.security.identity import IdentityService
from bees_core.storage.database import Database
from bees_core.storage.store import NotFoundError, StateStore, StoreError
from bees_core.tasks import TaskError
from bees_core.tools import ToolError


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "bees-api"
    version: str = __version__
    stage: str = "foundation"


class StorageStatus(BaseModel):
    status: str = "ready"
    engine: str = "sqlite"
    schema_version: int


def is_api_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized == "api" or normalized.startswith("api/")


class SPAStaticFiles(StaticFiles):
    """Fallback somente para navegação; API e assets ausentes continuam 404."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        if is_api_path(path):
            raise HTTPException(status_code=404)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or PurePosixPath(path).suffix:
                raise
            return await super().get_response("index.html", scope)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        validate_sqlite_runtime()
        if config.deployment_mode == "container":
            prepare_managed_directories(config.vault_key_file, config.data_dir)
        database = Database(config.data_dir / "bees.sqlite3")
        database.initialize()
        store = StateStore(database, cache_ttl_seconds=config.cache_ttl_seconds)
        store.prune_cache(limit=config.cache_prune_limit)
        application.state.store = store
        application.state.database = database
        application.state.identity = IdentityService(database)
        if config.deployment_mode == "container":
            key = managed_vault_key(config.vault_key_file, config.data_dir, database)
            application.state.vault = FileSecretVault(
                config.data_dir / "vault", backend=FernetBackend(key)
            )
        else:
            application.state.vault = FileSecretVault(
                config.data_dir / "vault", key=config.vault_key
            )
        application.state.resolver = build_secret_resolver(application.state.vault)
        application.state.providers = ProviderService(database, application.state.resolver)
        application.state.receipts = Receipts()
        try:
            yield
        finally:
            del application.state.store
            del application.state.database
            del application.state.identity
            del application.state.vault
            del application.state.resolver
            del application.state.providers
            del application.state.receipts

    app = FastAPI(title="Bees API", version=__version__, lifespan=lifespan)
    install_auth(
        app,
        allowed_origins=config.allowed_origins,
        secure_cookie=config.secure_cookie,
        health_authorities=(f"127.0.0.1:{config.port}", f"localhost:{config.port}"),
    )
    app.add_middleware(RequestSafetyMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=config.allowed_hosts)
    app.include_router(onboarding_router)
    app.include_router(configuration_router)
    app.include_router(profiles_router)
    app.include_router(tasks_router)
    app.include_router(policies_router)
    app.include_router(approvals_router)
    app.include_router(tools_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, error: RequestValidationError) -> JSONResponse:
        # ValidationError pode carregar senha/chave em input; nunca devolver seu conteúdo.
        return JSONResponse(
            {"error": {"code": "validation_failed", "message": "Confira os campos informados."}},
            status_code=422,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException) -> JSONResponse:
        detail = (
            error.detail
            if isinstance(error.detail, dict)
            else {
                "code": "request_rejected",
                "message": str(error.detail),
            }
        )
        return JSONResponse({"error": detail}, status_code=error.status_code, headers=error.headers)

    @app.exception_handler(ProviderError)
    async def provider_error(request: Request, error: ProviderError) -> JSONResponse:
        status = (
            422
            if error.code
            in (
                "invalid_config",
                "invalid_request",
                "unsupported_capability",
                "request_too_large",
                "local_model_required",
                "invalid_secret",
                "invalid_secret_reference",
            )
            else 409
            if error.code in ("state_conflict", "policy_approval_required")
            else 403
            if error.code == "policy_denied"
            else 502
        )
        return JSONResponse(
            {
                "error": {"code": error.code, "message": str(error)}
                | (
                    {"upstream_status": error.upstream_status}
                    if error.upstream_status is not None
                    else {}
                )
            },
            status_code=status,
        )

    @app.exception_handler(StoreError)
    async def store_error(request: Request, error: StoreError) -> JSONResponse:
        code = "not_found" if isinstance(error, NotFoundError) else "state_conflict"
        status = 404 if isinstance(error, NotFoundError) else 409
        return JSONResponse(
            {"error": {"code": code, "message": "Registro ausente ou alterado; atualize a tela."}},
            status_code=status,
        )

    @app.exception_handler(TaskError)
    async def task_error(request: Request, error: TaskError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": error.code, "message": "Comando indisponível; atualize a tarefa."}},
            status_code=409,
        )

    @app.exception_handler(PolicyError)
    async def policy_error(request: Request, error: PolicyError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": error.code,
                    "message": "Confira a regra de autonomia e atualize seu estado.",
                }
            },
            status_code=409 if error.code == "idempotency_conflict" else 422,
        )

    @app.exception_handler(ApprovalError)
    async def approval_error(request: Request, error: ApprovalError) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": error.code, "message": "Atualize a decisão e confira seu escopo."}},
            status_code=422 if error.code == "invalid_approval" else 409,
        )

    @app.exception_handler(ToolError)
    async def tool_error(request: Request, error: ToolError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": error.code,
                    "message": "Confira a ferramenta e atualize seu estado.",
                }
            },
            status_code=409,
        )

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.get("/api/v1/state/status", response_model=StorageStatus, tags=["state"])
    def state_status() -> StorageStatus:
        return StorageStatus(schema_version=app.state.database.schema_version())

    if (config.web_dist / "index.html").is_file():
        app.mount("/", SPAStaticFiles(directory=config.web_dist, html=True), name="web")
    else:

        @app.get("/{path:path}", include_in_schema=False)
        async def frontend_unavailable(path: str) -> None:
            if is_api_path(path):
                raise HTTPException(status_code=404)
            raise HTTPException(
                status_code=503,
                detail="Frontend indisponível. Gere apps/web/dist e reinicie o serviço.",
            )

    return app
