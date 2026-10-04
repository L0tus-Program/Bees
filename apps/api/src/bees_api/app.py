"""API inicial e distribuição opcional do frontend na mesma origem."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import PurePosixPath

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from bees_api import __version__
from bees_api.config import Settings
from bees_api.runtime import validate_sqlite_runtime


class HealthResponse(BaseModel):
    status: str = "ok"
    service: str = "bees-api"
    version: str = __version__
    stage: str = "foundation"


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


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    validate_sqlite_runtime()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    app = FastAPI(title="Bees API", version=__version__, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["health"])
    async def health() -> HealthResponse:
        return HealthResponse()

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
