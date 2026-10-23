#!/usr/bin/env bash
# Inicia o Bees com Docker Compose em Linux/macOS e abre o primeiro acesso no navegador.
# Equivalente a scripts/start.ps1. O código de primeiro acesso fica só em memória.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${BEES_HTTP_PORT:-8080}"
PROJECT="bees"
BUILD=1
BROWSER=1
LOG_DIR="$ROOT/logs"

while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --no-browser) BROWSER=0 ;;
    --port) shift; PORT="${1:-}" ;;
    --project-name) shift; PROJECT="${1:-}" ;;
    --log-dir) shift; LOG_DIR="${1:-}" ;;
    -h|--help) echo "Uso: $0 [--no-build] [--no-browser] [--port N] [--project-name nome]"; exit 0 ;;
    *) echo "Opção desconhecida: $1" >&2; exit 2 ;;
  esac
  shift
done

fail() { echo "Falha ao iniciar o Bees: $1" >&2; exit 1; }

case "$PORT" in ''|*[!0-9]*) fail "porta inválida." ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail "porta inválida."
printf '%s' "$PROJECT" | grep -Eq '^[a-z0-9][a-z0-9_-]*$' || fail "nome de projeto inválido."

mkdir -p "$LOG_DIR" || fail "não foi possível criar $LOG_DIR."
LOG="$LOG_DIR/startup.log"

command -v docker >/dev/null 2>&1 || fail "instale o Docker (Engine 28+ com Compose 2.20+)."
[ "$(docker info --format '{{.OSType}}' 2>/dev/null)" = "linux" ] \
  || fail "inicie o Docker e aguarde o serviço de containers ficar disponível."

export BEES_HTTP_PORT="$PORT"
compose() { docker compose --project-name "$PROJECT" --project-directory "$ROOT" -f "$ROOT/compose.yaml" "$@"; }

echo "Preparando o Bees. Na primeira vez, o Docker precisa baixar e compilar a aplicação."
if [ "$BUILD" = 1 ]; then
  compose build >"$LOG" 2>&1 || fail "não foi possível compilar. Confira $LOG."
fi
existing="$(compose ps --all --quiet bees 2>/dev/null)"
if [ -n "$existing" ]; then
  previous="$(docker inspect --format '{{.Image}}' "$existing" 2>/dev/null)"
  next="$(docker image inspect --format '{{.Id}}' bees-local:0.1.0 2>/dev/null)"
  if [ -n "$previous" ] && [ -n "$next" ] && [ "$previous" != "$next" ]; then
    # Nenhum worker ativo durante a migração para a nova imagem.
    compose stop worker bees >>"$LOG" 2>&1 || fail "não foi possível parar a versão anterior."
  fi
fi
compose up --detach --wait --wait-timeout 180 >>"$LOG" 2>&1 \
  || fail "não foi possível iniciar. Confira a porta $PORT e $LOG."

# Saída privada: nunca vai para o log.
receipt="$(compose exec -T bees bees-auth bootstrap --format json 2>/dev/null)" \
  || fail "não foi possível preparar o primeiro acesso. Inicie novamente."
url="http://localhost:$PORT/"
case "$receipt" in
  *'"configured":true'*) ;;
  *'"configured":false'*)
    token="$(printf '%s' "$receipt" | sed -n 's/.*"bootstrap_token":"\([A-Za-z0-9_-]\{43\}\)".*/\1/p')"
    [ -n "$token" ] || fail "código de primeiro acesso inválido."
    url="${url}#setup=$token"
    ;;
  *) fail "resposta de primeiro acesso inválida." ;;
esac
receipt=""

if [ "$BROWSER" = 1 ]; then
  opener="${BEES_OPEN_COMMAND:-}"
  if [ -z "$opener" ]; then
    if command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then opener="open"
    elif command -v xdg-open >/dev/null 2>&1; then opener="xdg-open"; fi
  fi
  if [ -n "$opener" ]; then "$opener" "$url" >/dev/null 2>&1 || true
  else echo "Abra no navegador: http://localhost:$PORT/ (o código de primeiro acesso não é exibido aqui)."; fi
fi
token=""; url=""
echo "Bees pronto em http://localhost:$PORT/."
