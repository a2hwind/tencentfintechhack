#!/bin/sh
# Caddy's start command in docker-compose.yml. With DEMO_PASSWORD set in .env, the whole site asks for a
# password (user DEMO_USER, default "judge"); Slack's signed webhook stays open. The password is hashed
# here at startup, so .env holds it as typed and nobody has to produce a bcrypt hash by hand.
# After changing .env, run `docker compose up -d` (a `caddy reload` inside the container would not see it).
set -eu
if [ -n "${DEMO_PASSWORD:-}" ]; then
	DEMO_PASSWORD_HASH=$(printf '%s\n' "$DEMO_PASSWORD" | caddy hash-password)
	DEMO_AUTH=on
	echo "caddy-start: password prompt on (user ${DEMO_USER:-judge})"
else
	DEMO_PASSWORD_HASH=
	DEMO_AUTH=off
	echo "caddy-start: no DEMO_PASSWORD, the site is open to anyone with the address"
fi
export DEMO_AUTH DEMO_PASSWORD_HASH
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
