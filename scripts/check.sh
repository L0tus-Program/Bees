#!/usr/bin/env sh
set -eu
bees_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$bees_root"
uv sync --locked
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest
npm --prefix apps/web ci
npm --prefix apps/web run lint
npm --prefix apps/web run test
npm --prefix apps/web run build
