$ErrorActionPreference = 'Stop'
$beesRoot = Split-Path -Parent $PSScriptRoot
Push-Location $beesRoot
try {
    & uv sync --locked
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências Python.' }
    & uv run --locked ruff check .
    if ($LASTEXITCODE -ne 0) { throw 'Falha na análise Python.' }
    & uv run --locked ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw 'Falha na formatação Python.' }
    & uv run --locked pytest
    if ($LASTEXITCODE -ne 0) { throw 'Falha nos testes Python.' }
    & npm --prefix apps/web ci
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências web.' }
    & npm --prefix apps/web run lint
    if ($LASTEXITCODE -ne 0) { throw 'Falha na análise web.' }
    & npm --prefix apps/web run test
    if ($LASTEXITCODE -ne 0) { throw 'Falha nos testes web.' }
    & npm --prefix apps/web run build
    if ($LASTEXITCODE -ne 0) { throw 'Falha no build web.' }
} finally {
    Pop-Location
}
