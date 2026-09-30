# Render (free web service): the whole demo in one container. Caddy listens on $PORT and serves the UI at /
# and the API at /api, as docker-compose.yml does with three containers. Blueprint: render.yaml at the repo
# root. Walkthrough: docs/deploy.md. Build context: the repo root.

# 1. The UI, built for same-origin API calls at /api.
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
ENV NEXT_PUBLIC_API_URL=/api NEXT_TELEMETRY_DISABLED=1
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

# 2. Caddy: one static binary.
FROM caddy:2-alpine AS caddy

# 3. Runtime: Node for the UI server, Python for the API, Caddy in front.
FROM node:22-bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 BRAIN_DATA_DIR=/tmp/brain \
    XDG_DATA_HOME=/tmp/caddy-data XDG_CONFIG_HOME=/tmp/caddy-config
WORKDIR /app
COPY pyproject.toml README.md ./
COPY internal_brain ./internal_brain
RUN python3 -m venv /opt/venv && pip install --no-cache-dir .
COPY scripts ./scripts
COPY --from=ui /ui/.next/standalone ./ui
COPY --from=ui /ui/.next/static ./ui/.next/static
COPY --from=caddy /usr/bin/caddy /usr/local/bin/caddy
COPY Caddyfile /etc/caddy/Caddyfile
COPY deploy/caddy-start.sh deploy/render/start.sh ./deploy/
EXPOSE 10000
CMD ["bash", "/app/deploy/start.sh"]
