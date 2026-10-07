import { useEffect, useState } from 'react';
import { AlertCircle, BellRing, ListVideo, Pause, Pencil, Play, RefreshCw, Trash2, Tv } from 'lucide-react';
import { Badge, Button, EmptyState, IconButton, PlatformBadge, ScreenHeader, Spinner } from '../components/ui';
import { ChannelAvatar, ChannelPicker, intervalLabel } from '../components/ChannelPicker';
import { EditDialog, ReviewDialog } from '../components/ChannelDialogs';
import {
  cancelFlow, channels, channelsLoaded, checkChannel, checking, fetchListing, flow, isManualOnly, nextCheckAt, removeChannel, saveChannel,
} from '../lib/channels';
import { parseUrls } from '../lib/probes';
import { isDouyinUrl } from '../lib/urls';
import { QUALITY_LABEL } from '../lib/quality';
import type { Quality } from '../lib/settings';
import { errorMessage, toAppError } from '../lib/errors';
import { formatDate } from '../lib/format';
import { channelPrefill, confirmDialog, toast } from '../lib/ui';
import type { Channel } from '../lib/types';

function ChannelCard({ c, onReview, onEdit }: { c: Channel; onReview: () => void; onEdit: () => void }) {
  const busy = checking.use().includes(c.id);
  const next = nextCheckAt(c);
  const pending = c.pendingNew.length;
  const manual = isManualOnly(c);

  async function toggleEnabled() {
    try { await saveChannel({ ...c, enabled: !c.enabled }); } catch (e) { toast('error', errorMessage(toAppError(e).code)); }
  }
  async function checkNow() {
    const r = await checkChannel(c.id);
    if (r === null) {
      if (!checking.get().includes(c.id)) {
        const err = channels.get().find((x) => x.id === c.id)?.lastError;
        if (err) toast('error', `${c.title}: ${errorMessage(err)}`);
      }
    } else if (r === 0) toast('info', `${c.title}: chưa có video mới.`);
    else toast('success', c.mode === 'download' ? `${c.title}: đã thêm ${r} video mới vào hàng đợi.` : `${c.title} có ${r} video mới.`);
  }
  async function remove() {
    const ok = await confirmDialog({
      title: 'Xoá kênh này?', okLabel: 'Xoá kênh', danger: true,
      text: `Ứng dụng sẽ ngừng theo dõi "${c.title}". Các tệp đã tải về vẫn được giữ nguyên trên máy.`,
    });
    if (!ok) return;
    try { await removeChannel(c.id); toast('info', 'Đã xoá kênh.'); } catch (e) { toast('error', errorMessage(toAppError(e).code)); }
  }

  return (
    <article className={`rounded-xl border bg-surface p-3.5 shadow-card ${pending ? 'border-accent/50' : 'border-line'}`}>
      <div className="flex items-start gap-3">
        <ChannelAvatar src={c.thumbnail} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="min-w-0 truncate text-[15px] font-semibold text-fg" title={c.title}>{c.title}</h3>
            {pending > 0 && <Badge tone="accent">{pending} video mới</Badge>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <PlatformBadge platform={c.platform} />
            {c.enabled
              ? <Badge tone={c.mode === 'download' ? 'success' : 'neutral'}>{c.mode === 'download' ? 'Tự tải về' : <><BellRing size={11} aria-hidden /> Chỉ báo</>}</Badge>
              : <Badge tone="warning">Đang tạm dừng</Badge>}
            <span className="text-xs text-fg-muted">{manual ? 'Douyin: chỉ quét khi bạn bấm' : `Kiểm tra mỗi ${intervalLabel(c.checkEveryHours)}`} · {QUALITY_LABEL[c.quality as Quality] ?? c.quality}</span>
          </div>
        </div>
      </div>
      <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-fg-muted">
        <dt>Kiểm tra lúc</dt><dd className="text-fg-2">{busy ? 'Đang kiểm tra…' : c.lastCheckedAt ? formatDate(c.lastCheckedAt) : 'Chưa kiểm tra'}</dd>
        <dt>Lần tới</dt><dd className="text-fg-2">{!c.enabled ? 'Đang tạm dừng' : manual ? 'Không tự quét — bấm “Kiểm tra ngay”' : next ? formatDate(new Date(next).toISOString()) : '—'}</dd>
        <dt>Thư mục</dt><dd className="truncate text-fg-2 select-text" title={c.outDir}>{c.outDir}</dd>
      </dl>
      {c.lastError && (
        <p role="alert" className="mt-2 flex items-start gap-1.5 rounded-lg bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          <AlertCircle size={14} className="mt-px shrink-0" aria-hidden />Lần kiểm tra gần nhất lỗi: {errorMessage(c.lastError)}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {pending > 0 && <Button size="sm" variant="primary" icon={<ListVideo size={15} />} onClick={onReview}>Xem video mới ({pending})</Button>}
        <Button size="sm" icon={busy ? <Spinner /> : <RefreshCw size={14} />} disabled={busy} onClick={() => void checkNow()}>Kiểm tra ngay</Button>
        <Button size="sm" variant="ghost" icon={c.enabled ? <Pause size={14} /> : <Play size={14} />} onClick={() => void toggleEnabled()}>{c.enabled ? 'Tạm dừng theo dõi' : 'Tiếp tục theo dõi'}</Button>
        <span className="ml-auto flex items-center">
          <IconButton label="Sửa kênh" onClick={onEdit}><Pencil size={15} /></IconButton>
          <IconButton label="Xoá kênh" onClick={() => void remove()} className="hover:!text-danger"><Trash2 size={15} /></IconButton>
        </span>
      </div>
    </article>
  );
}

export function ChannelsScreen() {
  const list = channels.use();
  const loaded = channelsLoaded.use();
  const f = flow.use();
  const pre = channelPrefill.use();
  const [text, setText] = useState(f.url);
  const [msg, setMsg] = useState<string | null>(null);
  const [review, setReview] = useState<string | null>(null);
  const [edit, setEdit] = useState<string | null>(null);

  useEffect(() => {
    if (pre) { setText(pre); setMsg(null); channelPrefill.set(null); }
  }, [pre]);

  function start(limit = 200) {
    const first = parseUrls(text).valid[0];
    if (!first) { setMsg('Hãy dán một liên kết kênh hợp lệ (bắt đầu bằng http:// hoặc https://).'); return; }
    setMsg(null);
    void fetchListing(first, limit);
  }

  if (f.phase === 'ready' && f.listing) return <ChannelPicker key={f.listing.channelId} listing={f.listing} more={!!f.more} />;

  const loading = f.phase === 'loading';
  return (
    <div className="flex h-full flex-col">
      <ScreenHeader title="Kênh" subtitle="Tải toàn bộ video của một kênh và tự động theo dõi video mới." />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
        <div className="rounded-xl border border-line bg-surface p-3 shadow-card">
          <label htmlFor="channel-url" className="text-[13px] font-semibold text-fg-2">Thêm kênh</label>
          <div className="mt-1.5 flex items-center gap-2">
            <input
              id="channel-url" value={text} disabled={loading} spellCheck={false}
              onChange={(e) => { setText(e.target.value); setMsg(null); }}
              onKeyDown={(e) => { if (e.key === 'Enter' && !loading) start(); }}
              placeholder="https://www.youtube.com/@tenkenh"
              className="h-9 min-w-0 flex-1 rounded-[10px] border border-line-strong bg-canvas px-3 text-sm text-fg placeholder:text-fg-muted"
            />
            <Button variant="primary" icon={loading ? <Spinner /> : <ListVideo size={16} />} disabled={loading || !text.trim()} onClick={() => start()}>Lấy danh sách video</Button>
          </div>
          {isDouyinUrl(text.trim()) && <p className="mt-2 text-xs text-fg-muted">Douyin: ứng dụng lấy danh sách qua máy chủ VidGrab, giới hạn theo số lượt tải bạn còn trong hôm nay. Kênh Douyin chỉ được quét khi bạn bấm, không tự kiểm tra nền.</p>}
          {msg && <p role="alert" className="mt-2 text-[13px] text-danger">{msg}</p>}
          {f.phase === 'error' && (
            <div role="alert" className="mt-2 flex items-start gap-2 rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">
              <AlertCircle size={16} className="mt-px shrink-0" aria-hidden />
              <span className="min-w-0 flex-1">{errorMessage(f.errorCode)}</span>
              <Button size="sm" icon={<RefreshCw size={14} />} onClick={() => start(f.limit)}>Thử lại</Button>
            </div>
          )}
        </div>

        {loading && (
          <div className="mt-4 rounded-xl border border-line bg-surface p-4 shadow-card" aria-busy="true">
            <div className="flex items-center gap-3">
              <Spinner className="text-accent" />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium text-fg">Đang lấy danh sách video…</p>
                <p className="truncate text-xs text-fg-muted">Kênh nhiều video có thể mất vài chục giây. {f.url}</p>
              </div>
              <Button size="sm" onClick={cancelFlow}>Huỷ</Button>
            </div>
            <div className="mt-3 flex flex-col gap-2">
              {[0, 1, 2].map((i) => <div key={i} className="vg-skeleton h-[45px] rounded-lg" />)}
            </div>
          </div>
        )}

        {!loading && loaded && list.length === 0 && (
          <EmptyState icon={<Tv size={24} />} title="Chưa theo dõi kênh nào" text="Dán liên kết kênh để tải hàng loạt video cũ, hoặc để ứng dụng tự tải / báo cho bạn khi kênh đăng video mới.">
            <div className="mt-1 flex max-w-md flex-col items-center gap-0.5 text-xs text-fg-muted"><span className="font-medium text-fg-2">Ví dụ (bấm để điền):</span>
              <button type="button" className="rounded-lg px-2 py-1 hover:bg-surface-2" onClick={() => setText('https://www.youtube.com/@tenkenh')}>https://www.youtube.com/@tenkenh</button>
              <button type="button" className="rounded-lg px-2 py-1 hover:bg-surface-2" onClick={() => setText('https://www.youtube.com/playlist?list=…')}>https://www.youtube.com/playlist?list=…</button>
              <button type="button" className="rounded-lg px-2 py-1 hover:bg-surface-2" onClick={() => setText('https://www.tiktok.com/@tenkenh')}>https://www.tiktok.com/@tenkenh</button>
              <button type="button" className="rounded-lg px-2 py-1 hover:bg-surface-2" onClick={() => setText('https://www.douyin.com/user/…')}>https://www.douyin.com/user/…</button>
            </div>
          </EmptyState>
        )}

        {list.length > 0 && (
          <section className="mt-4 flex flex-col gap-3" aria-label="Kênh đang theo dõi">
            <h2 className="text-[13px] font-semibold text-fg-2">{list.length} kênh đang theo dõi</h2>
            {list.map((c) => <ChannelCard key={c.id} c={c} onReview={() => setReview(c.id)} onEdit={() => setEdit(c.id)} />)}
            <p className="text-xs text-fg-muted">Ứng dụng tự kiểm tra kênh khi đang chạy. Đóng cửa sổ sẽ thu nhỏ xuống khay đồng hồ để tiếp tục theo dõi (đổi trong Cài đặt).</p>
          </section>
        )}
      </div>
      {review && <ReviewDialog channelId={review} onClose={() => setReview(null)} />}
      {edit && <EditDialog channelId={edit} onClose={() => setEdit(null)} />}
    </div>
  );
}
