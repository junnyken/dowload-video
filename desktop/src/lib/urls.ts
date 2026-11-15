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

/**
 * 0.19.3: a Facebook page / profile (or its Reels / Videos tab) — listed by the
 * VidGrab extension in the person's own Chrome (channel relay), like the
 * website does. Same shapes as the server (channel_relay.py): /<name>[/reels|/videos],
 * profile.php?id=…, /people/<name>/<id>/.
 */
export function isFacebookChannelUrl(raw: string): boolean {
  const t = (raw || '').trim().replace(/#.*$/, '');
  return /^https?:\/\/(www\.|web\.|m\.)?facebook\.com\/(?!(reel|reels|watch|groups|share|events|marketplace|pages|gaming|login|photo|photos|hashtag|story\.php|profile\.php|people)(\/|\?|#|$))[A-Za-z0-9.-]{1,80}(\/(reels|videos))?\/?(\?.*)?$/i.test(t)
    || /^https?:\/\/(www\.|web\.|m\.)?facebook\.com\/profile\.php\?(.*&)?id=\d{5,25}/i.test(t)
    || /^https?:\/\/(www\.|web\.|m\.)?facebook\.com\/people\/[^/?#]+\/\d{5,25}\/?(\?.*)?$/i.test(t);
}

/** v.douyin.com/<code>: resolves to either a video or a profile. */
export function isDouyinShortUrl(raw: string): boolean {
  const u = parse(raw);
  return !!u && u.hostname.toLowerCase() === 'v.douyin.com' && /^\/[A-Za-z0-9_-]+\/?$/.test(u.pathname);
}

/** douyin.com/user/<sec_uid>, iesdouyin.com/share/user/<sec_uid>, or a v.douyin.com short link. */
/** A Xiaohongshu / RedNote profile link (xiaohongshu.com or rednote.com /user/profile/<id>). */
export function isXhsProfileUrl(raw: string): boolean {
  const u = parse(raw);
  if (!u) return false;
  const h = bareHost(u);
  return (h === 'xiaohongshu.com' || h === 'rednote.com') && /^\/user\/profile\/[0-9a-zA-Z]+\/?$/.test(u.pathname);
}

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
  return isDouyinChannelUrl(raw) || isXhsProfileUrl(raw);
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

function stripTrailingUrlPunct(raw: string): string {
  let u = raw.trim();
  // Trim trailing sentence/bracket punctuation, preserving balanced `)` inside paths.
  while (u.length > 0 && /[.,;!?'"\]}>，。！？；：】》」』）]/.test(u[u.length - 1])) {
    u = u.slice(0, -1);
  }
  while (u.endsWith(')')) {
    const opens = (u.match(/\(/g) || []).length;
    const closes = (u.match(/\)/g) || []).length;
    if (closes > opens) u = u.slice(0, -1);
    else break;
  }
  return u;
}

/** Links in pasted text, including Douyin/Xiaohongshu/Bilibili/Zalo share text with glued CJK punctuation. */
export function parseUrls(text: string): { valid: string[]; invalid: string[] } {
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const t = raw.trim();
    if (!t) continue;
    const embedded = t.match(
      /https?:\/\/[^\s<>"'`\u3000\uff0c\u3002\uff01\uff1f\uff1b\uff1a\u3010\u3011\u300a\u300b\u300c\u300d\u300e\u300f\uff08\uff09]+/gi,
    );
    if (embedded && embedded.length > 0) {
      let anyOk = false;
      for (const piece of embedded) {
        const clean = stripTrailingUrlPunct(piece);
        let ok = false;
        try {
          const u = new URL(clean);
          ok = (u.protocol === 'http:' || u.protocol === 'https:') && u.hostname.includes('.');
        } catch { /* not a URL */ }
        if (ok) {
          anyOk = true;
          if (!valid.includes(clean)) valid.push(clean);
        }
      }
      if (!anyOk) invalid.push(t);
    } else {
      invalid.push(t);
    }
  }
  // Share text from Douyin/TikTok/Bilibili apps wraps the link in words.
  // When at least one link was found, only report pieces that look like a broken link.
  if (valid.length > 0) {
    return { valid, invalid: invalid.filter((t) => /^(https?:|www\.)/i.test(t)) };
  }
  return { valid, invalid };
}
