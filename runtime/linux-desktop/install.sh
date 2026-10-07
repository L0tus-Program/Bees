#!/bin/sh
set -eu
export LC_ALL=C
fail() { printf '%s\n' 'Bees: instalação recusada; preserve o estado e consulte o operador.' >&2; exit 1; }
[ "$#" -eq 1 ] && [ "$1" = '--confirm-guest-install' ] || fail
[ "$(id -u)" = 0 ] || fail
[ "$(dpkg --print-architecture)" = amd64 ] || fail
[ -f /etc/os-release ] || fail
. /etc/os-release
[ "$ID" = debian ] && [ "$VERSION_ID" = 13 ] || fail
taskKit=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd -P)
# A árvore e seus ancestrais devem pertencer a root e impedir substituição por
# outro usuário entre a conferência do checksum e o consumo pelo APT.
taskAncestor=$taskKit
while :; do
    [ "$(stat -c '%u' "$taskAncestor")" = 0 ] || fail
    taskMode=$(stat -c '%a' "$taskAncestor")
    [ "$((0$taskMode & 022))" = 0 ] || fail
    [ "$taskAncestor" != / ] || break
    taskAncestor=$(dirname -- "$taskAncestor")
done
[ -z "$(find "$taskKit" ! -user root -print -quit)" ] || fail
[ -z "$(find "$taskKit" -perm /022 -print -quit)" ] || fail
[ -z "$(find "$taskKit" -type f -links +1 -print -quit)" ] || fail
[ -z "$(find "$taskKit" ! -type f ! -type d -print -quit)" ] || fail
# Não seguir links no pacote nem aceitar extração parcial.
[ -z "$(find "$taskKit" -type l -print -quit)" ] || fail
[ -f "$taskKit/SHA256SUMS" ] && [ -f "$taskKit/packages.lock" ] || fail
(cd "$taskKit" && sha256sum --strict -c SHA256SUMS >/dev/null) || fail
taskActual=$(cd "$taskKit" && find . -type f -printf '%P\n' | sort)
taskExpected=$({ awk '{ print $2 }' "$taskKit/SHA256SUMS"; printf '%s\n' SHA256SUMS; } | sort)
[ "$taskActual" = "$taskExpected" ] || fail
[ -d "$taskKit/packages" ] || fail
[ "$(find "$taskKit/packages" -maxdepth 1 -type f -name '*.deb' | wc -l)" -gt 10 ] || fail
[ ! -L /workspace ] && [ ! -L /home/abelha ] && [ ! -L /var/lib/bees-desktop ] || fail
[ ! -L /var ] && [ ! -L /var/lib ] && [ ! -L /home ] || fail
taskState=/var/lib/bees-desktop
taskLockHash=$(sha256sum "$taskKit/packages.lock" | cut -d ' ' -f 1)
check_versions() {
    while IFS=' ' read -r taskPackage taskVersion; do
        case "$taskPackage" in *[!a-z0-9+.-]*|'') fail;; esac
        taskInstalled=$(dpkg-query -W -f='${Version}' "$taskPackage" 2>/dev/null || true)
        if [ "$1" = exact ]; then
            [ "$taskInstalled" = "$taskVersion" ] || fail
            [ "$(dpkg-query -W -f='${db:Status-Status}' "$taskPackage")" = installed ] || fail
        elif [ -n "$taskInstalled" ]; then
            dpkg --compare-versions "$taskInstalled" le "$taskVersion" || fail
        fi
    done < "$taskKit/packages.lock"
}
check_account() {
    [ "$(id -u abelha)" = 10001 ] && [ "$(id -g abelha)" = 10001 ] || fail
    [ "$(id -G abelha)" = 10001 ] || fail
    [ "$(getent passwd abelha | cut -d: -f6)" = /home/abelha ] || fail
    [ "$(passwd -S abelha | cut -d ' ' -f2)" = L ] || fail
    [ "$(stat -c '%u:%g' /workspace)" = 10001:10001 ] || fail
}
if [ -e "$taskState" ]; then
    [ -d "$taskState" ] && [ -f "$taskState/complete" ] || fail
    [ "$(cat "$taskState/complete")" = "$taskLockHash" ] || fail
    check_versions exact
    check_account
    printf '%s\n' 'Bees: instalação existente conferida; nenhum efeito repetido.'
    exit 0
fi
# Não adotar contas ou arquivos anteriores como se fossem uma instalação Bees.
! getent passwd abelha >/dev/null || fail
! getent passwd 10001 >/dev/null || fail
! getent group abelha >/dev/null || fail
! getent group 10001 >/dev/null || fail
[ ! -e /home/abelha ] || fail
if [ -e /workspace ]; then
    [ -d /workspace ] && [ -z "$(find /workspace -mindepth 1 -print -quit)" ] || fail
fi
check_versions compatible
mkdir -m 700 "$taskState"
printf '%s\n' "$taskLockHash" > "$taskState/installing"
# Fontes remotas do guest não são usadas ou modificadas. Os .deb têm hashes conferidos.
mkdir -m 700 "$taskState/apt"
mkdir -m 700 "$taskState/apt/sources.list.d" "$taskState/apt/apt.conf.d" \
    "$taskState/apt/preferences.d" "$taskState/apt/methods"
: > "$taskState/apt/sources.list"
: > "$taskState/apt/apt.conf"
# APT precisa adquirir os .deb locais para seu cache. --no-download impede essa
# cópia no APT3; oferecemos somente métodos locais, sem HTTP/HTTPS no instalador.
for taskMethod in file copy store; do
    [ -x "/usr/lib/apt/methods/$taskMethod" ] || fail
    ln -s "/usr/lib/apt/methods/$taskMethod" "$taskState/apt/methods/$taskMethod"
done
DEBIAN_FRONTEND=noninteractive apt-get \
    -o Dir::Etc="$taskState/apt" -o Dir::Etc::main=apt.conf \
    -o Dir::Etc::parts=apt.conf.d -o Dir::Etc::sourcelist=sources.list \
    -o Dir::Etc::sourceparts=sources.list.d -o Dir::State::lists="$taskState/apt/lists" \
    -o Dir::Bin::methods="$taskState/apt/methods" \
    --no-install-recommends -y install "$taskKit"/packages/*.deb
check_versions exact
groupadd --gid 10001 abelha
useradd --uid 10001 --gid 10001 --create-home --home-dir /home/abelha --shell /bin/bash --password '!' abelha
mkdir -p /workspace
chown 10001:10001 /workspace
chmod 700 /workspace
check_account
mv "$taskState/installing" "$taskState/complete"
printf '%s\n' 'Bees: payload instalado; VM e conexão ao Bees continuam pendentes.'
