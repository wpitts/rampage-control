FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ARG OPENRGB_URL=https://codeberg.org/OpenRGB/OpenRGB/releases/download/release_0.9/openrgb_0.9_amd64_bookworm_b5f46e3.deb
# The OpenRGB .deb postinstall reloads udev rules, which has no socket inside a
# build container and makes dpkg --configure fail. Shim udevadm to a no-op so
# the package configures; the rules are re-read on the host at runtime anyway.
RUN for shim in /usr/bin/udevadm /usr/sbin/udevadm; do \
        printf '#!/bin/sh\nexit 0\n' > "$shim" && chmod +x "$shim"; \
    done \
    && curl -fsSL -o /tmp/openrgb.deb "$OPENRGB_URL" \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/openrgb.deb \
    && rm /tmp/openrgb.deb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/rampage-control
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static

ENV PYTHONUNBUFFERED=1 \
    RAMPAGE_CONFIG=/config/config.json \
    RAMPAGE_PORT=8787 \
    QT_QPA_PLATFORM=offscreen \
    HOME=/config

EXPOSE 8787
VOLUME ["/config"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health')" || exit 1
CMD ["python", "-m", "app"]
