// Typed wrappers over Tauri commands/events (docs/desktop/C1-CONTRACT.md §1).
//
// Mock safety: the mock layer is used ONLY when (a) this is a Vite dev build
// (import.meta.env.DEV — statically false in `vite build`, so the mock code is
// dropped from the production bundle) AND (b) window.__TAURI_INTERNALS__ is
// absent. Inside Tauri the real invoke() is always used.
import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import type { DoneEvent, HistoryItem, LogEvent, ProbeResult, ProgressEvent, ToolVersions } from './types';

export const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
export const mockMode = import.meta.env.DEV && !inTauri;

type MockModule = typeof import('./tauri.mock');
let mockPromise: Promise<MockModule> | null = null;
export function loadMock(): Promise<MockModule> {
  if (!mockMode) throw { code: 'not_in_app', message: 'mock is disabled' };
  mockPromise ??= import('./tauri.mock');
  return mockPromise;
}

async function call<T>(cmd: string, args: Record<string, unknown> = {}): Promise<T> {
  if (inTauri) return invoke<T>(cmd, args);
  if (mockMode) return (await loadMock()).mockInvoke(cmd, args) as Promise<T>;
  throw { code: 'not_in_app', message: 'Not running inside Tauri' };
}

async function on<T>(event: string, cb: (p: T) => void): Promise<UnlistenFn> {
  if (inTauri) return listen<T>(event, (e) => cb(e.payload));
  if (mockMode) return (await loadMock()).mockListen(event, cb as (p: unknown) => void);
  return () => {};
}

export const api = {
  probe: (url: string) => call<ProbeResult>('probe', { url }),
  cancelProbe: (url: string) => call<null>('cancel_probe', { url }),
  startDownload: (a: { jobId: string; url: string; outDir: string; formatId?: string; audioOnly?: boolean }) =>
    call<null>('start_download', a),
  pauseDownload: (jobId: string) => call<null>('pause_download', { jobId }),
  cancelDownload: (jobId: string) => call<null>('cancel_download', { jobId }),
  pickFolder: () => call<string | null>('pick_folder'),
  defaultDownloadDir: () => call<string>('default_download_dir'),
  diskFree: (path: string) => call<number>('disk_free', { path }),
  revealPath: (path: string) => call<null>('reveal_path', { path }),
  openPath: (path: string) => call<null>('open_path', { path }),
  // Rust allows only https on dvid.vibe1.tinhgon.xyz / dvid-api.vibe1.tinhgon.xyz.
  openUrl: (url: string) => call<null>('open_url', { url }),
  historyList: (a: { limit?: number; offset?: number; query?: string } = {}) => call<HistoryItem[]>('history_list', a),
  historyAdd: (item: HistoryItem) => call<null>('history_add', { item }),
  historyDelete: (id: string) => call<null>('history_delete', { id }),
  historyClear: () => call<null>('history_clear'),
  historyMarkSynced: (ids: string[]) => call<null>('history_mark_synced', { ids }),
  authSave: (session: string) => call<null>('auth_save', { session }),
  authLoad: () => call<string | null>('auth_load'),
  authClear: () => call<null>('auth_clear'),
  getVersion: () => call<string>('get_version'),
  toolVersions: () => call<ToolVersions>('tool_versions'),
};

export const onProgress = (cb: (e: ProgressEvent) => void) => on<ProgressEvent>('download://progress', cb);
export const onLog = (cb: (e: LogEvent) => void) => on<LogEvent>('download://log', cb);
export const onDone = (cb: (e: DoneEvent) => void) => on<DoneEvent>('download://done', cb);
