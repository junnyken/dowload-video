"""
Cookie-session provider — SCAFFOLD ONLY (not registered in wave 1).

Plan §11: accepts internal cookie IDs from the owner-managed pool only, never
raw cookie text from a frontend user. The existing per-request "Dùng cookie
của tôi" feature is NOT this provider and stays as it is (owner correction #1):
that file is handed to the native provider for the one request and never
cached.
"""
from __future__ import annotations

from app.services.china_platforms.errors import ProviderFailureError, make_failure
from app.services.china_platforms.providers.base import BaseProvider


class CookieSessionProvider(BaseProvider):
    mode = "cookie_session"
    paid = False

    def __init__(self, name: str, platform: str, cookie_pool_platform: str):
        super().__init__()
        self.name = name
        self.platform = platform
        self.cookie_pool_platform = cookie_pool_platform

    def is_configured(self) -> bool:
        return False

    async def resolve(self, request, context):
        raise ProviderFailureError(make_failure(
            self.platform, self.name, "provider_unavailable", "cookie_session provider not implemented (wave 2)"))
