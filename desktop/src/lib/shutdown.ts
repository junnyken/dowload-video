// "Tắt máy khi tải xong" (14/10): watches the queue while armed; when the last
// download finishes, opens a 60 s countdown (ShutdownDialog) the user can
// cancel. One-shot: disarmed when it fires; never re-armed across restarts.
import { queue } from './queue';
import { schedule, setShutdownArmed } from './schedule';
import { hasWork, shouldShutdown, SHUTDOWN_COUNTDOWN_S } from './schedule-core';
import { createStore } from './store';
import { api } from './tauri';

export const shutdownPrompt = createStore<{ deadline: number } | null>(null);

let started = false;
export function startShutdownWatch() {
  if (started) return;
  started = true;
  let hadWork = hasWork(queue.get());
  let baseline = new Set<string>();
  let armed = schedule.get().shutdownArmed;
  const completedIds = () => new Set(queue.get().filter((i) => i.state === 'completed').map((i) => i.id));

  schedule.subscribe(() => {
    const now = schedule.get().shutdownArmed;
    if (now && !armed) { baseline = completedIds(); hadWork = hasWork(queue.get()); }
    armed = now;
  });
  queue.subscribe(() => {
    const items = queue.get();
    const finished = armed ? [...completedIds()].filter((id) => !baseline.has(id)).length : 0;
    if (shouldShutdown(armed, hadWork, items, finished)) {
      setShutdownArmed(false);
      shutdownPrompt.set({ deadline: Date.now() + SHUTDOWN_COUNTDOWN_S * 1000 });
      // The window may be hidden in the tray: a toast so someone at the PC can open it and cancel. wording: BA review
      void api.notify('VidGrab', `Đã tải xong. Máy sẽ tắt sau ${SHUTDOWN_COUNTDOWN_S} giây — mở VidGrab để huỷ.`).catch(() => {});
    }
    hadWork = hasWork(items);
  });
}

export function cancelShutdown() {
  shutdownPrompt.set(null);
}
