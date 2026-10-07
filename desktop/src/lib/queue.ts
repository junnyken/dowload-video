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
import { claimForStart, clearGate, quotaGate, refreshQuota, settle } from './quota';
import { gatePaused } from './quota-core';
import { cookieState, planFor, refreshCookieStatus } from './cookies';
import {
  cookieErrorCode, cookiePlatformOf, douyinLocalArgs, pickNext, settleThenFallback, shouldFallbackToServer, type Plan, type Route,
} from './cookies-core';

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
  /** Server claim for the daily allowance (quota.ts); NO_CLAIM = nothing to settle. Persisted, so a resume after a restart does not claim twice. */
  claimId?: string | null;
  /** Route of the current/last run (PLAN-32D §2). 'server' is sticky once a Douyin job fell back to it. */
  route?: Route;
  /** The one Douyin L1 -> server fallback already happened. */
  cookieFallback?: boolean;
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
  if (gatePaused(quotaGate.get(), Date.now())) return; // the server refused (daily limit): wait for sign-in / retry / next day
  const limit = settings.get().concurrency;
  for (;;) {
    // At most one running job per platform that uses the user's cookies (PLAN-32D §3.3).
    const next = pickNext(queue.get(), limit, planOf);
    if (!next) return;
    void start(next);
  }
}

const SERVER: Plan = { route: 'server', useCookies: false, platform: 'douyin' };
/** Route for this run: a Douyin job that already fell back stays on the server; otherwise decided now. */
function planOf(item: QueueItem): Plan {
  return item.route === 'server' && isDouyinUrl(item.url) ? SERVER : planFor(item.url);
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
    void refreshQuota(); // the server counted it itself (no claim to settle): update the badge now
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
  const plan = planOf(item);
  // Synchronous, before any await: pump() reads `route` to keep one cookie job per platform.
  patch(item.id, { state: 'running', pausing: false, stage: 'downloading', errorCode: null, route: plan.route });
  try {
    let extra = {};
    if (plan.route === 'server') {
      if (item.claimId) {
        // An earlier L1 attempt claimed locally but its cookies are gone now: refund before the server counts.
        await settle(item.claimId, 'cancelled');
        patch(item.id, { claimId: null });
      }
      extra = await douyinArgs(item); // the server already counts this one
    } else {
      if (!item.claimId) {
        const v = await claimForStart(item.url, plan.route); // daily allowance (PLAN-32D §5); resumed items keep their claim
        if (!v.ok) { refusedByQuota(item.id); return; }
        patch(item.id, { claimId: v.claimId });
        if (!queue.get().some((i) => i.id === item.id)) { void settle(v.claimId, 'cancelled'); return; } // cancelled while claiming
      }
      if (isDouyinUrl(item.url)) {
        // Douyin L1 (0.7.2): yt-dlp's Douyin extractor cannot sign Douyin's API
        // ("Fresh cookies are needed"), so a hidden Douyin page with the user's
        // cookies resolves the direct link, and yt-dlp downloads that link.
        let r;
        try {
          r = await api.douyinResolveLocal(item.url, settings.get().douyinDebugWindow);
        } catch (e) {
          const cur = queue.get().find((i) => i.id === item.id);
          if (cur && cur.state === 'running') void fallBackToServer(cur, toAppError(e).code);
          return;
        }
        patch(item.id, { title: r.title || item.title, uploader: r.author ?? item.uploader });
        extra = douyinLocalArgs(r, item);
      } else if (plan.useCookies) {
        extra = { useCookies: true };
      }
    }
    if (!queue.get().some((i) => i.id === item.id && i.state === 'running')) return; // cancelled / paused while resolving
    await api.startDownload({ jobId: item.id, url: item.url, outDir: item.outDir, formatId: item.formatId, audioOnly: item.audioOnly, ...extra });
  } catch (e) {
    finishFailed(item.id, toAppError(e).code);
  }
}

/**
 * Douyin L1 failed: the hidden page gave no link (timeout, verification, no
 * saved cookies) or the CDN refused the link (forbidden). Refund the local
 * claim FIRST (the server route counts on its own), then run the same job once
 * through the server.
 */
/** Why the local Douyin route failed, in the toast (debug, owner test 2026-10-07). */
function douyinLocalReason(code: string): string {
  // wording: BA review
  switch (code) {
    case 'timeout': return 'trang Douyin không trả video kịp';
    case 'forbidden': return 'Douyin yêu cầu xác minh';
    case 'not_found': return 'trang không có link video';
    case 'cookie_required': return 'chưa kết nối Douyin';
    case 'private_or_login': return 'phiên đăng nhập';
    default: return `mã lỗi: ${code}`;
  }
}

async function fallBackToServer(it: QueueItem, code: string) {
  forget(it.id);
  patch(it.id, { cookieFallback: true, stage: null, speedBps: null, etaSec: null, pausing: false }); // stays 'running' meanwhile
  void refreshCookieStatus();
  const claimId = it.claimId;
  const moved = await settleThenFallback(
    () => settle(claimId, 'failed', code),
    () => queue.get().some((i) => i.id === it.id && i.state === 'running'),
    () => patch(it.id, { claimId: null, route: 'server', state: 'queued', errorCode: null }),
  );
  // wording: BA review
  if (moved) toast('info', code === 'private_or_login'
    ? `Phiên Douyin trên máy có thể đã hết hạn. Đang tải lại qua máy chủ VidGrab: ${it.title}`
    : `Không tải được video Douyin trực tiếp trên máy này (${douyinLocalReason(code)}). Đang tải qua máy chủ VidGrab: ${it.title}`); // wording: BA review
  pump();
}

function toHistory(i: QueueItem, state: 'completed' | 'failed', extra: Partial<HistoryItem>): HistoryItem {
  return {
    id: i.id, url: i.url, title: i.title, platform: i.platform, formatLabel: i.formatLabel,
    filePath: null, fileSize: null, state, errorCode: null, createdAt: i.addedAt,
    finishedAt: new Date().toISOString(), synced: false, ...extra,
  };
}

/** The daily limit refused this item: it fails with the server's own text. No history row, no toast per item (quota.ts shows one). */
function refusedByQuota(id: string) {
  patch(id, { state: 'failed', errorCode: 'api:quota_exceeded_daily', speedBps: null, etaSec: null, pausing: false, stage: null });
  pump();
}

function finishFailed(id: string, code: string) {
  const it = queue.get().find((i) => i.id === id);
  if (!it) return;
  forget(id);
  void settle(it.claimId, 'failed', code); // refund when the server allows it
  patch(id, { claimId: null });
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
        void settle(it.claimId, 'completed');
        patch(e.jobId, { state: 'completed', percent: 100, stage: null, speedBps: null, etaSec: null, filePath: e.filePath ?? null, fileSize: e.fileSize ?? it.totalBytes, pausing: false });
        void api.historyAdd(toHistory(it, 'completed', { filePath: e.filePath ?? null, fileSize: e.fileSize ?? it.totalBytes })).then(() => syncSoon()).catch(() => {});
        toast('success', `Đã tải xong: ${it.title}`);
        pump();
        break;
      case 'failed':
        if (shouldFallbackToServer(it, e.errorCode ?? 'unknown')) {
          void fallBackToServer(it, e.errorCode ?? 'unknown');
          break;
        }
        if (e.errorCode === 'forbidden' && isDouyinUrl(it.url) && it.route === 'server' && !refreshed.has(e.jobId)) {
          // The signed link expired or was refused: ask the server for a new one, once.
          refreshed.add(e.jobId);
          resolved.delete(e.jobId);
          patch(e.jobId, { state: 'queued', stage: null, speedBps: null, etaSec: null, pausing: false, errorCode: null });
          pump();
          break;
        }
        if (e.cookiesUsed) void refreshCookieStatus(); // Rust may have flagged the blob
        finishFailed(e.jobId, cookieErrorCode(e.errorCode ?? 'unknown', {
          cookiesUsed: !!e.cookiesUsed, platform: cookiePlatformOf(it.url), enabled: cookieState.get().enabled ?? [],
        }));
        break;
      case 'paused':
        patch(e.jobId, { state: 'paused', pausing: false, speedBps: null, etaSec: null });
        pump();
        break;
      case 'cancelled':
        forget(e.jobId);
        void settle(it.claimId, 'cancelled');
        queue.set((l) => l.filter((i) => i.id !== e.jobId));
        pump();
        break;
    }
  });
  quotaGate.subscribe(() => { if (!gatePaused(quotaGate.get(), Date.now())) pump(); });
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
  clearGate();
  patch(id, { state: 'queued' });
  pump();
}

export function retry(id: string) {
  forget(id);
  clearGate();
  patch(id, { state: 'queued', claimId: null, route: undefined, cookieFallback: false, errorCode: null, percent: null, downloadedBytes: null, speedBps: null, etaSec: null });
  pump();
}

export async function cancel(id: string) {
  const it = queue.get().find((i) => i.id === id);
  if (!it) return;
  forget(id);
  void settle(it.claimId, 'cancelled');
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
