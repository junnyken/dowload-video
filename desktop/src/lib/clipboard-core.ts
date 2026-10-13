// Clipboard link watch — pure logic (no app imports: unit-tested with plain
// node, `npm test`). Market comparison 14/10: JDownloader-style "copy a link,
// the app offers to download it".
//
// Privacy: Rust only forwards a clipboard that is ONE http(s) URL (no spaces,
// ≤ 2048 chars); everything else never reaches the webview. Here a URL is
// acted on only when its host is a platform VidGrab downloads from; nothing
// leaves the machine until the user clicks "Tải" (or chose automatic mode).

export type ClipMode = 'ask' | 'auto' | 'off';

/** Hosts (and their subdomains) VidGrab downloads from. */
const HOSTS: Record<string, string> = {
  'youtube.com': 'YouTube', 'youtu.be': 'YouTube',
  'tiktok.com': 'TikTok',
  'douyin.com': 'Douyin', 'iesdouyin.com': 'Douyin',
  'instagram.com': 'Instagram',
  'facebook.com': 'Facebook', 'fb.watch': 'Facebook',
  'x.com': 'X', 'twitter.com': 'X',
  'threads.net': 'Threads', 'threads.com': 'Threads',
  'reddit.com': 'Reddit', 'redd.it': 'Reddit',
  'vimeo.com': 'Vimeo',
  'bilibili.com': 'Bilibili', 'b23.tv': 'Bilibili',
  'dailymotion.com': 'Dailymotion', 'dai.ly': 'Dailymotion',
  'soundcloud.com': 'SoundCloud',
  'pinterest.com': 'Pinterest', 'pin.it': 'Pinterest',
  'xiaohongshu.com': 'Xiaohongshu', 'xhslink.com': 'Xiaohongshu',
  'twitch.tv': 'Twitch',
  'vk.com': 'VK', 'vkvideo.ru': 'VK',
  'snapchat.com': 'Snapchat',
  'lemon8-app.com': 'Lemon8',
  'odysee.com': 'Odysee',
  'open.spotify.com': 'Spotify',
};

/** Platform label for a downloadable link, or null (not ours / not a link). */
export function platformOf(raw: string): string | null {
  let u: URL;
  try { u = new URL(String(raw).trim()); } catch { return null; }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return null;
  const host = u.hostname.toLowerCase().replace(/\.$/, '');
  for (const [h, label] of Object.entries(HOSTS)) {
    if (host === h || host.endsWith('.' + h)) {
      // A bare home page is not something to download.
      if ((u.pathname === '/' || u.pathname === '') && !u.search) return null;
      return label;
    }
  }
  return null;
}

/** Same link for dedupe: no hash, no tracking params, no trailing slash. */
export function normalize(raw: string): string {
  try {
    const u = new URL(String(raw).trim());
    u.hash = '';
    for (const k of [...u.searchParams.keys()]) {
      if (/^(utm_|si$|igsh$|igshid$|is_from_webapp$|sender_device$|share_)/i.test(k)) u.searchParams.delete(k);
    }
    return u.toString().replace(/\/$/, '');
  } catch {
    return String(raw).trim();
  }
}

export const DEDUPE_MS = 10 * 60 * 1000;

/**
 * Decides what to do with a link that just appeared on the clipboard.
 * `seen` maps normalized link -> last time handled (mutated); `ignored` holds
 * links the app itself copied (consumed once).
 */
export function decide(
  raw: string,
  mode: ClipMode,
  seen: Map<string, number>,
  ignored: Set<string>,
  now: number,
): { action: 'none' } | { action: 'ask' | 'auto'; url: string; platform: string } {
  if (mode === 'off') return { action: 'none' };
  const platform = platformOf(raw);
  if (!platform) return { action: 'none' };
  const key = normalize(raw);
  if (ignored.delete(key)) return { action: 'none' };
  const last = seen.get(key);
  if (last !== undefined && now - last < DEDUPE_MS) return { action: 'none' };
  seen.set(key, now);
  for (const [k, t] of seen) if (now - t >= DEDUPE_MS) seen.delete(k);
  return { action: mode, url: String(raw).trim(), platform };
}
