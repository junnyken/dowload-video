// "Tải xuống" screen state: URLs -> probe cards (max PROBE_CONCURRENCY at once).
import { createStore } from './store';
import { api } from './tauri';
import { toAppError } from './errors';
import { PROBE_CONCURRENCY } from './config';
import { douyinPlaceholder } from './douyin';
import { isDouyinUrl } from './urls';
import { cookieState, planFor, routesFor } from './cookies';
import { cookieErrorCode } from './cookies-core';
import { NO_FALLTHROUGH, platformOf } from './routes-core';
import { platformLabel } from './format';
import type { ProbeResult } from './types';
import type { Quality } from './settings';

export type Card = {
  url: string;
  status: 'loading' | 'ready' | 'error';
  result?: ProbeResult;
  errorCode?: string;
  quality?: Quality; // user's explicit choice; default resolved at render time
  outDir?: string; // user's explicit choice
  /** Not analysed on this PC: the download goes straight through the VidGrab server (route S0). */
  serverOnly?: boolean;
};

/** A card for a link the app does not analyse locally (server-only platform, or the user chose the server after a local error). */
export function serverPlaceholder(url: string): ProbeResult {
  const platform = platformOf(url);
  const base = { height: null, fps: null, filesize: null, vcodec: null, acodec: null, requiresMerge: false };
  return {
    url, platform, title: `Video ${platform === 'other' ? '' : platformLabel(platform)}`.trim(), thumbnail: null, duration: null, uploader: null, // wording: BA review
    formats: [
      { ...base, id: 'server-mp4', label: 'Video (MP4)', ext: 'mp4', audioOnly: false },
      { ...base, id: 'server-audio', label: 'Âm thanh (MP3)', ext: 'mp3', audioOnly: true },
    ],
  };
}

/** After a local analysis error: may this link still be downloaded through the VidGrab server? */
export function serverCanTry(url: string, code: string | undefined): boolean {
  return !NO_FALLTHROUGH.has(code ?? 'unknown') && routesFor(url).includes('S0');
}

export const probes = createStore<{ cards: Card[]; invalid: string[] }>({ cards: [], invalid: [] });

let running = 0;
const pending: string[] = [];

import { parseUrls } from './urls';
export { parseUrls };

function patchCard(url: string, p: Partial<Card>) {
  probes.set((s) => ({ ...s, cards: s.cards.map((c) => (c.url === url ? { ...c, ...p } : c)) }));
}

function drain() {
  while (running < PROBE_CONCURRENCY && pending.length) {
    const url = pending.shift()!;
    running++;
    // Douyin: no probe at all. Without cookies the local yt-dlp cannot read it and the server
    // would count a download; with cookies (L1) a probe would be one more signed-in request to
    // Douyin per pasted link for a video that has a single MP4 anyway. The queue picks the route.
    const plan = planFor(url);
    // Server-only platforms (Kuaishou, Xiaohongshu, iQIYI when the server allows it): nothing to analyse here.
    const serverOnly = !isDouyinUrl(url) && routesFor(url)[0] === 'S0';
    (isDouyinUrl(url) ? Promise.resolve<ProbeResult>(douyinPlaceholder(url))
      : serverOnly ? Promise.resolve<ProbeResult>(serverPlaceholder(url))
      : api.probe(url, plan.useCookies || undefined))
      .then((r) => patchCard(url, { status: 'ready', result: r, errorCode: undefined, serverOnly: serverOnly || undefined }))
      .catch((e) => patchCard(url, {
        status: 'error',
        errorCode: cookieErrorCode(toAppError(e).code, { cookiesUsed: plan.useCookies, platform: plan.platform, enabled: cookieState.get().enabled ?? [] }),
      }))
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

/** "Tải qua máy chủ VidGrab" on an error card: the card becomes a server-only download. */
export function switchToServer(url: string) {
  patchCard(url, { status: 'ready', result: serverPlaceholder(url), errorCode: undefined, serverOnly: true });
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
