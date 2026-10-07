// Pure logic of the daily-allowance feature (PLAN-32D §5). No Tauri, React or
// network imports so `node --test` can run it (like urls.ts). lib/quota.ts
// wires it to the API and the queue.

export type QuotaSnapshot = {
  limit: number;
  usedToday: number;
  remaining: number; // -1 = unlimited
  resetTimeVn: string; // "07:00"
  requester: string; // user | device | anon | admin ...
  deviceCode: string | null;
  mode: 'shadow' | 'enforce';
  enforced: boolean;
  offlineGrace: number;
  refundDailyMax: number;
};

export const DEFAULT_OFFLINE_GRACE = 3;
/** claimId stored on a queue item that needs no settle (not counted, or counted offline). */
export const NO_CLAIM = '-';

export type Refusal = {
  detail: string; // Vietnamese text from the server, shown as-is
  upsell: 'signin' | 'upgrade' | null;
  reason: string | null;
  resetTimeVn: string | null;
  limit: number | null;
  usedToday: number | null;
};

export type ClaimDecision =
  | { kind: 'proceed'; claimId: string; counted: boolean; data: Record<string, unknown> | null }
  | { kind: 'refused'; refusal: Refusal }
  | { kind: 'offline' };

const obj = (d: unknown): Record<string, unknown> => (d && typeof d === 'object' ? (d as Record<string, unknown>) : {});
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const str = (v: unknown): string | null => (typeof v === 'string' && v.trim() ? v : null);

/**
 * What to do with the answer of POST /client/quota/claim. `status` 0 means the
 * request never got an answer (network error).
 *  - 200 allowed         -> proceed with the claimId
 *  - 503 client_quota_disabled / 400 invalid_url -> proceed, nothing counted
 *  - 403 / 429 quota_exceeded_daily -> refused (the server's text is shown)
 *  - network error, 5xx, any other answer -> offline (the caller applies the grace)
 */
export function decideClaim(status: number, data: unknown): ClaimDecision {
  const o = obj(data);
  if (status === 200 && o.allowed === true && str(o.claimId)) {
    return { kind: 'proceed', claimId: o.claimId as string, counted: true, data: o };
  }
  if (status === 503 && o.error_code === 'client_quota_disabled') {
    return { kind: 'proceed', claimId: NO_CLAIM, counted: false, data: null };
  }
  if (status === 400 && o.error_code === 'invalid_url') {
    return { kind: 'proceed', claimId: NO_CLAIM, counted: false, data: null };
  }
  if ((status === 403 || status === 429) && o.error_code === 'quota_exceeded_daily') {
    const up = o.upsell === 'signin' || o.upsell === 'upgrade' ? o.upsell : null;
    return {
      kind: 'refused',
      refusal: {
        detail: str(o.detail) ?? '', upsell: up, reason: str(o.reason),
        resetTimeVn: str(o.resetTimeVn), limit: num(o.limit), usedToday: num(o.usedToday),
      },
    };
  }
  return { kind: 'offline' };
}

// ---- refusal gate: stop starting queued items until something changes ---------

export type Gate = { day: string; refusal: Refusal } | null;

/** The queue is paused while a refusal from the same UTC day stands (the allowance resets at 00:00 UTC). */
export function gatePaused(g: Gate, now: number): boolean {
  return g != null && g.day === utcDay(now);
}

// ---- offline grace ledger --------------------------------------------------------

export type LedgerEntry = { url: string; at: number };
/** `used` = downloads allowed offline on `day` (UTC); `pending` = those still to report with retro=true. */
export type Ledger = { day: string; used: number; pending: LedgerEntry[] };

export function utcDay(now: number): string {
  return new Date(now).toISOString().slice(0, 10);
}

export const emptyLedger = (now: number): Ledger => ({ day: utcDay(now), used: 0, pending: [] });

/** Reads a stored ledger defensively and rolls the per-day counter over at 00:00 UTC (pending reports are kept). */
export function normalizeLedger(raw: unknown, now: number): Ledger {
  const o = obj(raw);
  const pending: LedgerEntry[] = Array.isArray(o.pending)
    ? o.pending
        .filter((e): e is LedgerEntry => !!e && typeof (e as LedgerEntry).url === 'string' && Number.isFinite((e as LedgerEntry).at))
        .slice(0, 500)
    : [];
  const today = utcDay(now);
  const used = o.day === today ? Math.max(0, Math.floor(num(o.used) ?? 0)) : 0;
  return { day: today, used, pending };
}

export function graceLeft(l: Ledger, grace: number, now: number): number {
  const used = l.day === utcDay(now) ? l.used : 0;
  return Math.max(0, grace - used);
}

/** Uses one offline download if the grace allows it. */
export function tryGrace(l: Ledger, grace: number, url: string, now: number): { ok: boolean; ledger: Ledger } {
  const cur = normalizeLedger(l, now);
  if (cur.used >= grace) return { ok: false, ledger: cur };
  return { ok: true, ledger: { day: cur.day, used: cur.used + 1, pending: [...cur.pending, { url, at: now }] } };
}

/**
 * Reports pending offline downloads one by one (`send` does the retro claim).
 * 'done' and 'drop' remove the entry (counted, or nothing more to do);
 * 'retry' stops and keeps it and everything after it. `used` is kept: the
 * grace is per day, not per connection.
 */
export async function flushLedger(l: Ledger, send: (e: LedgerEntry) => Promise<'done' | 'retry' | 'drop'>): Promise<Ledger> {
  const rest = [...l.pending];
  while (rest.length) {
    if ((await send(rest[0])) === 'retry') break;
    rest.shift();
  }
  return { ...l, pending: rest };
}

// ---- display ----------------------------------------------------------------------

export const isUnlimited = (s: Pick<QuotaSnapshot, 'limit' | 'remaining'>): boolean => s.remaining < 0 || s.limit <= 0;

/** Badge text: "Hôm nay 3/5 lượt" or "Không giới hạn". */
export function badgeText(s: Pick<QuotaSnapshot, 'limit' | 'remaining' | 'usedToday'>): string {
  // wording: BA review
  return isUnlimited(s) ? 'Không giới hạn' : `Hôm nay ${Math.min(s.usedToday, s.limit)}/${s.limit} lượt`;
}

/** Parses GET /client/quota; null when it is not a quota answer. */
export function parseSnapshot(d: unknown): QuotaSnapshot | null {
  const o = obj(d);
  const limit = num(o.limit), used = num(o.usedToday), remaining = num(o.remaining);
  if (limit == null || used == null || remaining == null) return null;
  return {
    limit, usedToday: used, remaining, resetTimeVn: str(o.resetTimeVn) ?? '', requester: str(o.requester) ?? '',
    deviceCode: str(o.deviceCode), mode: o.mode === 'enforce' ? 'enforce' : 'shadow', enforced: o.enforced === true,
    offlineGrace: Math.max(0, Math.floor(num(o.offlineGrace) ?? DEFAULT_OFFLINE_GRACE)),
    refundDailyMax: Math.max(0, Math.floor(num(o.refundDailyMax) ?? 0)),
  };
}

/** Merges the counters a claim/settle answer carries into the last snapshot. */
export function mergeCounters(prev: QuotaSnapshot | null, d: unknown): QuotaSnapshot | null {
  const o = obj(d);
  const limit = num(o.limit), used = num(o.usedToday), remaining = num(o.remaining);
  if (limit == null || used == null || remaining == null) return prev;
  const base: QuotaSnapshot = prev ?? {
    limit, usedToday: used, remaining, resetTimeVn: '', requester: '', deviceCode: null, mode: 'shadow', enforced: false,
    offlineGrace: DEFAULT_OFFLINE_GRACE, refundDailyMax: 0,
  };
  return {
    ...base, limit, usedToday: used, remaining, resetTimeVn: str(o.resetTimeVn) ?? base.resetTimeVn,
    requester: str(o.requester) ?? base.requester, mode: o.mode === 'enforce' ? 'enforce' : o.mode === 'shadow' ? 'shadow' : base.mode,
  };
}

// ---- channels: claim-batch (PLAN-32D §6) -------------------------------------------

export type BatchItem = { url: string; allowed: boolean; claimId: string; detail: string | null };
export type BatchDecision =
  | { kind: 'ok'; items: BatchItem[]; data: Record<string, unknown> }
  | { kind: 'disabled' }
  | { kind: 'offline' };

/**
 * POST /client/quota/claim-batch answer, one entry per requested URL (same
 * order: the server answers item by item). invalid_url = nothing counted
 * (enqueue without a claim, like a single claim's 400). 503
 * client_quota_disabled = today's behaviour. Anything unexpected = offline
 * (the queue then claims each item on its own when it starts).
 */
export function decideBatch(status: number, data: unknown, urls: readonly string[]): BatchDecision {
  const o = obj(data);
  if (status === 503 && o.error_code === 'client_quota_disabled') return { kind: 'disabled' };
  if (status !== 200 || !Array.isArray(o.items) || o.items.length !== urls.length) return { kind: 'offline' };
  const items = (o.items as unknown[]).map((raw, i): BatchItem => {
    const it = obj(raw);
    if (it.allowed === true && str(it.claimId)) return { url: urls[i], allowed: true, claimId: it.claimId as string, detail: null };
    if (it.error_code === 'invalid_url') return { url: urls[i], allowed: true, claimId: NO_CLAIM, detail: null };
    return { url: urls[i], allowed: false, claimId: NO_CLAIM, detail: str(it.detail) };
  });
  return { kind: 'ok', items, data: o };
}

/**
 * How many videos the channel picker lets the user select: the remaining
 * downloads of today when the server enforces the allowance for this
 * requester; null = no cap (shadow mode, unlimited, unknown or disabled).
 */
export function selectionCap(q: { status: string; snap: QuotaSnapshot | null }): number | null {
  if (q.status !== 'enabled' || !q.snap || !q.snap.enforced || isUnlimited(q.snap)) return null;
  return Math.max(0, q.snap.remaining);
}

/** Keeps the first `cap` ids (the list order is the picker's: newest first). */
export function capIds<T>(ids: readonly T[], cap: number | null): T[] {
  return cap == null ? [...ids] : ids.slice(0, Math.max(0, cap));
}
