// Pure URL classification (no app imports, so it can be unit-tested with plain
// node: `npm test`). Channel / playlist links vs single videos, plus Douyin.

function parse(raw: string): URL | null {
  let u: URL;
  try { u = new URL(raw.trim()); } catch { return null; }
  return u.protocol === 'http:' || u.protocol === 'https:' ? u : null;
}

/** Hostname without a leading www./m. */
function bareHost(u: URL): string {
  return u.hostname.toLowerCase().replace(/^(www|m)\./, '');
}

/** Any douyin.com / iesdouyin.com link (including v.douyin.com short links). */
export function isDouyinUrl(raw: string): boolean {
  const u = parse(raw);
  if (!u) return false;
  const h = u.hostname.toLowerCase();
  return h === 'douyin.com' || h.endsWith('.douyin.com') || h === 'iesdouyin.com' || h.endsWith('.iesdouyin.com');
}

/** v.douyin.com/<code>: resolves to either a video or a profile. */
export function isDouyinShortUrl(raw: string): boolean {
  const u = parse(raw);
  return !!u && u.hostname.toLowerCase() === 'v.douyin.com' && /^\/[A-Za-z0-9_-]+\/?$/.test(u.pathname);
}

/** douyin.com/user/<sec_uid>, iesdouyin.com/share/user/<sec_uid>, or a v.douyin.com short link. */
export function isDouyinChannelUrl(raw: string): boolean {
  const u = parse(raw);
  if (!u) return false;
  const h = bareHost(u);
  if (h === 'douyin.com') return /^\/user\/[^/]+/.test(u.pathname);
  if (h === 'iesdouyin.com') return /^\/share\/user\/[^/]+/.test(u.pathname);
  return isDouyinShortUrl(raw);
}

/** A Douyin link the Download screen treats as one video (short links count: see ChannelsScreen for profiles). */
export function isDouyinVideoUrl(raw: string): boolean {
  return isDouyinUrl(raw) && (isDouyinShortUrl(raw) || !isDouyinChannelUrl(raw));
}

/** Channel / playlist links (not a single video). */
export function looksLikeChannelUrl(raw: string): boolean {
  const u = parse(raw);
  if (!u) return false;
  const host = bareHost(u);
  const path = u.pathname;
  if (host === 'youtube.com') {
    if (/^\/(@[^/]+|channel\/[^/]+|c\/[^/]+|user\/[^/]+)/.test(path)) return true;
    return path === '/playlist' && u.searchParams.has('list');
  }
  if (host === 'tiktok.com') return /^\/@[^/]+\/?$/.test(path);
  return isDouyinChannelUrl(raw);
}

/** Download screen: a short Douyin link is downloaded as a video; only unambiguous channel links are redirected. */
export function isChannelOnDownloadScreen(raw: string): boolean {
  return looksLikeChannelUrl(raw) && !isDouyinShortUrl(raw);
}

/** The numeric video id of a douyin.com/video/<id> (or ?modal_id=<id>) link, if visible in the URL. */
export function douyinVideoId(raw: string): string | null {
  const u = parse(raw);
  if (!u) return null;
  const m = /\/(?:share\/)?video\/(\d+)/.exec(u.pathname);
  if (m) return m[1];
  const modal = u.searchParams.get('modal_id');
  return modal && /^\d{15,25}$/.test(modal) ? modal : null;
}

/** Links in pasted text, one per line or separated by spaces/commas. */
export function parseUrls(text: string): { valid: string[]; invalid: string[] } {
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const t = raw.trim();
    if (!t) continue;
    let ok = false;
    try {
      const u = new URL(t);
      ok = (u.protocol === 'http:' || u.protocol === 'https:') && u.hostname.includes('.');
    } catch { /* not a URL */ }
    if (ok) { if (!valid.includes(t)) valid.push(t); } else invalid.push(t);
  }
  // Share text from Douyin/TikTok apps ("7.97 V@L.Jv 06/03 reO:/ 《…》 https://v.douyin.com/x/ 复制此链接…")
  // wraps the link in words. When at least one link was found, only report
  // pieces that look like a broken link, not the words around it (owner test 2026-10-07).
  if (valid.length > 0) {
    return { valid, invalid: invalid.filter((t) => /^(https?:|www\.)/i.test(t)) };
  }
  return { valid, invalid };
}
