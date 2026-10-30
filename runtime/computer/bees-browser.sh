#!/bin/sh
# Navegador padrão da área de trabalho da abelha (ícone do painel, links, xdg-open).
# Com o navegador da abelha aberto, abre a aba nele (mesma sessão e logins); senão,
# usa um perfil próprio para não travar o perfil que a abelha usa via Playwright.
for chrome in /ms-playwright/chromium-*/chrome-linux*/chrome; do
  [ -x "$chrome" ] || continue
  profile="$HOME/.config/bees-browser"
  if pgrep -u "$(id -u)" -f -- "--user-data-dir=$HOME/.bees-browser" >/dev/null 2>&1; then
    profile="$HOME/.bees-browser"
  fi
  exec "$chrome" --no-sandbox --no-first-run --no-default-browser-check --disable-dev-shm-usage \
    --user-data-dir="$profile" "$@"
done
echo "bees-browser: Chromium não encontrado em /ms-playwright" >&2
exit 1
