# VidGrab Cobalt

Own Cobalt instance for the no-cookie step (task #6127, runbook
`docs/runbooks/cookie-blocked.md`). Built from this folder on Vibe Host.

Env on this service: `API_URL=https://<this host>/`, `COBALT_API_KEY=<uuid4>` (secret).
Env on the backend (`dvid-api`): `COBALT_API_URLS=https://<this host>/`, `COBALT_API_KEY=<same uuid4>` (secret).

Start locally: `docker build -t vg-cobalt . && docker run --rm -p 9055:3000 -e API_URL=http://127.0.0.1:9055/ -e COBALT_API_KEY=$(python3 -c 'import uuid;print(uuid.uuid4())') vg-cobalt`
