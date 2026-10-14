// Queue schedule + "shut down when done" (market comparison 14/10, like
// Allavsoft). Pure, unit-tested (`npm test`).

type Item = { state: 'queued' | 'running' | 'paused' | 'completed' | 'failed' };

/** Next local time HH:MM after `now` (today if still ahead, else tomorrow). */
export function nextAt(hhmm: string, now: Date): number | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm.trim());
  if (!m) return null;
  const h = Number(m[1]), min = Number(m[2]);
  if (h > 23 || min > 59) return null;
  const t = new Date(now);
  t.setHours(h, min, 0, 0);
  if (t.getTime() <= now.getTime()) t.setDate(t.getDate() + 1);
  return t.getTime();
}

/** Downloads may start: no schedule, or its time has come. */
export function startAllowed(startAt: number | null, now: number): boolean {
  return startAt == null || now >= startAt;
}

/** Work left: something queued, running or paused (paused = the user may resume; never shut down on it). */
export function hasWork(items: Item[]): boolean {
  return items.some((i) => i.state === 'queued' || i.state === 'running' || i.state === 'paused');
}

/**
 * Shut down now? Only on the transition "had work → no work" while armed,
 * and only if at least one download ran to the end — never because the queue
 * was emptied by hand.
 */
export function shouldShutdown(armed: boolean, hadWork: boolean, items: Item[], finishedSinceArmed: number): boolean {
  return armed && hadWork && !hasWork(items) && finishedSinceArmed > 0;
}

export const SHUTDOWN_COUNTDOWN_S = 60;
