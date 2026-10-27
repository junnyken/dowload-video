// 0.21.0 (quality #3): the label of a finished download, built ONLY from what
// ffprobe measured on the file (Rust media.rs). Nothing measured → null, and
// the caller keeps the label of the format that was asked for.
// Same parts as the website (frontend/src/lib/mediaLabel.js).

export type Media = {
  shortSide?: number;
  vcodec?: string;
  fps?: number;
  bitrateKbps?: number;
  hdr: boolean;
  acodec?: string;
  audioKbps?: number;
};

const CODEC: Record<string, string> = { h264: 'H.264', hevc: 'HEVC', vp9: 'VP9', av1: 'AV1', aac: 'AAC', mp3: 'MP3', opus: 'Opus', vorbis: 'Vorbis', flac: 'FLAC' };

const res = (h: number) => (h >= 2160 ? '4K' : h >= 1440 ? '2K' : `${h}p`);
const rate = (k: number) => (k >= 1000 ? `${(k / 1000).toFixed(1)} Mbps` : `${k} kbps`);
const name = (c: string) => CODEC[c] ?? c.toUpperCase();

/** e.g. "4K · VP9 · 60 fps · 12.0 Mbps · HDR", "MP3 · 320 kbps"; null when nothing usable was measured. */
export function mediaLabel(m: Media | null | undefined): string | null {
  if (!m) return null;
  const parts: string[] = [];
  if (m.vcodec) {
    if (m.shortSide) parts.push(res(m.shortSide));
    parts.push(name(m.vcodec));
    if (m.fps) parts.push(`${Math.round(m.fps)} fps`);
    if (m.bitrateKbps) parts.push(rate(m.bitrateKbps));
    if (m.hdr) parts.push('HDR');
  } else if (m.acodec) {
    parts.push(name(m.acodec));
    const k = m.audioKbps ?? m.bitrateKbps;
    if (k) parts.push(`${k} kbps`);
  }
  return parts.length ? parts.join(' · ') : null;
}

/** True when the measured video codec may not open in older players (anything but H.264). */
export function needsModernPlayer(m: Media | null | undefined): boolean {
  return !!m?.vcodec && m.vcodec !== 'h264';
}
