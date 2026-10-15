// Auto cleanup (app 0.17.0, owner 15/10): when enabled in Settings, every
// 6 hours (and once at start) the files VidGrab downloaded that are older
// than N days, or the oldest ones while the download drive is below X GB,
// go to the Recycle Bin (Rust cleanup.rs decides which files; never a hard
// delete, never a file VidGrab did not produce).

import { api } from './tauri';
import { settings } from './settings';
import { notifySafe } from './channels';
import { formatBytes } from './format';

const EVERY_MS = 6 * 3_600_000;

export function cleanupArgs(): { olderThanDays?: number; minFreeGb?: number; dir?: string } | null {
  const s = settings.get();
  const c = s.cleanup;
  if (c.olderThanDays == null && c.minFreeGb == null) return null;
  return { olderThanDays: c.olderThanDays ?? undefined, minFreeGb: c.minFreeGb ?? undefined, dir: s.outDir ?? undefined };
}

/** One run now (also the Settings "Dọn ngay" button). Returns what moved. */
export async function runCleanup(): Promise<{ moved: number; bytes: number; failed: number } | null> {
  const a = cleanupArgs();
  if (!a) return null;
  const r = await api.cleanupRun(a);
  // wording: BA review
  if (r.moved) notifySafe('VidGrab', `Đã chuyển ${r.moved} video cũ (${formatBytes(r.bytes)}) vào Thùng rác.`);
  return r;
}

let started = false;
export function startCleanupWatch() {
  if (started) return;
  started = true;
  const tick = () => {
    if (settings.get().cleanup.enabled) void runCleanup().catch(() => { /* next tick */ });
  };
  setTimeout(tick, 60_000); // not during start-up
  setInterval(tick, EVERY_MS);
}
