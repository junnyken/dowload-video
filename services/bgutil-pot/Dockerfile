# PO Token provider for yt-dlp.
#
# YouTube issues its signed videoplayback URLs to a specific InnerTube client,
# and for the android_vr client those URLs also need a PO token — without one
# the CDN answers 403 on download even when the requesting IP matches the ip=
# embedded in the link. That is exactly what was happening: extraction
# succeeded, every byte fetch failed.
#
# The backend already ships the matching yt-dlp plugin
# (bgutil-ytdlp-pot-provider in requirements.txt) and a patch for the
# server/plugin version-string mismatch (scripts/patch_bgutil.py). Only the
# provider itself was missing: docker-compose.yml declares it as the service
# `bgutil-pot`, a name that does not resolve on a split-project host, so
# BGUTIL_POT_URL pointed at nothing.
#
# This wrapper exists because the platform builds projects from a repo rather
# than from a bare image reference. It adds nothing to the upstream image.
FROM ghcr.io/jim60105/bgutil-pot:latest

# The provider serves its HTTP API here; the plugin is pointed at it through
# BGUTIL_POT_URL on the backend.
EXPOSE 4416
