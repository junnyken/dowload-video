// Douyin (抖音) through the VidGrab server: the app's local yt-dlp cannot read
// it. POST /api/v1/client/douyin/channel lists a profile (free), POST
// /api/v1/client/douyin/video resolves one video to a short-lived signed CDN
// link and counts ONE download. Direct links and tokens are never logged.
import { apiFetch } from './http';
import { getAccessToken } from './auth';
import { rememberServerMessage } from './errors';
import { douyinVideoId } from './urls';
import type { ChannelListing, ProbeResult } from './types';

export type DouyinItem = { id: string; url: string; title: string; thumbnail: string | null; durationSec: number | null };
export type DouyinChannel = {
  platform: 'douyin'; channelTitle: string; channelUrl: string; cap: number; fromCache: boolean; items: DouyinItem[];
};
export type DouyinVideo = {
  platform: 'douyin'; id: string; url: string; title: string; uploader: string | null; thumbnail: string | null;
  durationSec: number | null; directUrl: string; audioUrl: string | null;
  headers: Record<string, string>; expiresAt: string | null; cacheHit: boolean;
};

const KNOWN = new Set([
  'unsupported_url', 'quota_exceeded', 'quota_exceeded_daily', 'budget_exceeded', 'already_processing',
  'no_media_found', 'platform_disabled', 'provider_unavailable',
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

export const douyinChannel = (url: string, limit: number) =>
  post<DouyinChannel>('/api/v1/client/douyin/channel', { url, limit: Math.max(1, Math.min(500, Math.round(limit))) });

export const douyinVideo = (url: string) => post<DouyinVideo>('/api/v1/client/douyin/video', { url });

/** The server's channel answer in the shape the Channels screen already uses. */
export function toListing(c: DouyinChannel): ChannelListing {
  const m = /\/user\/([^/?#]+)/.exec(c.channelUrl);
  return {
    channelId: m ? decodeURIComponent(m[1]) : c.channelUrl, url: c.channelUrl, title: c.channelTitle, platform: 'douyin',
    uploader: c.channelTitle, thumbnail: c.items.find((i) => i.thumbnail)?.thumbnail ?? null,
    videos: c.items.map((i) => ({ id: i.id, url: i.url, title: i.title, duration: i.durationSec, uploadDate: null, thumbnail: i.thumbnail })),
    truncated: false, // the cap is the limit, not something "load more" can raise
    cap: c.cap,
  };
}

export async function fetchDouyinListing(url: string, limit: number): Promise<ChannelListing> {
  return toListing(await douyinChannel(url, limit));
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

/** Headers the local yt-dlp must send with the direct link (Rust allows Referer / User-Agent only). */
export function toHeaderList(h: Record<string, string> | null | undefined): { name: string; value: string }[] {
  return Object.entries(h ?? {})
    .filter(([k, v]) => /^(referer|user-agent)$/i.test(k) && typeof v === 'string' && v)
    .map(([name, value]) => ({ name, value }));
}
