// 0.22.0 (quality #7 app): translate the .srt written beside a finished download
// into Vietnamese with the server's existing translation jobs (the website's
// "Phụ đề → Dịch": POST /transcript-translate/upload, AI quota per account),
// then save it as `<tên>.vi.srt` next to the video. Rust (subs.rs) reads and
// writes the files; this module only talks to the server.

export type SubtransDeps = {
  list: (filePath: string) => Promise<string[]>;
  read: (filePath: string, lang: string) => Promise<string>;
  write: (filePath: string, lang: string, content: string) => Promise<string>;
  token: () => Promise<string | null>;
  /** fetch against the API host, with the app's X-VG headers */
  fetch: (path: string, init: RequestInit) => Promise<Response>;
  sleep: (ms: number) => Promise<void>;
  onProgress?: (pct: number) => void;
};

export type SubtransCode = 'login' | 'no_subtitle' | 'has_vi' | 'rejected' | 'failed' | 'timeout' | 'exists';

export class SubtransError extends Error {
  code: SubtransCode;
  constructor(code: SubtransCode, message: string) {
    super(message);
    this.code = code;
  }
}

const isVi = (l: string) => /^vi([-_]|$)/i.test(l);

/** Which subtitle to translate: none when Vietnamese is already there; English first. */
export function pickSource(langs: string[]): string | null | 'has_vi' {
  if (langs.some(isVi)) return 'has_vi';
  return langs.find((l) => /^en([-_]|$)/i.test(l)) ?? langs[0] ?? null;
}

type Job = { id: string; status: string; progress_pct?: number; error?: string | null };

const POLL_MS = 3000;
const MAX_WAIT_MS = 20 * 60 * 1000;

async function detail(res: Response): Promise<string> {
  try {
    const d = await res.json();
    const v = d?.rejected?.[0]?.reason ?? d?.detail?.message ?? d?.detail;
    return typeof v === 'string' ? v : '';
  } catch {
    return '';
  }
}

// wording: BA review (every message below)
export async function translateSubtitle(filePath: string, d: SubtransDeps): Promise<string> {
  const token = await d.token();
  if (!token) throw new SubtransError('login', 'Đăng nhập để dịch phụ đề.');
  const src = pickSource(await d.list(filePath));
  if (src === 'has_vi') throw new SubtransError('has_vi', 'Video này đã có phụ đề tiếng Việt.');
  if (!src) {
    throw new SubtransError('no_subtitle', 'Không có tệp phụ đề cạnh video. Chọn Cài đặt → Phụ đề → "Tệp .srt riêng" rồi tải lại video.');
  }
  const text = await d.read(filePath, src);
  const auth = { Authorization: `Bearer ${token}` };

  const form = new FormData();
  const base = filePath.split(/[\\/]/).pop()?.replace(/\.[^.]+$/, '') || 'subtitle';
  form.append('files', new Blob([text], { type: 'text/plain' }), `${base.slice(0, 100)}.${src}.srt`);
  form.append('target_lang', 'vi');
  const up = await d.fetch('/api/v1/transcript-translate/upload', { method: 'POST', headers: auth, body: form });
  if (up.status === 401) throw new SubtransError('login', 'Đăng nhập để dịch phụ đề.');
  const upData = up.ok ? await up.clone().json().catch(() => null) : null;
  const jobId: string | undefined = upData?.jobs?.[0]?.id;
  if (!up.ok || !jobId) throw new SubtransError('rejected', (await detail(up)) || 'Máy chủ không nhận tệp phụ đề này.');

  for (let waited = 0; waited < MAX_WAIT_MS; waited += POLL_MS) {
    await d.sleep(POLL_MS);
    const r = await d.fetch('/api/v1/transcript-translate/jobs', { method: 'GET', headers: auth });
    if (!r.ok) continue;
    const job: Job | undefined = (await r.json().catch(() => null))?.jobs?.find((j: Job) => j.id === jobId);
    if (!job) continue;
    if (job.status === 'failed') throw new SubtransError('failed', job.error || 'Dịch phụ đề không thành công.');
    if (job.status === 'done') {
      const dl = await d.fetch(`/api/v1/transcript-translate/jobs/${encodeURIComponent(jobId)}/download`, { method: 'GET', headers: auth });
      if (!dl.ok) throw new SubtransError('failed', 'Không tải được bản dịch.');
      try {
        return await d.write(filePath, 'vi', await dl.text());
      } catch (e) {
        const m = String((e as { message?: string })?.message ?? e);
        if (m.includes('already exists')) throw new SubtransError('exists', 'Đã có tệp phụ đề tiếng Việt cạnh video.');
        throw e;
      }
    }
    if (typeof job.progress_pct === 'number') d.onProgress?.(job.progress_pct);
  }
  throw new SubtransError('timeout', 'Dịch phụ đề quá lâu. Xem tiến độ ở trang Phụ đề → Dịch trên web.');
}
