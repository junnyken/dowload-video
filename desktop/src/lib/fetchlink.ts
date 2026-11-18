// S0 (PLAN-32D §2, P2): POST /api/v1/fetch-link asks the VidGrab server to get
// the video; the app then downloads the server's file (or the direct link) with
// its own yt-dlp. The server counts the download itself (per URL, once a day).
// Pure parsing: routes-core.ts. Links are never logged.
import { API_BASE } from './config';
import { apiFetch } from './http';
import { getAccessToken } from './auth';
import { fetchLinkBody, parseFetchLink, serverQuality, type FetchLinkOutcome } from './routes-core';

// Ephemeral browser-session cookie jars forwarded from Chrome Extension by URL (kept in RAM only).
const extUrlCookies = new Map<string, string>();

export function rememberExtUrlCookieJar(url: string, cookieJar: string | null | undefined) {
  const cleanUrl = (url || '').trim();
  const cleanJar = (cookieJar || '').trim();
  if (!cleanUrl || !cleanJar) return;
  if (extUrlCookies.size >= 50) {
    const first = extUrlCookies.keys().next().value;
    if (first !== undefined) extUrlCookies.delete(first);
  }
  extUrlCookies.set(cleanUrl, cleanJar);
}

function toBase64Utf8(text: string): string {
  try {
    return btoa(unescape(encodeURIComponent(text)));
  } catch {
    return '';
  }
}

export async function fetchLink(it: { url: string; title: string; audioOnly: boolean; quality?: string; formatId?: string; formatLabel?: string; extCookieJar?: string }): Promise<FetchLinkOutcome> {
  const baseBody = fetchLinkBody(it.url, serverQuality(it));
  const rawCookieJar = it.extCookieJar || extUrlCookies.get((it.url || '').trim()) || '';
  const cookiesB64 = rawCookieJar ? toBase64Utf8(rawCookieJar) : '';
  const body = cookiesB64 ? { ...baseBody, user_cookies_b64: cookiesB64 } : baseBody;
  try {
    let token = await getAccessToken();
    let r = await apiFetch('/api/v1/fetch-link', { method: 'POST', body, token, headers: { 'X-VG-Source': 'desktop' }, timeoutMs: 45_000 });
    if (r.status === 401 && token) { // expired access token: refresh once
      token = await getAccessToken(true);
      r = await apiFetch('/api/v1/fetch-link', { method: 'POST', body, token, headers: { 'X-VG-Source': 'desktop' }, timeoutMs: 45_000 });
    }
    return parseFetchLink(r.status, r.data, { apiBase: API_BASE, audioOnly: it.audioOnly, fallbackTitle: it.title });
  } catch {
    return { kind: 'error', code: 'network', message: '' };
  }
}
