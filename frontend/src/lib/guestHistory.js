// Guest "Tải gần đây" lives in the browser only: the server no longer keeps a
// shared anonymous history (privacy). Signed-in users keep the server history.
// Every storage access is wrapped — localStorage throws in private mode.

const KEY = 'vg.guestHistory.v1';
const MAX = 20;

export function readGuestHistory() {
  try {
    const raw = localStorage.getItem(KEY);
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((r) => r && r.id) : [];
  } catch {
    return [];
  }
}

function write(list) {
  try { localStorage.setItem(KEY, JSON.stringify(list.slice(0, MAX))); } catch { /* private mode */ }
}

const newId = () => {
  try { if (crypto?.randomUUID) return crypto.randomUUID(); } catch { /* ignore */ }
  return `g${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
};

/**
 * Prepend one entry (newest first) and return the new list.
 * `data` is a /fetch-link response (or {} for a failure).
 */
export function addGuestHistory({ title, original_url, status = 'success', data = {} }) {
  const entry = {
    id: newId(),
    title: title || data.title || '',
    original_url: original_url || data.original_url || '',
    status,
    created_at: new Date().toISOString(),
    local_file_id: data.local_file_id || null,
    local_file_path: data.local_file_path || null,
    local_mp3_file_id: data.local_mp3_file_id || null,
    local_mp3_path: data.local_mp3_path || null,
    direct_mp4_url: data.direct_mp4_url || null,
    slugified_name: data.slugified_name || null,
  };
  const next = [entry, ...readGuestHistory()].slice(0, MAX);
  write(next);
  return next;
}

export function removeGuestHistory(id) {
  const next = readGuestHistory().filter((r) => r.id !== id);
  write(next);
  return next;
}

export function clearGuestHistory() {
  try { localStorage.removeItem(KEY); } catch { /* private mode */ }
  return [];
}
