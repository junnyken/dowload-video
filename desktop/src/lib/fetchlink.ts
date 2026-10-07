// S0 (PLAN-32D §2, P2): POST /api/v1/fetch-link asks the VidGrab server to get
// the video; the app then downloads the server's file (or the direct link) with
// its own yt-dlp. The server counts the download itself (per URL, once a day).
// Pure parsing: routes-core.ts. Links are never logged.
import { API_BASE } from './config';
import { apiFetch } from './http';
import { getAccessToken } from './auth';
import { fetchLinkBody, parseFetchLink, serverQuality, type FetchLinkOutcome } from './routes-core';

export async function fetchLink(it: { url: string; title: string; audioOnly: boolean; quality?: string; formatId?: string; formatLabel?: string }): Promise<FetchLinkOutcome> {
  const body = fetchLinkBody(it.url, serverQuality(it));
  try {
    let token = await getAccessToken();
    let r = await apiFetch('/api/v1/fetch-link', { method: 'POST', body, token, headers: { 'X-VG-Source': 'desktop' } });
    if (r.status === 401 && token) { // expired access token: refresh once
      token = await getAccessToken(true);
      r = await apiFetch('/api/v1/fetch-link', { method: 'POST', body, token, headers: { 'X-VG-Source': 'desktop' } });
    }
    return parseFetchLink(r.status, r.data, { apiBase: API_BASE, audioOnly: it.audioOnly, fallbackTitle: it.title });
  } catch {
    return { kind: 'error', code: 'network', message: '' };
  }
}
