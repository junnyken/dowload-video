// Queue schedule ("Hẹn giờ bắt đầu") + "Tắt máy khi tải xong" (14/10).
// Pure rules: schedule-core.ts. The queue reads `scheduleAllows()` in pump().
import { createStore } from './store';
import { startAllowed } from './schedule-core';

type Sched = { startAt: number | null; shutdownArmed: boolean };
const KEY = 'vg.schedule';

function load(): Sched {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    if (raw && typeof raw === 'object') {
      const startAt = typeof raw.startAt === 'number' && raw.startAt > Date.now() ? raw.startAt : null;
      // Never re-arm a shutdown across an app restart: the user must ask again.
      return { startAt, shutdownArmed: false };
    }
  } catch { /* corrupted */ }
  return { startAt: null, shutdownArmed: false };
}

export const schedule = createStore<Sched>(load());

function save() {
  try { localStorage.setItem(KEY, JSON.stringify(schedule.get())); } catch { /* quota */ }
}

export function setStartAt(t: number | null) {
  schedule.set((s) => ({ ...s, startAt: t }));
  save();
}

export function setShutdownArmed(on: boolean) {
  schedule.set((s) => ({ ...s, shutdownArmed: on }));
  save();
}

/** May the queue start downloads now? Clears a schedule whose time has come. */
export function scheduleAllows(now = Date.now()): boolean {
  const { startAt } = schedule.get();
  if (startAllowed(startAt, now)) {
    if (startAt != null) setStartAt(null);
    return true;
  }
  return false;
}
