// Download queue: persisted in localStorage, scheduled by the concurrency
// setting, driven by Rust events (download://progress|done).
import { createStore } from './store';
import { api, onDone, onProgress } from './tauri';
import { settings } from './settings';
import { errorMessage, rememberServerMessage, toAppError } from './errors';
import { toast } from './ui';
import { newId } from './format';
import type { HistoryItem, Stage } from './types';
import { syncSoon } from './sync';
import { douyinVideo, isExpired, toHeaderList, type DouyinVideo } from './douyin';
import { isDouyinUrl } from './urls';
import { claimForStart, clearGate, quotaGate, refreshQuota, refuseFromServer, settle } from './quota';
import { gatePaused, tokenStale } from './quota-core';
import { cookieState, refreshCookieStatus, routesFor } from './cookies';
import { cookieErrorCode, cookiePlatformOf, pickNext, settleThenFallback, type Plan, type Route } from './cookies-core';
import {
  claimRoute, currentStep, isLocalStep, legacyRoute, nextRoute, triedBefore, type FetchLinkOutcome, type RouteId,
} from './routes-core';
import { fetchLink } from './fetchlink';
import { capForGuest } from './quality';
import { auth } from './auth';

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
  /** PLAN-32E P3: the server's signed token for claimId; Rust's start_download checks it. */
  claimToken?: string | null;
  /** Old route name of the current/last run (0.7.x; pickNext keeps one 'local_cookie' job per platform). */
  route?: Route;
  /** 0.7.x: the one Douyin L1 -> server fallback already happened. Unused since 0.8.0 (Douyin is server-only). */
  cookieFallback?: boolean;
  /** Route step of the current/last run (PLAN-32D §2, routes-core.ts): shown on the card. */
  step?: RouteId;
  /** Steps this job already used (each step runs at most once per job). */
  tried?: RouteId[];
  /** Quality preset picked by the user ('best' | '1080' | ... | 'audio'): the quality asked from the server on S0. */
  quality?: string;
  /** The server's own text for this item's error (channel claim-batch refusal); wins over errorMessage(errorCode). */
  errorText?: string | null;
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

export type NewJob = Pick<QueueItem, 'url' | 'title' | 'thumbnail' | 'platform' | 'uploader' | 'outDir' | 'formatId' | 'audioOnly' | 'formatLabel' | 'estSize'> & {
  quality?: string;
  /** A claim made before enqueueing (channel claim-batch): start() does not claim again. */
  claimId?: string | null;
  claimToken?: string | null;
  /** 'S0': skip the local steps (the local analysis already failed and the user chose the server). */
  startAt?: 'S0';
};

export function enqueue(job: NewJob): string {
  const { startAt, claimId, claimToken, ...rest } = job;
  const item: QueueItem = {
    ...rest, id: newId(), state: 'queued', stage: null, percent: null, downloadedBytes: null, totalBytes: null,
    speedBps: null, etaSec: null, filePath: null, fileSize: null, errorCode: null, addedAt: new Date().toISOString(),
    claimId: claimId || null, claimToken: claimToken || null, tried: startAt === 'S0' ? ['L0', 'L1'] : [],
  };
  queue.set((l) => [...l, item]);
  pump();
  return item.id;
}

/** A channel video the daily allowance refused (claim-batch): shown as failed with the server's reason, never started. */
export function enqueueRefused(job: NewJob, detail: string | null): string {
  const { startAt: _s, claimId: _c, claimToken: _t, ...rest } = job;
  const item: QueueItem = {
    ...rest, id: newId(), state: 'failed', stage: null, percent: null, downloadedBytes: null, totalBytes: null,
    speedBps: null, etaSec: null, filePath: null, fileSize: null, errorCode: 'api:quota_exceeded_daily', errorText: detail,
    addedAt: new Date().toISOString(), claimId: null, tried: [],
  };
  queue.set((l) => [...l, item]);
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

/** The step this item runs next (or is running): the first step of its plan it has not used yet. */
function stepOf(item: QueueItem): RouteId | null {
  return currentStep(routesFor(item.url), item.tried ?? []);
}
/** pickNext's view: the old route name of the step this item would take now. */
function planOf(item: QueueItem): Plan {
  const step = stepOf(item) ?? 'L0';
  return { route: legacyRoute(step), useCookies: step === 'L1', platform: cookiePlatformOf(item.url) };
}

// Douyin: the queue item keeps the douyin.com link; the signed CDN link the
// server resolved it to lives only here (never persisted, never logged).
const resolved = new Map<string, { v: DouyinVideo; at: number }>();
const refreshed = new Set<string>(); // jobs that already got their one fresh link after an HTTP 403
// S0: the server's answer for a job (a resume after a pause reuses it; the server keeps its file 2 h).
const serverLinks = new Map<string, { v: Extract<FetchLinkOutcome, { kind: 'ok' }>; at: number }>();
const SERVER_LINK_TTL = 60 * 60_000;
const forget = (id: string) => { resolved.delete(id); refreshed.delete(id); serverLinks.delete(id); };

async function douyinArgs(item: QueueItem) {
  let r = resolved.get(item.id);
  if (!r || isExpired(r.v, r.at) || tokenStale(r.v.vgToken, Date.now())) {
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
    ...(v.vgToken ? { claimToken: v.vgToken } : {}), // PLAN-32E P3: signed by the server for these CDN links
  };
}

const fileIdOf = (item: QueueItem) => item.id.replace(/[^A-Za-z0-9_-]/g, '').slice(0, 12) || 'video';

/**
 * S0: POST /fetch-link, then the app downloads the server's file (or direct
 * link) itself. null = the server refused by the daily allowance (the item is
 * failed and the queue gated, like a refused claim). Other errors throw.
 */
async function serverArgs(item: QueueItem) {
  let r = serverLinks.get(item.id);
  if (!r || Date.now() - r.at > SERVER_LINK_TTL) {
    const out = await fetchLink(item); // counts one download on the server (once per URL a day)
    if (out.kind === 'quota') {
      refuseFromServer(out.refusal);
      refusedByQuota(item.id);
      return null;
    }
    if (out.kind === 'error') {
      rememberServerMessage(out.code, out.message);
      throw { code: out.code, message: out.message };
    }
    void refreshQuota(); // the server counted it itself (no claim to settle): update the badge now
    r = { v: out, at: Date.now() };
    serverLinks.set(item.id, r);
    patch(item.id, { title: out.title || item.title, thumbnail: item.thumbnail ?? out.thumbnail });
    if (out.note) toast('info', `${out.title || item.title}: ${out.note}`); // server's own sentence (task #6134)
  }
  return {
    url: r.v.url,
    formatId: undefined, // one file: height selectors would match nothing
    headers: r.v.headers,
    fileTitle: r.v.title || item.title, fileId: fileIdOf(item),
    useCookies: false,
    ...(r.v.vgToken ? { claimToken: r.v.vgToken } : {}), // PLAN-32E P3 (a direct link needs it; the API host's file does not)
  };
}

async function start(item: QueueItem) {
  const step = stepOf(item);
  if (!step) { finishFailed(item.id, item.errorCode ?? 'unknown'); return; } // every step of its plan was used
  // Synchronous, before any await: pump() reads `route` to keep one cookie job per platform.
  patch(item.id, { state: 'running', pausing: false, stage: 'downloading', errorCode: null, step, route: legacyRoute(step) });
  try {
    let extra = {};
    if (step === 'DOUYIN_SERVER' || step === 'S0') {
      if (item.claimId) {
        // A local claim is still open (e.g. the cookies were removed meanwhile): refund BEFORE the server counts.
        await settle(item.claimId, 'failed', 'server_fallback');
        patch(item.id, { claimId: null, claimToken: null });
      }
      if (step === 'DOUYIN_SERVER') {
        extra = await douyinArgs(item); // the server already counts this one
      } else {
        const a = await serverArgs(item);
        if (!a) return;
        extra = a;
      }
    } else {
      // A resumed item whose signed token ran out (2 h) claims again: Rust would refuse the old one,
      // and the server counts the same URL only once a day (PLAN-32E P3).
      if (!item.claimId || tokenStale(item.claimToken, Date.now())) {
        const v = await claimForStart(item.url, claimRoute(step)); // daily allowance (PLAN-32D §5); resumed items keep their claim
        if (!v.ok) { refusedByQuota(item.id); return; }
        patch(item.id, { claimId: v.claimId, claimToken: v.token ?? null });
        if (!queue.get().some((i) => i.id === item.id)) { void settle(v.claimId, 'cancelled'); return; } // cancelled while claiming
      }
      const tok = queue.get().find((i) => i.id === item.id)?.claimToken;
      extra = tok ? { claimToken: tok } : {};
      if (step === 'L1') extra = { ...extra, useCookies: true };
    }
    if (!queue.get().some((i) => i.id === item.id && i.state === 'running')) return; // cancelled / paused while resolving
    // a guest's local download stops at 1080p (server-route steps override formatId in `extra`)
    const formatId = capForGuest(item.formatId, item.audioOnly, auth.get().status !== 'in');
    await api.startDownload({ jobId: item.id, url: item.url, outDir: item.outDir, formatId, audioOnly: item.audioOnly, ...extra });
  } catch (e) {
    finishFailed(item.id, toAppError(e).code);
  }
}

/**
 * A local step failed and the route table has a next step (routes-core
 * nextRoute). L0 -> L1 keeps the claim (same URL, same day). L0/L1 -> S0
 * refunds the local claim FIRST and only then queues the server step: the
 * server counts /fetch-link on its own, an open claim would count twice.
 */
async function moveOn(it: QueueItem, plan: RouteId[], next: RouteId, code: string) {
  forget(it.id);
  const tried = triedBefore(plan, it.tried ?? [], next);
  const reset = { stage: null, speedBps: null, etaSec: null, pausing: false, percent: null, downloadedBytes: null, totalBytes: null } as const;
  if (next === 'L1') {
    patch(it.id, { ...reset, tried, step: next, route: legacyRoute(next), state: 'queued', errorCode: null });
    pump();
    return;
  }
  patch(it.id, reset); // stays 'running' while the refund is sent
  const claimId = it.claimId;
  const moved = await settleThenFallback(
    () => settle(claimId, 'failed', code),
    () => queue.get().some((i) => i.id === it.id && i.state === 'running'),
    () => patch(it.id, { claimId: null, claimToken: null, tried, step: next, route: legacyRoute(next), state: 'queued', errorCode: null }),
  );
  // wording: BA review
  if (moved) toast('info', `Không tải được trực tiếp trên máy này. Đang thử qua máy chủ VidGrab: ${it.title}`);
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
  patch(id, { claimId: null, claimToken: null });
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
      case 'failed': {
        const code = e.errorCode ?? 'unknown';
        if (isLocalStep(it.step)) {
          const plan = routesFor(it.url);
          const next = nextRoute(plan, it.tried ?? [], it.step, code);
          if (next) {
            if (e.cookiesUsed) void refreshCookieStatus(); // Rust may have flagged the blob
            void moveOn(it, plan, next, code);
            break;
          }
        }
        if (code === 'forbidden' && isDouyinUrl(it.url) && it.route === 'server' && !refreshed.has(e.jobId)) {
          // The signed link expired or was refused: ask the server for a new one, once.
          refreshed.add(e.jobId);
          resolved.delete(e.jobId);
          patch(e.jobId, { state: 'queued', stage: null, speedBps: null, etaSec: null, pausing: false, errorCode: null });
          pump();
          break;
        }
        if (e.cookiesUsed) void refreshCookieStatus(); // Rust may have flagged the blob
        finishFailed(e.jobId, cookieErrorCode(code, {
          cookiesUsed: !!e.cookiesUsed, platform: cookiePlatformOf(it.url), enabled: cookieState.get().enabled ?? [],
        }));
        break;
      }
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
  patch(id, { state: 'queued', claimId: null, claimToken: null, route: undefined, step: undefined, tried: [], cookieFallback: false, errorCode: null, errorText: null, percent: null, downloadedBytes: null, speedBps: null, etaSec: null });
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
