// "Tải xuống" screen state: URLs -> probe cards (max PROBE_CONCURRENCY at once).
import { createStore } from './store';
import { api } from './tauri';
import { toAppError } from './errors';
import { PROBE_CONCURRENCY } from './config';
import { douyinPlaceholder } from './douyin';
import { isDouyinUrl } from './urls';
import type { ProbeResult } from './types';
import type { Quality } from './settings';

export type Card = {
  url: string;
  status: 'loading' | 'ready' | 'error';
  result?: ProbeResult;
  errorCode?: string;
  quality?: Quality; // user's explicit choice; default resolved at render time
  outDir?: string; // user's explicit choice
};

export const probes = createStore<{ cards: Card[]; invalid: string[] }>({ cards: [], invalid: [] });

let running = 0;
const pending: string[] = [];

export function parseUrls(text: string): { valid: string[]; invalid: string[] } {
  const valid: string[] = [];
  const invalid: string[] = [];
  for (const raw of text.split(/[\s,;]+/)) {
    const t = raw.trim();
    if (!t) continue;
    let ok = false;
    try {
      const u = new URL(t);
      ok = (u.protocol === 'http:' || u.protocol === 'https:') && u.hostname.includes('.');
    } catch { /* not a URL */ }
    if (ok) { if (!valid.includes(t)) valid.push(t); } else invalid.push(t);
  }
  return { valid, invalid };
}

function patchCard(url: string, p: Partial<Card>) {
  probes.set((s) => ({ ...s, cards: s.cards.map((c) => (c.url === url ? { ...c, ...p } : c)) }));
}

function drain() {
  while (running < PROBE_CONCURRENCY && pending.length) {
    const url = pending.shift()!;
    running++;
    // Douyin: the local yt-dlp cannot read it, and asking the server now would use up a download.
    (isDouyinUrl(url) ? Promise.resolve<ProbeResult>(douyinPlaceholder(url)) : api.probe(url))
      .then((r) => patchCard(url, { status: 'ready', result: r, errorCode: undefined }))
      .catch((e) => patchCard(url, { status: 'error', errorCode: toAppError(e).code }))
      .finally(() => { running--; drain(); });
  }
}

/** Returns how many new cards were added. */
export function analyze(text: string): number {
  const { valid, invalid } = parseUrls(text);
  const existing = new Set(probes.get().cards.map((c) => c.url));
  const fresh = valid.filter((u) => !existing.has(u));
  probes.set((s) => ({ invalid, cards: [...s.cards, ...fresh.map((url): Card => ({ url, status: 'loading' }))] }));
  pending.push(...fresh);
  drain();
  return fresh.length;
}

export function retryCard(url: string) {
  patchCard(url, { status: 'loading', errorCode: undefined });
  pending.push(url);
  drain();
}

export function setCard(url: string, p: Partial<Card>) {
  patchCard(url, p);
}

export function removeCard(url: string) {
  const c = probes.get().cards.find((x) => x.url === url);
  const i = pending.indexOf(url);
  if (i >= 0) pending.splice(i, 1);
  else if (c?.status === 'loading') void api.cancelProbe(url).catch(() => {});
  probes.set((s) => ({ ...s, cards: s.cards.filter((x) => x.url !== url) }));
}

export function clearInvalid() {
  probes.set((s) => ({ ...s, invalid: [] }));
}
