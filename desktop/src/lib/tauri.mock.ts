// DEV-ONLY browser mock of the Tauri commands, events, API and auth, so the UI
// can run in a normal browser via `npm run dev`. Never loaded in a production
// build or inside Tauri (see lib/tauri.ts). Everything here is simulated.
import type { DoneEvent, HistoryItem, ProbeFormat, ProbeResult, ProgressEvent } from './types';

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

function startSim(a: { jobId: string; url: string; outDir: string; audioOnly?: boolean }) {
  if (sims.has(a.jobId)) return;
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
        emit('download://done', { jobId: a.jobId, state: 'completed', filePath: `${a.outDir}\\video-${a.jobId.slice(0, 6)}.${a.audioOnly ? 'm4a' : 'mp4'}`, fileSize: total } satisfies DoneEvent);
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

// ---- invoke ----------------------------------------------------------------
let authBlob: string | null = null;
export async function mockInvoke(cmd: string, a: Record<string, unknown>): Promise<unknown> {
  await sleep(40);
  switch (cmd) {
    case 'probe': return mockProbe(a.url as string);
    case 'cancel_probe': return null;
    case 'start_download': startSim(a as never); return null;
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
    case 'get_version': return '0.1.0-dev';
    case 'tool_versions': return { ytdlp: '2026.09.30', ffmpeg: '7.1', deno: '2.5.0' };
  }
  throw err('unknown', `mock: unknown command ${cmd}`);
}

// ---- API (dvid-api) --------------------------------------------------------
export async function mockApi<T>(path: string, opts: { method?: string; body?: unknown; token?: string | null }): Promise<{ status: number; data: T | null }> {
  await sleep(250);
  const r = (status: number, data: unknown) => ({ status, data: data as T });
  if (path.startsWith('/api/v1/client/version')) return r(200, { latest: '0.2.0', minSupported: '0.1.0', notes: 'Bản mô phỏng', downloadUrl: 'https://dvid.vibe1.tinhgon.xyz/download' });
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
export async function mockSignIn(email: string, password: string) {
  await sleep(500);
  if (password === 'wrong') throw { message: 'Invalid login credentials', status: 400, code: 'invalid_credentials' };
  if (password === 'offline') throw { message: 'fetch failed', name: 'AuthRetryableFetchError', status: 0 };
  return { access_token: 'mock-token', user: { email } };
}
