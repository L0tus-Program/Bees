#!/usr/bin/env bash
# Inicia o Bees com Docker Compose em Linux/macOS e abre o primeiro acesso no navegador.
# Equivalente a scripts/start.ps1. O código de primeiro acesso fica só em memória.
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${BEES_HTTP_PORT:-8080}"
PROJECT="bees"
BUILD=1
BROWSER=1
SHORTCUT=0
LOG_DIR="$ROOT/logs"

# Mensagens pelo idioma do sistema (LC_ALL > LC_MESSAGES > LANG); português por padrão.
LOCALE="${LC_ALL:-${LC_MESSAGES:-${LANG:-pt_BR}}}"
case "$LOCALE" in en*) LANGUAGE_CODE=en ;; es*) LANGUAGE_CODE=es ;; *) LANGUAGE_CODE=pt ;; esac

t() {
  case "$LANGUAGE_CODE:$1" in
    pt:usage) echo "Uso: $0 [--no-build] [--no-browser] [--port N] [--project-name nome] [--install-shortcut]" ;;
    en:usage) echo "Usage: $0 [--no-build] [--no-browser] [--port N] [--project-name name] [--install-shortcut]" ;;
    es:usage) echo "Uso: $0 [--no-build] [--no-browser] [--port N] [--project-name nombre] [--install-shortcut]" ;;
    pt:unknown) echo "Opção desconhecida: $2" ;;
    en:unknown) echo "Unknown option: $2" ;;
    es:unknown) echo "Opción desconocida: $2" ;;
    pt:fail) echo "Falha ao iniciar o Bees: $2" ;;
    en:fail) echo "Could not start Bees: $2" ;;
    es:fail) echo "No se pudo iniciar Bees: $2" ;;
    pt:port) echo "porta inválida." ;;
    en:port) echo "invalid port." ;;
    es:port) echo "puerto inválido." ;;
    pt:project) echo "nome de projeto inválido." ;;
    en:project) echo "invalid project name." ;;
    es:project) echo "nombre de proyecto inválido." ;;
    pt:logdir) echo "não foi possível criar $2." ;;
    en:logdir) echo "could not create $2." ;;
    es:logdir) echo "no se pudo crear $2." ;;
    pt:busy) echo "o Bees já está sendo iniciado (processo $2). Aguarde essa janela terminar." ;;
    en:busy) echo "Bees is already starting (process $2). Wait for that window to finish." ;;
    es:busy) echo "Bees ya se está iniciando (proceso $2). Espera a que esa ventana termine." ;;
    pt:docker) echo "instale o Docker (Engine 28+ com Compose 2.20+)." ;;
    en:docker) echo "install Docker (Engine 28+ with Compose 2.20+)." ;;
    es:docker) echo "instala Docker (Engine 28+ con Compose 2.20+)." ;;
    pt:engine) echo "inicie o Docker e aguarde o serviço de containers ficar disponível." ;;
    en:engine) echo "start Docker and wait for the container service to be available." ;;
    es:engine) echo "inicia Docker y espera a que el servicio de contenedores esté disponible." ;;
    pt:preparing) echo "Preparando o Bees. Na primeira vez, o Docker precisa baixar e compilar a aplicação." ;;
    en:preparing) echo "Preparing Bees. The first time, Docker needs to download and build the application." ;;
    es:preparing) echo "Preparando Bees. La primera vez, Docker necesita descargar y compilar la aplicación." ;;
    pt:build) echo "não foi possível compilar. Confira $2." ;;
    en:build) echo "the build failed. Check $2." ;;
    es:build) echo "no se pudo compilar. Revisa $2." ;;
    pt:stop) echo "não foi possível parar a versão anterior." ;;
    en:stop) echo "could not stop the previous version." ;;
    es:stop) echo "no se pudo detener la versión anterior." ;;
    pt:up) echo "não foi possível iniciar. Confira a porta $PORT e $2." ;;
    en:up) echo "could not start. Check port $PORT and $2." ;;
    es:up) echo "no se pudo iniciar. Revisa el puerto $PORT y $2." ;;
    pt:bootstrap) echo "não foi possível preparar o primeiro acesso. Inicie novamente." ;;
    en:bootstrap) echo "could not prepare the first access. Start again." ;;
    es:bootstrap) echo "no se pudo preparar el primer acceso. Inicia de nuevo." ;;
    pt:code) echo "código de primeiro acesso inválido." ;;
    en:code) echo "invalid first-access code." ;;
    es:code) echo "código de primer acceso inválido." ;;
    pt:receipt) echo "resposta de primeiro acesso inválida." ;;
    en:receipt) echo "invalid first-access response." ;;
    es:receipt) echo "respuesta de primer acceso inválida." ;;
    pt:open) echo "Abra no navegador: http://localhost:$PORT/ (o código de primeiro acesso não é exibido aqui)." ;;
    en:open) echo "Open in your browser: http://localhost:$PORT/ (the first-access code is not shown here)." ;;
    es:open) echo "Abre en el navegador: http://localhost:$PORT/ (el código de primer acceso no se muestra aquí)." ;;
    pt:ready) echo "Bees pronto em http://localhost:$PORT/." ;;
    en:ready) echo "Bees is ready at http://localhost:$PORT/." ;;
    es:ready) echo "Bees listo en http://localhost:$PORT/." ;;
    pt:shortcut) echo "Atalho criado: $2" ;;
    en:shortcut) echo "Shortcut created: $2" ;;
    es:shortcut) echo "Acceso directo creado: $2" ;;
    pt:noshortcut) echo "atalho de menu disponível só em Linux com XDG." ;;
    en:noshortcut) echo "menu shortcut is only available on Linux with XDG." ;;
    es:noshortcut) echo "el acceso directo de menú solo está disponible en Linux con XDG." ;;
  esac
}

fail() { t fail "$(t "$1" "${2:-}")" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --no-browser) BROWSER=0 ;;
    --install-shortcut) SHORTCUT=1 ;;
    --port) shift; PORT="${1:-}" ;;
    --project-name) shift; PROJECT="${1:-}" ;;
    --log-dir) shift; LOG_DIR="${1:-}" ;;
    -h|--help) t usage; exit 0 ;;
    *) t unknown "$1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$SHORTCUT" = 1 ]; then
  # Atalho do menu (Linux/XDG). O caminho é citado: pastas com espaços/acentos funcionam.
  [ "$(uname)" = "Linux" ] || fail noshortcut
  applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  mkdir -p "$applications" || fail logdir "$applications"
  target="$applications/bees.desktop"
  escaped="$(printf '%s' "$ROOT/scripts/start.sh" | sed 's/\\/\\\\/g; s/"/\\"/g; s/`/\\`/g; s/\$/\\$/g')"
  {
    echo "[Desktop Entry]"
    echo "Type=Application"
    echo "Name=Bees"
    echo "Comment=Iniciar o Bees"
    echo "Exec=\"$escaped\""
    echo "Icon=$ROOT/apps/web/public/icon-192.png"
    echo "Terminal=true"
    echo "Categories=Utility;"
  } >"$target" || fail logdir "$target"
  chmod 0644 "$target"
  t shortcut "$target"
  exit 0
fi

case "$PORT" in ''|*[!0-9]*) fail port ;; esac
[ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || fail port
printf '%s' "$PROJECT" | grep -Eq '^[a-z0-9][a-z0-9_-]*$' || fail project

mkdir -p "$LOG_DIR" || fail logdir "$LOG_DIR"
LOG="$LOG_DIR/startup.log"

# Uma inicialização por vez (portável: mkdir é atômico também no macOS, sem flock).
LOCK="$LOG_DIR/.start.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  other="$(cat "$LOCK/pid" 2>/dev/null)"
  case "$other" in ''|*[!0-9]*) other="" ;; esac
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then fail busy "$other"; fi
  # Trava abandonada (processo encerrado): recupera.
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || fail busy "?"
fi
echo "$$" >"$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

command -v docker >/dev/null 2>&1 || fail docker
[ "$(docker info --format '{{.OSType}}' 2>/dev/null)" = "linux" ] || fail engine

export BEES_HTTP_PORT="$PORT"
compose() { docker compose --project-name "$PROJECT" --project-directory "$ROOT" -f "$ROOT/compose.yaml" "$@"; }

t preparing
if [ "$BUILD" = 1 ]; then
  compose build >"$LOG" 2>&1 || fail build "$LOG"
fi
existing="$(compose ps --all --quiet bees 2>/dev/null)"
if [ -n "$existing" ]; then
  previous="$(docker inspect --format '{{.Image}}' "$existing" 2>/dev/null)"
  next="$(docker image inspect --format '{{.Id}}' bees-local:0.1.0 2>/dev/null)"
  if [ -n "$previous" ] && [ -n "$next" ] && [ "$previous" != "$next" ]; then
    # Nenhum worker ativo durante a migração para a nova imagem.
    compose stop worker bees >>"$LOG" 2>&1 || fail stop
  fi
fi
compose up --detach --wait --wait-timeout 180 >>"$LOG" 2>&1 || fail up "$LOG"

# Saída privada: nunca vai para o log.
receipt="$(compose exec -T bees bees-auth bootstrap --format json 2>/dev/null)" || fail bootstrap
url="http://localhost:$PORT/"
case "$receipt" in
  *'"configured":true'*) ;;
  *'"configured":false'*)
    token="$(printf '%s' "$receipt" | sed -n 's/.*"bootstrap_token":"\([A-Za-z0-9_-]\{43\}\)".*/\1/p')"
    [ -n "$token" ] || fail code
    url="${url}#setup=$token"
    ;;
  *) fail receipt ;;
esac
receipt=""

if [ "$BROWSER" = 1 ]; then
  opener="${BEES_OPEN_COMMAND:-}"
  if [ -z "$opener" ]; then
    if command -v open >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then opener="open"
    elif command -v xdg-open >/dev/null 2>&1; then opener="xdg-open"; fi
  fi
  if [ -n "$opener" ]; then "$opener" "$url" >/dev/null 2>&1 || true
  else t open; fi
fi
token=""; url=""
t ready
