// Douyin (抖音) through the VidGrab server: the app's local yt-dlp cannot read
// it. POST /api/v1/client/douyin/channel lists a profile (free), POST
// /api/v1/client/douyin/video resolves one video to a short-lived signed CDN
// link and counts ONE download. Direct links and tokens are never logged.
import { apiFetch } from './http';
import { auth, getAccessToken } from './auth';
import { api } from './tauri';
import { createStore } from './store';
import { DOUYIN_ALL, listsMore, mergeListings, needsMore } from './douyin-core';
import { rememberServerMessage, toAppError } from './errors';
import { douyinVideoId } from './urls';
import type { ChannelListing, ProbeResult } from './types';

export type DouyinItem = { id: string; url: string; title: string; thumbnail: string | null; durationSec: number | null };
export type DouyinChannel = {
  platform: 'douyin' | 'xiaohongshu'; channelTitle: string; channelUrl: string; cap: number; fromCache: boolean; items: DouyinItem[];
  /** 20/10: Douyin has more than the server could list; 'douyin_extension' = point to the browser extension. */
  hasMore?: boolean; moreHint?: string | null;
};
export type DouyinVideo = {
  platform: 'douyin'; id: string; url: string; title: string; uploader: string | null; thumbnail: string | null;
  durationSec: number | null; directUrl: string; audioUrl: string | null;
  headers: Record<string, string>; expiresAt: string | null; cacheHit: boolean;
  /** PLAN-32E P3: signed by the server for directUrl/audioUrl; Rust checks it. */
  vgToken?: string;
};

const KNOWN = new Set([
  'unsupported_url', 'quota_exceeded', 'quota_exceeded_daily', 'budget_exceeded', 'already_processing',
  'no_media_found', 'platform_disabled', 'provider_unavailable', 'login_required',
]);

/** Server error -> { code, message }: the server's Vietnamese `detail` is remembered and shown as-is. */
export function douyinError(status: number, data: unknown): { code: string; message: string } {
  const o = (data && typeof data === 'object' ? data : {}) as { detail?: unknown; error_code?: unknown };
  const detail = typeof o.detail === 'string' ? o.detail : '';
  let code: string;
  if (typeof o.error_code === 'string' && KNOWN.has(o.error_code)) code = `api:${o.error_code}`;
  else if (status === 429) code = 'rate_limited';
  else if (status === 401) code = 'unauthorized';
  else if (status >= 500 && !detail) code = 'server';
  else code = 'api:other';
  rememberServerMessage(code, detail);
  return { code, message: detail };
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let token = await getAccessToken();
  let r = await apiFetch<T>(path, { method: 'POST', body, token });
  if (r.status === 401 && token) { // expired access token: refresh once, otherwise carry on as the server decides
    token = await getAccessToken(true);
    r = await apiFetch<T>(path, { method: 'POST', body, token });
  }
  if (r.status >= 200 && r.status < 300 && r.data) return r.data;
  throw douyinError(r.status, r.data);
}

const clampLimit = (limit: number) => Math.max(1, Math.min(DOUYIN_ALL, Math.round(limit)));

export const douyinChannel = (url: string, limit: number) =>
  post<DouyinChannel>('/api/v1/client/douyin/channel', { url, limit: clampLimit(limit) });

/** Xiaohongshu / RedNote profile through the server (signed-in only, ≤ 20 per scan, paid per scan). */
export const xhsChannel = (url: string, limit: number) =>
  post<DouyinChannel>('/api/v1/client/xiaohongshu/channel', { url, limit: Math.max(1, Math.min(20, Math.round(limit))) });

export async function fetchXhsListing(url: string, limit: number): Promise<ChannelListing> {
  return toListing(await xhsChannel(url, limit));
}

export const douyinVideo = (url: string) => post<DouyinVideo>('/api/v1/client/douyin/video', { url });

/** The server's channel answer in the shape the Channels screen already uses. */
export function toListing(c: DouyinChannel): ChannelListing {
  const m = /\/user\/(?:profile\/)?([^/?#]+)/.exec(c.channelUrl);
  return {
    channelId: m ? decodeURIComponent(m[1]) : c.channelUrl, url: c.channelUrl, title: c.channelTitle, platform: c.platform || 'douyin',
    uploader: c.channelTitle, thumbnail: c.items.find((i) => i.thumbnail)?.thumbnail ?? null,
    videos: c.items.map((i) => ({ id: i.id, url: i.url, title: i.title, duration: i.durationSec, uploadDate: null, thumbnail: i.thumbnail })),
    truncated: false, // the whole channel is asked for up front (0.18.2), "load more" adds nothing
    cap: c.cap,
    moreHint: c.moreHint ?? null,
  };
}

/** Which step of a Douyin channel scan is running (the Channels screen's loading text). */
export type DouyinStage = 'server' | 'page' | 'pageShown' | 'deep';
export const douyinStage = createStore<DouyinStage | null>(null);

/**
 * 0.18.2 (owner 21/10): the whole channel, not only what the person can
 * download today.
 *  1. the server's free signed listing: one page (~41 newest), fast;
 *  2. Douyin has more → the app's own page window scrolls the profile on the
 *     user's IP (page_listing.js), free;
 *  3. that fails / finds fewer, and the person is signed in → the server's
 *     paid "deep" listing (Apify, capped on the server).
 * Any later step that fails keeps the best listing so far (with the
 * extension hint when Douyin had more).
 */
export async function fetchDouyinListing(url: string, limit: number, showPage = false): Promise<ChannelListing> {
  try {
    douyinStage.set('server');
    const first = toListing(await douyinChannel(url, limit));
    if (!needsMore(first, limit)) return first;
    douyinStage.set(showPage ? 'pageShown' : 'page');
    let pageNote: string | null = null; // 0.18.4: why the page step gave nothing (shown in the picker)
    const page = await api.channelFetch(first.url, limit, undefined, showPage || undefined).catch((e: unknown) => {
      const err = toAppError(e);
      pageNote = `${err.code}${err.message ? `: ${err.message}` : ''}`.slice(0, 240);
      return null;
    });
    const pageCount = page ? page.videos.length : null;
    if (listsMore(first, page)) return mergeListings(first, page, limit);
    if (!pageNote && pageCount != null) pageNote = `page listed ${pageCount}`;
    const withNote = { ...first, pageNote };
    if (auth.get().status !== 'in') return withNote;
    douyinStage.set('deep');
    let deepNote: string | null = null;
    const deep = await post<DouyinChannel>('/api/v1/client/douyin/channel', { url: first.url, limit: clampLimit(limit), deep: true })
      .then(toListing, (e: unknown) => { deepNote = toAppError(e).code; return null; });
    if (listsMore(first, deep)) return { ...deep, cap: undefined, moreHint: null };
    return { ...withNote, pageNote: [pageNote, deepNote && `server: ${deepNote}`].filter(Boolean).join(' · ') || null };
  } finally {
    douyinStage.set(null);
  }
}

/** What the Download screen shows for a Douyin link: nothing can be probed locally (and the server call would use a download). */
export function douyinPlaceholder(url: string): ProbeResult {
  const id = douyinVideoId(url);
  const base = { height: null, fps: null, filesize: null, vcodec: null, acodec: null, requiresMerge: false };
  return {
    url, platform: 'douyin', title: id ? `Video Douyin ${id}` : 'Video Douyin', thumbnail: null, duration: null, uploader: null,
    formats: [
      { ...base, id: 'douyin-mp4', label: 'Video (MP4)', ext: 'mp4', audioOnly: false },
      { ...base, id: 'douyin-audio', label: 'Âm thanh (MP3)', ext: 'mp3', audioOnly: true },
    ],
  };
}

/** True once the signed link is (about to be) past its expiry. */
export function isExpired(v: Pick<DouyinVideo, 'expiresAt'>, resolvedAt: number, now = Date.now()): boolean {
  const exp = v.expiresAt ? Date.parse(v.expiresAt) : NaN;
  return Number.isFinite(exp) ? exp - 15_000 <= now : now - resolvedAt > 20 * 60_000;
}

export { toHeaderList } from './cookies-core';
