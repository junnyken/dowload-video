// Channel Watch ("Theo dõi kênh", Phase 33B, task #6259): server calls,
// stores, the background poll and the one-tap download. Pure rules live in
// watch-core.ts. Every call carries the user's token (signed-in only).
import { createStore } from './store';
import { apiFetch } from './http';
import { auth, getAccessToken } from './auth';
import { settings, ensureOutDir } from './settings';
import { enqueueWithClaims, joinPath, notifySafe, refusedText, sanitizeFolderName } from './channels';
import { go, nav, toast } from './ui';
import {
  RESUME_MIN_GAP_MS, applyPoll, batchByChannel, emptyLocal, markDone, mergeItems, nextPollDelay, notifyText, oneTap, parseItems,
  parseStatus, parseSubscriptions, readLocal, serverMessage, watchVisible, writeLocal,
  type StatusResult, type WatchItem, type WatchLocal, type WatchSubscription,
} from './watch-core';

const BASE = '/api/v1/watch';
const LOCAL_KEY = 'vg.watch';

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';
type Answer = { status: number; data: unknown };

/** Network failure -> status 0. A 401 is retried once with a refreshed token. No token -> 401 without calling. */
async function call(path: string, method: Method = 'GET', body?: unknown): Promise<Answer> {
  try {
    let token = await getAccessToken();
    if (!token) return { status: 401, data: null };
    let r = await apiFetch(BASE + path, { method, body, token });
    if (r.status === 401) {
      token = await getAccessToken(true);
      if (!token) return { status: 401, data: r.data };
      r = await apiFetch(BASE + path, { method, body, token });
    }
    return { status: r.status, data: r.data };
  } catch {
    return { status: 0, data: null };
  }
}

// ---- stores ---------------------------------------------------------------------------

export const watchStatus = createStore<StatusResult | null>(null);
export const watchSubs = createStore<WatchSubscription[]>([]);
export const watchItems = createStore<WatchItem[]>([]);
export const watchLocal = createStore<WatchLocal>(emptyLocal());
export const watchLoading = createStore(false);

export const isWatchVisible = () => watchVisible(auth.get().status, watchStatus.get());

const userKey = () => auth.get().userId || auth.get().email || 'unknown';

function loadLocal() {
  try { watchLocal.set(readLocal(localStorage.getItem(LOCAL_KEY), userKey())); } catch { watchLocal.set(emptyLocal()); }
}
function saveLocal(l: WatchLocal) {
  watchLocal.set(l);
  try { localStorage.setItem(LOCAL_KEY, writeLocal(localStorage.getItem(LOCAL_KEY), userKey(), l)); } catch { /* storage unavailable */ }
}

// ---- server calls ------------------------------------------------------------------------

export async function refreshStatus(): Promise<StatusResult> {
  const r = await call('/status');
  const s = parseStatus(r.status, r.data);
  if (s.kind === 'error' && watchStatus.get()?.kind === 'ok') return watchStatus.get() as StatusResult; // keep what we knew
  watchStatus.set(s);
  return s;
}

/** Subscriptions + the last 7 days of new items (the screen). */
export async function refreshAll(): Promise<void> {
  watchLoading.set(true);
  try {
    const st = await refreshStatus();
    if (st.kind !== 'ok') return;
    const [s, i] = await Promise.all([call('/subscriptions'), call('/items')]);
    if (s.status === 200) watchSubs.set(parseSubscriptions(s.data));
    if (i.status === 200) watchItems.set(mergeItems(parseItems(i.data), []));
  } finally {
    watchLoading.set(false);
  }
}

/** wording: BA review */
const GENERIC = 'Có lỗi xảy ra. Vui lòng thử lại.';
const LOGIN = 'Đăng nhập để theo dõi kênh.';

export type ActionResult = { ok: true; message?: string } | { ok: false; message: string };

function failed(r: Answer): ActionResult {
  if (r.status === 0) return { ok: false, message: 'Không kết nối được máy chủ. Vui lòng kiểm tra mạng.' }; // wording: BA review
  if (r.status === 401) return { ok: false, message: LOGIN };
  return { ok: false, message: serverMessage(r.data, GENERIC) };
}

export async function addSource(url: string, mode: 'one_tap' | 'notify_only'): Promise<ActionResult> {
  const r = await call('/sources', 'POST', { url, mode });
  if (r.status !== 200 && r.status !== 201) return failed(r);
  await refreshAll();
  // wording: BA review
  return { ok: true, message: (r.data as { already_subscribed?: boolean })?.already_subscribed ? 'Bạn đã theo dõi kênh này rồi.' : 'Đã theo dõi kênh.' };
}

export async function setPaused(id: string, paused: boolean): Promise<ActionResult> {
  const r = await call(`/subscriptions/${encodeURIComponent(id)}`, 'PATCH', { status: paused ? 'paused' : 'active' });
  if (r.status !== 200) return failed(r);
  await refreshAll();
  return { ok: true };
}

export async function removeSource(id: string): Promise<ActionResult> {
  const r = await call(`/subscriptions/${encodeURIComponent(id)}`, 'DELETE');
  if (r.status !== 200) return failed(r);
  await refreshAll();
  return { ok: true };
}

// ---- background poll ----------------------------------------------------------------------

let lastPollAt = 0;
let polling: Promise<void> | null = null;

/** GET /items?since=<last seen>: one notification per channel for ids never announced before. */
export function pollNow(): Promise<void> {
  polling ??= (async () => {
    if (auth.get().status !== 'in') return;
    const st = await refreshStatus();
    if (!watchVisible('in', st)) return;
    lastPollAt = Date.now();
    const local = watchLocal.get();
    const r = await call(local.since ? `/items?since=${encodeURIComponent(local.since)}` : '/items');
    if (r.status !== 200) return;
    const items = parseItems(r.data);
    const out = applyPoll(local, items, new Date().toISOString());
    saveLocal(out.local); // persisted BEFORE notifying: a crash cannot cause a second notification
    watchItems.set((cur) => mergeItems(items, cur));
    for (const b of batchByChannel(out.toNotify)) {
      const t = notifyText(b);
      notifySafe(t.title, t.body);
    }
  })().finally(() => { polling = null; });
  return polling;
}

let timer: ReturnType<typeof setTimeout> | null = null;
function schedule() {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { timer = null; void pollNow().finally(schedule); }, nextPollDelay());
}

function onResume() {
  if (auth.get().status === 'in' && Date.now() - lastPollAt > RESUME_MIN_GAP_MS) void pollNow();
}

let started = false;
let lastUser: string | null = null;
/** App start: poll after sign-in is known, then every ~15 min, and when the window comes back / the network returns. */
export function startWatch() {
  if (started) return;
  started = true;
  const onAuth = () => {
    const a = auth.get();
    if (a.status === 'loading') return;
    const key = a.status === 'in' ? userKey() : null;
    if (key === lastUser) return;
    lastUser = key;
    watchSubs.set([]);
    watchItems.set([]);
    if (!key) {
      watchStatus.set(null);
      watchLocal.set(emptyLocal());
      if (nav.get().screen === 'watch') go('download'); // the entry is gone with the session
      return;
    }
    loadLocal();
    void pollNow();
  };
  auth.subscribe(onAuth);
  onAuth();
  schedule();
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') onResume(); });
  window.addEventListener('online', onResume);
  window.addEventListener('focus', onResume);
}

// ---- one-tap ---------------------------------------------------------------------------------

/** "Tải": the videos go through the normal channel path (allowance claimed, local queue). */
export async function downloadItems(items: WatchItem[]): Promise<void> {
  const base = await ensureOutDir();
  if (!base) { toast('error', 'Chưa chọn được thư mục lưu. Hãy chọn thư mục trong Cài đặt.'); return; } // wording: BA review
  const res = await oneTap(items, watchLocal.get(), {
    quality: settings.get().defaultQuality,
    outDirFor: (channel) => joinPath(base, sanitizeFolderName(channel.replace(/^@/, ''))),
  }, enqueueWithClaims);
  saveLocal(res.local);
  if (res.enqueued) toast('success', `Đã thêm ${res.enqueued} video vào hàng đợi.`); // wording: BA review
  // Refused (no downloads left today) stay under "Video mới", like the Channels review list.
  if (res.refused.length) toast('error', refusedText(res.refused.length, res.refused[0].detail));
}

/** "Bỏ qua": hide from "Video mới" without downloading. */
export function dismissItems(itemIds: string[]) {
  saveLocal(markDone(watchLocal.get(), itemIds));
}
