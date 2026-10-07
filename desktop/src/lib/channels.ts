// Channels: saved-channel store, add flow (fetch listing) and the background
// scheduler (C1-CONTRACT.md section 4). The scheduler lives in the UI process,
// so it keeps running while the window is hidden in the tray.
import { createStore } from './store';
import { api, onCheckNow, onQuitting } from './tauri';
import { enqueue, pauseAllForQuit } from './queue';
import { QUALITY_LABEL } from './quality';
import { settings, type Quality } from './settings';
import { errorMessage, toAppError } from './errors';
import { newId } from './format';
import { fetchDouyinListing } from './douyin';
import { isDouyinUrl } from './urls';
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

/** Channel listing: Douyin goes through the server, everything else through the local yt-dlp. */
export function fetchChannelListing(url: string, limit: number): Promise<ChannelListing> {
  return isDouyinUrl(url) ? fetchDouyinListing(url, limit) : api.channelFetch(url, limit);
}

export function qualityFormat(q: string): { formatId?: string; audioOnly: boolean; label: string } {
  const label = QUALITY_LABEL[q as Quality] ?? QUALITY_LABEL.best;
  if (q === 'audio') return { audioOnly: true, label };
  if (/^\d+$/.test(q)) return { formatId: `bv*[height<=${q}]+ba/b[height<=${q}]`, audioOnly: false, label };
  return { audioOnly: false, label };
}

/** Puts videos into the download queue exactly like the Download screen does (without a per-video probe). */
export function enqueueVideos(
  videos: ChannelVideo[],
  ch: { platform: string; title: string; quality: string; outDir: string },
) {
  const f = qualityFormat(ch.quality);
  if (ch.platform === 'douyin') f.formatId = undefined; // a direct MP4 has no height to select on
  for (const v of videos) {
    enqueue({
      url: v.url, title: v.title, thumbnail: v.thumbnail, platform: ch.platform, uploader: ch.title, outDir: ch.outDir,
      formatId: f.formatId, audioOnly: f.audioOnly, formatLabel: f.label, estSize: null,
    });
  }
}

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

/** "Tải" from the review list: enqueue + mark seen + drop from pending. */
export async function downloadPending(id: string, videoIds: string[]) {
  const ch = channels.get().find((c) => c.id === id);
  if (!ch) return;
  const set = new Set(videoIds);
  enqueueVideos(ch.pendingNew.filter((v) => set.has(v.id)), ch);
  await api.channelSeenAdd(id, videoIds);
  await patchChannel(id, (c) => ({ ...c, pendingNew: c.pendingNew.filter((v) => !set.has(v.id)) }));
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
  return c.lastCheckedAt ? Date.parse(c.lastCheckedAt) + c.checkEveryHours * 3_600_000 : 0;
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
      enqueueVideos(fresh, ch);
      await api.channelSeenAdd(id, fresh.map((v) => v.id));
      await patchChannel(id, (c) => ({ ...c, lastCheckedAt: stamp(), lastError: null }));
      notifySafe('VidGrab', `${ch.title}: đã thêm ${fresh.length} video mới vào hàng đợi`);
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
    failures.set(id, { n, retryAt: Date.now() + Math.min(5 * 2 ** (n - 1) * 60_000, ch.checkEveryHours * 3_600_000) });
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
