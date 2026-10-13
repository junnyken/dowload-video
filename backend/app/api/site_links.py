"""
Site links (task #6331).

  GET  /api/v1/site-links                 public: {"status_page_url": str|null}
  GET  /api/v1/admin/site-links           admin: same
  POST /api/v1/admin/site-links           admin: {"status_page_url": str|null}  (audited)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.admin import verify_admin
from app.core import site_links
from app.core.audit import log_admin_action

public_router = APIRouter()
admin_router = APIRouter()


class LinksBody(BaseModel):
    status_page_url: Optional[str] = None


@public_router.get("/site-links")
async def public_links():
    return site_links.get_links()


@admin_router.get("/site-links")
async def admin_links(_=Depends(verify_admin)):
    return site_links.get_links()


@admin_router.post("/site-links")
async def admin_set_links(body: LinksBody, request: Request, _=Depends(verify_admin)):
    old = site_links.get_links().get(site_links.STATUS_PAGE)
    try:
        new = site_links.set_link(site_links.STATUS_PAGE, body.status_page_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        log_admin_action(request, "admin.site_links.set", resource_type="site_link",
                         resource_id=site_links.STATUS_PAGE, metadata={"old": old, "new": new})
    except Exception:  # noqa: BLE001 — audit failure never blocks the change
        pass
    return site_links.get_links()
