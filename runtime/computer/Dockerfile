# syntax=docker/dockerfile:1
# Runtime dos computadores das abelhas: desktop XFCE em Xvfb, Chromium (Playwright),
# LibreOffice e terminal, uma conta Linux por computador. Não monta dados do host.
ARG BASE_IMAGE=mcr.microsoft.com/playwright/python:v1.64.1-noble
FROM ${BASE_IMAGE}
ARG APT_MIRROR=""
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8 TZ=America/Sao_Paulo PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|http://[a-z.]*archive.ubuntu.com|$APT_MIRROR|g; s|http://security.ubuntu.com|$APT_MIRROR|g" \
        /etc/apt/sources.list.d/ubuntu.sources; fi \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      xfce4 xfce4-terminal dbus-x11 x11vnc xvfb libreoffice-writer libreoffice-calc \
      pandoc iptables libcap2-bin fonts-dejavu fonts-liberation tzdata procps curl \
 && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN python3 -m pip install --no-cache-dir --break-system-packages "websockets>=13,<16" "pypdf>=5,<7" "playwright==1.64.1" \
 && sed -i 's/^UID_MIN.*/UID_MIN 20000/; s/^UID_MAX.*/UID_MAX 29999/; s/^GID_MIN.*/GID_MIN 20000/; s/^GID_MAX.*/GID_MAX 29999/' /etc/login.defs \
 && chmod 755 /ms-playwright
COPY apps/executor/src/bees_executor /opt/bees/bees_executor
COPY runtime/computer/entrypoint.sh /opt/bees/entrypoint.sh
ENV PYTHONPATH=/opt/bees BEES_COMPUTER_MODE=multiuser BEES_COMPUTERS_DIR=/computers \
    BEES_EXECUTOR_STATE=/var/lib/bees-executor
RUN chmod 755 /opt/bees/entrypoint.sh && mkdir -p /computers /var/lib/bees-executor \
 && chmod 711 /computers && chmod 700 /var/lib/bees-executor
ENTRYPOINT ["/opt/bees/entrypoint.sh"]
