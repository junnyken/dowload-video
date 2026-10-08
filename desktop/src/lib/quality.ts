import type { ProbeFormat } from './types';
import type { Quality } from './settings';

export type QualityOption = { value: Quality; label: string; format?: ProbeFormat; locked?: boolean };

/** Without an account the app stops at 1080p like the website: 2K/4K need a
 *  (free) account — owner 08/10, task #6134, "make guests sign up". */
export const GUEST_MAX_HEIGHT = 1080;
/** 1080p on the SHORT side, so a 1080x1920 portrait video still counts. */
export const GUEST_FORMAT =
  'bv*[width<=1080][height>1080]+ba/bv*[height<=1080]+ba/b[width<=1080][height>1080]/b[height<=1080]/b';
const LOCK_SUFFIX = ' · Đăng nhập để tải'; // wording: BA review

const PRESETS = [2160, 1440, 1080, 720, 480, 360] as const;
const PRESET_LABEL: Record<number, string> = {
  2160: '2160p (4K)', 1440: '1440p (2K)', 1080: '1080p (Full HD)', 720: '720p (HD)', 480: '480p', 360: '360p',
};
export const QUALITY_LABEL: Record<Quality, string> = {
  best: 'Tốt nhất', '2160': '2160p (4K)', '1440': '1440p (2K)', '1080': '1080p (Full HD)',
  '720': '720p (HD)', '480': '480p', '360': '360p', audio: 'Chỉ âm thanh',
};

/** Presets offered for a probe result: best, each resolution bucket present, audio only. */
export function qualityOptions(formats: ProbeFormat[], o: { guest?: boolean } = {}): QualityOption[] {
  const video = formats.filter((f) => !f.audioOnly && f.height != null);
  const out: QualityOption[] = [];
  const top = [...video].sort((a, b) => (b.height! - a.height!) || ((b.filesize ?? 0) - (a.filesize ?? 0)))[0];
  out.push({ value: 'best', label: QUALITY_LABEL.best, format: top });
  PRESETS.forEach((p, i) => {
    const lower = PRESETS[i + 1] ?? 0;
    const bucket = video.filter((f) => f.height! <= (i === 0 ? Infinity : p) && f.height! > lower);
    if (!bucket.length) return;
    const pick = bucket.sort((a, b) => (b.height! - a.height!) || ((b.filesize ?? 0) - (a.filesize ?? 0)))[0];
    out.push({ value: String(p) as Quality, label: PRESET_LABEL[p], format: pick });
  });
  const audio = formats.filter((f) => f.audioOnly).sort((a, b) => (b.filesize ?? 0) - (a.filesize ?? 0))[0];
  out.push({ value: 'audio', label: QUALITY_LABEL.audio, format: audio });
  // Douyin / server-only links are not probed locally (douyin.ts, probes.ts placeholders): one MP4 option plus audio.
  if (formats.some((f) => f.id === 'douyin-mp4' || f.id === 'server-mp4')) {
    out[0].label = 'Video (MP4)';
    out[out.length - 1].label = 'Chỉ âm thanh (MP3)';
    return out; // the server applies its own limit on that route
  }
  if (o.guest) {
    let capped: QualityOption | undefined;
    for (const opt of out) {
      if (!/^\d+$/.test(opt.value)) continue;
      if (Number(opt.value) > GUEST_MAX_HEIGHT) { opt.locked = true; opt.label += LOCK_SUFFIX; }
      else if (!capped) capped = opt;
    }
    if (out.some((x) => x.locked)) {
      out[0].label = `${QUALITY_LABEL.best} (tối đa ${GUEST_MAX_HEIGHT}p)`; // wording: BA review
      out[0].format = capped?.format; // the best class a guest may take; queue caps it again (capForGuest)
    }
  }
  return out;
}

/** Pick the option matching the user's default quality (closest not above, else best). */
export function resolveQuality(opts: QualityOption[], want: Quality): Quality {
  if (want === 'best' || want === 'audio') return want;
  const n = Number(want);
  const nums = opts.filter((o) => /^\d+$/.test(o.value) && !o.locked).map((o) => Number(o.value));
  const below = nums.filter((x) => x <= n).sort((a, b) => b - a)[0];
  if (below != null) return String(below) as Quality;
  return 'best';
}

/** Height preset selector. The trailing `/b` keeps sites whose formats carry no height (MangoTV) downloadable. */
export function presetFormat(h: number): string {
  return `bv*[height<=${h}]+ba/b[height<=${h}]/b`;
}

export function qualityFormat(q: string, guest = false): { formatId?: string; audioOnly: boolean; label: string } {
  const label = QUALITY_LABEL[q as Quality] ?? QUALITY_LABEL.best;
  if (q === 'audio') return { audioOnly: true, label };
  if (guest && (q === 'best' || (/^\d+$/.test(q) && Number(q) > GUEST_MAX_HEIGHT))) {
    return { formatId: GUEST_FORMAT, audioOnly: false, label: QUALITY_LABEL['1080'] };
  }
  if (/^\d+$/.test(q)) return { formatId: presetFormat(Number(q)), audioOnly: false, label };
  return { audioOnly: false, label };
}

/** Last guard before a LOCAL download starts: a guest's "no specific format"
 *  (= yt-dlp's best, possibly 4K) becomes the guest selector. The server
 *  route applies the same limit on its side. */
export function capForGuest(formatId: string | undefined, audioOnly: boolean, guest: boolean): string | undefined {
  return guest && !audioOnly && !formatId ? GUEST_FORMAT : formatId;
}
