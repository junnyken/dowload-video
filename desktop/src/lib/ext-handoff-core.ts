// Pure logic for browser-extension -> desktop-app stream handoff.
// Zero runtime imports so `node --test --experimental-strip-types` runs it directly.

export type ExtSubtitlePlan = { url: string; lang: string; format: string };

export type ExtHandoffInput = {
  url: string;
  title?: string | null;
  referer?: string | null;
  userAgent?: string | null;
  height?: number | null;
  stream?: boolean | null;
  cookieJar?: string | null;
  subtitles?: ExtSubtitlePlan[] | null;
};

export type HeaderPair = { name: string; value: string };

export type ExtHandoffPlan =
  | { mode: 'probe'; url: string; platform: string }
  | {
      mode: 'direct_queue';
      url: string;
      title: string;
      platform: string;
      headers: HeaderPair[];
      fileTitle: string;
      fileId: string;
      formatId?: string;
      audioOnly: boolean;
      formatLabel: string;
      quality: string;
      seriesFolder: string | null;
      subtitles: ExtSubtitlePlan[];
    };

const GUEST_MAX_HEIGHT = 1080;
const GUEST_FORMAT =
  'bv*[width<=1080][height>1080]+ba/bv*[height<=1080]+ba/b[width<=1080][height>1080]/b[height<=1080]/b';

const QUALITY_LABELS: Record<string, string> = {
  best: 'Tốt nhất',
  '2160': '2160p (4K)',
  '1440': '1440p (2K)',
  '1080': '1080p (Full HD)',
  '720': '720p (HD)',
  '480': '480p',
  '360': '360p',
  audio: 'Chỉ âm thanh',
};

const STREAM_EXT_RE = /\.(m3u8|mpd|mp4|m4v|webm|mov|mkv|m4a|mp3|ogg|oga|opus|flac|wav)(\?|$)/i;
const STREAM_HINT_RE = /(?:^|[/?&=_.-])(m3u8|hls\d*|mpd|manifest|playlist|master|chunklist|urlset)(?:$|[/?&=_.-])/i;
const EPHEMERAL_PARAMS = new Set(['token', 'sig', 'signature', 'expires', 'exp', 'hdnts', 'Policy', 'Signature', 'Key-Pair-Id']);
const JUNK_TITLE_RE = /^(?:just a moment\.{0,3}|attention required!?.*|(?:video\s*|jw\s*|html5\s*)?player|embed(?:\s+player)?|loading\.{0,3}|untitled|index|playlist|master|manifest)$/i;
const FILE_LIKE_TITLE_RE = /^[a-z0-9_-]+\.(?:m3u8?|mpd|mp4|ts|txt|html?|php)$/i;
const GENERIC_TRAIL_RE = /^(?:watch(?:\s+online)?(?:\s+free)?(?:\s+hd)?|free\s+(?:hd|videos?|movies?|streaming)|full\s+hd(?:\s+online)?|stream(?:ing)?\s+online|xem\s+phim(?:\s+online)?(?:\s+(?:hd|miễn\s+phí|mới(?:\s+nhất)?|nhanh|vietsub))?|phim\s+(?:hd|mới(?:\s+nhất)?|hay|lẻ|bộ)|trang\s+chủ|home)$/i;
const DOMAIN_TOKEN_RE = /\b[a-z0-9-]{2,}\.(?:com|net|org|io|vn|ws|tv|to|me|cc|co|xyz|pro|vip|club|top|site|online|stream|watch|live|today|one|fun|info|biz|asia|gg|la|sh|bz|su|icu|cyou|click|shop|lol|cfd)\b/i;

function fnv1aHex(input: string, seed: number): string {
  let h = seed >>> 0;
  for (let i = 0; i < input.length; i++) {
    h ^= input.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}

/** Deterministic, validate::file_id-safe ID (`hls-<16 hex>`, <= 64 chars). */
export function streamFileId(rawUrl: string): string {
  let key = String(rawUrl || '').trim();
  try {
    const u = new URL(key);
    u.hash = '';
    for (const k of [...u.searchParams.keys()]) {
      if (EPHEMERAL_PARAMS.has(k) || /^(utm_|token|sig|exp)/i.test(k)) u.searchParams.delete(k);
    }
    key = u.toString();
  } catch { /* keep raw */ }
  return `hls-${fnv1aHex(key, 0x811c9dc5)}${fnv1aHex(key, 0x9e3779b9)}`;
}

function cleanLine(raw: string | null | undefined, max: number): string {
  if (!raw) return '';
  let out = '';
  for (const ch of String(raw).trim()) {
    const code = ch.charCodeAt(0);
    if (code < 0x20 || code === 0x7f) continue;
    if (out.length >= max) break;
    out += ch;
  }
  return out.trim();
}

function hostStems(pageUrl: string | null | undefined): string[] {
  const u = parseHttpUrl(pageUrl);
  if (!u) return [];
  const h = u.hostname.toLowerCase().replace(/^(?:www\d*|m|web|v|play|player)\./, '');
  const first = h.split('.')[0] || '';
  const compact = first.replace(/[^a-z0-9]/g, '');
  const alpha = compact.replace(/\d+$/, '');
  return [h, first, compact, alpha.length >= 4 ? alpha : ''].filter((s) => s.length >= 3);
}

function isSiteSuffixSegment(seg: string, stems: string[]): boolean {
  const s = seg.trim();
  if (!s) return true;
  if (GENERIC_TRAIL_RE.test(s) || DOMAIN_TOKEN_RE.test(s)) return true;
  const low = s.toLowerCase();
  const compact = low.replace(/[^a-z0-9]/g, '');
  if (!compact) return true;
  for (const st of stems) {
    if (low === st || compact === st) return true;
    if (st.length >= 5 && compact.length <= st.length + 4 && compact.startsWith(st)) return true;
  }
  return false;
}

/** Strips notification counters, domain suffixes, and generic watch-online spam while keeping movie codes & episodes. */
export function cleanMediaTitle(raw: string | null | undefined, pageUrl?: string | null): string {
  let s = cleanLine(raw, 240);
  if (!s || /^https?:\/\//i.test(s)) return '';
  s = s.replace(/^(?:\(\d+\)\s*|▶\s*)+/, '').trim();
  if (!s || JUNK_TITLE_RE.test(s) || FILE_LIKE_TITLE_RE.test(s)) return '';

  const stems = hostStems(pageUrl);
  const parts = s.split(/\s+(?:\||::|—|–|•|»|-)\s+/).map((p) => p.trim()).filter(Boolean);
  while (parts.length > 1 && isSiteSuffixSegment(parts[parts.length - 1], stems)) {
    parts.pop();
  }
  while (parts.length > 1 && GENERIC_TRAIL_RE.test(parts[0])) {
    parts.shift();
  }
  s = parts.join(' - ');
  const strippedLead = s.replace(/^(?:xem\s+phim(?:\s+online)?|watch(?:\s+online)?(?:\s+free)?)\s*(?:[:\-–—]\s*|\s+)/i, '').trim();
  if (strippedLead.length >= 3) s = strippedLead;
  const strippedTail = s.replace(/(?:\s+(?:full\s*hd|fhd|hd|4k|vietsub|thuyết\s*minh|thuyet\s*minh|lồng\s*tiếng|long\s*tieng|online|miễn\s*phí|mien\s*phi|free))+\s*$/i, '').trim();
  if (strippedTail.length >= 3) s = strippedTail;

  s = s.replace(/[<>:"/\\|?*]+/g, ' ').replace(/\s+/g, ' ').trim().replace(/^[.\-\s]+|[.\-\s]+$/g, '');
  if (!s || JUNK_TITLE_RE.test(s) || FILE_LIKE_TITLE_RE.test(s)) return '';
  return s.length > 140 ? s.slice(0, 140).trim().replace(/[.\-\s]+$/, '') : s;
}

const WIN_RESERVED_STEM = /^(CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])$/i;
const SUB_LANG_RE = /^[a-z0-9_-]{1,16}$/i;

/** Splits a multi-episode anime/series title into `{ seriesTitle, episodeLabel }` (`null` for standalone movies). */
export function parseSeriesEpisode(rawTitle: string | null | undefined): { seriesTitle: string; episodeLabel: string } | null {
  const t = String(rawTitle || '').replace(/\s+/g, ' ').trim();
  if (!t || t.length < 5) return null;
  const patterns = [
    /^(.+?)(?:\s*[-–—:|•]\s*|\s+)((?:(?:phần|phan|mùa|mua|season)\s*\d+\s*[-–—:|•]?\s*)?(?:tập|tap|episode|ep\.?)\s*\d+(?:\.\d+)?(?:\s*(?:end|cuối|cuoi|final))?)\b/i,
    /^(.+?)(?:\s*[-–—:|•]\s*|\s+)(s\d{1,2}\s*e\d{1,4})\b/i,
    /^(.+?)(?:\s*[-–—:|•]\s*|\s+)(第\s*\d+(?:\.\d+)?\s*[話话集])/u,
  ];
  for (const re of patterns) {
    const m = re.exec(t);
    if (!m) continue;
    const series = m[1]
      .replace(/[\s\-–—:|•([{/]+$/g, '')
      .replace(/[<>:"/\\|?*]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const ep = m[2].replace(/\s+/g, ' ').trim();
    if (series.length >= 2 && series.length <= 90 && !/^\d+$/.test(series) && !DOMAIN_TOKEN_RE.test(series)) {
      return { seriesTitle: series, episodeLabel: ep };
    }
  }
  return null;
}

function sanitizeSeriesFolder(raw: string): string | null {
  // eslint-disable-next-line no-control-regex
  const normalized = String(raw || '').replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ').replace(/\s+/g, ' ').trim();
  let clean = Array.from(normalized).slice(0, 60).join('').replace(/[. ]+$/, '').trim();
  if (!clean) return null;
  const stem = clean.split('.')[0] ?? clean;
  if (WIN_RESERVED_STEM.test(stem)) {
    clean = Array.from(`_${clean}`).slice(0, 60).join('').replace(/[. ]+$/, '');
  }
  return clean || null;
}

function normalizeSubtitles(subs: ExtSubtitlePlan[] | null | undefined): ExtSubtitlePlan[] {
  if (!Array.isArray(subs) || subs.length === 0) return [];
  const out: ExtSubtitlePlan[] = [];
  for (const raw of subs.slice(0, 6)) {
    if (!raw || typeof raw !== 'object') continue;
    const u = parseHttpUrl(raw.url);
    if (!u || out.some((x) => x.url === u.toString())) continue;
    const rawLang = typeof raw.lang === 'string' ? raw.lang.trim() : '';
    const lang = SUB_LANG_RE.test(rawLang) ? rawLang : 'sub';
    const rawFmt = typeof raw.format === 'string' ? raw.format.trim().toLowerCase() : 'vtt';
    const format = rawFmt === 'srt' || rawFmt === 'ass' || rawFmt === 'hls' ? rawFmt : 'vtt';
    out.push({ url: u.toString(), lang, format });
  }
  return out;
}

function parseHttpUrl(raw: string | null | undefined): URL | null {
  if (!raw) return null;
  const s = String(raw).trim();
  if (!s || /[\x00-\x20\x7f]/.test(s)) return null;
  try {
    const u = new URL(s);
    if ((u.protocol !== 'http:' && u.protocol !== 'https:') || !u.hostname || u.username || u.password) return null;
    return u;
  } catch {
    return null;
  }
}

function samePageKey(u: URL): string {
  const c = new URL(u.toString());
  c.hash = '';
  return c.toString().replace(/\/$/, '');
}

function isSniffedStream(job: ExtHandoffInput, target: URL, ref: URL | null): boolean {
  if (job.stream === true) return true;
  if (STREAM_EXT_RE.test(target.pathname) || STREAM_HINT_RE.test(target.pathname) || STREAM_HINT_RE.test(target.search)) {
    return true;
  }
  if (ref && samePageKey(ref) !== samePageKey(target)) return true;
  return false;
}

function resolveStreamFormat(
  height: number | null | undefined,
  defaultQuality: string,
  guest: boolean,
): { formatId?: string; audioOnly: boolean; formatLabel: string; quality: string } {
  const validHeight = typeof height === 'number' && Number.isInteger(height) && height >= 144 && height <= 4320 ? height : null;
  const q = validHeight ? String(validHeight) : (defaultQuality || 'best');
  if (q === 'audio') {
    return { audioOnly: true, formatLabel: QUALITY_LABELS.audio, quality: 'audio' };
  }
  if (guest && (q === 'best' || (/^\d+$/.test(q) && Number(q) > GUEST_MAX_HEIGHT))) {
    return { formatId: GUEST_FORMAT, audioOnly: false, formatLabel: QUALITY_LABELS['1080'], quality: '1080' };
  }
  if (/^\d+$/.test(q)) {
    const h = Number(q);
    return {
      formatId: `bv*[height<=${h}]+ba/b[height<=${h}]/b`,
      audioOnly: false,
      formatLabel: QUALITY_LABELS[q] ?? `${h}p`,
      quality: q,
    };
  }
  return { audioOnly: false, formatLabel: QUALITY_LABELS.best, quality: 'best' };
}

export function planExtJob(
  job: ExtHandoffInput,
  opts: { defaultQuality: string; guest: boolean },
): ExtHandoffPlan {
  const url = String(job.url || '').trim();
  const target = parseHttpUrl(url);
  if (!target) return { mode: 'probe', url, platform: 'từ trình duyệt' };

  const ref = parseHttpUrl(job.referer);
  if (!isSniffedStream(job, target, ref)) {
    return { mode: 'probe', url: target.toString(), platform: 'từ trình duyệt' };
  }

  const title = cleanMediaTitle(job.title, job.referer) || cleanLine(job.title, 140) || 'Video từ trình duyệt';
  const headers: HeaderPair[] = [];

  if (ref) {
    let refVal = ref.toString();
    if (refVal.length > 512) {
      const shorter = `${ref.origin}${ref.pathname}`;
      refVal = shorter.length <= 512 ? shorter : `${ref.origin}/`;
    }
    if (refVal.length <= 512) headers.push({ name: 'Referer', value: refVal });
    if (ref.origin && ref.origin !== 'null' && ref.origin.length <= 512) {
      headers.push({ name: 'Origin', value: ref.origin });
    }
  }

  const rawUa = typeof job.userAgent === 'string' ? job.userAgent.trim() : '';
  if (rawUa && rawUa.length <= 512 && !/[\x00-\x1f\x7f]/.test(rawUa)) {
    headers.push({ name: 'User-Agent', value: rawUa });
  }

  const fmt = resolveStreamFormat(job.height, opts.defaultQuality, opts.guest);
  const parsedEp = parseSeriesEpisode(title);
  const seriesFolder = parsedEp ? sanitizeSeriesFolder(parsedEp.seriesTitle) : null;
  const subtitles = normalizeSubtitles(job.subtitles);
  return {
    mode: 'direct_queue',
    url: target.toString(),
    title,
    platform: 'other',
    headers,
    fileTitle: title,
    fileId: streamFileId(target.toString()),
    formatId: fmt.formatId,
    audioOnly: fmt.audioOnly,
    formatLabel: fmt.formatLabel,
    quality: fmt.quality,
    seriesFolder,
    subtitles,
  };
}
