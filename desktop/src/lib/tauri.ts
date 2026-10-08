// Typed wrappers over Tauri commands/events (docs/desktop/C1-CONTRACT.md §1).
//
// Mock safety: the mock layer is used ONLY when (a) this is a Vite dev build
// (import.meta.env.DEV — statically false in `vite build`, so the mock code is
// dropped from the production bundle) AND (b) window.__TAURI_INTERNALS__ is
// absent. Inside Tauri the real invoke() is always used.
import { invoke } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import type { DeviceInfo } from './device';
import type { CookieStatus, DouyinLocal } from './cookies-core';
import type { Channel, ChannelListing, DoneEvent, HistoryItem, LogEvent, ProbeResult, ProgressEvent, ToolVersions } from './types';

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
  // useCookies: Rust picks the platform from the URL host and uses its saved blob, if any.
  probe: (url: string, useCookies?: boolean) => call<ProbeResult>('probe', { url, useCookies }),
  cancelProbe: (url: string) => call<null>('cancel_probe', { url }),
  startDownload: (a: {
    jobId: string; url: string; outDir: string; formatId?: string; audioOnly?: boolean;
    // Direct-link downloads (Douyin): request headers, and the file name parts (the URL has none).
    headers?: { name: string; value: string }[]; fileTitle?: string; fileId?: string;
    // The user's own cookies for this URL's platform (PLAN-32D L1); Rust decides which blob from the host.
    useCookies?: boolean;
    // PLAN-32E P3: the server's signed claim token; Rust checks it before yt-dlp starts.
    claimToken?: string;
  }) =>
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
  // Browser sign-in (C1-CONTRACT.md §5): resolves with a Supabase refresh token.
  browserLogin: () => call<string>('browser_login'),
  cancelBrowserLogin: () => call<null>('cancel_browser_login'),
  getVersion: () => call<string>('get_version'),
  deviceInfo: () => call<DeviceInfo>('device_info'),
  toolVersions: () => call<ToolVersions>('tool_versions'),
  // Channels (C1-CONTRACT.md section 4)
  channelFetch: (url: string, limit?: number, useCookies?: boolean) => call<ChannelListing>('channel_fetch', { url, limit, useCookies }),
  cancelChannelFetch: (url: string) => call<null>('cancel_channel_fetch', { url }),
  channelSave: (channel: Channel) => call<null>('channel_save', { channel }),
  channelList: () => call<Channel[]>('channel_list'),
  channelDelete: (id: string) => call<null>('channel_delete', { id }),
  channelSeenAdd: (channelId: string, videoIds: string[]) => call<null>('channel_seen_add', { channelId, videoIds }),
  channelSeenList: (channelId: string) => call<string[]>('channel_seen_list', { channelId }),
  notify: (title: string, body: string) => call<null>('notify', { title, body }),
  autostartGet: () => call<boolean>('autostart_get'),
  autostartSet: (enabled: boolean) => call<null>('autostart_set', { enabled }),
  setCloseToTray: (enabled: boolean) => call<null>('set_close_to_tray', { enabled }),
  // Platform accounts (PLAN-32D §3). No command ever returns a cookie.
  cookiesLoginOpen: (platform: string) => call<null>('cookies_login_open', { platform }),
  cookiesLoginFinish: (platform: string) => call<{ platform: string; cookieCount: number; savedAt: number }>('cookies_login_finish', { platform }),
  cookiesStatus: () => call<CookieStatus[]>('cookies_status'),
  cookiesClear: (platform: string) => call<null>('cookies_clear', { platform }),
  // Douyin on this machine (0.7.2): a hidden Douyin page with the saved cookies resolves the direct link (Rust checks it).
  douyinResolveLocal: (url: string, debug = false) => call<DouyinLocal>('douyin_resolve_local', { url, debug }),
};

export const onProgress = (cb: (e: ProgressEvent) => void) => on<ProgressEvent>('download://progress', cb);
export const onLog = (cb: (e: LogEvent) => void) => on<LogEvent>('download://log', cb);
export const onDone = (cb: (e: DoneEvent) => void) => on<DoneEvent>('download://done', cb);
// Tray menu "Kiểm tra kênh ngay" and the real quit (downloads must be paused within the 3 s grace).
export const onCheckNow = (cb: () => void) => on<unknown>('channels://check-now', () => cb());
export const onQuitting = (cb: () => void) => on<unknown>('app://quitting', () => cb());
// A login-<platform> window was closed (by "Xong", "Xoá" or the user).
export const onLoginClosed = (cb: (p: { platform: string; saved?: boolean | null }) => void) =>
  on<{ platform: string; saved?: boolean | null }>('cookies://login-closed', cb);
