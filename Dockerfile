# syntax=docker/dockerfile:1
FROM node:22.17.0-bookworm-slim@sha256:b04ce4ae4e95b522112c2e5c52f781471a5cbc3b594527bcddedee9bc48c03a0 AS web
WORKDIR /build
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/index.html apps/web/vite.config.ts apps/web/tsconfig.json ./
COPY apps/web/src ./src
COPY apps/web/public ./public
RUN npm run build

FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789 AS python-build
COPY --from=ghcr.io/astral-sh/uv:0.7.21@sha256:a64333b61f96312df88eafce95121b017cbff72033ab2dbc6398edb4f24a75dd /uv /bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml ./apps/api/pyproject.toml
COPY apps/worker/pyproject.toml ./apps/worker/pyproject.toml
COPY apps/host/pyproject.toml ./apps/host/pyproject.toml
COPY packages/core/pyproject.toml ./packages/core/pyproject.toml
COPY apps/api/src ./apps/api/src
COPY apps/worker/src ./apps/worker/src
COPY apps/host/src ./apps/host/src
COPY packages/core/src ./packages/core/src
RUN uv sync --locked --no-dev --no-editable

FROM python:3.14.4-slim-bookworm@sha256:fc74d22ffd0d5ac395a4b7bdda75a4539758862c49ebf3005647084631e63789 AS runtime
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    BEES_DEPLOYMENT_MODE=container BEES_HOST=0.0.0.0 BEES_PORT=8000 \
    BEES_WEB_DIST=/app/web BEES_DATA_DIR=/var/lib/bees \
    BEES_VAULT_KEY_FILE=/run/bees-secrets/vault.key
RUN groupadd --gid 10001 bees && useradd --uid 10001 --gid bees --no-create-home bees \
    && mkdir -p /var/lib/bees /run/bees-secrets \
    && chown bees:bees /var/lib/bees /run/bees-secrets \
    && chmod 700 /var/lib/bees /run/bees-secrets
WORKDIR /app
COPY --from=python-build /app/.venv /app/.venv
COPY --from=web /build/dist /app/web
COPY containers/healthcheck.py /app/healthcheck.py
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=6 CMD ["python", "/app/healthcheck.py"]
CMD ["bees-api"]

# Alvo explícito de testes POSIX; não altera a imagem usada pela aplicação.
FROM python-build AS verification
RUN uv sync --locked --no-editable
COPY apps/api/tests ./apps/api/tests
COPY apps/worker/tests ./apps/worker/tests
COPY apps/host/tests ./apps/host/tests
COPY packages/core/tests ./packages/core/tests
CMD ["/app/.venv/bin/python", "-m", "pytest", "apps/api/tests/test_managed_key.py", "apps/api/tests/test_model_cli.py", "apps/api/tests/test_container_config.py", "apps/api/tests/test_bootstrap_cli.py", "apps/api/tests/test_policies_api.py", "apps/api/tests/test_approvals_api.py", "apps/api/tests/test_tools_api.py", "apps/api/tests/test_environments_api.py", "apps/api/tests/test_host_links_api.py", "apps/api/tests/test_host_link_cli.py", "apps/host/tests", "apps/worker/tests/test_worker_cli.py", "packages/core/tests/test_provider_vault.py", "packages/core/tests/test_execution_store.py", "packages/core/tests/test_tasks_execution.py", "packages/core/tests/test_policies.py", "packages/core/tests/test_execution_policies.py", "packages/core/tests/test_approvals.py", "packages/core/tests/test_execution_approvals.py", "packages/core/tests/test_tools.py", "packages/core/tests/test_environments.py", "packages/core/tests/test_hosts.py", "packages/core/tests/test_database.py"]
