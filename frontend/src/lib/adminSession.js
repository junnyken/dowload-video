/**
 * The admin panel's session token (POST /admin/login), when this browser has
 * a live one. Sent as X-Admin-Token on download requests so the backend can
 * apply "admin không giới hạn" (per-platform daily allowance, China access
 * canary). Same storage the admin panel uses (admin/hooks/useAdminAuth.ts);
 * read directly so the main bundle does not import the admin code.
 */
const STORAGE_KEY = 'vg_admin_session';

export function adminSessionToken() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const u = JSON.parse(raw);
    if (!u || !u.sessionToken) return null;
    if (u.expiresAt && new Date(u.expiresAt) < new Date()) return null;
    return u.sessionToken;
  } catch {
    return null;
  }
}
