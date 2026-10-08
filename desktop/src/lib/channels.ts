// Channels: saved-channel store, add flow (fetch listing) and the background
// scheduler (C1-CONTRACT.md section 4). The scheduler lives in the UI process,
// so it keeps running while the window is hidden in the tray.
import { createStore } from './store';
import { api, onCheckNow, onQuitting } from './tauri';
import { enqueue, enqueueRefused, pauseAllForQuit } from './queue';
import { qualityFormat } from './quality';
import { auth } from './auth';
import { settings } from './settings';
import { errorMessage, toAppError } from './errors';
import { newId } from './format';
import { fetchDouyinListing } from './douyin';
import { isDouyinUrl } from './urls';
import { cookiesUsableFor, planFor, routesFor } from './cookies';
import { claimBatch, quota } from './quota';
import { claimRoute, isLocalStep } from './routes-core';
import { toast } from './ui';
import type { Channel, ChannelListing, ChannelVideo } from './types';

// ---- helpers --------------------------------------------------------------------

/** Folder-name safe version of a channel title (Windows rules). */
export function sanitizeFolderName(s: string): string {
  const clean = s
    // eslint-disable-next-line no-control-regex
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 60)
    .replace(/[. ]+$/, '');
  return clean || 'Kenh';
}

export function joinPath(base: string, name: string): string {
  return `${base.replace(/[\\/]+$/, '')}\\${name}`;
}

export { looksLikeChannelUrl } from './urls';

/** Douyin channels are never scanned in the background: every scan costs the server money. */
export const isManualOnly = (c: { platform: string }) => c.platform === 'douyin';

/**
 * Channel listing: Douyin goes through the server (P1 keeps it there), everything else through
 * the local yt-dlp, with the user's cookies when that platform is connected (PLAN-32D §6).
 */
export function fetchChannelListing(url: string, limit: number): Promise<ChannelListing> {
  if (isDouyinUrl(url)) return fetchDouyinListing(url, limit);
  return api.channelFetch(url, limit, planFor(url).useCookies || undefined);
}

type ChannelTarget = { platform: string; title: string; quality: string; outDir: string };

function jobOf(v: ChannelVideo, ch: ChannelTarget) {
  const f = qualityFormat(ch.quality, auth.get().status !== 'in');
  if (ch.platform === 'douyin') f.formatId = undefined; // a direct MP4 has no height to select on
  return {
    url: v.url, title: v.title, thumbnail: v.thumbnail, platform: ch.platform, uploader: ch.title, outDir: ch.outDir,
    formatId: f.formatId, audioOnly: f.audioOnly, formatLabel: f.label, estSize: null, quality: ch.quality,
  };
}

/** Puts videos into the download queue exactly like the Download screen does (without a per-video probe). */
export function enqueueVideos(videos: ChannelVideo[], ch: ChannelTarget, claims?: Map<string, { claimId: string; token?: string }>) {
  for (const v of videos) {
    const c = claims?.get(v.id);
    enqueue({ ...jobOf(v, ch), claimId: c?.claimId || null, claimToken: c?.token ?? null });
  }
}

export type Refused = { video: ChannelVideo; detail: string | null };

/**
 * Channel videos -> queue, with the daily allowance claimed for the whole
 * list first (PLAN-32D §6, POST /client/quota/claim-batch). Only allowed
 * videos are queued (they carry their claim, so the queue does not claim
 * again). Videos whose first route is the server (Douyin, server-only
 * platforms) are queued as before: the server counts them itself. 503
 * client_quota_disabled / no answer = today's behaviour (each item claims
 * when it starts).
 */
export async function enqueueWithClaims(videos: ChannelVideo[], ch: ChannelTarget): Promise<{ enqueued: ChannelVideo[]; refused: Refused[] }> {
  const groups = new Map<'local' | 'local_cookie', ChannelVideo[]>();
  const direct: ChannelVideo[] = [];
  for (const v of videos) {
    const first = routesFor(v.url)[0];
    if (quota.get().status !== 'disabled' && isLocalStep(first)) {
      const r = claimRoute(first);
      groups.set(r, [...(groups.get(r) ?? []), v]);
    } else direct.push(v);
  }
  const claims = new Map<string, { claimId: string; token?: string }>();
  const allowed: ChannelVideo[] = [...direct];
  const refused: Refused[] = [];
  for (const [route, list] of groups) {
    const d = await claimBatch(list.map((v) => v.url), route);
    if (d.kind !== 'ok') { allowed.push(...list); continue; } // disabled / offline: claimed per item at start
    d.items.forEach((it, i) => {
      if (it.allowed) { allowed.push(list[i]); if (it.claimId) claims.set(list[i].id, { claimId: it.claimId, token: it.token }); }
      else refused.push({ video: list[i], detail: it.detail });
    });
  }
  const keep = new Set(allowed.map((v) => v.id));
  const enqueued = videos.filter((v) => keep.has(v.id)); // channel order
  enqueueVideos(enqueued, ch, claims);
  return { enqueued, refused };
}

/** Refused channel videos in the queue's "failed" list, each with the server's reason (picker). */
export function addRefused(refused: Refused[], ch: ChannelTarget) {
  for (const r of refused) enqueueRefused(jobOf(r.video, ch), r.detail);
}

/** One line for a refusal toast / notification. wording: BA review */
export function refusedText(n: number, detail: string | null): string {
  const reset = quota.get().snap?.resetTimeVn;
  // The server's own text already says when the allowance resets.
  return `${n} video chưa được tải vì đã hết lượt tải hôm nay. ${detail ?? (reset ? `Lượt mới lúc ${reset}.` : '')}`.trim();
}

/** The channel uses the user's own cookies: it is checked at most every 6 hours (PLAN-32D §6). */
export const usesCookies = (c: { url: string; platform: string }) => c.platform !== 'douyin' && cookiesUsableFor(c.url);
export const MIN_COOKIE_INTERVAL_H = 6;
const effectiveHours = (c: Channel) => (usesCookies(c) ? Math.max(c.checkEveryHours, MIN_COOKIE_INTERVAL_H) : c.checkEveryHours);

export function notifySafe(title: string, body: string) {
  void api.notify(title, body).catch(() => {});
}

// ---- saved channels --------------------------------------------------------------

export const channels = createStore<Channel[]>([]);
export const channelsLoaded = createStore(false);
export const checking = createStore<string[]>([]);

export const totalPending = (l: Channel[]) => l.reduce((n, c) => n + c.pendingNew.length, 0);

export async function loadChannels() {
  try {
    channels.set(await api.channelList());
  } catch { /* not in app / DB error: keep empty */ }
  channelsLoaded.set(true);
}

export async function saveChannel(c: Channel) {
  await api.channelSave(c);
  channels.set((l) => (l.some((x) => x.id === c.id) ? l.map((x) => (x.id === c.id ? c : x)) : [...l, c]));
}

/** Applies a patch to the CURRENT stored version (the user may have edited it meanwhile). */
async function patchChannel(id: string, p: (c: Channel) => Channel) {
  const cur = channels.get().find((x) => x.id === id);
  if (!cur) return;
  await saveChannel(p(cur));
}

export async function removeChannel(id: string) {
  await api.channelDelete(id);
  channels.set((l) => l.filter((c) => c.id !== id));
}

/** "Tải" from the review list: enqueue (allowance claimed first) + mark seen + drop from pending. Refused videos stay pending. */
export async function downloadPending(id: string, videoIds: string[]): Promise<{ enqueued: number; refused: Refused[] }> {
  const ch = channels.get().find((c) => c.id === id);
  if (!ch) return { enqueued: 0, refused: [] };
  const want = new Set(videoIds);
  const { enqueued, refused } = await enqueueWithClaims(ch.pendingNew.filter((v) => want.has(v.id)), ch);
  const done = new Set(enqueued.map((v) => v.id));
  if (done.size) await api.channelSeenAdd(id, [...done]);
  await patchChannel(id, (c) => ({ ...c, pendingNew: c.pendingNew.filter((v) => !done.has(v.id)) }));
  return { enqueued: done.size, refused };
}

/** "Bỏ qua": mark seen + drop from pending. */
export async function dismissPending(id: string, videoIds: string[]) {
  const set = new Set(videoIds);
  await api.channelSeenAdd(id, videoIds);
  await patchChannel(id, (c) => ({ ...c, pendingNew: c.pendingNew.filter((v) => !set.has(v.id)) }));
}

// ---- add flow ---------------------------------------------------------------------

export type Flow = {
  phase: 'idle' | 'loading' | 'error' | 'ready';
  url: string;
  limit: number;
  listing?: ChannelListing;
  errorCode?: string;
  more?: boolean; // loading a longer list while the picker stays open
};
export const flow = createStore<Flow>({ phase: 'idle', url: '', limit: 200 });
let flowToken = 0;

export async function fetchListing(url: string, limit: number, more = false) {
  const token = ++flowToken;
  const prev = flow.get().listing;
  flow.set({ phase: more && prev ? 'ready' : 'loading', url, limit, listing: more ? prev : undefined, more });
  try {
    const listing = await fetchChannelListing(url, limit);
    if (token !== flowToken) return;
    flow.set({ phase: 'ready', url, limit, listing });
  } catch (e) {
    if (token !== flowToken) return;
    const code = toAppError(e).code;
    if (more && prev) {
      flow.set({ phase: 'ready', url, limit: flow.get().limit, listing: prev });
      toast('error', errorMessage(code));
    } else {
      flow.set({ phase: 'error', url, limit, errorCode: code });
    }
  }
}

export function cancelFlow() {
  const f = flow.get();
  flowToken++;
  if (f.phase === 'loading' || f.more) void api.cancelChannelFetch(f.url).catch(() => {});
  flow.set({ phase: 'idle', url: f.url, limit: 200 });
}

export function resetFlow() {
  flowToken++;
  flow.set({ phase: 'idle', url: '', limit: 200 });
}

// ---- scheduler --------------------------------------------------------------------

const TICK_MS = 60_000;
const failures = new Map<string, { n: number; retryAt: number }>();
let lock: Promise<unknown> = Promise.resolve();

function dueAt(c: Channel): number {
  const f = failures.get(c.id);
  if (f) return f.retryAt;
  return c.lastCheckedAt ? Date.parse(c.lastCheckedAt) + effectiveHours(c) * 3_600_000 : 0;
}

export function nextCheckAt(c: Channel): number | null {
  if (!c.enabled || isManualOnly(c)) return null;
  return Math.max(dueAt(c), c.lastCheckedAt ? 0 : Date.now());
}

/** At most one channel fetch at a time; a channel already queued/running is not queued twice. */
export function checkChannel(id: string): Promise<number | null> {
  if (checking.get().includes(id)) return Promise.resolve(null);
  checking.set((l) => [...l, id]);
  const run = lock.then(() => doCheck(id)).finally(() => checking.set((l) => l.filter((x) => x !== id)));
  lock = run.catch(() => {});
  return run;
}

/** Returns the number of new videos found, or null when the check failed. */
async function doCheck(id: string): Promise<number | null> {
  const ch = channels.get().find((c) => c.id === id);
  if (!ch) return null;
  const stamp = () => new Date().toISOString();
  try {
    const listing = await fetchChannelListing(ch.url, 50);
    const seen = new Set(await api.channelSeenList(id));
    const pending = new Set(ch.pendingNew.map((v) => v.id));
    const fresh = listing.videos.filter((v) => !seen.has(v.id) && !pending.has(v.id));
    failures.delete(id);
    if (fresh.length && ch.mode === 'download') {
      const { enqueued, refused } = await enqueueWithClaims(fresh, ch);
      if (enqueued.length) await api.channelSeenAdd(id, enqueued.map((v) => v.id));
      // Refused (no downloads left today): kept as "new videos" so the user can download them later.
      const left = refused.map((r) => r.video);
      await patchChannel(id, (c) => ({ ...c, lastCheckedAt: stamp(), lastError: null, pendingNew: [...left, ...c.pendingNew] }));
      if (enqueued.length) notifySafe('VidGrab', `${ch.title}: đã thêm ${enqueued.length} video mới vào hàng đợi`);
      // wording: BA review
      if (left.length) notifySafe('VidGrab', `Kênh ${ch.title}: ${refusedText(left.length, null)}`);
    } else if (fresh.length) {
      await patchChannel(id, (c) => ({ ...c, lastCheckedAt: stamp(), lastError: null, pendingNew: [...fresh, ...c.pendingNew] }));
      notifySafe('VidGrab', `${ch.title} có ${fresh.length} video mới`);
    } else {
      await patchChannel(id, (c) => ({ ...c, lastCheckedAt: stamp(), lastError: null }));
    }
    return fresh.length;
  } catch (e) {
    const code = toAppError(e).code;
    const n = (failures.get(id)?.n ?? 0) + 1;
    // 5, 10, 20 ... minutes, never longer than the channel's own interval.
    failures.set(id, { n, retryAt: Date.now() + Math.min(5 * 2 ** (n - 1) * 60_000, effectiveHours(ch) * 3_600_000) });
    try { await patchChannel(id, (c) => ({ ...c, lastCheckedAt: stamp(), lastError: code })); } catch { /* DB error: keep in memory only */ }
    return null;
  }
}

async function tick(force = false) {
  const now = Date.now();
  for (const c of channels.get()) {
    if (!c.enabled || isManualOnly(c)) continue; // manual "Kiểm tra ngay" on the card still works
    if (!force && dueAt(c) > now) continue;
    await checkChannel(c.id);
  }
}

/** "Kiểm tra ngay" for every enabled channel (tray menu). */
export function checkAllNow() {
  void tick(true);
}

let started = false;
export async function startChannels() {
  if (started) return;
  started = true;
  void api.setCloseToTray(settings.get().closeToTray).catch(() => {});
  await loadChannels();
  await onCheckNow(() => checkAllNow());
  await onQuitting(() => pauseAllForQuit());
  setInterval(() => void tick(), TICK_MS);
  setTimeout(() => void tick(), 8_000); // first pass shortly after boot (also when started minimised)
}

export function newChannelFromListing(l: ChannelListing, o: Pick<Channel, 'mode' | 'quality' | 'outDir' | 'checkEveryHours'>): Channel {
  const existing = channels.get().find((c) => c.id === (l.channelId || ''));
  return {
    id: l.channelId || newId(), url: l.url, title: l.title, platform: l.platform, thumbnail: l.thumbnail,
    ...o, enabled: true,
    lastCheckedAt: new Date().toISOString(), lastError: null,
    pendingNew: existing?.pendingNew ?? [],
    createdAt: existing?.createdAt ?? new Date().toISOString(),
  };
}
