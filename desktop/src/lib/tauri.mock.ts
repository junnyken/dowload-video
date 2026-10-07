// DEV-ONLY browser mock of the Tauri commands, events, API and auth, so the UI
// can run in a normal browser via `npm run dev`. Never loaded in a production
// build or inside Tauri (see lib/tauri.ts). Everything here is simulated.
import type { Channel, ChannelListing, ChannelVideo, DoneEvent, HistoryItem, ProbeFormat, ProbeResult, ProgressEvent } from './types';

if (!import.meta.env.DEV || '__TAURI_INTERNALS__' in window) {
  throw new Error('tauri.mock must never run outside a dev browser');
}

// ---- event bus -------------------------------------------------------------
const listeners = new Map<string, Set<(p: unknown) => void>>();
export function mockListen(event: string, cb: (p: unknown) => void) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event)!.add(cb);
  return () => void listeners.get(event)?.delete(cb);
}
function emit(event: string, payload: unknown) {
  listeners.get(event)?.forEach((f) => f(payload));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Call log for click-through tests (Playwright reads window.__vgMockLog): "POST /api/v1/..." and "start_download <url>".
export const mockLog: string[] = [];
(window as unknown as { __vgMockLog: string[] }).__vgMockLog = mockLog;
const err = (code: string, message: string) => ({ code, message });

// ---- fake media ------------------------------------------------------------
function thumb(seed: string): string {
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 360;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${h},70%,55%)"/><stop offset="1" stop-color="hsl(${(h + 60) % 360},70%,35%)"/></linearGradient></defs><rect width="320" height="180" fill="url(#g)"/><circle cx="160" cy="90" r="26" fill="rgba(255,255,255,.85)"/><path d="M152 76l24 14-24 14z" fill="hsl(${h},60%,30%)"/></svg>`;
  return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
}

function platformOf(url: string): string {
  const h = new URL(url).hostname.replace(/^www\./, '');
  if (h.includes('youtu')) return 'youtube';
  if (h.includes('tiktok')) return 'tiktok';
  if (h.includes('instagram')) return 'instagram';
  if (h.includes('facebook') || h.includes('fb.')) return 'facebook';
  if (h.includes('twitter') || h === 'x.com') return 'twitter';
  return h.split('.')[0] || 'other';
}

function fmt(h: number, size: number, merge = true): ProbeFormat {
  return { id: `f${h}`, label: `${h}p`, height: h, ext: 'mp4', vcodec: 'avc1', acodec: merge ? null : 'aac', fps: 30, filesize: size, requiresMerge: merge, audioOnly: false };
}

async function mockProbe(url: string): Promise<ProbeResult> {
  await sleep(url.includes('slow') ? 6000 : 700 + Math.random() * 900);
  if (url.includes('private')) throw err('private_or_login', 'login required');
  if (url.includes('notfound')) throw err('not_found', '404');
  const platform = platformOf(url);
  const n = url.length % 5;
  const formats: ProbeFormat[] =
    platform === 'tiktok'
      ? [fmt(1080, 18e6, false), fmt(720, 11e6, false)]
      : [fmt(2160, 640e6), fmt(1440, 310e6), fmt(1080, 142e6), fmt(720, 78e6), fmt(480, 41e6), fmt(360, 25e6)];
  formats.push({ id: 'a1', label: 'Âm thanh', height: null, ext: 'm4a', vcodec: null, acodec: 'aac', fps: null, filesize: 9.4e6, requiresMerge: false, audioOnly: true });
  const titles = [
    'Hướng dẫn nấu phở bò Hà Nội chuẩn vị, nước dùng trong và ngọt thanh từ xương',
    'Review chi tiết laptop mỏng nhẹ 2026',
    'Cảnh hoàng hôn Đà Nẵng timelapse 4K',
    'Podcast: Khởi nghiệp từ con số 0',
    'Short vui nhộn #12',
  ];
  return {
    url, platform, title: titles[n], thumbnail: thumb(url), duration: 120 + n * 217,
    uploader: ['Bếp Nhà Mình', 'TechVN', 'Travel Clips', 'Startup Talk', '@vuive'][n], formats,
  };
}

// ---- downloads simulation --------------------------------------------------
type Sim = { timer: ReturnType<typeof setInterval>; pct: number; url: string; outDir: string; audio: boolean };
const sims = new Map<string, Sim>();
const partial = new Map<string, number>();

const forbiddenOnce = new Set<string>();
function startSim(a: { jobId: string; url: string; outDir: string; audioOnly?: boolean; useCookies?: boolean }) {
  if (sims.has(a.jobId)) return;
  // localStorage mock.localFail=1: every local yt-dlp run fails like an extractor error (L0/L1), so the queue
  // moves to the server step (S0). Downloads of the server's file / direct link (S0, Douyin) still work.
  const serverFile = a.url.includes('/api/v1/download-local') || a.url.includes('example.invalid');
  if (localStorage.getItem('mock.localFail') === '1' && !serverFile) {
    setTimeout(() => emit('download://done', { jobId: a.jobId, state: 'failed', errorCode: 'unknown', errorMessage: 'ERROR: Unable to extract video data (mock)', cookiesUsed: a.useCookies && mockCookiePlatform(a.url) != null && mockCookies.has(mockCookiePlatform(a.url)!) ? true : undefined } satisfies DoneEvent), 500);
    return;
  }
  // Like Rust: cookies only when asked AND a blob is saved for the URL's platform.
  const ck = a.useCookies ? mockCookiePlatform(a.url) : null;
  const cookiesUsed = ck != null && mockCookies.has(ck) ? true : undefined;
  if (cookiesUsed && localStorage.getItem('mock.cookieExpired') === '1') {
    // localStorage mock.cookieExpired=1: every run with cookies fails like yt-dlp's "Fresh cookies are needed".
    mockCookies.get(ck!)!.suspectExpired = true;
    setTimeout(() => emit('download://done', { jobId: a.jobId, state: 'failed', errorCode: 'private_or_login', errorMessage: 'ERROR: [Douyin] 1: Fresh cookies (not necessarily logged in) are needed', cookiesUsed } satisfies DoneEvent), 600);
    return;
  }
  if (a.url.includes('example.invalid') && localStorage.getItem('mock.douyin403') === '1' && !forbiddenOnce.has(a.jobId)) {
    forbiddenOnce.add(a.jobId); // first direct link "expired": the app must ask the server for a fresh one
    setTimeout(() => emit('download://done', { jobId: a.jobId, state: 'failed', errorCode: 'forbidden', errorMessage: 'HTTP Error 403: Forbidden' } satisfies DoneEvent), 300);
    return;
  }
  let pct = partial.get(a.jobId) ?? 0;
  const total = 142e6;
  const timer = setInterval(() => {
    pct += 1.2 + Math.random() * 1.6;
    const sim = sims.get(a.jobId)!;
    sim.pct = pct;
    if (a.url.includes('failme') && pct > 40) {
      clearInterval(timer); sims.delete(a.jobId); partial.delete(a.jobId);
      emit('download://done', { jobId: a.jobId, state: 'failed', errorCode: 'network', errorMessage: 'connection reset' } satisfies DoneEvent);
      return;
    }
    if (pct >= 100) {
      clearInterval(timer);
      emit('download://progress', { jobId: a.jobId, stage: 'merging', percent: 100, downloadedBytes: total, totalBytes: total, speedBps: null, etaSec: null } satisfies ProgressEvent);
      setTimeout(() => {
        sims.delete(a.jobId); partial.delete(a.jobId);
        emit('download://done', { jobId: a.jobId, state: 'completed', filePath: `${a.outDir}\\video-${a.jobId.slice(0, 6)}.${a.audioOnly ? 'm4a' : 'mp4'}`, fileSize: total, cookiesUsed } satisfies DoneEvent);
      }, 1200);
      return;
    }
    emit('download://progress', {
      jobId: a.jobId, stage: 'downloading', percent: pct, downloadedBytes: (pct / 100) * total, totalBytes: total,
      speedBps: 2.4e6 + Math.random() * 1.2e6, etaSec: ((100 - pct) / 100) * total / 3e6,
    } satisfies ProgressEvent);
  }, 400);
  sims.set(a.jobId, { timer, pct, url: a.url, outDir: a.outDir, audio: !!a.audioOnly });
}

// ---- history ---------------------------------------------------------------
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const history: HistoryItem[] = [
  { id: 'h1', url: 'https://www.youtube.com/watch?v=abc1', title: 'Hướng dẫn nấu phở bò Hà Nội chuẩn vị', platform: 'youtube', formatLabel: '1080p', filePath: 'C:\\Users\\demo\\Downloads\\VidGrab\\pho-bo.mp4', fileSize: 142e6, state: 'completed', errorCode: null, createdAt: ago(35), finishedAt: ago(33), synced: true },
  { id: 'h2', url: 'https://www.tiktok.com/@vuive/video/7311', title: 'Short vui nhộn #12', platform: 'tiktok', formatLabel: '720p', filePath: 'C:\\Users\\demo\\Downloads\\VidGrab\\short12.mp4', fileSize: 11e6, state: 'completed', errorCode: null, createdAt: ago(240), finishedAt: ago(239), synced: false },
  { id: 'h3', url: 'https://www.instagram.com/reel/xyz/', title: 'Reel du lịch Sapa', platform: 'instagram', formatLabel: 'Tốt nhất', filePath: null, fileSize: null, state: 'failed', errorCode: 'private_or_login', createdAt: ago(1500), finishedAt: ago(1499), synced: false },
  { id: 'h4', url: 'https://www.youtube.com/watch?v=podc', title: 'Podcast: Khởi nghiệp từ con số 0', platform: 'youtube', formatLabel: 'Chỉ âm thanh', filePath: 'C:\\Users\\demo\\Downloads\\VidGrab\\podcast.m4a', fileSize: 38e6, state: 'completed', errorCode: null, createdAt: ago(3000), finishedAt: ago(2998), synced: true },
];


// ---- channels ---------------------------------------------------------------
const MOCK_CHANNEL_ID = 'UCmockbep0000';
const day = (n: number) => { const d = new Date(Date.now() - n * 86_400_000); return d.toISOString().slice(0, 10).replace(/-/g, ''); };
const TOPICS = ['Phở bò', 'Bún chả', 'Cơm tấm', 'Bánh xèo', 'Gỏi cuốn', 'Chè đậu xanh', 'Canh chua', 'Bò kho', 'Xôi gấc', 'Bánh mì'];
function mockVideo(i: number): ChannelVideo {
  return {
    id: `mv${i}`, url: `https://www.youtube.com/watch?v=mv${i}`,
    title: `${TOPICS[i % TOPICS.length]} — công thức số ${i >= 1000 ? 300 + i - 999 : 300 - i}, làm tại nhà cực dễ${i % 7 === 0 ? ' và rất ngon, ai xem cũng mê' : ''}`,
    duration: 180 + ((i * 53) % 900), uploadDate: day(i >= 1000 ? 0 : i * 2), thumbnail: thumb('mv' + i),
  };
}
const MOCK_VIDEOS = Array.from({ length: 300 }, (_, i) => mockVideo(i));

async function mockChannelFetch(url: string, limit?: number): Promise<ChannelListing> {
  await sleep(url.includes('slow') ? 6000 : 900);
  if (url.includes('private')) throw err('private_or_login', 'login required');
  if (url.includes('notfound')) throw err('not_found', '404');
  const lim = Math.min(limit ?? 200, 5000);
  // Dev knob: localStorage mock.newVideos=N makes N extra, newer videos appear.
  const extra = Number(localStorage.getItem('mock.newVideos') ?? 0);
  const fresh = Array.from({ length: extra }, (_, i) => mockVideo(1000 + i));
  const all = [...fresh, ...MOCK_VIDEOS];
  return {
    channelId: MOCK_CHANNEL_ID, url, title: 'Bếp Nhà Mình (mô phỏng)', platform: platformOf(url), uploader: 'Bếp Nhà Mình',
    thumbnail: thumb('chan'), videos: all.slice(0, lim), truncated: all.length > lim,
  };
}

const mockChannels: Channel[] = [];
const mockSeen = new Map<string, Set<string>>();
if (localStorage.getItem('mock.seedChannels') === '1') {
  const base = { platform: 'youtube', mode: 'download' as const, quality: '1080', checkEveryHours: 6 as const, enabled: true, lastError: null, createdAt: ago(9000) };
  mockChannels.push(
    { ...base, id: MOCK_CHANNEL_ID, url: 'https://www.youtube.com/@bepnhaminh', title: 'Bếp Nhà Mình (mô phỏng)', thumbnail: thumb('chan'), outDir: 'C:\\Users\\demo\\Downloads\\VidGrab\\Bếp Nhà Mình', lastCheckedAt: ago(95), pendingNew: [] },
    { ...base, id: 'UCmocktech', url: 'https://www.youtube.com/@techvn', title: 'TechVN Review', thumbnail: thumb('tech'), mode: 'notify', checkEveryHours: 3, quality: 'best', outDir: 'D:\\Videos\\TechVN', lastCheckedAt: ago(40), pendingNew: [1001, 1002, 1003, 1004].map(mockVideo) },
    { ...base, id: 'UCmocktt', url: 'https://www.tiktok.com/@vuive', title: '@vuive', platform: 'tiktok', thumbnail: null, quality: '720', checkEveryHours: 12, enabled: false, outDir: 'C:\\Users\\demo\\Downloads\\VidGrab\\vuive', lastCheckedAt: ago(2000), lastError: 'network', pendingNew: [] },
  );
}
let autostart = false;

// ---- platform accounts (cookies_*): nothing real, no cookie values at all ------------
// localStorage switches: mock.cookiePlatforms=douyin,instagram (server list in /client/version),
// mock.cookieEmpty=1 ("Xong" finds no cookie), mock.cookieExpired=1 (runs with cookies fail with a login error).
const mockCookies = new Map<string, { savedAt: number; suspectExpired: boolean }>();
const mockLoginOpen = new Set<string>();
const COOKIE_SLUGS = ['douyin', 'instagram', 'facebook', 'twitter', 'youtube', 'bilibili', 'threads', 'reddit', 'pinterest', 'tiktok', 'vimeo'];
function mockCookiePlatform(url: string): string | null {
  const h = new URL(url).hostname.replace(/^www\./, '');
  if (/(^|\.)(douyin|iesdouyin)\.com$/.test(h)) return 'douyin';
  if (h === 'x.com' || h === 'twitter.com') return 'twitter';
  if (h === 'youtu.be' || h.endsWith('youtube.com')) return 'youtube';
  const p = h.split('.').slice(-2, -1)[0] ?? '';
  return COOKIE_SLUGS.includes(p) ? p : null;
}
function mockCookieCmd(cmd: string, platform: string): unknown {
  if (cmd !== 'cookies_status' && !COOKIE_SLUGS.includes(platform)) throw err('unknown', 'unknown platform');
  switch (cmd) {
    case 'cookies_login_open': mockLoginOpen.add(platform); return null;
    case 'cookies_login_finish': {
      if (!mockLoginOpen.has(platform)) throw err('cancelled', 'the login window is not open');
      if (localStorage.getItem('mock.cookieEmpty') === '1') throw err('cookie_required', 'no cookie of this platform in the login window yet');
      mockLoginOpen.delete(platform);
      const savedAt = Math.floor(Date.now() / 1000);
      mockCookies.set(platform, { savedAt, suspectExpired: false });
      setTimeout(() => emit('cookies://login-closed', { platform }), 50);
      return { platform, cookieCount: 7, savedAt };
    }
    case 'cookies_clear':
      mockCookies.delete(platform);
      if (mockLoginOpen.delete(platform)) setTimeout(() => emit('cookies://login-closed', { platform }), 50);
      return null;
    case 'cookies_status':
      return COOKIE_SLUGS.map((p) => {
        const c = mockCookies.get(p);
        return { platform: p, saved: !!c, savedAt: c?.savedAt ?? null, suspectExpired: !!c?.suspectExpired };
      });
  }
  throw err('unknown', cmd);
}

// ---- Douyin on this machine (douyin_resolve_local, 0.7.2): no hidden window here ------
// localStorage mock.douyinLocalFail=1: the hidden page gives no link (timeout) -> the queue
// refunds the claim and falls back to the server route. mock.douyin403=1 also hits this
// local link once (example.invalid), which exercises the download-403 fallback.
async function mockDouyinLocal(url: string) {
  if (!mockCookies.has('douyin')) throw err('cookie_required', 'no saved Douyin cookies');
  await sleep(1200);
  if (localStorage.getItem('mock.douyinLocalFail') === '1') throw err('timeout', 'the Douyin page did not give the video within 30 s');
  const id = /(\d{8,25})/.exec(url)?.[1] ?? '7000000000000000100';
  return {
    id, url: `https://example.invalid/local/${id}.mp4`, title: `Video Douyin trên máy ${id.slice(-3)}`, author: 'Kênh Douyin mô phỏng',
    durationSec: 31, headers: { Referer: 'https://www.douyin.com/', 'User-Agent': 'MockWebView/1.0' },
  };
}

// ---- invoke ----------------------------------------------------------------
let authBlob: string | null = null;
export async function mockInvoke(cmd: string, a: Record<string, unknown>): Promise<unknown> {
  await sleep(40);
  switch (cmd) {
    case 'probe': return mockProbe(a.url as string);
    case 'cancel_probe': return null;
    case 'start_download': mockLog.push(`start_download ${a.url as string}`); startSim(a as never); return null;
    case 'pause_download': {
      const s = sims.get(a.jobId as string);
      if (s) { clearInterval(s.timer); partial.set(a.jobId as string, s.pct); sims.delete(a.jobId as string); }
      emit('download://done', { jobId: a.jobId as string, state: 'paused' } satisfies DoneEvent);
      return null;
    }
    case 'cancel_download': {
      const s = sims.get(a.jobId as string);
      if (s) clearInterval(s.timer);
      sims.delete(a.jobId as string); partial.delete(a.jobId as string);
      emit('download://done', { jobId: a.jobId as string, state: 'cancelled' } satisfies DoneEvent);
      return null;
    }
    case 'pick_folder': return 'D:\\Videos\\VidGrab';
    case 'default_download_dir': return 'C:\\Users\\demo\\Downloads\\VidGrab';
    case 'disk_free': return 84e9;
    case 'reveal_path': case 'open_path': return null;
    case 'history_list': {
      const q = ((a.query as string) ?? '').toLowerCase();
      return history.filter((h) => !q || h.title.toLowerCase().includes(q) || h.url.toLowerCase().includes(q))
        .sort((x, y) => y.createdAt.localeCompare(x.createdAt)).slice((a.offset as number) ?? 0, ((a.offset as number) ?? 0) + ((a.limit as number) ?? 500));
    }
    case 'history_add': {
      const it = a.item as HistoryItem;
      const i = history.findIndex((h) => h.id === it.id);
      if (i >= 0) history[i] = it; else history.push(it);
      return null;
    }
    case 'history_delete': { const i = history.findIndex((h) => h.id === a.id); if (i >= 0) history.splice(i, 1); return null; }
    case 'history_clear': history.length = 0; return null;
    case 'history_mark_synced': history.forEach((h) => { if ((a.ids as string[]).includes(h.id)) h.synced = true; }); return null;
    case 'auth_save': authBlob = a.session as string; return null;
    case 'auth_load': return authBlob;
    case 'auth_clear': authBlob = null; return null;
    case 'browser_login': return mockBrowserLogin();
    case 'cancel_browser_login': mockLoginCancel?.(); return null;
    case 'channel_fetch': return mockChannelFetch(a.url as string, a.limit as number | undefined);
    case 'cancel_channel_fetch': return null;
    case 'channel_save': {
      const c = a.channel as Channel;
      const i = mockChannels.findIndex((x) => x.id === c.id);
      if (i >= 0) mockChannels[i] = c; else mockChannels.push(c);
      return null;
    }
    case 'channel_list': return structuredClone(mockChannels);
    case 'channel_delete': {
      const i = mockChannels.findIndex((x) => x.id === a.id);
      if (i >= 0) mockChannels.splice(i, 1);
      mockSeen.delete(a.id as string);
      return null;
    }
    case 'channel_seen_add': {
      const set = mockSeen.get(a.channelId as string) ?? new Set<string>();
      (a.videoIds as string[]).forEach((v) => set.add(v));
      mockSeen.set(a.channelId as string, set);
      return null;
    }
    case 'channel_seen_list': return [...(mockSeen.get(a.channelId as string) ?? new Set<string>(MOCK_VIDEOS.map((v) => v.id)))];
    case 'notify': console.info('[mock notify]', a.title, a.body); return null;
    case 'autostart_get': return autostart;
    case 'autostart_set': autostart = a.enabled as boolean; return null;
    case 'set_close_to_tray': return null;
    case 'get_version': return '0.7.2-dev';
    case 'cookies_login_open': case 'cookies_login_finish': case 'cookies_clear': case 'cookies_status':
      return mockCookieCmd(cmd, (a.platform as string) ?? '');
    case 'douyin_resolve_local': return mockDouyinLocal(a.url as string);
    case 'device_info': return { hash: 'a1b2c3d4'.repeat(8), code: 'A1B2C3D4', displayName: 'PC-MOCK (Windows 11 24H2, build 26100)', source: 'machine' };
    case 'tool_versions': return { ytdlp: '2026.09.30', ffmpeg: '7.1', deno: '2.5.0' };
  }
  throw err('unknown', `mock: unknown command ${cmd}`);
}

// ---- API (dvid-api) --------------------------------------------------------
export async function mockApi<T>(path: string, opts: { method?: string; body?: unknown; token?: string | null }): Promise<{ status: number; data: T | null }> {
  mockLog.push(`${opts.method ?? 'GET'} ${path.split('?')[0]}`);
  await sleep(250);
  const r = (status: number, data: unknown) => ({ status, data: data as T });
  if (path.startsWith('/api/v1/client/version')) {
    const cookiePlatforms = (localStorage.getItem('mock.cookiePlatforms') ?? '').split(',').map((x) => x.trim()).filter(Boolean);
    // localStorage mock.serverFallback=youtube,tiktok (default: every platform the server knows; "" = none).
    const fb = localStorage.getItem('mock.serverFallback');
    const serverFallbackPlatforms = fb == null ? ALL_FALLBACK : fb.split(',').map((x) => x.trim()).filter(Boolean);
    return r(200, {
      latest: '0.2.0', minSupported: '0.1.0', notes: 'Bản mô phỏng', downloadUrl: 'https://dvid.vibe1.tinhgon.xyz/download',
      features: { clientQuota: true, clientQuotaMode: 'enforce', offlineGrace: 3, cookiePlatforms, serverFallbackPlatforms },
    });
  }
  if (path.startsWith('/api/v1/fetch-link')) return mockFetchLink(opts.body as { url?: string; quality?: string }, !!opts.token) as { status: number; data: T | null };
  if (path.startsWith('/api/v1/client/quota')) return mockQuota(path, opts.method ?? 'GET', opts.body as { url?: string; retro?: boolean; claimId?: string; outcome?: string }, !!opts.token) as { status: number; data: T | null };
  if (path.startsWith('/api/v1/client/douyin/')) return mockDouyin(path, opts.body as { url?: string; limit?: number }, !!opts.token) as { status: number; data: T | null };
  if (!opts.token) return r(401, { detail: 'unauthorized' });
  if (path.startsWith('/api/v1/client/history') && opts.method === 'POST') {
    if (localStorage.getItem('mock.sync503') === '1') return r(503, { detail: 'client_api_disabled' });
    const items = (opts.body as { items: { clientId: string }[] }).items;
    return r(200, { accepted: items.length, ids: items.map((i) => i.clientId) });
  }
  if (path.startsWith('/api/v1/history')) {
    const off = Number(new URL('http://x' + path).searchParams.get('offset') ?? 0);
    const rows = [
      { id: 'w1', original_url: 'https://www.youtube.com/watch?v=web1', title: 'Học React 19 trong 30 phút', status: 'success', created_at: ago(60), platform: 'youtube' },
      { id: 'w2', original_url: 'https://www.tiktok.com/@a/video/22', title: 'Clip nấu ăn nhanh', status: 'success', created_at: ago(900), platform: 'tiktok' },
      { id: 'w3', original_url: 'https://www.facebook.com/watch/?v=33', title: 'Video chia sẻ kỷ niệm', status: 'failed', created_at: ago(4000), platform: 'facebook' },
    ];
    return r(200, { success: true, jobs: off === 0 ? rows : [] });
  }
  return r(404, null);
}

// ---- auth ------------------------------------------------------------------
// Browser sign-in: "the user signs in in the browser" after 2.5 s, unless
// cancelled. localStorage mock.login = 'timeout' simulates the 5-minute timeout.
let mockLoginCancel: (() => void) | null = null;
function mockBrowserLogin(): Promise<string> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => {
      mockLoginCancel = null;
      if (localStorage.getItem('mock.login') === 'timeout') reject(err('timeout', 'browser sign-in timed out'));
      else resolve('mockrefreshtoken');
    }, 2500);
    mockLoginCancel = () => { clearTimeout(t); mockLoginCancel = null; reject(err('cancelled', 'browser sign-in cancelled')); };
  });
}
export async function mockExchange(refreshToken: string) {
  await sleep(300);
  if (refreshToken !== 'mockrefreshtoken') throw { message: 'Invalid Refresh Token', status: 400, code: 'refresh_token_not_found' };
  return { access_token: 'mock-token', user: { email: 'ban@example.com' } };
}

// ---- Douyin via the server (MOCK: invented names and ids, nothing real) ---------
// Daily allowance like the server's: guest 5, signed in 20. localStorage
// mock.douyinQuota=0 makes every video call answer "hết lượt"; mock.douyin403=1
// makes the first direct link of each video look expired (to see the retry).
const dyUsed = { n: 0 };
function mockDouyin(path: string, body: { url?: string; limit?: number }, signedIn: boolean) {
  const r = (status: number, data: unknown) => ({ status, data });
  const url = body?.url ?? '';
  if (!/douyin\.com|iesdouyin\.com/.test(url)) return r(400, { detail: 'Liên kết này không phải của Douyin.', error_code: 'unsupported_url' });
  const allow = signedIn ? 20 : 5;
  const left = localStorage.getItem('mock.douyinQuota') === '0' ? 0 : Math.max(0, allow - dyUsed.n);
  if (path.endsWith('/channel')) {
    const n = Math.min(body.limit ?? 50, 40, left);
    if (n <= 0) return r(422, { detail: 'Bạn đã hết lượt tải hôm nay (bản mô phỏng).', error_code: 'quota_exceeded' });
    const items = Array.from({ length: n }, (_, i) => ({
      id: `7000000000000000${100 + i}`, url: `https://www.douyin.com/video/7000000000000000${100 + i}`,
      title: `Video Douyin mô phỏng số ${i + 1}`, thumbnail: thumb('dy' + i), durationSec: 12 + i * 7,
    }));
    return r(200, { platform: 'douyin', channelTitle: 'Kênh Douyin mô phỏng', channelUrl: 'https://www.douyin.com/user/MS4wLjABAAAAmockmockmock', cap: left, fromCache: false, items });
  }
  if (path.endsWith('/video')) {
    if (left <= 0) {
      return r(signedIn ? 403 : 429, { detail: 'Bạn đã hết lượt tải trong hôm nay (bản mô phỏng).', error_code: 'quota_exceeded_daily' });
    }
    dyUsed.n++;
    const id = /(\d{6,})/.exec(url)?.[1] ?? '7000000000000000100';
    return r(200, {
      platform: 'douyin', id, url: `https://www.douyin.com/video/${id}`, title: `Video Douyin mô phỏng ${id.slice(-3)}`, uploader: 'Kênh Douyin mô phỏng',
      thumbnail: thumb('dy' + id), durationSec: 31, directUrl: `https://example.invalid/mock/${id}.mp4`, audioUrl: null,
      headers: { Referer: 'https://www.douyin.com/', 'User-Agent': 'MockAgent/1.0' }, expiresAt: new Date(Date.now() + 3_600_000).toISOString(), cacheHit: false,
    });
  }
  return r(404, null);
}

// ---- daily allowance (client_quota.py). localStorage switches: mock.quotaDisabled=1 (503),
// mock.quotaLeft=0 (everything refused), mock.quotaOffline=1 (network error), mock.quotaUsed=N (used at page load),
// mock.quotaStale=1 (GET /client/quota keeps saying nothing is used: the claim answers are the truth).
// Guest 5/day, signed in 20. Like the server, a URL counts once a day (claim, claim-batch and /fetch-link share it).
const qMock = { used: Number(localStorage.getItem('mock.quotaUsed') ?? 0) || 0, claims: new Map<string, string>(), counted: new Set<string>() };
/** One counted download for `url` unless it already counted today. false = the allowance is used up. */
function qCount(url: string, limit: number): boolean {
  if (qMock.counted.has(url)) return true;
  if (localStorage.getItem('mock.quotaLeft') === '0' || qMock.used >= limit) return false;
  qMock.used++;
  qMock.counted.add(url);
  return true;
}
const ALL_FALLBACK = ['youtube', 'tiktok', 'instagram', 'facebook', 'twitter', 'threads', 'reddit', 'pinterest', 'vimeo', 'bilibili', 'soundcloud', 'youku', 'mgtv', 'iqiyi', 'kuaishou', 'xiaohongshu'];

// ---- POST /fetch-link (routes.py), the S0 route. localStorage mock.fetchShape=direct answers with a
// direct_mp4_url (no server file); default: the server's own file (local_file_id / local_mp3_file_id).
// mock.fetchFail=1 answers like an extraction failure.
function mockFetchLink(body: { url?: string; quality?: string }, signedIn: boolean) {
  const r = (status: number, data: unknown) => ({ status, data });
  const url = body?.url ?? '';
  const limit = signedIn ? 20 : 5;
  if (!qCount(url, limit)) {
    return r(signedIn ? 403 : 429, { detail: {
      error_code: 'quota_exceeded_daily', user_message: 'Bạn đã hết lượt tải hôm nay.',
      message: 'Bạn đã dùng hết lượt tải hôm nay (bản mô phỏng, máy chủ). Lượt mới lúc 07:00.',
      downloads_today: limit, daily_limit: limit, remaining: 0, reset_time_vn: '07:00', requester: signedIn ? 'user' : 'anon',
    } });
  }
  if (localStorage.getItem('mock.fetchFail') === '1') {
    return r(500, { detail: 'Máy chủ không lấy được video này (bản mô phỏng).', error_code: 'processing_failed', user_message: 'x', retryable: true });
  }
  const id = Array.from(url).reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7).toString(16).padStart(8, '0');
  const audio = (body.quality ?? '').startsWith('mp3');
  const title = `Video qua máy chủ ${id.slice(0, 4)} (mô phỏng)`;
  if (localStorage.getItem('mock.fetchShape') === 'direct') {
    return r(200, { success: true, title, thumbnail_url: thumb(url), direct_mp4_url: `https://example.invalid/server/${id}.mp4`, local_file_id: null, local_mp3_file_id: null, duration: 60, file_size_mb: 12 });
  }
  return r(200, {
    success: true, title, thumbnail_url: thumb(url), direct_mp4_url: '',
    local_file_id: `${id}${'0'.repeat(24)}.mp4`, local_mp3_file_id: audio ? `${id}${'1'.repeat(24)}.mp3` : null, duration: 60, file_size_mb: 12,
  });
}

function mockQuota(path: string, method: string, body: { url?: string; retro?: boolean; claimId?: string; outcome?: string; items?: { url: string }[]; route?: string }, signedIn: boolean) {
  const r = (status: number, data: unknown) => ({ status, data });
  if (localStorage.getItem('mock.quotaOffline') === '1') throw { code: 'network', message: 'mock offline' };
  if (localStorage.getItem('mock.quotaDisabled') === '1') return r(503, { detail: 'Tính năng đếm lượt tải của app Windows chưa được bật.', error_code: 'client_quota_disabled' });
  const limit = signedIn ? 20 : 5;
  const used = localStorage.getItem('mock.quotaLeft') === '0' ? limit : qMock.used;
  const refusal = () => ({
    allowed: false, error_code: 'quota_exceeded_daily', reason: 'daily_limit', upsell: signedIn ? 'upgrade' : 'signin', limit, usedToday: limit, remaining: 0, resetTimeVn: '07:00', requester: signedIn ? 'user' : 'device',
    detail: signedIn ? 'Bạn đã dùng hết 20 lượt tải hôm nay (bản mô phỏng). Lượt mới được cộng lại lúc 07:00.' : 'Bạn đã dùng hết 5 lượt tải của khách hôm nay (bản mô phỏng). Lượt mới được cộng lại lúc 07:00.',
  });
  const live = () => ({ limit, usedToday: Math.min(qMock.used, limit), remaining: Math.max(0, limit - qMock.used), resetTimeVn: '07:00', requester: signedIn ? 'user' : 'device' });
  if (path.endsWith('/quota/claim-batch') && method === 'POST') {
    const items = (body?.items ?? []).map(({ url }) => {
      if (!url || url.length < 8) return { url, allowed: false, error_code: 'invalid_url', detail: 'Link không hợp lệ.' };
      if (!qCount(url, limit)) return { url, ...refusal() };
      const claimId = `mockclaim${qMock.claims.size + 1}`;
      qMock.claims.set(claimId, url);
      return { url, allowed: true, claimId, platform: 'youtube', alreadyCounted: false, overLimit: false, mode: 'enforce', ...live() };
    });
    return r(200, { items, mode: 'enforce', ...live() });
  }
  const counters = () => ({ limit, usedToday: Math.min(used, limit), remaining: Math.max(0, limit - used), resetTimeVn: '07:00', requester: signedIn ? 'user' : 'device' });
  if (path.endsWith('/quota/claim') && method === 'POST') {
    if (!body?.url || body.url.length < 8) return r(400, { detail: 'Liên kết không hợp lệ.', error_code: 'invalid_url' });
    if (body.retro) { qMock.used++; qMock.counted.add(body.url); }
    else if (!qCount(body.url, limit)) return r(signedIn ? 403 : 429, refusal());
    const claimId = `mockclaim${qMock.claims.size + 1}`;
    qMock.claims.set(claimId, body.url);
    return r(200, { allowed: true, claimId, platform: 'youtube', alreadyCounted: false, overLimit: false, mode: 'enforce', ...{ ...counters(), usedToday: Math.min(qMock.used, limit), remaining: Math.max(0, limit - qMock.used) } });
  }
  if (path.endsWith('/quota/settle') && method === 'POST') {
    const claimed = qMock.claims.get(body?.claimId ?? '');
    const refunded = body?.outcome !== 'completed' && qMock.claims.delete(body?.claimId ?? '') && qMock.used > 0;
    if (refunded) { qMock.used--; if (claimed) qMock.counted.delete(claimed); }
    return r(200, { refunded: !!refunded, ...live() });
  }
  if (method === 'GET' && localStorage.getItem('mock.quotaStale') === '1') return r(200, { limit, usedToday: 0, remaining: limit, resetTimeVn: '07:00', requester: signedIn ? 'user' : 'device', deviceCode: 'A1B2C3D4', mode: 'enforce', enforced: true, offlineGrace: 3, refundDailyMax: 10 });
  if (method === 'GET') return r(200, { ...counters(), deviceCode: 'A1B2C3D4', mode: 'enforce', enforced: true, offlineGrace: 3, refundDailyMax: 10 });
  return r(404, null);
}
