/**
 * Early, friendly check for disposable / temporary email domains.
 *
 * The real gate is the database trigger of migration 036 (auth.users BEFORE
 * INSERT). This only lets the sign-up form explain the problem before a
 * request (and a captcha token) is spent. The list (~150 KB) is a static file
 * generated from the same pinned source by scripts/gen-disposable-domains.py
 * and is fetched only when someone submits the sign-up form. If it cannot be
 * fetched we let the request through and the server decides.
 */
let listPromise = null;

function loadList() {
  listPromise ??= fetch('/disposable-domains.json', { credentials: 'omit' })
    .then((r) => (r.ok ? r.json() : null))
    .then((j) => new Set(Array.isArray(j?.domains) ? j.domains : []))
    .catch(() => { listPromise = null; return new Set(); });
  return listPromise;
}

/** 'x@a.b.mailinator.com' -> ['a.b.mailinator.com', 'b.mailinator.com', 'mailinator.com', 'com'] */
export function domainSuffixes(email) {
  const e = String(email || '').trim().toLowerCase();
  const at = e.lastIndexOf('@');
  if (at < 0) return [];
  const domain = e.slice(at + 1).replace(/\.+$/, '');
  if (!domain) return [];
  const parts = domain.split('.');
  return parts.map((_, i) => parts.slice(i).join('.'));
}

export async function isDisposableEmail(email) {
  const suffixes = domainSuffixes(email);
  if (!suffixes.length) return false;
  const set = await loadList();
  return suffixes.some((d) => set.has(d));
}
