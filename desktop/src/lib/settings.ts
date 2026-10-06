import { createStore } from './store';
import { api } from './tauri';
import { newId } from './format';

export type Quality = 'best' | '2160' | '1440' | '1080' | '720' | '480' | '360' | 'audio';
export type Theme = 'system' | 'light' | 'dark';

export type Settings = {
  outDir: string | null;
  concurrency: number;
  defaultQuality: Quality;
  theme: Theme;
  autoSync: boolean;
  closeToTray: boolean;
};

const KEY = 'vg.settings';
const DEFAULTS: Settings = { outDir: null, concurrency: 2, defaultQuality: 'best', theme: 'system', autoSync: true, closeToTray: true };

function load(): Settings {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    if (raw && typeof raw === 'object') {
      const s = { ...DEFAULTS, ...raw } as Settings;
      s.concurrency = Math.min(4, Math.max(1, Math.round(Number(s.concurrency)) || 2));
      return s;
    }
  } catch { /* corrupted -> defaults */ }
  return DEFAULTS;
}

export const settings = createStore<Settings>(load());

export function updateSettings(patch: Partial<Settings>) {
  settings.set((s) => ({ ...s, ...patch }));
  try { localStorage.setItem(KEY, JSON.stringify(settings.get())); } catch { /* quota */ }
  if (patch.theme) applyTheme();
}

/** Stable per-install id sent with synced history (never personal). */
export function deviceId(): string {
  let id = localStorage.getItem('vg.deviceId');
  if (!id) {
    id = newId();
    localStorage.setItem('vg.deviceId', id);
  }
  return id;
}

export function applyTheme() {
  const t = settings.get().theme;
  const dark = t === 'dark' || (t === 'system' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
}

/** Resolves the output folder, filling the default from Rust on first use. */
export async function ensureOutDir(): Promise<string | null> {
  const cur = settings.get().outDir;
  if (cur) return cur;
  try {
    const d = await api.defaultDownloadDir();
    updateSettings({ outDir: d });
    return d;
  } catch {
    return null;
  }
}
