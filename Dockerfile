# OverHub: FastAPI + Vue, steuert die Apps über den Docker- und den Tailscale-Socket des Hosts.

# Static Vite output is identical for every architecture: build natively.
FROM --platform=$BUILDPLATFORM node:22-slim AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Static CLIs from the official images (docker + compose plugin, tailscale).
FROM docker:27-cli AS dockercli
FROM tailscale/tailscale:stable AS tailscale

FROM python:3.12-slim
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
COPY --from=tailscale /usr/local/bin/tailscale /usr/local/bin/tailscale

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY catalog ./catalog
COPY --from=frontend /app/dist ./static

ARG APP_VERSION=dev
ENV OVERHUB_VERSION=$APP_VERSION \
    OVERHUB_CATALOG_DIR=/app/catalog \
    OVERHUB_STATIC_DIR=/app/static \
    PYTHONUNBUFFERED=1

# Runs with network_mode: host — 127.0.0.1:10443 is the host's loopback, which
# `tailscale serve --https=443` targets, and from where the apps' own loopback
# ports are reachable for health checks.
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:10443/api/health', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "10443", "--proxy-headers"]
