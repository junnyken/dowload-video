#!/bin/sh
# Writes the one API key from the environment into Cobalt's key file, then
# starts Cobalt. Refuses to start without a key: an open instance on a public
# URL would serve anyone.
set -eu
key="${COBALT_API_KEY:-}"
if ! printf '%s' "$key" | grep -Eq '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'; then
  echo "vg-cobalt: COBALT_API_KEY must be a lowercase UUIDv4 — refusing to start" >&2
  exit 1
fi
umask 077
printf '{"%s":{"name":"vidgrab","limit":"unlimited"}}\n' "$key" > /tmp/vg-cobalt-keys.json
unset COBALT_API_KEY
exec docker-entrypoint.sh node src/cobalt
