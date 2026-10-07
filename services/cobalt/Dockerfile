# VidGrab's own Cobalt instance (task #6127): the no-cookie step tried for
# Instagram / Facebook / X before a pool cookie is spent.
# Listens on 3000 (the port Vibe Host routes to).
# Required env: API_URL (this service's public https URL, with trailing /)
#               COBALT_API_KEY (UUIDv4, same value set on the backend)
# Every request must carry "Authorization: Api-Key <key>" (API_AUTH_REQUIRED=1),
# so the public URL cannot be used by anyone else.
FROM ghcr.io/imputnet/cobalt:11

USER root
COPY entrypoint.sh /usr/local/bin/vg-cobalt-entry.sh
RUN chmod 755 /usr/local/bin/vg-cobalt-entry.sh
USER node

ENV API_PORT=3000 \
    API_AUTH_REQUIRED=1 \
    API_KEY_URL=file:///tmp/vg-cobalt-keys.json

EXPOSE 3000
ENTRYPOINT ["/usr/local/bin/vg-cobalt-entry.sh"]
