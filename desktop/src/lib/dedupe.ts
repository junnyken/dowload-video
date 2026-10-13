// Already-downloaded check (pure rules in dedupe-core.ts).
import { sameVideo, shortDate, videoKey } from './dedupe-core';
import { queue } from './queue';
import { api } from './tauri';
import type { HistoryItem } from './types';

export type Duplicate =
  | { kind: 'queued' }
  | { kind: 'downloaded'; item: HistoryItem; date: string };

/** Is this video already in the queue, or downloaded before on this PC? */
export async function findDuplicate(url: string): Promise<Duplicate | null> {
  if (queue.get().some((q) => q.state !== 'failed' && q.state !== 'completed' && sameVideo(q.url, url))) {
    return { kind: 'queued' };
  }
  const key = videoKey(url);
  if (!key) return null;
  try {
    const rows = await api.historyList({ query: key, limit: 50 });
    const hit = rows.find((h) => h.state === 'completed' && sameVideo(h.url, url));
    return hit ? { kind: 'downloaded', item: hit, date: shortDate(hit.finishedAt ?? hit.createdAt) } : null;
  } catch {
    return null; // history unreadable: do not block the download
  }
}
