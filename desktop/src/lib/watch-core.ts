// Channel Watch ("Theo dõi kênh", Phase 33B, task #6259): pure rules, no
// Tauri / React / network, so they run under `npm test`.
//
// Server side: backend/app/api/watch.py (Phase 33A). The server scans the
// channel; the app only asks GET /api/v1/watch/items?since=… and, when the
// user taps "Tải", downloads the video itself through the normal queue
// (bytes come through the user's own machine, daily allowance claimed first).

export type WatchLimits = { max_sources: number; min_interval_sec: number; replace_cooldown_sec: number };

export type WatchStatus = {
  enabled: boolean;
  adminOnly: boolean;
  platforms: string[];
  limits: WatchLimits;
  used: number;
};

/** What GET /watch/status said, folded to what the app needs. */
export type StatusResult =
  | { kind: 'ok'; status: WatchStatus }
  | { kind: 'off' } // 404/403/503 watch_disabled: the feature is not there for this user
  | { kind: 'login' } // 401: no (valid) session
  | { kind: 'error' }; // network / 5xx: keep what we knew

export type WatchSource = {
  id: string | null;
  platform: string | null;
  display_name: string | null;
  canonical_url: string | null;
  status: string | null; // active | degraded | idle | paused_admin
  last_scan_at: string | null;
  next_scan_at: string | null;
  last_failure_category: string | null;
};

export type WatchSubscription = {
  id: string;
  mode: 'notify_only' | 'one_tap' | string;
  status: 'active' | 'paused' | string;
  created_at: string | null;
  baseline_completed_at: string | null;
  new_items_7d: number;
  source: WatchSource;
};

export type WatchItem = {
  id: string;
  source_id: string;
  subscription_id: string;
  channel: string | null;
  platform: string | null;
  title: string | null;
  url: string | null;
  published_at: string | null;
  discovered_at: string | null;
};

const num = (v: unknown, d = 0) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

export function parseStatus(httpStatus: number, data: unknown): StatusResult {
  if (httpStatus === 401) return { kind: 'login' };
  if (httpStatus === 403 || httpStatus === 404 || httpStatus === 503) return { kind: 'off' };
  if (httpStatus !== 200 || !data || typeof data !== 'object') return { kind: 'error' };
  const d = data as Record<string, unknown>;
  const l = (d.limits && typeof d.limits === 'object' ? d.limits : {}) as Record<string, unknown>;
  return {
    kind: 'ok',
    status: {
      enabled: d.enabled === true,
      adminOnly: d.admin_only === true,
      platforms: Array.isArray(d.platforms) ? d.platforms.filter((p): p is string => typeof p === 'string') : [],
      limits: { max_sources: num(l.max_sources), min_interval_sec: num(l.min_interval_sec), replace_cooldown_sec: num(l.replace_cooldown_sec) },
      used: num(d.used),
    },
  };
}

/**
 * The sidebar entry (and the background poll) exist only for a signed-in
 * user whose status says enabled — or who still has subscriptions to manage
 * (e.g. the trial was narrowed after they subscribed).
 */
export function watchVisible(authStatus: 'loading' | 'out' | 'in', r: StatusResult | null): boolean {
  if (authStatus !== 'in' || !r || r.kind !== 'ok') return false;
  return r.status.enabled || r.status.used > 0;
}

export function parseSubscriptions(data: unknown): WatchSubscription[] {
  const list = (data as { subscriptions?: unknown })?.subscriptions;
  if (!Array.isArray(list)) return [];
  return list
    .filter((s): s is Record<string, unknown> => !!s && typeof s === 'object' && typeof (s as { id?: unknown }).id === 'string')
    .map((s) => {
      const src = (s.source && typeof s.source === 'object' ? s.source : {}) as Record<string, unknown>;
      return {
        id: s.id as string,
        mode: str(s.mode) ?? 'one_tap',
        status: str(s.status) ?? 'active',
        created_at: str(s.created_at),
        baseline_completed_at: str(s.baseline_completed_at),
        new_items_7d: num(s.new_items_7d),
        source: {
          id: str(src.id), platform: str(src.platform), display_name: str(src.display_name), canonical_url: str(src.canonical_url),
          status: str(src.status), last_scan_at: str(src.last_scan_at), next_scan_at: str(src.next_scan_at),
          last_failure_category: str(src.last_failure_category),
        },
      };
    });
}

export function parseItems(data: unknown): WatchItem[] {
  const list = (data as { items?: unknown })?.items;
  if (!Array.isArray(list)) return [];
  return list
    .filter((i): i is Record<string, unknown> => !!i && typeof i === 'object' && typeof (i as { id?: unknown }).id === 'string')
    .map((i) => ({
      id: i.id as string, source_id: str(i.source_id) ?? '', subscription_id: str(i.subscription_id) ?? '',
      channel: str(i.channel), platform: str(i.platform), title: str(i.title), url: str(i.url),
      published_at: str(i.published_at), discovered_at: str(i.discovered_at),
    }));
}

/** Server refusal text, shown verbatim ({error_code, message, detail}). */
export function serverMessage(data: unknown, fallback: string): string {
  const d = data as { message?: unknown; detail?: unknown } | null;
  if (d && typeof d.message === 'string' && d.message) return d.message;
  if (d && typeof d.detail === 'string' && d.detail) return d.detail;
  return fallback;
}

// ---- local state (persisted per account) -----------------------------------------

/**
 * since: the newest discovered_at already seen by a poll (next poll asks
 * ?since=<it>). notified: item ids already announced — the server filters
 * with >= so the newest item comes back on every poll; this set stops a
 * second notification (also after a restart). done: items the user
 * downloaded or dismissed (hidden from the "Video mới" list).
 */
export type WatchLocal = { since: string | null; notified: string[]; done: string[] };

export const MAX_IDS = 500;
export const emptyLocal = (): WatchLocal => ({ since: null, notified: [], done: [] });

const ids = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(-MAX_IDS) : []);

/** Storage holds { [userId]: WatchLocal }; anything malformed = fresh state. */
export function readLocal(raw: string | null, userId: string): WatchLocal {
  try {
    const all = JSON.parse(raw ?? 'null');
    const v = all && typeof all === 'object' ? (all as Record<string, unknown>)[userId] : null;
    if (!v || typeof v !== 'object') return emptyLocal();
    const o = v as Record<string, unknown>;
    return { since: str(o.since) && !Number.isNaN(Date.parse(o.since as string)) ? (o.since as string) : null, notified: ids(o.notified), done: ids(o.done) };
  } catch {
    return emptyLocal();
  }
}

export function writeLocal(raw: string | null, userId: string, local: WatchLocal): string {
  let all: Record<string, unknown> = {};
  try {
    const p = JSON.parse(raw ?? 'null');
    if (p && typeof p === 'object' && !Array.isArray(p)) all = p as Record<string, unknown>;
  } catch { /* start over */ }
  all[userId] = { since: local.since, notified: local.notified.slice(-MAX_IDS), done: local.done.slice(-MAX_IDS) };
  return JSON.stringify(all);
}

const addIds = (cur: string[], more: string[]) => {
  const s = new Set(cur);
  const out = [...cur];
  for (const id of more) if (!s.has(id)) { s.add(id); out.push(id); }
  return out.slice(-MAX_IDS);
};

export function markDone(local: WatchLocal, itemIds: string[]): WatchLocal {
  return { ...local, done: addIds(local.done, itemIds) };
}

export type PollOutcome = { local: WatchLocal; toNotify: WatchItem[] };

/**
 * Applies one poll's answer. The first poll ever for this account is a
 * baseline: it records what is there without notifying (the list still shows
 * it). Later polls notify only ids never notified before.
 */
export function applyPoll(local: WatchLocal, items: WatchItem[], nowIso: string): PollOutcome {
  const first = local.since === null && local.notified.length === 0;
  const known = new Set([...local.notified, ...local.done]);
  const fresh = first ? [] : items.filter((i) => !known.has(i.id) && !!i.url);
  let since = local.since;
  for (const i of items) {
    if (i.discovered_at && !Number.isNaN(Date.parse(i.discovered_at)) && (!since || Date.parse(i.discovered_at) > Date.parse(since))) since = i.discovered_at;
  }
  // Nothing found yet: start from now so the next poll does not ask for 7 days again.
  if (!since) since = nowIso;
  return { local: { ...local, since, notified: addIds(local.notified, items.map((i) => i.id)) }, toNotify: fresh };
}

export type Batch = { subscriptionId: string; channel: string; items: WatchItem[] };

/** One notification per channel: items grouped by subscription, in the order the server gave them. */
export function batchByChannel(items: WatchItem[]): Batch[] {
  const by = new Map<string, Batch>();
  for (const i of items) {
    const key = i.subscription_id || i.source_id;
    let b = by.get(key);
    if (!b) { b = { subscriptionId: key, channel: channelLabel(i.channel), items: [] }; by.set(key, b); }
    b.items.push(i);
  }
  return [...by.values()];
}

export function channelLabel(name: string | null | undefined): string {
  const n = (name ?? '').trim();
  if (!n) return 'kênh bạn theo dõi'; // wording: BA review
  return n.startsWith('@') ? n : `@${n}`;
}

/** wording: BA review */
export function notifyText(b: Batch): { title: string; body: string } {
  return {
    title: `${b.items.length} video mới từ ${b.channel}`,
    body: 'Mở VidGrab → Theo dõi kênh, bấm “Tải” để tải về máy.',
  };
}

/** Items still shown under "Video mới": not downloaded / dismissed, with a link. */
export function pendingItems(items: WatchItem[], local: WatchLocal): WatchItem[] {
  const done = new Set(local.done);
  return items.filter((i) => !!i.url && !done.has(i.id));
}

/** Merges two item lists by id (newest first by discovered_at). */
export function mergeItems(a: WatchItem[], b: WatchItem[]): WatchItem[] {
  const m = new Map<string, WatchItem>();
  for (const i of [...a, ...b]) m.set(i.id, i);
  return [...m.values()].sort((x, y) => (Date.parse(y.discovered_at ?? '') || 0) - (Date.parse(x.discovered_at ?? '') || 0));
}

/** The queue's channel-video shape (channels.ts enqueueWithClaims). */
export function toChannelVideo(i: WatchItem): { id: string; url: string; title: string; duration: null; uploadDate: null; thumbnail: null } {
  return { id: i.id, url: i.url ?? '', title: i.title || i.url || 'Video', duration: null, uploadDate: null, thumbnail: null };
}

// ---- poll timing ---------------------------------------------------------------------

export const POLL_MS = 15 * 60_000;
export const RESUME_MIN_GAP_MS = 5 * 60_000;

/** 15 min ± 2 min, so many apps do not hit the server on the same second. */
export function nextPollDelay(rand: number = Math.random()): number {
  return POLL_MS + Math.round((rand * 2 - 1) * 2 * 60_000);
}

// ---- one-tap -------------------------------------------------------------------------

type Video = ReturnType<typeof toChannelVideo>;
export type ChannelTarget = { platform: string; title: string; quality: string; outDir: string };
/** channels.ts enqueueWithClaims: claim-batch the daily allowance, then queue only the allowed ones. */
export type EnqueueWithClaims = (videos: Video[], ch: ChannelTarget) => Promise<{ enqueued: { id: string }[]; refused: { video: { id: string }; detail: string | null }[] }>;

export type OneTapResult = { local: WatchLocal; enqueued: number; refused: { item: WatchItem; detail: string | null }[] };

/**
 * "Tải" on new videos: per channel, the videos go through the app's normal
 * channel path (allowance claimed first, then the local download queue).
 * Only queued items are marked done; refused ones stay in "Video mới".
 */
export async function oneTap(
  items: WatchItem[], local: WatchLocal,
  o: { quality: string; outDirFor: (channel: string) => string },
  enqueueWithClaims: EnqueueWithClaims,
): Promise<OneTapResult> {
  let enqueued = 0;
  const refused: OneTapResult['refused'] = [];
  const doneIds: string[] = [];
  for (const b of batchByChannel(items.filter((i) => !!i.url))) {
    const byId = new Map(b.items.map((i) => [i.id, i]));
    const platform = b.items[0]?.platform || 'other';
    const title = (b.items[0]?.channel ?? '').trim() || b.channel;
    const r = await enqueueWithClaims(b.items.map(toChannelVideo), { platform, title, quality: o.quality, outDir: o.outDirFor(title) });
    enqueued += r.enqueued.length;
    doneIds.push(...r.enqueued.map((v) => v.id));
    for (const x of r.refused) { const it = byId.get(x.video.id); if (it) refused.push({ item: it, detail: x.detail }); }
  }
  return { local: markDone(local, doneIds), enqueued, refused };
}
