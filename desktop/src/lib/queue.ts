// Download queue: persisted in localStorage, scheduled by the concurrency
// setting, driven by Rust events (download://progress|done).
import { createStore } from './store';
import { api, onDone, onProgress } from './tauri';
import { settings } from './settings';
import { errorMessage, toAppError } from './errors';
import { toast } from './ui';
import { newId } from './format';
import type { HistoryItem, Stage } from './types';
import { syncSoon } from './sync';
import { douyinVideo, isExpired, toHeaderList, type DouyinVideo } from './douyin';
import { isDouyinUrl } from './urls';

export type QueueState = 'queued' | 'running' | 'paused' | 'completed' | 'failed';

export type QueueItem = {
  id: string; // = jobId
  url: string;
  title: string;
  thumbnail: string | null;
  platform: string;
  uploader: string | null;
  outDir: string;
  formatId?: string;
  audioOnly: boolean;
  formatLabel: string;
  estSize: number | null;
  state: QueueState;
  pausing?: boolean;
  stage: Stage | null;
  percent: number | null;
  downloadedBytes: number | null;
  totalBytes: number | null;
  speedBps: number | null;
  etaSec: number | null;
  filePath: string | null;
  fileSize: number | null;
  errorCode: string | null;
  addedAt: string;
};

const KEY = 'vg.queue';

function load(): QueueItem[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? '[]');
    if (!Array.isArray(raw)) return [];
    return raw.map((i: QueueItem) =>
      // The app was closed mid-download: the process is gone, partial files remain.
      i.state === 'running' ? { ...i, state: 'paused', pausing: false, speedBps: null, etaSec: null } : i,
    );
  } catch {
    return [];
  }
}

export const queue = createStore<QueueItem[]>(load());

let persistTimer: ReturnType<typeof setTimeout> | null = null;
queue.subscribe(() => {
  persistTimer ??= setTimeout(() => {
    persistTimer = null;
    try { localStorage.setItem(KEY, JSON.stringify(queue.get())); } catch { /* quota */ }
  }, 800);
});

function patch(id: string, p: Partial<QueueItem>) {
  queue.set((l) => l.map((i) => (i.id === id ? { ...i, ...p } : i)));
}

export function activeCount(l: QueueItem[]): number {
  return l.filter((i) => i.state === 'queued' || i.state === 'running' || i.state === 'paused').length;
}

export type NewJob = Pick<QueueItem, 'url' | 'title' | 'thumbnail' | 'platform' | 'uploader' | 'outDir' | 'formatId' | 'audioOnly' | 'formatLabel' | 'estSize'>;

export function enqueue(job: NewJob): string {
  const item: QueueItem = {
    ...job, id: newId(), state: 'queued', stage: null, percent: null, downloadedBytes: null, totalBytes: null,
    speedBps: null, etaSec: null, filePath: null, fileSize: null, errorCode: null, addedAt: new Date().toISOString(),
  };
  queue.set((l) => [...l, item]);
  pump();
  return item.id;
}

let quitting = false;
/** App is really quitting: pause everything and never start another job. */
export function pauseAllForQuit() {
  quitting = true;
  for (const i of queue.get()) if (i.state === 'running') void pause(i.id);
}

export function pump() {
  if (quitting) return;
  const limit = settings.get().concurrency;
  for (;;) {
    const l = queue.get();
    if (l.filter((i) => i.state === 'running').length >= limit) return;
    const next = l.find((i) => i.state === 'queued');
    if (!next) return;
    void start(next);
  }
}

// Douyin: the queue item keeps the douyin.com link; the signed CDN link the
// server resolved it to lives only here (never persisted, never logged).
const resolved = new Map<string, { v: DouyinVideo; at: number }>();
const refreshed = new Set<string>(); // jobs that already got their one fresh link after an HTTP 403
const forget = (id: string) => { resolved.delete(id); refreshed.delete(id); };

async function douyinArgs(item: QueueItem) {
  let r = resolved.get(item.id);
  if (!r || isExpired(r.v, r.at)) {
    r = { v: await douyinVideo(item.url), at: Date.now() }; // counts one download on the server
    resolved.set(item.id, r);
    patch(item.id, { title: r.v.title || item.title, thumbnail: r.v.thumbnail ?? item.thumbnail, uploader: r.v.uploader ?? item.uploader });
  }
  const v = r.v;
  const safeId = v.id.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);
  return {
    url: item.audioOnly && v.audioUrl ? v.audioUrl : v.directUrl,
    formatId: undefined, // a direct file has one format; height selectors would match nothing
    headers: toHeaderList(v.headers),
    fileTitle: v.title || item.title, fileId: safeId || item.id.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 12),
  };
}

async function start(item: QueueItem) {
  patch(item.id, { state: 'running', pausing: false, stage: 'downloading', errorCode: null });
  try {
    const extra = isDouyinUrl(item.url) ? await douyinArgs(item) : {};
    if (!queue.get().some((i) => i.id === item.id && i.state === 'running')) return; // cancelled / paused while resolving
    await api.startDownload({ jobId: item.id, url: item.url, outDir: item.outDir, formatId: item.formatId, audioOnly: item.audioOnly, ...extra });
  } catch (e) {
    finishFailed(item.id, toAppError(e).code);
  }
}

function toHistory(i: QueueItem, state: 'completed' | 'failed', extra: Partial<HistoryItem>): HistoryItem {
  return {
    id: i.id, url: i.url, title: i.title, platform: i.platform, formatLabel: i.formatLabel,
    filePath: null, fileSize: null, state, errorCode: null, createdAt: i.addedAt,
    finishedAt: new Date().toISOString(), synced: false, ...extra,
  };
}

function finishFailed(id: string, code: string) {
  const it = queue.get().find((i) => i.id === id);
  if (!it) return;
  forget(id);
  patch(id, { state: 'failed', errorCode: code, speedBps: null, etaSec: null, pausing: false });
  void api.historyAdd(toHistory(it, 'failed', { errorCode: code })).then(() => syncSoon()).catch(() => {});
  toast('error', `Tải thất bại: ${it.title}. ${errorMessage(code)}`);
  pump();
}

let started = false;
export async function initQueue() {
  if (started) return;
  started = true;
  await onProgress((e) => {
    const it = queue.get().find((i) => i.id === e.jobId);
    if (!it || it.state !== 'running') return;
    patch(e.jobId, {
      stage: e.stage, percent: e.percent, downloadedBytes: e.downloadedBytes,
      totalBytes: e.totalBytes ?? it.totalBytes, speedBps: e.speedBps, etaSec: e.etaSec,
    });
  });
  await onDone((e) => {
    const it = queue.get().find((i) => i.id === e.jobId);
    if (!it) return; // cancelled & already removed, or unknown job
    switch (e.state) {
      case 'completed':
        forget(e.jobId);
        patch(e.jobId, { state: 'completed', percent: 100, stage: null, speedBps: null, etaSec: null, filePath: e.filePath ?? null, fileSize: e.fileSize ?? it.totalBytes, pausing: false });
        void api.historyAdd(toHistory(it, 'completed', { filePath: e.filePath ?? null, fileSize: e.fileSize ?? it.totalBytes })).then(() => syncSoon()).catch(() => {});
        toast('success', `Đã tải xong: ${it.title}`);
        pump();
        break;
      case 'failed':
        if (e.errorCode === 'forbidden' && isDouyinUrl(it.url) && !refreshed.has(e.jobId)) {
          // The signed link expired or was refused: ask the server for a new one, once.
          refreshed.add(e.jobId);
          resolved.delete(e.jobId);
          patch(e.jobId, { state: 'queued', stage: null, speedBps: null, etaSec: null, pausing: false, errorCode: null });
          pump();
          break;
        }
        finishFailed(e.jobId, e.errorCode ?? 'unknown');
        break;
      case 'paused':
        patch(e.jobId, { state: 'paused', pausing: false, speedBps: null, etaSec: null });
        pump();
        break;
      case 'cancelled':
        forget(e.jobId);
        queue.set((l) => l.filter((i) => i.id !== e.jobId));
        pump();
        break;
    }
  });
  pump();
}

export async function pause(id: string) {
  patch(id, { pausing: true });
  try {
    await api.pauseDownload(id);
  } catch (e) {
    patch(id, { pausing: false });
    toast('error', errorMessage(toAppError(e).code));
  }
}

export function resume(id: string) {
  patch(id, { state: 'queued' });
  pump();
}

export function retry(id: string) {
  forget(id);
  patch(id, { state: 'queued', errorCode: null, percent: null, downloadedBytes: null, speedBps: null, etaSec: null });
  pump();
}

export async function cancel(id: string) {
  const it = queue.get().find((i) => i.id === id);
  if (!it) return;
  forget(id);
  queue.set((l) => l.filter((i) => i.id !== id));
  if (it.state === 'running' || it.state === 'paused') {
    try { await api.cancelDownload(id); } catch { /* process may already be gone */ }
  }
  pump();
}

export function removeItem(id: string) {
  queue.set((l) => l.filter((i) => i.id !== id));
}

export function clearFinished() {
  queue.set((l) => l.filter((i) => i.state !== 'completed' && i.state !== 'failed'));
}

// Re-run the scheduler when the concurrency setting is raised.
let lastLimit = settings.get().concurrency;
settings.subscribe(() => {
  const c = settings.get().concurrency;
  if (c !== lastLimit) {
    lastLimit = c;
    pump();
  }
});
