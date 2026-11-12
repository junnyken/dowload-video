// Channel Watch ("Theo dõi kênh", Phase 33B/33F): pure rules, no
// Tauri / React / network, so they run under `npm test`.
//
// Server side: backend/app/api/watch.py (Phase 33A/33F). The server scans the
// channel; the app polls GET /api/v1/watch/items and downloads videos on THIS
// machine through the normal channel queue (enqueueWithClaims: daily allowance
// claimed first, saved to <base>\<sanitizeFolderName(channel)>\).

export type WatchMode = 'notify_only' | 'one_tap' | 'auto_download';

export type WatchLimits = { max_sources: number; min_interval_sec: number; replace_cooldown_sec: number };

export type WatchStatus = {
  enabled: boolean;
  adminOnly: boolean;
  platforms: string[];
  limits: WatchLimits;
  used: number;
  autoDownloadAllowed: boolean;
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
  mode: WatchMode | string;
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

/** One row of GET /watch/inbox: a WatchItem plus its read-state. */
export type InboxItem = WatchItem & { read: boolean };
export type Inbox = { items: InboxItem[]; unread: number; ready: boolean };

// ---- Windows-safe channel folder helpers -----------------------------------------

const WIN_RESERVED_STEM = /^(CON|PRN|AUX|NUL|COM[1-9\u00b9\u00b2\u00b3]|LPT[1-9\u00b9\u00b2\u00b3])$/i;

/** Folder-name safe version of a channel title (Windows rules). */
export function sanitizeFolderName(s: string): string {
  const normalized = String(s ?? '')
    // eslint-disable-next-line no-control-regex
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  // Slice by Unicode code points so surrogate pairs (emoji) are never split in half.
  let clean = Array.from(normalized)
    .slice(0, 60)
    .join('')
    .replace(/[. ]+$/, '')
    .trim();
  if (!clean) return 'Kenh';
  const stem = clean.split('.')[0] ?? clean;
  if (WIN_RESERVED_STEM.test(stem)) {
    clean = Array.from(`_${clean}`).slice(0, 60).join('').replace(/[. ]+$/, '');
  }
  return clean || 'Kenh';
}

export function joinPath(base: string, name: string): string {
  return `${base.replace(/[\\/]+$/, '')}\\${name}`;
}

// ---- parsers & mode gates --------------------------------------------------------

const num = (v: unknown, d = 0) => (typeof v === 'number' && Number.isFinite(v) ? v : d);
const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null);

export function parseStatus(httpStatus: number, data: unknown): StatusResult {
  if (httpStatus === 401) return { kind: 'login' };
  // 503 means "off" only when the server says so (watch_disabled): a gateway
  // 503 from an overloaded host is a network problem, not a disabled feature.
  const code = data && typeof data === 'object' ? (data as Record<string, unknown>).error_code : undefined;
  if (httpStatus === 403 || httpStatus === 404) return { kind: 'off' };
  if (httpStatus === 503) return code === 'watch_disabled' ? { kind: 'off' } : { kind: 'error' };
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
      autoDownloadAllowed: d.auto_download_allowed === true,
    },
  };
}

/** Modes selectable for this user's plan (`auto_download` requires paid tier). */
export function allowedModes(status: WatchStatus | null | undefined): WatchMode[] {
  return status?.autoDownloadAllowed
    ? ['notify_only', 'one_tap', 'auto_download']
    : ['notify_only', 'one_tap'];
}

/** `known`: this account had the entry before (saved per account). While the
 * server cannot be reached the entry stays, so a network problem does not look
 * like a removed feature. */
export function watchVisible(authStatus: 'loading' | 'out' | 'in', r: StatusResult | null, known = false): boolean {
  if (authStatus !== 'in') return false;
  if (!r || r.kind === 'error') return known;
  if (r.kind !== 'ok') return false;
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

/** GET /watch/inbox. Unknown fields ignored, wrong types dropped; `ready` is false only when the server says so. */
export function parseInbox(data: unknown): Inbox {
  const d = (data && typeof data === 'object' ? data : {}) as Record<string, unknown>;
  const list = Array.isArray(d.items) ? d.items : [];
  const items = list
    .filter((i): i is Record<string, unknown> => !!i && typeof i === 'object' && typeof (i as { id?: unknown }).id === 'string')
    .map((i) => ({
      id: i.id as string, source_id: str(i.source_id) ?? '', subscription_id: str(i.subscription_id) ?? '',
      channel: str(i.channel), platform: str(i.platform), title: str(i.title), url: str(i.url),
      published_at: str(i.published_at), discovered_at: str(i.discovered_at), read: i.read === true,
    }));
  return { items, unread: Math.max(0, Math.floor(num(d.unread))), ready: d.ready !== false };
}

/** Server refusal text, shown verbatim ({error_code, message, detail}). */
export function serverMessage(data: unknown, fallback: string): string {
  const d = data as { message?: unknown; detail?: unknown } | null;
  if (d && typeof d.message === 'string' && d.message) return d.message;
  if (d && typeof d.detail === 'string' && d.detail) return d.detail;
  return fallback;
}

// ---- local state (persisted per account) -----------------------------------------

export type WatchLocal = {
  since: string | null;
  notified: string[];
  done: string[];
  known?: boolean;
  /** UTC YYYY-MM-DD when the daily quota-exhausted warning was already shown. */
  quotaWarnedDay?: string;
};

export const MAX_IDS = 500;
export const MAX_AUTO_PER_CHANNEL = 20;
export const emptyLocal = (): WatchLocal => ({ since: null, notified: [], done: [], known: false });

const ids = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string').slice(-MAX_IDS) : []);

/** Storage holds { [userId]: WatchLocal }; anything malformed = fresh state. */
export function readLocal(raw: string | null, userId: string): WatchLocal {
  try {
    const all = JSON.parse(raw ?? 'null');
    const v = all && typeof all === 'object' ? (all as Record<string, unknown>)[userId] : null;
    if (!v || typeof v !== 'object') return emptyLocal();
    const o = v as Record<string, unknown>;
    const base: WatchLocal = {
      since: str(o.since) && !Number.isNaN(Date.parse(o.since as string)) ? (o.since as string) : null,
      notified: ids(o.notified),
      done: ids(o.done),
      known: o.known === true,
    };
    const qDay = str(o.quotaWarnedDay);
    if (qDay) base.quotaWarnedDay = qDay;
    return base;
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
  const entry: Record<string, unknown> = {
    since: local.since,
    notified: local.notified.slice(-MAX_IDS),
    done: local.done.slice(-MAX_IDS),
    known: local.known === true,
  };
  if (local.quotaWarnedDay) entry.quotaWarnedDay = local.quotaWarnedDay;
  all[userId] = entry;
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

/** Windows notification when auto_download queues new videos into the channel folder. */
export function autoDownloadedNotifyText(count: number, channel: string): { title: string; body: string } {
  return {
    title: `Đã tải ${count} video mới từ ${channelLabel(channel)}`,
    body: 'Bấm để mở thư mục kênh.',
  };
}

/** Single daily notification when auto_download hits daily quota limit. */
export function quotaExhaustedNotifyText(waiting: number): { title: string; body: string } {
  const msg = `Hết lượt tải hôm nay, còn ${waiting} video chờ`;
  return { title: 'Hết lượt tải hôm nay', body: msg };
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

// ---- one-tap & auto-download ---------------------------------------------------------

type Video = ReturnType<typeof toChannelVideo>;
export type ChannelTarget = { platform: string; title: string; quality: string; outDir: string };
/** channels.ts enqueueWithClaims: claim-batch the daily allowance, then queue only the allowed ones. */
export type EnqueueWithClaims = (videos: Video[], ch: ChannelTarget) => Promise<{ enqueued: { id: string }[]; refused: { video: { id: string }; detail: string | null }[] }>;
export type FileExistsInDir = (outDir: string, item: WatchItem) => Promise<boolean> | boolean;

export type DownloadedChannelBatch = { subscriptionId: string; channel: string; outDir: string; count: number };

export type OneTapResult = {
  local: WatchLocal;
  enqueued: number;
  refused: { item: WatchItem; detail: string | null }[];
  downloadedBatches: DownloadedChannelBatch[];
};

/**
 * "Tải" on new videos (and auto_download): per channel, the videos go through
 * the app's normal channel path (allowance claimed first, then the local
 * download queue). Items already in `local.done` or already saved in the
 * channel folder on disk are skipped and marked done. Only queued/existing
 * items are marked done; refused ones stay in "Video mới".
 */
export async function oneTap(
  items: WatchItem[],
  local: WatchLocal,
  o: { quality: string; outDirFor: (channel: string) => string; fileExistsInDir?: FileExistsInDir },
  enqueueWithClaims: EnqueueWithClaims,
): Promise<OneTapResult> {
  let enqueued = 0;
  const refused: OneTapResult['refused'] = [];
  const doneSet = new Set(local.done);
  const doneIds: string[] = [];
  const downloadedBatches: DownloadedChannelBatch[] = [];

  for (const b of batchByChannel(items.filter((i) => !!i.url && !doneSet.has(i.id)))) {
    const platform = b.items[0]?.platform || 'other';
    const title = (b.items[0]?.channel ?? '').trim() || b.channel;
    const outDir = o.outDirFor(title);
    const toQueue: WatchItem[] = [];
    for (const it of b.items) {
      if (o.fileExistsInDir && (await o.fileExistsInDir(outDir, it))) {
        doneIds.push(it.id);
      } else {
        toQueue.push(it);
      }
    }
    if (toQueue.length === 0) continue;

    const byId = new Map(toQueue.map((i) => [i.id, i]));
    const r = await enqueueWithClaims(toQueue.map(toChannelVideo), { platform, title, quality: o.quality, outDir });
    enqueued += r.enqueued.length;
    doneIds.push(...r.enqueued.map((v) => v.id));
    if (r.enqueued.length > 0) {
      downloadedBatches.push({
        subscriptionId: b.subscriptionId,
        channel: b.channel,
        outDir,
        count: r.enqueued.length,
      });
    }
    for (const x of r.refused) {
      const it = byId.get(x.video.id);
      if (it) refused.push({ item: it, detail: x.detail });
    }
  }
  return { local: markDone(local, doneIds), enqueued, refused, downloadedBatches };
}

export type AutoDownloadPollResult = {
  local: WatchLocal;
  notifyBatches: Batch[];
  downloadedBatches: DownloadedChannelBatch[];
  quotaWarning: string | null;
};

/**
 * Full poll + catch-up orchestration:
 * - Updates `local.since` & `local.notified` via `applyPoll`.
 * - Manual subscriptions (`notify_only` / `one_tap`) emit `notifyBatches`.
 * - Active `auto_download` subscriptions (when `autoDownloadAllowed && baseDir`)
 *   catch up undownloaded items capped at `MAX_AUTO_PER_CHANNEL` (20) per channel
 *   through `oneTap(..., enqueueWithClaims)` into `<baseDir>\<sanitizeFolderName(channel)>`.
 * - Quota refusals keep items pending in "Video mới" and emit `quotaWarning` once per UTC day.
 */
export async function runAutoDownloadPoll(opts: {
  local: WatchLocal;
  items: WatchItem[];
  subs: WatchSubscription[];
  autoDownloadAllowed: boolean;
  nowIso: string;
  quality: string;
  baseDir: string | null;
  enqueueWithClaims: EnqueueWithClaims;
  fileExistsInDir?: FileExistsInDir;
}): Promise<AutoDownloadPollResult> {
  const { items, subs, autoDownloadAllowed, nowIso, quality, baseDir, enqueueWithClaims, fileExistsInDir } = opts;
  const poll = applyPoll(opts.local, items, nowIso);
  let nextLocal = poll.local;

  const autoSubs = new Map<string, WatchSubscription>();
  if (autoDownloadAllowed) {
    for (const s of subs) {
      if (s.status === 'active' && s.mode === 'auto_download') {
        autoSubs.set(s.id, s);
      }
    }
  }

  // Manual channels get normal new-video notifications; auto_download channels notify after enqueueing.
  const notifyBatches = batchByChannel(poll.toNotify.filter((i) => !autoSubs.has(i.subscription_id)));
  if (autoSubs.size === 0 || !baseDir) {
    return { local: nextLocal, notifyBatches, downloadedBatches: [], quotaWarning: null };
  }

  const done = new Set(nextLocal.done);
  const perSubCount = new Map<string, number>();
  let overflowWaiting = 0;
  const candidates: WatchItem[] = [];

  for (const it of items) {
    const sub = autoSubs.get(it.subscription_id);
    if (!sub || !it.url || done.has(it.id)) continue;
    const cur = perSubCount.get(sub.id) ?? 0;
    const channelName = it.channel || sub.source.display_name || null;
    if (cur < MAX_AUTO_PER_CHANNEL) {
      perSubCount.set(sub.id, cur + 1);
      candidates.push(it.channel ? it : { ...it, channel: channelName });
    } else {
      overflowWaiting += 1;
    }
  }

  if (candidates.length === 0) {
    return { local: nextLocal, notifyBatches, downloadedBatches: [], quotaWarning: null };
  }

  const tap = await oneTap(
    candidates,
    nextLocal,
    {
      quality,
      outDirFor: (channel) => joinPath(baseDir, sanitizeFolderName(channel.replace(/^@/, ''))),
      fileExistsInDir,
    },
    enqueueWithClaims,
  );
  nextLocal = tap.local;

  let quotaWarning: string | null = null;
  const today = nowIso.slice(0, 10);
  if (tap.refused.length > 0) {
    const waiting = tap.refused.length + overflowWaiting;
    if (nextLocal.quotaWarnedDay !== today) {
      quotaWarning = quotaExhaustedNotifyText(waiting).body;
      nextLocal = { ...nextLocal, quotaWarnedDay: today };
    }
  }

  return {
    local: nextLocal,
    notifyBatches,
    downloadedBatches: tap.downloadedBatches,
    quotaWarning,
  };
}
