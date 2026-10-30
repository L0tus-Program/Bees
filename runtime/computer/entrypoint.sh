#!/bin/sh
# Filtro de rede aplicado fora das contas das abelhas (UIDs 20000–29999): bloqueia
# redes privadas, link-local/metadados, CGNAT e o próprio plano de controle. O
# supervisor (root) mantém a conexão de saída com a API; depois perde CAP_NET_ADMIN.
set -eu
BEES_UIDS=20000-29999
if iptables -L OUTPUT >/dev/null 2>&1; then
  iptables -F OUTPUT
  # Telas VNC locais: somente o supervisor (root) conecta.
  iptables -A OUTPUT -m owner --uid-owner "$BEES_UIDS" -o lo -p tcp --dport 5900:6199 -j REJECT
  for net in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 100.64.0.0/10 224.0.0.0/4; do
    iptables -A OUTPUT -m owner --uid-owner "$BEES_UIDS" -d "$net" -j REJECT
  done
  if ip6tables -L OUTPUT >/dev/null 2>&1; then
    ip6tables -F OUTPUT
    for net in fc00::/7 fe80::/10 ff00::/8; do
      ip6tables -A OUTPUT -m owner --uid-owner "$BEES_UIDS" -d "$net" -j REJECT
    done
  fi
  echo "bees-computer: filtro de rede aplicado" >&2
elif [ "${BEES_REQUIRE_NETWORK_FILTER:-1}" = "1" ]; then
  echo "bees-computer: sem CAP_NET_ADMIN para o filtro de rede; recusando iniciar" >&2
  exit 1
fi
exec capsh --drop=cap_net_admin,cap_net_raw,cap_sys_admin -- -c \
  'exec python3 -m bees_executor.cli computer "$@"' -- "$@"
