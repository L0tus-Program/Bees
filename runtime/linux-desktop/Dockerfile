FROM debian:13.7-slim@sha256:918311b7b6c4c6f68b232ba516584925f6c78ad82b6fd534b98979df6438e483 AS packages
ENV DEBIAN_FRONTEND=noninteractive
COPY runtime/linux-desktop/snapshot.sources /etc/apt/sources.list.d/debian.sources
RUN apt-get update \
 && echo '0584fba32e13e0ab8285fb16c27adea1ec03a73669c18702821094fd6ca86675  /var/lib/apt/lists/snapshot.debian.org_archive_debian_20261005T000000Z_dists_trixie_InRelease' | sha256sum -c - \
 && echo '1a25f10a8e266ba293823ac2c52325b7333e49dddbc2ef74de71f8d7c6d6b7f8  /var/lib/apt/lists/snapshot.debian.org_archive_debian-security_20261005T000000Z_dists_trixie-security_InRelease' | sha256sum -c - \
 && apt-get install -y --no-install-recommends python3 gpgv ca-certificates xfce4 chromium libreoffice-writer dbus-x11 xvfb fonts-dejavu-core
COPY runtime/linux-desktop/prepare.py /recipe/prepare.py
COPY runtime/linux-desktop/install.sh /recipe/install.sh
RUN mkdir -p /out/payload/packages \
 && cd /out/payload/packages \
 && dpkg-query -W -f='${Package}=${Version}\n' | xargs -r -n 40 apt-get download \
 && python3 /recipe/prepare.py

# Imagem apenas para verificar assinatura e testar o payload em laboratório.
FROM packages AS verification-image
COPY runtime/linux-desktop/smoke.py /recipe/smoke.py
ENTRYPOINT []
CMD ["python3", "/recipe/smoke.py"]

# Prova adicional: instalar todos os .deb em base mínima, sem acesso à rede.
FROM debian:13.7-slim@sha256:918311b7b6c4c6f68b232ba516584925f6c78ad82b6fd534b98979df6438e483 AS installed-lab
COPY --from=packages /out/payload /out/payload
COPY runtime/linux-desktop/smoke.py /recipe/smoke.py
RUN --network=none /bin/sh /out/payload/install.sh --confirm-guest-install
CMD ["python3", "/recipe/smoke.py", "--installed"]

# Não é imagem de VM nem imagem de execução do Bees.
FROM scratch AS kit-export
COPY --from=packages /out/bees-linux-desktop-v1-payload.tar.gz /out/bees-linux-desktop-v1-payload.tar.gz
COPY --from=packages /out/payload-inventory.json /out/payload-inventory.json
