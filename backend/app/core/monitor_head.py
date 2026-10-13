"""HEAD for the endpoints external uptime monitors watch (Phase 34A-1).

UptimeRobot's free plan checks with HEAD; FastAPI routes are GET-only and
answer 405, so every API monitor showed "down" on 13/10/2026 although the
API was up. For a short allow-list of cheap, read-only paths a HEAD is run
as GET and the body is dropped (headers incl. Content-Length kept, as HEAD
requires). Every other path is untouched.
"""
from __future__ import annotations

MONITOR_PATHS = frozenset({
    "/health",
    "/api/v1/client/version",
    "/api/v1/platform-status",
    "/api/v1/watch/status",
})


class MonitorHeadMiddleware:
    def __init__(self, app, paths=MONITOR_PATHS):
        self.app = app
        self.paths = frozenset(paths)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") != "HEAD" or scope.get("path") not in self.paths:
            await self.app(scope, receive, send)
            return

        async def send_no_body(message):
            if message.get("type") == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_no_body)
