import { useState } from 'react';
import { AlertCircle, CheckCircle2, FolderOpen, ListChecks, Pause, Play, RotateCw, Trash2, X, ExternalLink } from 'lucide-react';
import { Badge, Button, EmptyState, IconButton, PlatformBadge, ProgressBar, ScreenHeader, Tabs, Thumb } from '../components/ui';
import { QuotaBadge, QuotaNotice } from '../components/QuotaBadge';
import { cancel, clearFinished, pause, queue, removeItem, resume, retry, type QueueItem } from '../lib/queue';
import { formatBytes, formatEta, formatSpeed } from '../lib/format';
import { errorMessage } from '../lib/errors';
import { api } from '../lib/tauri';
import { confirmDialog, go, toast } from '../lib/ui';

type Tab = 'active' | 'done' | 'failed';
const STAGE: Record<string, string> = { downloading: 'Đang tải', merging: 'Đang ghép', processing: 'Đang xử lý' };

async function reveal(path: string | null) {
  if (!path) return;
  try { await api.revealPath(path); } catch { toast('error', 'Không mở được thư mục. Có thể tệp đã bị di chuyển hoặc xoá.'); }
}
async function openFile(path: string | null) {
  if (!path) return;
  try { await api.openPath(path); } catch { toast('error', 'Không mở được tệp. Có thể tệp đã bị di chuyển hoặc xoá.'); }
}

function Row({ it }: { it: QueueItem }) {
  const running = it.state === 'running';
  const stage = it.stage ? STAGE[it.stage] : 'Đang tải';
  const indeterminate = running && (it.stage !== 'downloading' || it.percent == null);
  async function askCancel() {
    const ok = await confirmDialog({ title: 'Huỷ tải xuống?', text: `“${it.title}” sẽ bị dừng và các tệp tải dở sẽ bị xoá.`, okLabel: 'Huỷ tải', danger: true });
    if (ok) void cancel(it.id);
  }
  return (
    <li className="flex gap-3 rounded-xl border border-line bg-surface p-3 shadow-card">
      <Thumb src={it.thumbnail} className="h-[54px] w-24" />
      <div className="flex min-w-0 flex-1 flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <h3 className="min-w-0 flex-1 truncate text-sm font-semibold text-fg select-text" title={it.title}>{it.title}</h3>
          <PlatformBadge platform={it.platform} />
          <Badge tone="neutral">{it.formatLabel}</Badge>
        </div>

        {(it.state === 'running' || it.state === 'paused' || it.state === 'queued') && (
          <>
            <ProgressBar percent={it.percent} tone={it.state === 'paused' ? 'warning' : 'accent'} indeterminate={indeterminate || (it.state === 'queued')} />
            <p className="flex flex-wrap gap-x-3 text-xs text-fg-muted">
              {it.state === 'queued' && <span>Đang chờ đến lượt…</span>}
              {it.state === 'paused' && <><span className="font-medium text-warning">Đã tạm dừng</span>{it.percent != null && <span>{Math.round(it.percent)}%</span>}</>}
              {running && (
                <>
                  <span className="font-medium text-accent-text">{it.pausing ? 'Đang tạm dừng…' : stage}</span>
                  {it.percent != null && <span>{Math.round(it.percent)}%</span>}
                  <span>{formatBytes(it.downloadedBytes)} / {formatBytes(it.totalBytes ?? it.estSize)}</span>
                  <span>{formatSpeed(it.speedBps)}</span>
                  <span>Còn {formatEta(it.etaSec)}</span>
                </>
              )}
            </p>
          </>
        )}
        {it.state === 'completed' && (
          <p className="flex items-center gap-1.5 text-xs text-success"><CheckCircle2 size={14} aria-hidden />Hoàn tất · {formatBytes(it.fileSize)}</p>
        )}
        {it.state === 'failed' && (
          <p className="flex items-start gap-1.5 text-xs text-danger"><AlertCircle size={14} className="mt-px shrink-0" aria-hidden />{errorMessage(it.errorCode)}</p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1 self-center">
        {running && <Button size="sm" icon={<Pause size={14} />} disabled={it.pausing} onClick={() => void pause(it.id)}>Tạm dừng</Button>}
        {it.state === 'paused' && <Button size="sm" icon={<Play size={14} />} onClick={() => resume(it.id)}>Tiếp tục</Button>}
        {it.state === 'failed' && <Button size="sm" icon={<RotateCw size={14} />} onClick={() => retry(it.id)}>Thử lại</Button>}
        {it.state === 'completed' && (
          <>
            <Button size="sm" icon={<FolderOpen size={14} />} disabled={!it.filePath} onClick={() => void reveal(it.filePath)}>Mở thư mục</Button>
            <IconButton label="Mở tệp" disabled={!it.filePath} onClick={() => void openFile(it.filePath)}><ExternalLink size={16} /></IconButton>
          </>
        )}
        {(it.state === 'running' || it.state === 'paused' || it.state === 'queued') && <IconButton label="Huỷ tải xuống" onClick={() => void askCancel()}><X size={16} /></IconButton>}
        {(it.state === 'completed' || it.state === 'failed') && <IconButton label="Xoá khỏi hàng đợi" onClick={() => removeItem(it.id)}><Trash2 size={16} /></IconButton>}
      </div>
    </li>
  );
}

export function QueueScreen() {
  const items = queue.use();
  const [tab, setTab] = useState<Tab>('active');
  const groups: Record<Tab, QueueItem[]> = {
    active: items.filter((i) => i.state === 'queued' || i.state === 'running' || i.state === 'paused'),
    done: items.filter((i) => i.state === 'completed'),
    failed: items.filter((i) => i.state === 'failed'),
  };
  const list = groups[tab];
  const finished = groups.done.length + groups.failed.length;
  const empty = { active: 'Không có tải xuống nào đang chạy', done: 'Chưa có video nào tải xong', failed: 'Không có lỗi nào' }[tab];

  return (
    <div className="flex h-full flex-col">
      <ScreenHeader
        title="Hàng đợi"
        subtitle="Các video đang tải, đang chờ và đã tải xong."
        actions={<><QuotaBadge /><Button size="sm" icon={<Trash2 size={14} />} disabled={finished === 0} onClick={clearFinished}>Xoá mục đã xong</Button></>}
      />
      <QuotaNotice />
      <div className="px-6 pb-3">
        <Tabs<Tab> value={tab} onChange={setTab} items={[
          { value: 'active', label: 'Đang chạy', count: groups.active.length },
          { value: 'done', label: 'Hoàn tất' },
          { value: 'failed', label: 'Lỗi', count: groups.failed.length },
        ]} />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
        {list.length === 0 ? (
          <EmptyState icon={<ListChecks size={24} />} title={empty} text={tab === 'active' ? 'Video bạn thêm từ trang Tải xuống sẽ hiện ở đây.' : undefined}>
            {tab === 'active' && <Button variant="primary" onClick={() => go('download')}>Đến trang Tải xuống</Button>}
          </EmptyState>
        ) : (
          <ul className="flex flex-col gap-2.5">{list.map((it) => <Row key={it.id} it={it} />)}</ul>
        )}
      </div>
    </div>
  );
}
