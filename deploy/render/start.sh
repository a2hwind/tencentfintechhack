#!/usr/bin/env bash
# Render's start command: the API, the UI and Caddy in one container (docker-compose.yml runs them as three).
# Caddy listens on $PORT (Render sets it, 10000 by default) and sends /api to the API and everything else to
# the UI. If any of the three stops, the container stops too and Render starts a fresh one.
set -uo pipefail
cd /app

export UVICORN_ROOT_PATH=/api
uvicorn internal_brain.api.app:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5 &
(cd /app/ui && PORT=3000 HOSTNAME=127.0.0.1 exec node server.js) &

export SITE_ADDRESS=":${PORT:-10000}" API_UPSTREAM=127.0.0.1:8000 UI_UPSTREAM=127.0.0.1:3000
sh /app/deploy/caddy-start.sh &

stop() {
	kill -TERM $(jobs -p) 2>/dev/null
	wait
	exit 0
}
trap stop TERM INT

wait -n
status=$?
echo "start.sh: a process exited (status $status); stopping the container so Render restarts it" >&2
kill -TERM $(jobs -p) 2>/dev/null
wait
exit 1
