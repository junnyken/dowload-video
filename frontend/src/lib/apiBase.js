/**
 * Single source of truth for the backend base URL.
 *
 * Why this exists: a large number of call sites used a RELATIVE path
 * (`fetch('/api/v1/...')`). That only works when frontend and backend sit
 * behind one origin, which is the docker-compose layout — nginx.conf proxies
 * /api/ to `http://backend:8000`. In the Vibe Host deployment they are two
 * separate projects on two domains, the `backend` hostname does not resolve,
 * and every one of those calls returned 502 Bad Gateway. That is what made the
 * admin panel unreachable ("Login failed (502). Check ADMIN_PASSWORD env." —
 * the password was never the problem).
 *
 * A second group read `VITE_API_BASE` or `VITE_API_BASE_URL`, neither of which
 * is defined in any build; only VITE_API_URL is. Those silently fell back to
 * '' and became relative too.
 *
 * VITE_API_URL is the configured name; the other two are accepted as fallbacks
 * so no existing deployment regresses. Empty means same-origin, which remains
 * correct for the compose layout.
 */
const RAW =
  import.meta.env.VITE_API_URL ||
  import.meta.env.VITE_API_BASE ||
  import.meta.env.VITE_API_BASE_URL ||
  '';

export const API_BASE = RAW.replace(/\/+$/, '');

/** Build an absolute backend URL from a root-relative path. */
export function apiUrl(path) {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}
