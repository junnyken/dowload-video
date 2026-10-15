// Optional conditions for a watched channel's NEW videos (app 0.17.0, owner
// 15/10). Pure, unit-tested (`npm test`). An empty filter lets everything
// through (the old behaviour). A condition whose value the listing does not
// carry (YouTube's Videos tab has no view count; a page-read TikTok list has
// no duration) does NOT drop the video: it would silently drop every video of
// that channel. Rust validates the stored filter (channels.rs ChannelFilter).

import type { ChannelFilter, ChannelVideo } from './types';

export const EMPTY_FILTER: ChannelFilter = { minSeconds: null, maxSeconds: null, minViews: null, maxAgeDays: null, includeWords: [], excludeWords: [] };

/** Lower case, no Vietnamese diacritics ("Đánh giá" → "danh gia"). */
export function fold(s: string): string {
  return s.normalize('NFD').replace(/[̀-ͯ]/g, '').replace(/đ/g, 'd').replace(/Đ/g, 'D').toLowerCase();
}

export function isEmptyFilter(f: ChannelFilter | null | undefined): boolean {
  if (!f) return true;
  return f.minSeconds == null && f.maxSeconds == null && f.minViews == null && f.maxAgeDays == null && !f.includeWords.length && !f.excludeWords.length;
}

/** YYYYMMDD → ms (UTC midnight), or null. */
function dateMs(d: string | null): number | null {
  if (!d || !/^\d{8}$/.test(d)) return null;
  const t = Date.UTC(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8));
  return Number.isFinite(t) ? t : null;
}

export type Verdict = { pass: boolean; reason: string | null };

export function judge(v: ChannelVideo, f: ChannelFilter | null | undefined, nowMs: number): Verdict {
  if (isEmptyFilter(f) || !f) return { pass: true, reason: null };
  const d = v.duration;
  if (d != null && f.minSeconds != null && d < f.minSeconds) return { pass: false, reason: 'short' };
  if (d != null && f.maxSeconds != null && d > f.maxSeconds) return { pass: false, reason: 'long' };
  const views = v.viewCount ?? null;
  if (views != null && f.minViews != null && views < f.minViews) return { pass: false, reason: 'views' };
  const at = dateMs(v.uploadDate);
  if (at != null && f.maxAgeDays != null && nowMs - at > (f.maxAgeDays + 1) * 86_400_000) return { pass: false, reason: 'old' };
  const title = fold(v.title || '');
  if (f.excludeWords.some((w) => title.includes(fold(w.trim())))) return { pass: false, reason: 'excluded' };
  if (f.includeWords.length && !f.includeWords.some((w) => title.includes(fold(w.trim())))) return { pass: false, reason: 'keyword' };
  return { pass: true, reason: null };
}

export function split(videos: ChannelVideo[], f: ChannelFilter | null | undefined, nowMs: number) {
  const keep: ChannelVideo[] = [];
  const skip: ChannelVideo[] = [];
  for (const v of videos) (judge(v, f, nowMs).pass ? keep : skip).push(v);
  return { keep, skip };
}

/** Which conditions this listing cannot check (shown next to the filter). */
export function unknownFields(videos: ChannelVideo[], f: ChannelFilter | null | undefined): ('duration' | 'views' | 'date')[] {
  if (!f || !videos.length) return [];
  const out: ('duration' | 'views' | 'date')[] = [];
  if ((f.minSeconds != null || f.maxSeconds != null) && videos.every((v) => v.duration == null)) out.push('duration');
  if (f.minViews != null && videos.every((v) => v.viewCount == null)) out.push('views');
  if (f.maxAgeDays != null && videos.every((v) => !v.uploadDate)) out.push('date');
  return out;
}

/** "Top N xem nhiều nhất": null when the listing has no view counts. */
export function topByViews(videos: ChannelVideo[], n: number): ChannelVideo[] | null {
  const withViews = videos.filter((v) => v.viewCount != null);
  if (!withViews.length) return null;
  return [...withViews].sort((a, b) => (b.viewCount ?? 0) - (a.viewCount ?? 0)).slice(0, n);
}

/** Keywords typed as "a, b; c" → ["a","b","c"] (trimmed, unique, at most 25 of 100 chars). */
export function parseWords(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const w of text.split(/[,;\n]/)) {
    const t = w.trim().slice(0, 100);
    if (t && !seen.has(fold(t))) { seen.add(fold(t)); out.push(t); }
    if (out.length >= 25) break;
  }
  return out;
}
