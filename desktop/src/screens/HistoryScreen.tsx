import { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Cloud, ExternalLink, FolderOpen, History as HistoryIcon, LogIn, RotateCw, Search, Trash2 } from 'lucide-react';
import { Badge, Button, EmptyState, IconButton, PlatformBadge, ScreenHeader, Spinner, Tabs } from '../components/ui';
import { SyncStatus } from '../components/SyncStatus';
import { api } from '../lib/tauri';
import { apiFetch } from '../lib/http';
import { auth, getAccessToken } from '../lib/auth';
import { queue } from '../lib/queue';
import { confirmDialog, openSignIn, redownload, toast } from '../lib/ui';
import { errorMessage, toAppError } from '../lib/errors';
import { formatBytes, formatDate } from '../lib/format';
import type { HistoryItem } from '../lib/types';

type Tab = 'local' | 'web';

function LocalTab() {
  const [rows, setRows] = useState<HistoryItem[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [tick, setTick] = useState(0);
  const finished = queue.use().filter((i) => i.state === 'completed' || i.state === 'failed').length;

  useEffect(() => {
    const f = () => setTick((t) => t + 1);
    window.addEventListener('vg:history-synced', f);
    return () => window.removeEventListener('vg:history-synced', f);
  }, []);

  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      api.historyList({ limit: 300, query: q.trim() || undefined })
        .then((r) => { if (alive) { setRows(r); setErr(null); } })
        .catch((e) => { if (alive) setErr(errorMessage(toAppError(e).code)); });
    }, q ? 250 : 0);
    return () => { alive = false; clearTimeout(t); };
  }, [q, tick, finished]);

  async function del(id: string) {
    try { await api.historyDelete(id); setRows((r) => r?.filter((x) => x.id !== id) ?? r); }
    catch (e) { toast('error', errorMessage(toAppError(e).code)); }
  }
  async function clearAll() {
    const ok = await confirmDialog({ title: 'Xoá toàn bộ lịch sử?', text: 'Chỉ xoá danh sách trong ứng dụng. Các tệp đã tải trên máy của bạn được giữ nguyên.', okLabel: 'Xoá lịch sử', danger: true });
    if (!ok) return;
    try { await api.historyClear(); setRows([]); } catch (e) { toast('error', errorMessage(toAppError(e).code)); }
  }
  async function act(fn: (p: string) => Promise<unknown>, p: string | null) {
    if (!p) return;
    try { await fn(p); } catch { toast('error', 'Không mở được tệp. Có thể tệp đã bị di chuyển hoặc xoá.'); }
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 px-6 pb-3">
        <label className="relative min-w-[200px] flex-1">
          <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-muted" aria-hidden />
          <input
            aria-label="Tìm trong lịch sử"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Tìm theo tên hoặc liên kết…"
            className="h-9 w-full rounded-[10px] border border-line-strong bg-surface pl-9 pr-3 text-sm text-fg placeholder:text-fg-muted"
          />
        </label>
        <SyncStatus />
        <Button size="sm" variant="danger" icon={<Trash2 size={14} />} disabled={!rows?.length} onClick={clearAll}>Xoá lịch sử</Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
        {err ? (
          <EmptyState icon={<AlertCircle size={24} />} title="Không đọc được lịch sử" text={err} />
        ) : rows === null ? (
          <div className="flex justify-center py-14 text-fg-muted"><Spinner /></div>
        ) : rows.length === 0 ? (
          <EmptyState icon={<HistoryIcon size={24} />} title={q ? 'Không tìm thấy kết quả' : 'Chưa có lịch sử tải xuống'} text={q ? 'Thử từ khoá khác.' : 'Video đã tải xong hoặc bị lỗi sẽ được ghi lại ở đây.'} />
        ) : (
          <ul className="flex flex-col gap-2">
            {rows.map((h) => (
              <li key={h.id} className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3 shadow-card">
                <div className="min-w-0 flex-1">
                  <h3 className="truncate text-sm font-semibold text-fg select-text" title={h.title}>{h.title}</h3>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <PlatformBadge platform={h.platform} />
                    <Badge>{h.formatLabel}</Badge>
                    {h.state === 'failed' ? <Badge tone="danger">Lỗi</Badge> : <span className="text-xs text-fg-muted">{formatBytes(h.fileSize)}</span>}
                    <span className="text-xs text-fg-muted">{formatDate(h.finishedAt ?? h.createdAt)}</span>
                    {h.synced ? <Badge tone="success">Đã đồng bộ</Badge> : <Badge tone="warning">Chờ đồng bộ</Badge>}
                  </div>
                  {h.state === 'failed' && <p className="mt-1 text-xs text-danger">{errorMessage(h.errorCode)}</p>}
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  {h.state === 'completed' && (
                    <>
                      <IconButton label="Mở tệp" disabled={!h.filePath} onClick={() => void act(api.openPath, h.filePath)}><ExternalLink size={16} /></IconButton>
                      <IconButton label="Mở thư mục chứa tệp" disabled={!h.filePath} onClick={() => void act(api.revealPath, h.filePath)}><FolderOpen size={16} /></IconButton>
                    </>
                  )}
                  <IconButton label="Tải lại" onClick={() => redownload(h.url)}><RotateCw size={16} /></IconButton>
                  <IconButton label="Xoá khỏi lịch sử" onClick={() => void del(h.id)}><Trash2 size={16} /></IconButton>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}

type WebJob = { id: string; title: string; url: string; platform: string; status: string; at: string };
const PER = 20;

function normalize(j: Record<string, unknown>): WebJob {
  const s = (k: string) => (typeof j[k] === 'string' ? (j[k] as string) : '');
  const url = s('original_url') || s('url');
  return { id: s('id') || url, title: s('title') || url, url, platform: s('platform') || s('source_surface'), status: s('status').toLowerCase(), at: s('created_at') || s('createdAt') };
}

function WebTab() {
  const a = auth.use();
  const [jobs, setJobs] = useState<WebJob[]>([]);
  const [loading, setLoading] = useState(false);
  const [more, setMore] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (offset: number) => {
    setLoading(true);
    setErr(null);
    try {
      const token = await getAccessToken();
      const res = await apiFetch<{ jobs?: Record<string, unknown>[] }>(`/api/v1/history?limit=${PER}&offset=${offset}`, { token });
      if (res.status === 401) throw { code: 'unauthorized' };
      if (res.status >= 400) throw { code: 'server' };
      const list = (res.data?.jobs ?? []).map(normalize);
      setJobs((cur) => (offset === 0 ? list : [...cur, ...list]));
      setMore(list.length >= PER);
    } catch (e) {
      setErr(errorMessage(toAppError(e).code));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (a.status === 'in') void load(0); else setJobs([]); }, [a.status, load]);

  if (a.status !== 'in') {
    return (
      <div className="px-6">
        <EmptyState icon={<Cloud size={24} />} title="Đăng nhập để xem lịch sử trên web" text="Danh sách video bạn đã tải bằng website VidGrab sẽ hiện ở đây. Lịch sử trên máy này cũng được đồng bộ lên tài khoản.">
          <Button variant="primary" icon={<LogIn size={16} />} className="mt-2" onClick={() => openSignIn()}>Đăng nhập</Button>
        </EmptyState>
      </div>
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
      {err && (
        <div role="alert" className="mb-3 flex items-center gap-3 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
          <AlertCircle size={16} aria-hidden /><span className="flex-1">{err}</span>
          <Button size="sm" icon={<RotateCw size={14} />} onClick={() => void load(0)}>Thử lại</Button>
        </div>
      )}
      {!err && jobs.length === 0 && !loading && <EmptyState icon={<Cloud size={24} />} title="Chưa có lịch sử trên web" text="Các video tải bằng website sẽ hiện ở đây." />}
      <ul className="flex flex-col gap-2">
        {jobs.map((j) => (
          <li key={j.id} className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3 shadow-card">
            <div className="min-w-0 flex-1">
              <h3 className="truncate text-sm font-semibold text-fg select-text" title={j.title}>{j.title}</h3>
              <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                {j.platform && <PlatformBadge platform={j.platform} />}
                {/(fail|error)/.test(j.status) ? <Badge tone="danger">Lỗi</Badge> : /(success|complete|done)/.test(j.status) ? <Badge tone="success">Hoàn tất</Badge> : j.status ? <Badge>{j.status}</Badge> : null}
                <span className="text-xs text-fg-muted">{formatDate(j.at)}</span>
              </div>
            </div>
            {j.url && <Button size="sm" icon={<RotateCw size={14} />} onClick={() => redownload(j.url)}>Tải lại bằng app</Button>}
          </li>
        ))}
      </ul>
      {loading && <div className="flex justify-center py-6 text-fg-muted"><Spinner /></div>}
      {more && !loading && <div className="mt-3 flex justify-center"><Button onClick={() => void load(jobs.length)}>Tải thêm</Button></div>}
    </div>
  );
}

export function HistoryScreen() {
  const [tab, setTab] = useState<Tab>('local');
  return (
    <div className="flex h-full flex-col">
      <ScreenHeader title="Lịch sử" subtitle="Video đã tải trên máy này và trên tài khoản web của bạn." />
      <div className="px-6 pb-3">
        <Tabs<Tab> value={tab} onChange={setTab} items={[{ value: 'local', label: 'Trên máy này' }, { value: 'web', label: 'Trên web' }]} />
      </div>
      {tab === 'local' ? <LocalTab /> : <WebTab />}
    </div>
  );
}
