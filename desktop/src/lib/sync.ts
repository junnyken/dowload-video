// Push unsynced local history to the account (POST /api/v1/client/history).
// Never sends file paths. 503 (client_api_disabled / storage_not_ready) is a
// quiet status, not an error. Network/5xx errors back off and retry.
import { createStore } from './store';
import { api } from './tauri';
import { apiFetch } from './http';
import { auth, getAccessToken } from './auth';
import { settings } from './settings';
import { deviceHash } from './device';
import type { HistoryItem } from './types';


export type SyncStatus = 'idle' | 'syncing' | 'ok' | 'disabled' | 'offline' | 'off' | 'signedout';
export const syncState = createStore<{ status: SyncStatus; pending: number; lastAt: string | null }>({
  status: 'idle', pending: 0, lastAt: null,
});

const BATCH = 100;
const BACKOFF = [30_000, 60_000, 120_000, 300_000, 600_000];
let running = false;
let failures = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
let clientVersion = 'unknown';

function schedule(ms: number) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { timer = null; void syncNow(); }, ms);
}

/** Debounced trigger (after a download finishes, sign-in, setting change…). */
export function syncSoon(delay = 1500) {
  schedule(delay);
}

async function collectUnsynced(): Promise<HistoryItem[]> {
  const out: HistoryItem[] = [];
  const page = 500;
  for (let off = 0; off < 20_000; off += page) {
    const rows = await api.historyList({ limit: page, offset: off });
    out.push(...rows.filter((r) => !r.synced));
    if (rows.length < page) break;
  }
  return out;
}

export async function refreshPending() {
  try {
    const n = (await collectUnsynced()).length;
    syncState.set((s) => ({ ...s, pending: n }));
  } catch { /* history unavailable */ }
}

export async function syncNow(): Promise<void> {
  if (running) return;
  if (auth.get().status !== 'in') {
    syncState.set((s) => ({ ...s, status: 'signedout' }));
    return;
  }
  if (!settings.get().autoSync) {
    syncState.set((s) => ({ ...s, status: 'off' }));
    return;
  }
  running = true;
  syncState.set((s) => ({ ...s, status: 'syncing' }));
  try {
    const items = await collectUnsynced();
    syncState.set((s) => ({ ...s, pending: items.length }));
    for (let i = 0; i < items.length; i += BATCH) {
      const batch = items.slice(i, i + BATCH);
      let token = await getAccessToken();
      if (!token) { syncState.set((s) => ({ ...s, status: 'signedout' })); return; }
      const body = {
        deviceId: (await deviceHash()) ?? '', // machine hash (device.rs); was a per-install random id
        clientVersion,
        items: batch.map((h) => ({
          clientId: h.id, url: h.url, title: h.title, platform: h.platform, formatLabel: h.formatLabel,
          fileSize: h.fileSize, state: h.state, errorCode: h.errorCode, finishedAt: h.finishedAt,
          // filePath is deliberately NOT sent.
        })),
      };
      let res = await apiFetch<{ ids?: string[] }>('/api/v1/client/history', { method: 'POST', body, token });
      if (res.status === 401) {
        token = await getAccessToken(true);
        if (!token) { syncState.set((s) => ({ ...s, status: 'signedout' })); return; }
        res = await apiFetch('/api/v1/client/history', { method: 'POST', body, token });
      }
      if (res.status === 503) {
        syncState.set((s) => ({ ...s, status: 'disabled' }));
        schedule(15 * 60_000); // quiet retry; the server flag may be turned on later
        return;
      }
      if (res.status === 429 || res.status >= 500 || res.status === 401) throw new Error(`http ${res.status}`);
      if (res.status >= 400) {
        // Rejected batch (e.g. 422): do not hammer the server; treat as offline-ish and back off.
        throw new Error(`http ${res.status}`);
      }
      const ids = Array.isArray(res.data?.ids) ? res.data!.ids! : batch.map((b) => b.id);
      await api.historyMarkSynced(ids.filter((id) => batch.some((b) => b.id === id)));
      syncState.set((s) => ({ ...s, pending: Math.max(0, s.pending - ids.length) }));
    }
    failures = 0;
    syncState.set({ status: 'ok', pending: 0, lastAt: new Date().toISOString() });
    window.dispatchEvent(new Event('vg:history-synced'));
  } catch {
    syncState.set((s) => ({ ...s, status: 'offline' }));
    schedule(BACKOFF[Math.min(failures++, BACKOFF.length - 1)]);
  } finally {
    running = false;
  }
}

export function initSync() {
  api.getVersion().then((v) => (clientVersion = v)).catch(() => {});
  auth.subscribe(() => {
    if (auth.get().status === 'in') syncSoon(500);
    else syncState.set((s) => ({ ...s, status: 'signedout' }));
  });
  settings.subscribe(() => {
    if (settings.get().autoSync) { if (auth.get().status === 'in' && syncState.get().status === 'off') syncSoon(300); }
    else syncState.set((s) => ({ ...s, status: 'off' }));
  });
  window.addEventListener('online', () => { failures = 0; syncSoon(1000); });
  setInterval(() => void syncNow(), 5 * 60_000);
}
