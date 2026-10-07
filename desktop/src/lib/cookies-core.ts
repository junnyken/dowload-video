// Pure rules of "Tài khoản nền tảng" (PLAN-32D §2-§3, P1): which platform a
// URL belongs to, which route a download takes, when Douyin falls back to the
// server, which error code the user sees, and the per-platform concurrency
// limit. No Tauri/React/network imports so `node --test` runs it. Wired up in
// lib/cookies.ts and lib/queue.ts. Rust (cookies.rs) keeps its own copy of the
// host table and decides on its own which cookie file a run gets.

export type CookieStatus = { platform: string; saved: boolean; savedAt: number | null; suspectExpired: boolean };

export type CookiePlatform = { slug: string; label: string; hosts: readonly string[]; hint?: string };

// wording: BA review (labels, hint)
export const COOKIE_PLATFORMS: readonly CookiePlatform[] = [
  { slug: 'douyin', label: 'Douyin', hosts: ['douyin.com', 'iesdouyin.com'], hint: 'Không cần đăng nhập: mở một video Douyin và bấm phát, rồi bấm Xong (hoặc đóng cửa sổ Douyin).' },
  { slug: 'instagram', label: 'Instagram', hosts: ['instagram.com', 'instagr.am'] },
  { slug: 'facebook', label: 'Facebook', hosts: ['facebook.com', 'fb.watch', 'fb.com'] },
  { slug: 'twitter', label: 'X (Twitter)', hosts: ['x.com', 'twitter.com'] },
  { slug: 'youtube', label: 'YouTube', hosts: ['youtube.com', 'youtu.be'] },
  { slug: 'bilibili', label: 'Bilibili', hosts: ['bilibili.com', 'b23.tv'] },
  { slug: 'threads', label: 'Threads', hosts: ['threads.net', 'threads.com'] },
  { slug: 'reddit', label: 'Reddit', hosts: ['reddit.com', 'redd.it'] },
  { slug: 'pinterest', label: 'Pinterest', hosts: ['pinterest.com', 'pin.it'] },
  { slug: 'tiktok', label: 'TikTok', hosts: ['tiktok.com'] },
  { slug: 'vimeo', label: 'Vimeo', hosts: ['vimeo.com'] },
];

const KNOWN = new Set(COOKIE_PLATFORMS.map((p) => p.slug));

export const platformLabelOf = (slug: string): string => COOKIE_PLATFORMS.find((p) => p.slug === slug)?.label ?? slug;

/** `host` is `domain` or one of its subdomains ("evil-douyin.com" / "douyin.com.evil.com" are not). */
export function domainMatches(host: string, domain: string): boolean {
  const h = host.trim().toLowerCase().replace(/\.$/, '');
  const d = domain.trim().toLowerCase().replace(/^\./, '').replace(/\.$/, '');
  if (!h || !d.includes('.')) return false;
  return h === d || h.endsWith('.' + d);
}

/** Cookie platform of a download URL (from the host only), or null. */
export function cookiePlatformOf(raw: string): string | null {
  let u: URL;
  try { u = new URL(raw.trim()); } catch { return null; }
  if ((u.protocol !== 'http:' && u.protocol !== 'https:') || u.username || u.password) return null;
  return COOKIE_PLATFORMS.find((p) => p.hosts.some((d) => domainMatches(u.hostname, d)))?.slug ?? null;
}

/** features.cookiePlatforms from GET /client/version: known slugs only, deduplicated. */
export function parseCookiePlatforms(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  return [...new Set(v.filter((x): x is string => typeof x === 'string' && KNOWN.has(x)))];
}

// ---- routes (PLAN-32D §2) -------------------------------------------------------------

/** local = yt-dlp without cookies (L0) · local_cookie = with the user's cookies (L1) · server = Douyin via the VidGrab server. */
export type Route = 'local' | 'local_cookie' | 'server';
export type Plan = { route: Route; useCookies: boolean; platform: string | null };

export type CookieCtx = {
  /** features.cookiePlatforms (server switch). */
  enabled: readonly string[];
  /** A blob is saved for this platform AND the user agreed to the first-use text. */
  usable: (slug: string) => boolean;
};

/**
 * Douyin: L1 when the server enabled 'douyin' and a Douyin blob exists, else the
 * server route (today's behaviour). Other platforms: L1 when enabled and saved,
 * else L0 (unchanged).
 */
export function planRoute(url: string, ctx: CookieCtx): Plan {
  const platform = cookiePlatformOf(url);
  const cookie = platform != null && ctx.enabled.includes(platform) && ctx.usable(platform);
  if (platform === 'douyin') return cookie ? { route: 'local_cookie', useCookies: true, platform } : { route: 'server', useCookies: false, platform };
  return { route: cookie ? 'local_cookie' : 'local', useCookies: cookie, platform };
}

/** Errors after which a Douyin L1 download is retried ONCE through the server (login, forbidden, "Fresh cookies"). */
export const DOUYIN_FALLBACK_CODES: ReadonlySet<string> = new Set(['private_or_login', 'forbidden']);

export function shouldFallbackToServer(it: { url: string; route?: Route; cookieFallback?: boolean }, code: string): boolean {
  return it.route === 'local_cookie' && cookiePlatformOf(it.url) === 'douyin' && !it.cookieFallback && DOUYIN_FALLBACK_CODES.has(code);
}

/**
 * Refund FIRST, then hand the job to the server route: the server counts the
 * Douyin download on its own, so a claim that is still open would count the
 * user twice. `requeue` runs only after `settle` has resolved, and only when
 * the item is still waiting for it (not cancelled meanwhile).
 */
export async function settleThenFallback(
  settle: () => Promise<void>,
  stillWaiting: () => boolean,
  requeue: () => void,
): Promise<boolean> {
  try { await settle(); } catch { /* a lost settle only costs the refund */ }
  if (!stillWaiting()) return false;
  requeue();
  return true;
}

/**
 * The code the user sees. A login error with cookies used = the session
 * expired (cookie_expired); without cookies on a platform the server allows
 * cookies for = connect the account (cookie_required). Anything else unchanged.
 */
export function cookieErrorCode(code: string, o: { cookiesUsed: boolean; platform: string | null; enabled: readonly string[] }): string {
  if (code !== 'private_or_login') return code;
  if (o.cookiesUsed) return 'cookie_expired';
  if (o.platform && o.enabled.includes(o.platform)) return 'cookie_required';
  return code;
}

// ---- pump: at most one running job per platform that uses cookies ----------------------

export type PumpItem = { id: string; url: string; state: string; route?: Route };

/** Next queued item to start, or null. `planOf` gives the route the item would take now. */
export function pickNext<T extends PumpItem>(list: readonly T[], limit: number, planOf: (it: T) => Plan): T | null {
  const running = list.filter((i) => i.state === 'running');
  if (running.length >= limit) return null;
  const busy = new Set(running.filter((i) => i.route === 'local_cookie').map((i) => cookiePlatformOf(i.url)));
  for (const it of list) {
    if (it.state !== 'queued') continue;
    const p = planOf(it);
    if (p.route === 'local_cookie' && busy.has(p.platform)) continue; // wait: same account already downloading
    return it;
  }
  return null;
}

/** Settings row state. */
export type RowState = 'none' | 'saved' | 'suspect';
export function rowState(s: CookieStatus | undefined): RowState {
  if (!s?.saved) return 'none';
  return s.suspectExpired ? 'suspect' : 'saved';
}
