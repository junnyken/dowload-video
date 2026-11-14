// Pure logic for browser-extension -> desktop-app stream handoff.
// Zero runtime imports so `node --test --experimental-strip-types` runs it directly.

export type ExtHandoffInput = {
  url: string;
  title?: string | null;
  referer?: string | null;
  userAgent?: string | null;
  height?: number | null;
  stream?: boolean | null;
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
const STREAM_HINT_RE = /(?:^|[/?&=_.-])(m3u8|hls|mpd|manifest|playlist)(?:$|[/?&=_.-])/i;
const EPHEMERAL_PARAMS = new Set(['token', 'sig', 'signature', 'expires', 'exp', 'hdnts', 'Policy', 'Signature', 'Key-Pair-Id']);

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

  const title = cleanLine(job.title, 200) || 'Video từ trình duyệt';
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
  };
}
