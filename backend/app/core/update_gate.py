"""
Update gate for the Windows app (task #6125, PLAN-32E P1 step 1).

App versions below DESKTOP_MIN_VERSION never claim downloads (0.1–0.5), so
an enforced daily allowance can be dodged by staying on an old build. When
CLIENT_UPDATE_GATE_ENABLED is on, requests from the app to the app routes
(/api/v1/client/*, /api/v1/fetch-link) that carry a missing or too-low
X-VG-Client answer 426 {error_code: "update_required", minSupported, latest,
downloadUrl}. GET /api/v1/client/version is always allowed: the app must
still learn what to install.

"From the app" = Origin of the app's webview (tauri.localhost) or
X-VG-Source: desktop. The website sends neither, so it is never gated.
Flag off (default) → this middleware does nothing.
"""
import os
import re
from typing import Optional, Tuple

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

APP_ORIGINS = ("http://tauri.localhost", "https://tauri.localhost", "tauri://localhost")
GATED_PREFIXES = ("/api/v1/client/", "/api/v1/fetch-link")
ALWAYS_OPEN = ("/api/v1/client/version",)
_VER = re.compile(r"^\s*v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})")


def gate_enabled() -> bool:
    return (os.environ.get("CLIENT_UPDATE_GATE_ENABLED") or "").strip().lower() in ("1", "true", "yes", "on")


def parse_version(raw: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """'0.8.0' / 'v0.8.0-beta' → (0, 8, 0); anything else → None."""
    m = _VER.match(raw or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def min_version() -> Tuple[int, int, int]:
    return parse_version(os.environ.get("DESKTOP_MIN_VERSION")) or (0, 1, 0)


def from_app(request: Request) -> bool:
    origin = (request.headers.get("origin") or "").strip().lower()
    source = (request.headers.get("x-vg-source") or "").strip().lower()
    return origin in APP_ORIGINS or source == "desktop"


def too_old(request: Request) -> bool:
    """The app's own version (X-VG-Client) is missing, malformed or below
    DESKTOP_MIN_VERSION. Apps before 0.6.0 send no version at all."""
    v = parse_version(request.headers.get("x-vg-client"))
    return v is None or v < min_version()


def should_gate(request: Request) -> bool:
    if not gate_enabled() or request.method == "OPTIONS":
        return False
    path = request.url.path
    if path in ALWAYS_OPEN or not path.startswith(GATED_PREFIXES):
        return False
    return from_app(request) and too_old(request)


def update_required_response() -> JSONResponse:
    env = os.environ.get
    from app.core import desktop_release  # noqa: PLC0415
    rel = desktop_release.current()        # cached; GitHub release when newer
    return JSONResponse(status_code=426, content={
        "error_code": "update_required",
        # wording: BA review
        "detail": "Ứng dụng VidGrab trên máy bạn đã cũ. Vui lòng cập nhật bản mới để tiếp tục tải.",
        "minSupported": (env("DESKTOP_MIN_VERSION") or "0.1.0").strip(),
        "latest": rel["latest"],
        "downloadUrl": rel["downloadUrl"],
    })


class UpdateGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method != "OPTIONS" and request.url.path.startswith(GATED_PREFIXES) and from_app(request):
            # app version mix for the admin page (task #6126, PLAN-32E §5.3); never raises
            from app.core.desktop_signals import note_version  # noqa: PLC0415
            note_version(request.headers.get("x-vg-client"))
        if should_gate(request):
            return update_required_response()
        return await call_next(request)
