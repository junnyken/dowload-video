import type { ProbeFormat } from './types';
import type { Quality } from './settings';

export type QualityOption = { value: Quality; label: string; format?: ProbeFormat };

const PRESETS = [2160, 1440, 1080, 720, 480, 360] as const;
const PRESET_LABEL: Record<number, string> = {
  2160: '2160p (4K)', 1440: '1440p (2K)', 1080: '1080p (Full HD)', 720: '720p (HD)', 480: '480p', 360: '360p',
};
export const QUALITY_LABEL: Record<Quality, string> = {
  best: 'Tốt nhất', '2160': '2160p (4K)', '1440': '1440p (2K)', '1080': '1080p (Full HD)',
  '720': '720p (HD)', '480': '480p', '360': '360p', audio: 'Chỉ âm thanh',
};

/** Presets offered for a probe result: best, each resolution bucket present, audio only. */
export function qualityOptions(formats: ProbeFormat[]): QualityOption[] {
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
  }
  return out;
}

/** Pick the option matching the user's default quality (closest not above, else best). */
export function resolveQuality(opts: QualityOption[], want: Quality): Quality {
  if (want === 'best' || want === 'audio') return want;
  const n = Number(want);
  const nums = opts.filter((o) => /^\d+$/.test(o.value)).map((o) => Number(o.value));
  const below = nums.filter((x) => x <= n).sort((a, b) => b - a)[0];
  if (below != null) return String(below) as Quality;
  return 'best';
}

/** Height preset selector. The trailing `/b` keeps sites whose formats carry no height (MangoTV) downloadable. */
export function presetFormat(h: number): string {
  return `bv*[height<=${h}]+ba/b[height<=${h}]/b`;
}

export function qualityFormat(q: string): { formatId?: string; audioOnly: boolean; label: string } {
  const label = QUALITY_LABEL[q as Quality] ?? QUALITY_LABEL.best;
  if (q === 'audio') return { audioOnly: true, label };
  if (/^\d+$/.test(q)) return { formatId: presetFormat(Number(q)), audioOnly: false, label };
  return { audioOnly: false, label };
}
