import { API_BASE } from './config';
import { loadMock, mockMode } from './tauri';
import { vgHeaders } from './device';

export type HttpResult<T = unknown> = { status: number; data: T | null };

/** JSON request to the VidGrab API. Throws { code: 'network' } when unreachable. */
export async function apiFetch<T = unknown>(
  path: string,
  opts: { method?: 'GET' | 'POST'; body?: unknown; token?: string | null; headers?: Record<string, string> } = {},
): Promise<HttpResult<T>> {
  if (mockMode) return (await loadMock()).mockApi<T>(path, opts);
  const vg = await vgHeaders(); // X-VG-Device / X-VG-Client on every call (PLAN-32D §4)
  try {
    const res = await fetch(API_BASE + path, {
      method: opts.method ?? 'GET',
      headers: {
        Accept: 'application/json',
        ...vg,
        ...(opts.headers ?? {}),
        ...(opts.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        ...(opts.token ? { Authorization: `Bearer ${opts.token}` } : {}),
      },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    let data: T | null = null;
    try {
      data = (await res.json()) as T;
    } catch {
      /* empty or non-JSON body */
    }
    return { status: res.status, data };
  } catch {
    throw { code: 'network', message: 'fetch failed' };
  }
}
