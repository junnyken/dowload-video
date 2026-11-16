import { API_BASE } from './config';
import { loadMock, mockMode } from './tauri';
import { vgHeaders } from './device';
import { noteApiAnswer } from './update';

export type HttpResult<T = unknown> = { status: number; data: T | null };

const DEFAULT_TIMEOUT_MS = 12_000;

/** JSON request to the VidGrab API. Throws { code: 'network' } when unreachable or timed out. */
export async function apiFetch<T = unknown>(
  path: string,
  opts: { method?: 'GET' | 'POST' | 'PATCH' | 'DELETE'; body?: unknown; token?: string | null; headers?: Record<string, string>; timeoutMs?: number } = {},
): Promise<HttpResult<T>> {
  if (mockMode) return (await loadMock()).mockApi<T>(path, opts);
  const vg = await vgHeaders(); // X-VG-Device / X-VG-Client on every call (PLAN-32D §4)
  // Always cap HTTP waits (13/10/2026: vibe1 host overloaded) so stalled
  // sockets abort cleanly instead of leaving UI actions spinning forever.
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const ctl = timeoutMs > 0 ? new AbortController() : null;
  const tm = ctl ? setTimeout(() => ctl.abort(), timeoutMs) : null;
  try {
    const res = await fetch(API_BASE + path, {
      signal: ctl?.signal,
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
    noteApiAnswer(res.status, data); // 426 update_required → blocking screen (PLAN-32E P1)
    if (res.status === 401) noteSignedOut(data);
    return { status: res.status, data };
  } catch {
    throw { code: 'network', message: 'fetch failed' };
  } finally {
    if (tm) clearTimeout(tm);
  }
}

/** 0.19.4 device linking (backend app/core/device_link.py): the account is on
 * more devices than its plan allows (Free 2 / Pro 5) and this one was signed
 * out — sign out here too and say why. Once per answer burst. */
let signedOutAt = 0;
function noteSignedOut(data: unknown) {
  const d = (data as { detail?: { error_code?: string; user_message?: string } } | null)?.detail;
  if (!d || d.error_code !== 'device_signed_out' || Date.now() - signedOutAt < 10_000) return;
  signedOutAt = Date.now();
  void Promise.all([import('./auth'), import('./ui')]).then(([a, ui]) => {
    void a.signOut();
    // wording: BA review
    ui.toast('error', d.user_message || 'Tài khoản đang dùng trên quá nhiều thiết bị nên máy này đã bị đăng xuất. Đăng nhập lại để dùng tiếp.');
  });
}
