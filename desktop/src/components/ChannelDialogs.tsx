import { useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { Button, Modal, Select, Spinner, Thumb } from './ui';
import { IntervalSelect, ModeSelect, fmtUploadDate } from './ChannelPicker';
import { channels, downloadPending, dismissPending, saveChannel } from '../lib/channels';
import { QUALITY_LABEL } from '../lib/quality';
import type { Quality } from '../lib/settings';
import { api } from '../lib/tauri';
import { formatDuration } from '../lib/format';
import { errorMessage, toAppError } from '../lib/errors';
import { toast } from '../lib/ui';
import type { ChannelInterval, ChannelMode } from '../lib/types';

export function ReviewDialog({ channelId, onClose }: { channelId: string; onClose: () => void }) {
  const ch = channels.use().find((c) => c.id === channelId);
  const [sel, setSel] = useState<Set<string> | null>(null);
  const [busy, setBusy] = useState(false);
  if (!ch) return null;
  // Default: everything selected (until the user changes it).
  const cur = sel ?? new Set(ch.pendingNew.map((v) => v.id));
  const ids = ch.pendingNew.filter((v) => cur.has(v.id)).map((v) => v.id);
  const toggle = (id: string) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); setSel(n); };

  async function run(kind: 'download' | 'dismiss') {
    setBusy(true);
    try {
      if (kind === 'download') {
        await downloadPending(channelId, ids);
        toast('info', `Đã thêm ${ids.length} video vào hàng đợi.`);
      } else {
        await dismissPending(channelId, ids);
        toast('info', `Đã bỏ qua ${ids.length} video.`);
      }
      setSel(null);
      if ((channels.get().find((c) => c.id === channelId)?.pendingNew.length ?? 0) === 0) onClose();
    } catch (e) {
      toast('error', errorMessage(toAppError(e).code));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Video mới của ${ch.title}`} onClose={onClose} width="max-w-2xl">
      {ch.pendingNew.length === 0 ? (
        <p className="py-8 text-center text-sm text-fg-muted">Không còn video mới nào cần xem.</p>
      ) : (
        <>
          <div className="mb-2 flex items-center gap-2 text-[13px] text-fg-2">
            <Button size="sm" variant="ghost" onClick={() => setSel(new Set(ch.pendingNew.map((v) => v.id)))}>Chọn tất cả</Button>
            <Button size="sm" variant="ghost" onClick={() => setSel(new Set())}>Bỏ chọn</Button>
            <span className="ml-auto">{ids.length}/{ch.pendingNew.length} đã chọn</span>
          </div>
          <ul className="max-h-[320px] divide-y divide-line overflow-y-auto rounded-xl border border-line">
            {ch.pendingNew.map((v) => (
              <li key={v.id}>
                <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-surface-2">
                  <input type="checkbox" checked={cur.has(v.id)} onChange={() => toggle(v.id)} aria-label={`Chọn ${v.title}`} className="h-4 w-4 shrink-0 accent-[var(--vg-accent)]" />
                  <Thumb src={v.thumbnail} className="h-[45px] w-[80px]" />
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-fg" title={v.title}>{v.title}</span>
                  <span className="w-12 shrink-0 text-right text-xs tabular-nums text-fg-muted">{formatDuration(v.duration)}</span>
                  <span className="w-[84px] shrink-0 text-right text-xs tabular-nums text-fg-muted">{fmtUploadDate(v.uploadDate)}</span>
                </label>
              </li>
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-fg-muted">Tải về thư mục <span className="select-text">{ch.outDir}</span> · {QUALITY_LABEL[ch.quality as Quality] ?? ch.quality}</p>
            <div className="flex gap-2">
              <Button disabled={busy || ids.length === 0} onClick={() => void run('dismiss')}>Bỏ qua {ids.length}</Button>
              <Button variant="primary" disabled={busy || ids.length === 0} icon={busy ? <Spinner /> : undefined} onClick={() => void run('download')}>Tải {ids.length} video</Button>
            </div>
          </div>
        </>
      )}
    </Modal>
  );
}

export function EditDialog({ channelId, onClose }: { channelId: string; onClose: () => void }) {
  const ch = channels.use().find((c) => c.id === channelId);
  const [mode, setMode] = useState<ChannelMode>(ch?.mode ?? 'download');
  const [every, setEvery] = useState<ChannelInterval>(ch?.checkEveryHours ?? 6);
  const [quality, setQuality] = useState(ch?.quality ?? 'best');
  const [dir, setDir] = useState(ch?.outDir ?? '');
  const [busy, setBusy] = useState(false);
  if (!ch) return null;

  async function pick() {
    try {
      const d = await api.pickFolder();
      if (d) setDir(d);
    } catch { /* dialog unavailable */ }
  }
  async function save() {
    setBusy(true);
    try {
      await saveChannel({ ...ch!, mode, checkEveryHours: every, quality, outDir: dir });
      toast('success', 'Đã lưu cài đặt kênh.');
      onClose();
    } catch (e) {
      toast('error', errorMessage(toAppError(e).code));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal title={`Sửa kênh: ${ch.title}`} onClose={onClose}>
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-3"><span className="text-sm text-fg">Khi có video mới</span><ModeSelect mode={mode} onChange={setMode} /></div>
        <div className="flex items-center justify-between gap-3"><span className="text-sm text-fg">Kiểm tra mỗi</span><IntervalSelect value={every} onChange={setEvery} /></div>
        <div className="flex items-center justify-between gap-3">
          <span className="text-sm text-fg">Chất lượng</span>
          <Select label="Chất lượng" value={quality} onChange={setQuality} className="w-[190px]">
            {(Object.keys(QUALITY_LABEL) as Quality[]).map((q) => <option key={q} value={q}>{QUALITY_LABEL[q]}</option>)}
          </Select>
        </div>
        <div>
          <span className="text-sm text-fg">Thư mục lưu</span>
          <button type="button" onClick={pick} title={dir} className="mt-1 flex h-9 w-full items-center gap-1.5 rounded-[10px] border border-line-strong bg-surface px-2.5 text-left text-[13px] text-fg-2 hover:bg-surface-2">
            <FolderOpen size={15} className="shrink-0" aria-hidden /><span className="truncate">{dir || 'Chọn thư mục…'}</span>
          </button>
        </div>
      </div>
      <div className="mt-5 flex justify-end gap-2">
        <Button onClick={onClose}>Huỷ</Button>
        <Button variant="primary" disabled={busy || !dir} icon={busy ? <Spinner /> : undefined} onClick={() => void save()}>Lưu</Button>
      </div>
    </Modal>
  );
}
