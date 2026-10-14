// Per-platform download defaults (market comparison 14/10, like 4K Video
// Downloader's Smart Mode). Pure, unit-tested (`npm test`).
// Channels keep their own quality/folder (channels.ts); this is for links.

import type { Quality } from './settings';

export type PlatformPreset = { quality?: Quality; outDir?: string };
export type Presets = Record<string, PlatformPreset>;

/** Platforms offered in Settings (the rest use the global defaults). */
export const PRESET_PLATFORMS = ['youtube', 'tiktok', 'douyin', 'instagram', 'facebook', 'twitter'] as const;

const QUALITIES: Quality[] = ['best', '2160', '1440', '1080', '720', '480', '360', 'audio'];

/** Probe/queue platform name → preset key (x.com is "twitter"). */
export function presetKey(platform: string | null | undefined): string {
  const p = (platform || '').toLowerCase().trim();
  return p === 'x' ? 'twitter' : p;
}

/** Quality and folder for a new download of `platform`: preset first, then the global defaults. */
export function defaultsFor(
  platform: string | null | undefined,
  presets: Presets | null | undefined,
  defaultQuality: Quality,
  defaultDir: string | null,
): { quality: Quality; outDir: string | null } {
  const p = presets?.[presetKey(platform)];
  return {
    quality: p?.quality && QUALITIES.includes(p.quality) ? p.quality : defaultQuality,
    outDir: p?.outDir ? p.outDir : defaultDir,
  };
}

/** Drops unknown keys/values (localStorage may hold anything). */
export function cleanPresets(raw: unknown): Presets {
  const out: Presets = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const k of PRESET_PLATFORMS) {
    const v = (raw as Record<string, unknown>)[k];
    if (!v || typeof v !== 'object') continue;
    const q = (v as PlatformPreset).quality;
    const d = (v as PlatformPreset).outDir;
    const p: PlatformPreset = {};
    if (q && QUALITIES.includes(q)) p.quality = q;
    if (typeof d === 'string' && d.trim()) p.outDir = d;
    if (p.quality || p.outDir) out[k] = p;
  }
  return out;
}
