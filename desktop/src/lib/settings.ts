import { createStore } from './store';
import type { ClipMode } from './clipboard-core';
import { cleanPresets, type Presets } from './presets-core';
import { api } from './tauri';

export type Quality = 'best' | '2160' | '1440' | '1080' | '720' | '480' | '360' | 'audio';
export type Theme = 'system' | 'light' | 'dark';

export type Settings = {
  outDir: string | null;
  concurrency: number;
  defaultQuality: Quality;
  theme: Theme;
  autoSync: boolean;
  closeToTray: boolean;
  /** Show the Douyin link window and keep it open on failure (debug, owner test 2026-10-07). */
  douyinDebugWindow: boolean;
  /** Clipboard link watch: ask before downloading, download at once, or off (14/10). */
  clipboardMode: ClipMode;
  /** Per-platform quality/folder for links (presets-core.ts, 14/10). */
  platformPresets: Presets;
  /** 0.17.0 file names (engine.rs output_template keys). */
  nameStyle: NameStyle;
  /** 0.17.0 sub-folders, applied to downloads into the main folder only (a channel already has its own folder). */
  folderStyle: FolderStyle;
  /** 0.17.0 auto cleanup to the Recycle Bin (cleanup.rs). null = that trigger is off. */
  cleanup: { enabled: boolean; olderThanDays: number | null; minFreeGb: number | null };
};

export type NameStyle = 'title' | 'date_title' | 'date_channel_title';
export type FolderStyle = 'none' | 'platform' | 'month' | 'platform_month';
export const NAME_STYLES: NameStyle[] = ['title', 'date_title', 'date_channel_title'];
export const FOLDER_STYLES: FolderStyle[] = ['none', 'platform', 'month', 'platform_month'];

const KEY = 'vg.settings';
const DEFAULTS: Settings = { outDir: null, concurrency: 2, defaultQuality: 'best', theme: 'system', autoSync: true, closeToTray: true, douyinDebugWindow: false, clipboardMode: 'ask', platformPresets: {},
  nameStyle: 'title', folderStyle: 'none', cleanup: { enabled: false, olderThanDays: 30, minFreeGb: null } };

/** Windows paths: case-insensitive, either slash, no trailing separator. */
export function samePath(a: string, b: string): boolean {
  const n = (p: string) => p.replace(/[\\/]+/g, '\\').replace(/\\$/, '').toLowerCase();
  return n(a) === n(b);
}

/** Days 1–3650, GB 0.5–10000, anything else = that trigger off. */
export function cleanCleanup(c: unknown): Settings['cleanup'] {
  const o = (c && typeof c === 'object' ? c : {}) as Record<string, unknown>;
  const days = Math.round(Number(o.olderThanDays));
  const gb = Number(o.minFreeGb);
  return {
    enabled: o.enabled === true,
    olderThanDays: o.olderThanDays != null && days >= 1 && days <= 3650 ? days : null,
    minFreeGb: o.minFreeGb != null && Number.isFinite(gb) && gb >= 0.5 && gb <= 10000 ? gb : null,
  };
}

function load(): Settings {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? 'null');
    if (raw && typeof raw === 'object') {
      const s = { ...DEFAULTS, ...raw } as Settings;
      s.concurrency = Math.min(4, Math.max(1, Math.round(Number(s.concurrency)) || 2));
      if (!['ask', 'auto', 'off'].includes(s.clipboardMode)) s.clipboardMode = 'ask';
      s.platformPresets = cleanPresets(s.platformPresets);
      if (!NAME_STYLES.includes(s.nameStyle)) s.nameStyle = 'title';
      if (!FOLDER_STYLES.includes(s.folderStyle)) s.folderStyle = 'none';
      s.cleanup = cleanCleanup(s.cleanup);
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
