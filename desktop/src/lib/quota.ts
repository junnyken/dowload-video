// Daily allowance for downloads the app makes itself (PLAN-32D §5, server:
// backend/app/api/client_quota.py). Reserve-first: claim before the download
// starts, settle when it ends (a failed/cancelled download may be refunded).
// Pure rules live in quota-core.ts. Douyin downloads through the server are
// NOT claimed here (POST /client/douyin/video counts them); Douyin downloads
// with the user's own cookies (PLAN-32D L1) are, with route "local_cookie".
import { createStore } from './store';
import { apiFetch } from './http';
import { auth, getAccessToken } from './auth';
import { rememberServerMessage } from './errors';
import { toast } from './ui';
import { deviceInfo, appVersion } from './device';
import {
  NO_CLAIM, decideBatch, decideClaim, emptyLedger, flushLedger, gatePaused, graceLeft, isIpLimit, mergeCounters, normalizeLedger,
  parseHint, parseSnapshot, tryGrace, utcDay, type BatchDecision, type Gate, type Ledger, type QuotaSnapshot, type Refusal,
} from './quota-core';
import type { ServerRefusal } from './routes-core';

export { NO_CLAIM };

/** 'unknown' until the server answered once (badge hidden); 'disabled' = 503 (badge hidden, app behaves as before). */
export type QuotaState = { status: 'unknown' | 'enabled' | 'disabled'; snap: QuotaSnapshot | null };
export const quota = createStore<QuotaState>({ status: 'unknown', snap: null });
/** Set when the server refused a download: the queue stops starting items while it is set. */
export const quotaGate = createStore<Gate>(null);

const HINT_KEY = 'vg.quota.hint';
const LEDGER_KEY = 'vg.quota.ledger';

// Last known facts, so the offline grace still works when the app starts without a network.
// Nothing stored yet = assumed on (fail-closed, PLAN-32E §5.2; parseHint).
function readHint(): { enabled: boolean; grace: number } {
  try {
    return parseHint(JSON.parse(localStorage.getItem(HINT_KEY) ?? 'null'));
  } catch {
    return parseHint(null);
  }
}
function writeHint(enabled: boolean, grace: number) {
  try { localStorage.setItem(HINT_KEY, JSON.stringify({ enabled, grace })); } catch { /* storage unavailable */ }
}

// P0: the offline ledger lives in localStorage (a user can clear it; the server
// copy is the retro claim sent later). A tamper-resistant store is a later step.
function readLedger(): Ledger {
  try { return normalizeLedger(JSON.parse(localStorage.getItem(LEDGER_KEY) ?? 'null'), Date.now()); } catch { return emptyLedger(Date.now()); }
}
function writeLedger(l: Ledger) {
  try { localStorage.setItem(LEDGER_KEY, JSON.stringify(l)); } catch { /* storage unavailable */ }
}

function setSnap(snap: QuotaSnapshot | null) {
  quota.set({ status: 'enabled', snap });
  writeHint(true, snap?.offlineGrace ?? readHint().grace);
}
function setDisabled() {
  quota.set({ status: 'disabled', snap: null });
  writeHint(false, readHint().grace);
}

type Answer = { status: number; data: unknown };

/** Network failure -> status 0. A 401 with a token is retried once with a refreshed token. */
async function call(path: string, method: 'GET' | 'POST', body?: unknown, headers?: Record<string, string>): Promise<Answer> {
  try {
    let token = await getAccessToken();
    let r = await apiFetch(path, { method, body, token, headers });
    if (r.status === 401 && token) {
      token = await getAccessToken(true);
      r = await apiFetch(path, { method, body, token, headers });
    }
    return { status: r.status, data: r.data };
  } catch {
    return { status: 0, data: null };
  }
}

/** ASCII-only display name for the GET /client/quota header. */
async function nameHeaders(): Promise<Record<string, string>> {
  const d = await deviceInfo();
  return d?.displayName ? { 'X-VG-Device-Name': d.displayName.replace(/[^\x20-\x7e]/g, '?').slice(0, 120) } : {};
}

let refreshing: Promise<void> | null = null;
/** GET /client/quota: app start, after sign-in/out, after each settle. Also flushes the offline ledger. */
export function refreshQuota(): Promise<void> {
  refreshing ??= (async () => {
    const r = await call('/api/v1/client/quota', 'GET', undefined, await nameHeaders());
    if (r.status === 200) {
      const s = parseSnapshot(r.data);
      if (s) { setSnap(s); void flushOffline(); }
    } else if (r.status === 503) {
      setDisabled();
    } // network / other: keep what we know
  })().finally(() => { refreshing = null; });
  return refreshing;
}

let flushing = false;
/** Reports the downloads made offline (claim with retro=true). Runs when the API answers again. */
export async function flushOffline(): Promise<void> {
  if (flushing) return;
  const l0 = readLedger();
  if (!l0.pending.length) return;
  flushing = true;
  try {
    const v = (await appVersion()) ?? '';
    const l1 = await flushLedger(l0, async (e) => {
      const r = await call('/api/v1/client/quota/claim', 'POST', { url: e.url, route: 'local', clientVersion: v, retro: true });
      if (r.status === 200) setSnap(mergeCounters(quota.get().snap, r.data));
      const d = decideClaim(r.status, r.data);
      // update_required: keep the report for the updated app; proceed / refused / disabled: the server has had its say
      return d.kind === 'offline' || d.kind === 'update_required' ? 'retry' : 'drop';
    });
    // Keep entries added while we were sending.
    const now = readLedger();
    const sent = l0.pending.length - l1.pending.length;
    writeLedger({ ...now, pending: now.pending.slice(sent) });
  } finally {
    flushing = false;
  }
}

/** token: the server's signed claim (PLAN-32E P3), handed to Rust's start_download as-is. */
export type StartVerdict = { ok: true; claimId: string; token?: string } | { ok: false };

/** Called by the queue before a local download starts. */
export async function claimForStart(url: string, route: 'local' | 'local_cookie' = 'local'): Promise<StartVerdict> {
  const v = (await appVersion()) ?? '';
  const r = await call('/api/v1/client/quota/claim', 'POST', { url, route, clientVersion: v });
  const d = decideClaim(r.status, r.data);
  if (d.kind === 'proceed') {
    if (d.counted) setSnap(mergeCounters(quota.get().snap, d.data));
    else if (r.status === 503) setDisabled();
    return { ok: true, claimId: d.claimId, ...(d.token ? { token: d.token } : {}) };
  }
  if (d.kind === 'refused') {
    refuse(d.refusal);
    return { ok: false };
  }
  if (d.kind === 'update_required') return { ok: false }; // the update screen says why; no offline grace
  // Offline grace: unless the server last said the feature is off (hint, fail-closed).
  const hint = readHint();
  if (!hint.enabled) return { ok: true, claimId: NO_CLAIM };
  const grace = quota.get().snap?.offlineGrace ?? hint.grace;
  const g = tryGrace(readLedger(), grace, url, Date.now());
  if (g.ok) {
    writeLedger(g.ledger);
    return { ok: true, claimId: NO_CLAIM };
  }
  // wording: BA review
  toast('error', `Không kết nối được máy chủ và đã dùng hết ${grace} lượt tải ngoại tuyến của hôm nay. Hãy kết nối Internet rồi thử lại.`);
  return { ok: false };
}

/**
 * POST /client/quota/claim-batch for videos picked in a channel (PLAN-32D §6).
 * One answer for the whole list; refused items are NOT a gate (the allowed ones
 * already hold their claims and must run).
 */
export async function claimBatch(urls: string[], route: 'local' | 'local_cookie'): Promise<BatchDecision> {
  if (!urls.length) return { kind: 'ok', items: [], data: {} };
  // The server takes at most 100 items per call.
  const items: Extract<BatchDecision, { kind: 'ok' }>['items'] = [];
  for (let i = 0; i < urls.length; i += 100) {
    const part = urls.slice(i, i + 100);
    const r = await call('/api/v1/client/quota/claim-batch', 'POST', { items: part.map((url) => ({ url })), route });
    const d = decideBatch(r.status, r.data, part);
    if (d.kind === 'disabled') { setDisabled(); return d; }
    if (d.kind === 'offline') {
      // claimId '' = not claimed: the rest is claimed one by one when each item starts (claimForStart, with the offline grace).
      items.push(...urls.slice(i).map((url) => ({ url, allowed: true, claimId: '', detail: null })));
      break;
    }
    const s = mergeCounters(quota.get().snap, d.data);
    if (s) setSnap(s);
    items.push(...d.items);
  }
  return { kind: 'ok', items, data: {} };
}

/** POST /fetch-link refused the download (daily allowance): same gate and toast as a refused claim. */
export function refuseFromServer(r: ServerRefusal) {
  refuse({ detail: r.detail, upsell: r.upsell, reason: r.reason ?? 'daily_limit', resetTimeVn: r.resetTimeVn, limit: r.limit, usedToday: r.usedToday });
}

/** Offline downloads still allowed today (for the UI). */
export const offlineLeft = (): number => graceLeft(readLedger(), quota.get().snap?.offlineGrace ?? readHint().grace, Date.now());

function refuse(refusal: Refusal) {
  rememberServerMessage('api:quota_exceeded_daily', refusal.detail);
  const first = !gatePaused(quotaGate.get(), Date.now());
  quotaGate.set({ day: utcDay(Date.now()), refusal });
  // The network's guest cap leaves this person's own counters as they are.
  if (!isIpLimit(refusal) && refusal.limit != null && refusal.usedToday != null) {
    quota.set((q) => ({ status: 'enabled', snap: mergeCounters(q.snap, { limit: refusal.limit, usedToday: refusal.usedToday, remaining: 0, resetTimeVn: refusal.resetTimeVn }) }));
  }
  if (first) toast('error', refusal.detail || 'Bạn đã hết lượt tải hôm nay.'); // ONE toast per refusal episode
}

/** The user signed in, the day changed or they pressed retry. */
export function clearGate() {
  if (quotaGate.get() != null) quotaGate.set(null);
}

/** POST /client/quota/settle; fire-and-forget (a missed settle only costs the user a refund). */
export async function settle(claimId: string | null | undefined, outcome: 'completed' | 'failed' | 'cancelled', errorCode?: string): Promise<void> {
  if (!claimId || claimId === NO_CLAIM) return;
  const r = await call('/api/v1/client/quota/settle', 'POST', { claimId, outcome, ...(errorCode ? { errorCode: errorCode.slice(0, 64) } : {}) });
  if (r.status === 200) {
    const s = mergeCounters(quota.get().snap, r.data);
    if (s) setSnap(s);
  }
  void refreshQuota();
}

let inited = false;
export function initQuota() {
  if (inited) return;
  inited = true;
  // Fires at app start (auth leaves 'loading') and on every sign-in / sign-out.
  auth.subscribe(() => {
    if (auth.get().status === 'loading') return;
    clearGate();
    void refreshQuota();
  });
  window.addEventListener('online', () => void refreshQuota());
  // The allowance resets at 00:00 UTC: a refusal from yesterday no longer holds the queue.
  setInterval(() => { const g = quotaGate.get(); if (g && !gatePaused(g, Date.now())) { clearGate(); void refreshQuota(); } }, 60_000);
}
