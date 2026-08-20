/**
 * Snapshot of the auth fragment, taken before anything else can consume it.
 *
 * Supabase's client is created with detectSessionInUrl, so on load it parses
 * `#access_token=…` / `#error=…` and then clears the fragment. Any component
 * reading window.location.hash in an effect therefore sees nothing, and an
 * expired or already-used recovery link renders as a normal empty form with no
 * explanation.
 *
 * This module is imported first in main.jsx — ES modules evaluate in import
 * order, so this runs before the Supabase client module is even created.
 */
function parseHash() {
  try {
    const raw = (window.location.hash || '').replace(/^#/, '');
    if (!raw) return {};
    const p = new URLSearchParams(raw);
    const out = {};
    for (const k of ['error', 'error_code', 'error_description', 'type']) {
      const v = p.get(k);
      if (v) out[k] = decodeURIComponent(v.replace(/\+/g, ' '));
    }
    return out;
  } catch {
    return {};
  }
}

export const authFragment = parseHash();

/**
 * Human-readable reason the link failed, or '' when there was none.
 *
 * Prefers the load-time snapshot, since by the time anything asks the live
 * fragment has usually been cleared. Falls back to re-reading it for the case
 * where the fragment arrived without a page load, when the snapshot is empty
 * but the hash is not.
 */
export function authLinkError() {
  const fromSnapshot = authFragment.error_description || authFragment.error || '';
  if (fromSnapshot) return fromSnapshot;
  const live = parseHash();
  return live.error_description || live.error || '';
}
