// Update gate store + check (task #6125, PLAN-32E P1 step 3). The server's
// minimum version comes from GET /client/version (never gated) and from any
// 426 update_required answer (http.ts reports it here).
import { createStore } from './store';
import { NO_UPDATE, fromApiAnswer, fromVersionInfo, type UpdateInfo } from './update-core';

export const updateGate = createStore<UpdateInfo>(NO_UPDATE);

/** Called by http.ts for every API answer; only a 426 update_required matters. */
export function noteApiAnswer(status: number, data: unknown) {
  const u = fromApiAnswer(status, data);
  if (u) updateGate.set(u);
}

/** Ask the server what the minimum version is; sets or clears the gate. */
export async function checkUpdate(): Promise<UpdateInfo> {
  const [{ apiFetch }, { appVersion }] = await Promise.all([import('./http'), import('./device')]);
  try {
    const r = await apiFetch('/api/v1/client/version');
    if (r.status === 200) {
      const u = fromVersionInfo(await appVersion(), r.data);
      updateGate.set(u);
      return u;
    }
  } catch { /* offline: keep the current state */ }
  return updateGate.get();
}

let timer: ReturnType<typeof setInterval> | null = null;
export function initUpdateGate() {
  void checkUpdate();
  timer ??= setInterval(() => void checkUpdate(), 6 * 60 * 60 * 1000);
}
