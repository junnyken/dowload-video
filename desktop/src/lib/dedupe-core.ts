// Already-downloaded check for single links (market comparison 14/10:
// Downie-style "chống tải trùng"). Pure, unit-tested (`npm test`).
// Channels already skip videos they have seen (channel_seen_*); this covers
// links pasted or copied one by one.

function parse(raw: string): URL | null {
  try { return new URL(String(raw).trim()); } catch { return null; }
}

/**
 * The part of a link that names the video: YouTube `v=` / youtu.be id /
 * shorts id, otherwise the last path segment. Used as the history search
 * term; null when the link has nothing distinctive.
 */
export function videoKey(raw: string): string | null {
  const u = parse(raw);
  if (!u) return null;
  const host = u.hostname.toLowerCase().replace(/^(www|m)\./, '');
  if (host === 'youtube.com' || host === 'music.youtube.com') {
    const v = u.searchParams.get('v');
    if (v) return v;
  }
  const segs = u.pathname.split('/').filter(Boolean);
  if (host === 'youtu.be' && segs[0]) return segs[0];   // the id, whatever its length
  const last = segs[segs.length - 1];
  return last && last.length >= 5 ? last : null;
}

function site(u: URL): string {
  const h = u.hostname.toLowerCase().replace(/^(www|m)\./, '');
  return h === 'youtu.be' || h === 'music.youtube.com' ? 'youtube.com' : h;
}

/** Same video: same site (youtu.be = youtube.com) and same video key. */
export function sameVideo(a: string, b: string): boolean {
  const ua = parse(a), ub = parse(b);
  if (!ua || !ub) return false;
  const ha = site(ua);
  const hb = site(ub);
  const ka = videoKey(a), kb = videoKey(b);
  return ha === hb && !!ka && ka === kb;
}

/** dd/mm/yyyy for a confirm text; '' when unparsable. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()}`;
}
