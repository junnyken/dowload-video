// Pure rules of the per-platform route table (PLAN-32D §2, P2): which platform
// a URL belongs to (server slugs, app/backend/app/core/platform_key.py), the
// ORDER of routes a download tries, which failure moves to which next step,
// the /fetch-link request and how its answer becomes a local download. No
// Tauri/React/network imports so `node --test` runs it. Wired up in queue.ts.

/**
 * L0 = local yt-dlp without cookies · L1 = local yt-dlp with the user's own
 * cookies · S0 = the VidGrab server (POST /api/v1/fetch-link), then the app
 * downloads the server's file/link · DOUYIN_SERVER = POST /client/douyin/video
 * (unchanged since 0.5).
 */
export type RouteId = 'L0' | 'L1' | 'S0' | 'DOUYIN_SERVER';
export const LOCAL_STEPS: readonly RouteId[] = ['L0', 'L1'];
export const isLocalStep = (r: RouteId | undefined | null): r is 'L0' | 'L1' => r === 'L0' || r === 'L1';

// ---- platform slug (mirror of platform_key.py) ---------------------------------------

/** Host == domain or a subdomain of it ("evil-tiktok.com" is not tiktok.com). */
function hostIs(host: string, domains: readonly string[]): boolean {
  return domains.some((d) => host === d || host.endsWith('.' + d));
}

// Same slugs as platform_key.py; the server matches the first group as a
// substring of the whole URL, the app on the host only (a query string that
// mentions "youtube.com" must not route a link to YouTube). reddit, pinterest,
// vimeo and soundcloud are "other" for the server's counters but have their own
// slug in CLIENT_SERVER_FALLBACK_PLATFORMS (client_api.SERVER_FALLBACK_SLUGS).
const HOSTS: readonly (readonly [string, readonly string[]])[] = [
  ['youtube', ['youtube.com', 'youtu.be']],
  ['tiktok', ['tiktok.com']],
  ['instagram', ['instagram.com']],
  ['facebook', ['facebook.com', 'fb.watch']],
  ['douyin', ['douyin.com', 'iesdouyin.com']],
  ['threads', ['threads.net', 'threads.com']],
  ['spotify', ['spotify.com']],
  ['twitter', ['twitter.com', 'x.com']],
  ['linkedin', ['linkedin.com']],
  ['kuaishou', ['kuaishou.com', 'kuaishou.cn', 'chenzhongtech.com', 'gifshow.com']],
  ['xiaohongshu', ['xiaohongshu.com', 'xhslink.com', 'xhslink.cn', 'rednote.com']],
  ['bilibili', ['bilibili.com', 'b23.tv', 'bilibili.tv']],
  ['iqiyi', ['iq.com', 'iqiyi.com']],
  ['youku', ['youku.com']],
  ['mgtv', ['mgtv.com']],
  ['reddit', ['reddit.com', 'redd.it']],
  ['pinterest', ['pinterest.com', 'pin.it']],
  ['vimeo', ['vimeo.com']],
  ['soundcloud', ['soundcloud.com']],
];

export const PLATFORM_SLUGS: readonly string[] = [...HOSTS.map(([s]) => s), 'other'];

/** Server-side platform slug of a download URL; 'other' when unknown or not http(s). */
export function platformOf(raw: string): string {
  let u: URL;
  try { u = new URL(raw.trim()); } catch { return 'other'; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return 'other';
  const host = u.hostname.toLowerCase().replace(/\.$/, '');
  return HOSTS.find(([, d]) => hostIs(host, d))?.[0] ?? 'other';
}

/** features.serverFallbackPlatforms from GET /client/version: known slugs only, deduplicated. */
export function parseServerFallback(v: unknown): string[] {
  if (!Array.isArray(v)) return [];
  const known = new Set(PLATFORM_SLUGS);
  return [...new Set(v.filter((x): x is string => typeof x === 'string' && known.has(x)))];
}

// ---- route order (PLAN-32D §2) -----------------------------------------------------------

/**
 * Base order per platform. Douyin always goes through the server (the owner's
 * Windows test 2026-10-07: Douyin cannot be signed locally). Kuaishou,
 * Xiaohongshu and iQIYI have no usable local extractor: server only. Spotify
 * is not a yt-dlp site and has no server fallback switch: local as before.
 */
const ORDER: Record<string, readonly RouteId[]> = {
  youtube: ['L0', 'L1', 'S0'],
  tiktok: ['L0', 'S0'],
  instagram: ['L1', 'S0'],
  facebook: ['L0', 'L1', 'S0'],
  twitter: ['L0', 'L1', 'S0'],
  threads: ['L0', 'S0'],
  bilibili: ['L0', 'L1', 'S0'],
  douyin: ['DOUYIN_SERVER'],
  kuaishou: ['S0'],
  xiaohongshu: ['S0'],
  iqiyi: ['S0'],
  youku: ['L0', 'S0'],
  mgtv: ['L0', 'S0'],
};
const DEFAULT_ORDER: readonly RouteId[] = ['L0', 'L1', 'S0'];

export type RouteCtx = {
  /** L1 is possible: the platform is in features.cookiePlatforms AND a saved blob (with consent) exists. */
  cookies: boolean;
  /** features.serverFallbackPlatforms. */
  serverFallback: readonly string[];
};

/**
 * The steps this URL may take, in order, keeping only those available now.
 * Nothing available (server switch off for a server-only platform, Instagram
 * without cookies and without fallback) -> L0, i.e. the behaviour before P2.
 * Instagram without cookies starts with L0 too (public reels work locally).
 */
export function planRoutes(url: string, ctx: RouteCtx): RouteId[] {
  const platform = platformOf(url);
  const order = ORDER[platform] ?? DEFAULT_ORDER;
  const out = order.filter((r) => (r === 'L1' ? ctx.cookies : r === 'S0' ? ctx.serverFallback.includes(platform) : true));
  if (platform === 'instagram' && !out.includes('L1')) out.unshift('L0');
  return out.length ? out : ['L0'];
}

/** First step of the plan not tried yet; null when every step was used. */
export function currentStep(plan: readonly RouteId[], tried: readonly RouteId[]): RouteId | null {
  return plan.find((r) => !tried.includes(r)) ?? null;
}

/** Failures the next step cannot fix (the server would fail the same way, or the cause is on this PC). */
export const NO_FALLTHROUGH: ReadonlySet<string> = new Set([
  'not_found', 'unsupported', 'geo_blocked', 'drm', 'invalid_url',
  'api:quota_exceeded_daily', 'quota_exceeded_daily', 'quota_offline_limit',
  'disk_full', 'tool_missing', 'tool_tampered', 'cancelled',
]);
/** L0 failures a login fixes: try L1 when there is one. */
export const LOGIN_CODES: ReadonlySet<string> = new Set(['private_or_login', 'forbidden', 'cookie_required', 'cookie_expired']);

/**
 * Where a job goes after `failed` ended with `code`, or null (the job fails).
 * Each step runs at most once per job (`tried` already holds the steps used).
 *  - not_found / unsupported / geo_blocked / drm / quota ... : no next step
 *  - L0 login/forbidden -> L1 if available, else S0
 *  - any other L0 / L1 failure -> S0 if available
 *  - S0 and DOUYIN_SERVER are last
 */
export function nextRoute(plan: readonly RouteId[], tried: readonly RouteId[], failed: RouteId, code: string): RouteId | null {
  if (NO_FALLTHROUGH.has(code) || !isLocalStep(failed)) return null;
  const used = new Set([...tried, failed]);
  const rest = plan.slice(plan.indexOf(failed) + 1).filter((r) => !used.has(r));
  if (failed === 'L0' && LOGIN_CODES.has(code) && rest.includes('L1')) return 'L1';
  return rest.includes('S0') ? 'S0' : null;
}

/** `tried` after moving to `next`: every step of the plan before it counts as used (L0 network -> S0 skips L1). */
export function triedBefore(plan: readonly RouteId[], tried: readonly RouteId[], next: RouteId): RouteId[] {
  const i = plan.indexOf(next);
  return [...new Set([...tried, ...(i > 0 ? plan.slice(0, i) : [])])];
}

/** The quota route name for a local step (POST /client/quota/claim). */
export const claimRoute = (r: 'L0' | 'L1'): 'local' | 'local_cookie' => (r === 'L1' ? 'local_cookie' : 'local');

/** Older route names (0.7.x queue items) for the per-platform cookie limit in pickNext. */
export function legacyRoute(r: RouteId): 'local' | 'local_cookie' | 'server' {
  return r === 'L1' ? 'local_cookie' : r === 'L0' ? 'local' : 'server';
}

/** Label on the queue card. wording: BA review */
export function routeLabel(r: RouteId | undefined | null): string | null {
  switch (r) {
    case 'L0': return 'Tải trên máy này'; // wording: BA review
    case 'L1': return 'Tải bằng tài khoản của bạn'; // wording: BA review
    case 'S0': case 'DOUYIN_SERVER': return 'Qua máy chủ VidGrab'; // wording: BA review
    default: return null;
  }
}

// ---- S0: POST /api/v1/fetch-link ----------------------------------------------------------

/**
 * The server's quality value (routes.py FetchLinkRequest.quality) for a queue
 * item: audio -> mp3_128, a height preset -> video_<h>, otherwise "video"
 * (best the server allows for this user).
 */
export function serverQuality(it: { audioOnly: boolean; quality?: string; formatId?: string; formatLabel?: string }): string {
  if (it.audioOnly) return 'mp3_128';
  const h = (it.quality && /^\d{3,4}$/.test(it.quality) ? it.quality : null)
    ?? /height<=(\d{3,4})/.exec(it.formatId ?? '')?.[1]
    ?? /^(\d{3,4})p\b/.exec(it.formatLabel ?? '')?.[1];
  return h ? `video_${h}` : 'video';
}

/** Body of POST /fetch-link: the URL and the quality only (no subtitles, no watermark work, no schedule). */
export function fetchLinkBody(url: string, quality: string) {
  return { url, quality };
}

export type ServerRefusal = {
  detail: string; upsell: 'signin' | 'upgrade'; limit: number | null; usedToday: number | null; resetTimeVn: string | null;
};

export type FetchLinkOutcome =
  | { kind: 'ok'; url: string; title: string | null; thumbnail: string | null; headers: { name: string; value: string }[]; source: 'file' | 'direct' }
  | { kind: 'quota'; refusal: ServerRefusal }
  | { kind: 'error'; code: string; message: string };

const obj = (d: unknown): Record<string, unknown> => (d && typeof d === 'object' && !Array.isArray(d) ? (d as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v.trim() : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** The server's file in its downloads/ folder: /api/v1/download-local?file=<id>&filename=<name> (no auth, routes.py). */
export function downloadLocalUrl(apiBase: string, fileId: string, title: string): string {
  const ext = /\.[A-Za-z0-9]{1,5}$/.exec(fileId)?.[0] ?? '';
  const name = (title.replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80) || 'video') + ext;
  return `${apiBase}/api/v1/download-local?file=${encodeURIComponent(fileId)}&filename=${encodeURIComponent(name)}`;
}

/** Absolute http(s) link, or a server-relative "/api/..." link made absolute; null otherwise. */
function absolute(apiBase: string, raw: string | null): string | null {
  if (!raw) return null;
  if (raw.startsWith('/') && !raw.startsWith('//')) return apiBase + raw;
  try {
    const u = new URL(raw);
    return u.protocol === 'https:' || u.protocol === 'http:' ? u.toString() : null;
  } catch {
    return null;
  }
}

/** Headers yt-dlp may send with the link (Rust allows Referer / User-Agent only). /fetch-link sends none today. */
function headerList(h: unknown): { name: string; value: string }[] {
  return Object.entries(obj(h))
    .filter(([k, v]) => /^(referer|user-agent)$/i.test(k) && typeof v === 'string' && v)
    .map(([name, value]) => ({ name, value: value as string }));
}

/**
 * What to do with the answer of POST /fetch-link. The server's own file is
 * preferred over `direct_mp4_url` (that link is often signed for the server's
 * IP or proxy; YouTube's is blanked by the server for that reason). Audio uses
 * the MP3 the server made when there is one. 403/429 with
 * detail.error_code = quota_exceeded_daily = the daily allowance (refusal).
 */
export function parseFetchLink(status: number, data: unknown, o: { apiBase: string; audioOnly: boolean; fallbackTitle: string }): FetchLinkOutcome {
  const d = obj(data);
  if (status === 200 && d.success === true) {
    const title = str(d.title);
    const name = title ?? o.fallbackTitle;
    const fileId = (o.audioOnly ? str(d.local_mp3_file_id) ?? str(d.local_file_id) : str(d.local_file_id)) ?? null;
    if (fileId) return { kind: 'ok', url: downloadLocalUrl(o.apiBase, fileId, name), title, thumbnail: str(d.thumbnail_url), headers: [], source: 'file' };
    const direct = absolute(o.apiBase, str(d.direct_mp4_url));
    if (direct) return { kind: 'ok', url: direct, title, thumbnail: str(d.thumbnail_url), headers: headerList(d.http_headers ?? d.headers), source: 'direct' };
    return { kind: 'error', code: 'server_fetch_failed', message: '' };
  }
  const det = obj(d.detail);
  const code = str(det.error_code) ?? str(d.error_code);
  if ((status === 403 || status === 429) && code === 'quota_exceeded_daily') {
    return {
      kind: 'quota',
      refusal: {
        detail: str(det.message) ?? str(det.user_message) ?? str(d.detail) ?? '',
        upsell: status === 403 ? 'upgrade' : 'signin',
        limit: num(det.daily_limit), usedToday: num(det.downloads_today), resetTimeVn: str(det.reset_time_vn),
      },
    };
  }
  const message = str(d.detail) ?? str(det.message) ?? str(det.user_message) ?? str(d.message) ?? '';
  if (status === 202) return { kind: 'error', code: 'server_busy', message }; // queued behind the fairness cap
  if (status === 401) return { kind: 'error', code: 'unauthorized', message };
  // The server classified the extraction (extraction_errors.py): keep the codes the app knows.
  const known: Record<string, string> = { geo_blocked: 'geo_blocked', video_unavailable: 'not_found', unsupported_url: 'unsupported', drm_protected: 'drm' };
  return { kind: 'error', code: (code && known[code]) || 'server_fetch_failed', message };
}
