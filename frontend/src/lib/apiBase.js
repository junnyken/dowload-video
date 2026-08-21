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
/**
 * A third group read `localStorage.getItem('vg_api_base') || ''` directly —
 * 16 call sites across AccountMenu, UsageContent, UserHistoryContent,
 * PlaylistsPage, ApiKeysPage, LinkBotPage, AnalyticsPage, UpgradeModal and
 * AuthContext's preferences.
 *
 * Nothing in the codebase ever WRITES that key. Not one setItem call exists.
 * So every one of those reads returned '', fell back to a relative URL, and
 * hit the frontend origin — which answers 502 for /api/v1/* on the Vibe Host
 * layout. That is why a user upgraded to Enterprise still saw "Nâng cấp Pro":
 * AccountMenu's usage fetch never reached a backend, so it rendered the
 * signed-out-ish default.
 *
 * Keeping the key as an override rather than deleting it: it is a useful
 * escape hatch for pointing a built bundle at a different backend from the
 * browser console. It just cannot be the only source.
 */
const OVERRIDE =
  (typeof localStorage !== 'undefined' && localStorage.getItem('vg_api_base')) || '';

const RAW =
  OVERRIDE ||
  import.meta.env.VITE_API_URL ||
  import.meta.env.VITE_API_BASE ||
  import.meta.env.VITE_API_BASE_URL ||
  '';

export const API_BASE = RAW.replace(/\/+$/, '');

/** Build an absolute backend URL from a root-relative path. */
export function apiUrl(path) {
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;
}
