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
  RESUME_MIN_GAP_MS, applyPoll, batchByChannel, emptyLocal, markDone, mergeItems, nextPollDelay, notifyText, oneTap, parseInbox, parseItems,
  parseStatus, parseSubscriptions, readLocal, serverMessage, watchVisible, writeLocal,
  type Inbox, type InboxItem, type StatusResult, type WatchItem, type WatchLocal, type WatchSubscription,
} from './watch-core';

const BASE = '/api/v1/watch';
const LOCAL_KEY = 'vg.watch';

type Method = 'GET' | 'POST' | 'PATCH' | 'DELETE';
const CALL_TIMEOUT_MS = 20_000; // watch calls are small; a silent host must not spin the screen
type Answer = { status: number; data: unknown };

/** Network failure -> status 0. A 401 is retried once with a refreshed token. No token -> 401 without calling. */
async function call(path: string, method: Method = 'GET', body?: unknown): Promise<Answer> {
  try {
    let token = await getAccessToken();
    if (!token) return { status: 401, data: null };
    let r = await apiFetch(BASE + path, { method, body, token, timeoutMs: CALL_TIMEOUT_MS });
    if (r.status === 401) {
      token = await getAccessToken(true);
      if (!token) return { status: 401, data: r.data };
      r = await apiFetch(BASE + path, { method, body, token, timeoutMs: CALL_TIMEOUT_MS });
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
export const watchInbox = createStore<Inbox>({ items: [], unread: 0, ready: true });
export const watchLocal = createStore<WatchLocal>(emptyLocal());
export const inboxQuery = createStore<InboxQuery>({ subscriptionId: '', unreadOnly: false });
export const watchLoading = createStore(false);

export const isWatchVisible = () => watchVisible(auth.get().status, watchStatus.get(), watchLocal.get().known === true);

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
  // Remember per account whether the entry exists, so an outage keeps it.
  const known = watchLocal.get().known === true;
  if (s.kind === 'ok' && watchVisible('in', s) && !known) saveLocal({ ...watchLocal.get(), known: true });
  if ((s.kind === 'off' || (s.kind === 'ok' && !watchVisible('in', s))) && known) saveLocal({ ...watchLocal.get(), known: false });
  return s;
}

export type InboxQuery = { subscriptionId?: string; unreadOnly?: boolean };

/** GET /watch/inbox (newest first, last 30 days). Keeps the previous inbox on failure. */
export async function fetchInbox(q: InboxQuery = {}): Promise<Inbox | null> {
  const p = new URLSearchParams();
  if (q.subscriptionId) p.set('subscription_id', q.subscriptionId);
  if (q.unreadOnly) p.set('unread', 'true');
  const qs = p.toString();
  const r = await call(`/inbox${qs ? `?${qs}` : ''}`);
  if (r.status !== 200) return null;
  const inbox = parseInbox(r.data);
  watchInbox.set(inbox);
  return inbox;
}

/** POST /watch/inbox/read. Optimistic; reverts silently on error unless `onError` wants the server text. */
export async function markInboxRead(target: string[] | 'all', onError?: (message: string) => void): Promise<boolean> {
  if (target !== 'all' && target.length === 0) return true;
  const prev = watchInbox.get();
  const set = target === 'all' ? null : new Set(target);
  const items = prev.items.map((i) => (!set || set.has(i.id) ? { ...i, read: true } : i));
  const newlyRead = prev.items.filter((i, k) => !i.read && items[k].read).length;
  watchInbox.set({ ...prev, items, unread: target === 'all' ? 0 : Math.max(0, prev.unread - newlyRead) });
  const r = await call('/inbox/read', 'POST', target === 'all' ? { all: true } : { item_ids: target });
  if (r.status === 200) {
    const u = (r.data as { unread?: unknown })?.unread;
    if (typeof u === 'number' && Number.isFinite(u)) watchInbox.set((cur) => ({ ...cur, unread: Math.max(0, u) }));
    return true;
  }
  watchInbox.set(prev);
  if (onError) onError(serverMessage(r.data, GENERIC));
  return false;
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
    await fetchInbox(inboxQuery.get());
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
    watchInbox.set({ items: [], unread: 0, ready: true });
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
export async function downloadItems(items: WatchItem[]): Promise<string[]> {
  const base = await ensureOutDir();
  if (!base) { toast('error', 'Chưa chọn được thư mục lưu. Hãy chọn thư mục trong Cài đặt.'); return []; } // wording: BA review
  const before = new Set(watchLocal.get().done);
  const res = await oneTap(items, watchLocal.get(), {
    quality: settings.get().defaultQuality,
    outDirFor: (channel) => joinPath(base, sanitizeFolderName(channel.replace(/^@/, ''))),
  }, enqueueWithClaims);
  saveLocal(res.local);
  const queued = res.local.done.filter((id) => !before.has(id));
  if (res.enqueued) toast('success', `Đã thêm ${res.enqueued} video vào hàng đợi.`); // wording: BA review
  // Refused (no downloads left today) stay under "Video mới", like the Channels review list.
  if (res.refused.length) toast('error', refusedText(res.refused.length, res.refused[0].detail));
  return queued; // ids that really went into the queue
}

/** Inbox "Tải": one-tap download, then the queued ones are marked read on the server. */
export async function downloadInboxItems(items: InboxItem[]): Promise<void> {
  const queued = await downloadItems(items);
  const unread = new Set(items.filter((i) => !i.read).map((i) => i.id));
  const ids = queued.filter((id) => unread.has(id));
  if (ids.length) await markInboxRead(ids);
}

/** "Bỏ qua": hide from "Video mới" without downloading. */
export function dismissItems(itemIds: string[]) {
  saveLocal(markDone(watchLocal.get(), itemIds));
}
