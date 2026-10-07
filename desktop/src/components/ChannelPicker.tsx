import { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ChevronLeft, ChevronRight, Download, FolderOpen, Info, Search, Tv } from 'lucide-react';
import { Badge, Button, PlatformBadge, Select, Spinner, Thumb, Toggle } from './ui';
import { api } from '../lib/tauri';
import { ensureOutDir, settings, type Quality } from '../lib/settings';
import { QUALITY_LABEL } from '../lib/quality';
import { formatBytes, formatDuration } from '../lib/format';
import { errorMessage, toAppError } from '../lib/errors';
import { toast } from '../lib/ui';
import { enqueueVideos, fetchListing, flow, joinPath, newChannelFromListing, resetFlow, sanitizeFolderName, saveChannel, channels } from '../lib/channels';
import type { ChannelInterval, ChannelListing, ChannelMode } from '../lib/types';

const PAGE = 50;
const QUALITIES = Object.keys(QUALITY_LABEL) as Quality[];
export const INTERVALS: ChannelInterval[] = [1, 3, 6, 12, 24];
export const intervalLabel = (h: number) => `${h} giờ`;

/** YYYYMMDD -> dd/mm/yyyy */
export function fmtUploadDate(d: string | null): string {
  return d && /^\d{8}$/.test(d) ? `${d.slice(6)}/${d.slice(4, 6)}/${d.slice(0, 4)}` : '';
}

export function ModeSelect({ mode, onChange, inline }: { mode: ChannelMode; onChange: (m: ChannelMode) => void; inline?: boolean }) {
  const pre = inline ? 'Khi có video mới: ' : '';
  return (
    <Select label="Khi có video mới" value={mode} onChange={(v) => onChange(v as ChannelMode)} className={inline ? 'w-[250px]' : 'w-[190px]'}>
      <option value="download">{pre}Tự tải về</option>
      <option value="notify">{pre}Chỉ báo cho tôi</option>
    </Select>
  );
}

export function IntervalSelect({ value, onChange, inline }: { value: ChannelInterval; onChange: (v: ChannelInterval) => void; inline?: boolean }) {
  return (
    <Select label="Kiểm tra mỗi" value={String(value)} onChange={(v) => onChange(Number(v) as ChannelInterval)} className={inline ? 'w-[175px]' : 'w-[110px]'}>
      {INTERVALS.map((h) => <option key={h} value={h}>{inline ? 'Kiểm tra mỗi ' : ''}{intervalLabel(h)}</option>)}
    </Select>
  );
}

export function ChannelPicker({ listing, more }: { listing: ChannelListing; more: boolean }) {
  const st = settings.use();
  const { limit } = flow.use();
  const saved = channels.use().find((c) => c.id === listing.channelId);

  const [sel, setSel] = useState<Set<string>>(() => new Set());
  const [query, setQuery] = useState('');
  const [since, setSince] = useState('');
  const [page, setPage] = useState(0);
  const [quality, setQuality] = useState<Quality>(st.defaultQuality);
  const [baseDir, setBaseDir] = useState<string | null>(null);
  const [customDir, setCustomDir] = useState<string | null>(null);
  const [free, setFree] = useState<number | null>(null);
  const [follow, setFollow] = useState(true);
  const [mode, setMode] = useState<ChannelMode>('download');
  const [every, setEvery] = useState<ChannelInterval>(6);
  const [busy, setBusy] = useState(false);
  const douyin = listing.platform === 'douyin';

  useEffect(() => { void ensureOutDir().then(setBaseDir); }, []);
  const dir = customDir ?? (baseDir ? joinPath(baseDir, sanitizeFolderName(listing.title)) : null);

  useEffect(() => {
    if (!dir) return;
    let live = true;
    const probe = async () => {
      try { return await api.diskFree(dir); } catch { return baseDir ? await api.diskFree(baseDir) : null; }
    };
    probe().then((n) => live && setFree(n)).catch(() => live && setFree(null));
    return () => { live = false; };
  }, [dir, baseDir]);

  const hasDates = useMemo(() => listing.videos.some((v) => v.uploadDate), [listing.videos]);
  const { shown, hiddenNoDate } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const s = since ? since.replace(/-/g, '') : '';
    let hidden = 0;
    const out = listing.videos.filter((v) => {
      if (q && !v.title.toLowerCase().includes(q)) return false;
      if (s) {
        if (!v.uploadDate) { hidden++; return false; }
        if (v.uploadDate < s) return false;
      }
      return true;
    });
    return { shown: out, hiddenNoDate: hidden };
  }, [listing.videos, query, since]);

  const pages = Math.max(1, Math.ceil(shown.length / PAGE));
  const cur = Math.min(page, pages - 1);
  const rows = shown.slice(cur * PAGE, cur * PAGE + PAGE);
  const filtered = !!query.trim() || !!since;

  const toggle = (id: string) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const selectIds = (ids: string[]) => setSel(new Set(ids));
  const selectedVideos = listing.videos.filter((v) => sel.has(v.id));
  const n = selectedVideos.length;
  const allShownOn = shown.length > 0 && shown.every((v) => sel.has(v.id));

  const lowDisk = free != null && n > 0 && free < n * 80e6;

  async function submit() {
    if (!dir || busy) return;
    if (n === 0 && !follow) return;
    if (free != null && free < 300e6 && n > 0) {
      toast('error', 'Ổ đĩa không đủ dung lượng. Hãy chọn thư mục khác.');
      return;
    }
    setBusy(true);
    try {
      const ch = newChannelFromListing(listing, { mode, quality, outDir: dir, checkEveryHours: every });
      if (n > 0) enqueueVideos(selectedVideos, ch);
      // Everything shown here counts as "seen", so later checks only bring NEW videos.
      const seenIds = follow ? listing.videos.map((v) => v.id) : selectedVideos.map((v) => v.id);
      if (follow) {
        if (seenIds.length) await api.channelSeenAdd(ch.id, seenIds);
        await saveChannel(ch);
      } else if (seenIds.length) {
        await api.channelSeenAdd(ch.id, seenIds).catch(() => {}); // channel not saved: best effort
      }
      toast('success', follow
        ? (n > 0 ? `Đã thêm ${n} video vào hàng đợi và bắt đầu theo dõi kênh.` : 'Đã bắt đầu theo dõi kênh.')
        : `Đã thêm ${n} video vào hàng đợi.`);
      resetFlow();
    } catch (e) {
      toast('error', errorMessage(toAppError(e).code));
    } finally {
      setBusy(false);
    }
  }

  async function pickDir() {
    try {
      const d = await api.pickFolder();
      if (d) setCustomDir(d);
    } catch { /* dialog unavailable */ }
  }

  const label = follow
    ? (n > 0 ? `Tải ${n} video và theo dõi kênh` : 'Chỉ theo dõi kênh')
    : `Tải ${n} video đã chọn`;

  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-3 pt-4">
        <button type="button" onClick={resetFlow} className="mb-3 flex w-fit items-center gap-1.5 rounded-lg px-1 py-0.5 text-[13px] font-medium text-fg-2 hover:text-fg">
          <ArrowLeft size={15} aria-hidden /> Quay lại danh sách kênh
        </button>

        <section className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3 shadow-card">
          <Thumb src={listing.thumbnail} className="h-14 w-14 !rounded-full" />
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-base font-semibold text-fg select-text" title={listing.title}>{listing.title}</h2>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-fg-muted">
              <PlatformBadge platform={listing.platform} />
              <span>{listing.videos.length} video{listing.truncated ? ' (đã lấy)' : ''}</span>
              {listing.truncated && <Badge tone="warning">Danh sách có thể chưa đủ</Badge>}
              {saved && <Badge tone="accent">Kênh này đã được theo dõi</Badge>}
            </div>
            {douyin && (
              <p className="mt-1.5 flex items-start gap-1.5 text-xs text-fg-muted">
                <Info size={13} className="mt-px shrink-0" aria-hidden />
                <span>Douyin: danh sách chỉ lấy tối đa {listing.cap ?? listing.videos.length} video — đúng số lượt tải bạn còn trong hôm nay. Mỗi video tải về tính một lượt.</span>
              </p>
            )}
          </div>
          {listing.truncated && (
            <div className="flex shrink-0 items-center gap-2">
              {more && <Spinner />}
              {[1000, 5000].filter((x) => x > limit).slice(0, 2).map((x) => (
                <Button key={x} size="sm" disabled={more} onClick={() => void fetchListing(flow.get().url, x, true)}>
                  Tải thêm (tới {x})
                </Button>
              ))}
            </div>
          )}
        </section>

        <section className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2.5 rounded-xl border border-line bg-surface p-3 shadow-card" aria-label="Theo dõi kênh">
          <div className="flex min-w-[230px] flex-1 items-center gap-3">
            <Toggle checked={follow} onChange={setFollow} label="Theo dõi kênh này" />
            <div className="min-w-0">
              <p className="text-sm font-medium text-fg">Theo dõi kênh này</p>
              <p className="text-xs text-fg-muted">{douyin ? 'Douyin: chỉ quét khi bạn bấm “Kiểm tra ngay” ở mục Kênh, không tự kiểm tra nền.' : 'Tự kiểm tra video mới, kể cả khi thu nhỏ dưới khay.'}</p>
            </div>
          </div>
          {follow && (
            <div className="flex flex-wrap items-center gap-2">
              <ModeSelect inline mode={mode} onChange={setMode} />
              {!douyin && <IntervalSelect inline value={every} onChange={setEvery} />}
            </div>
          )}
        </section>

        <div className="mt-3 flex flex-wrap items-center gap-2" role="toolbar" aria-label="Chọn video">
          <span className="text-xs font-medium text-fg-muted">Chọn nhanh</span>
          <Button size="sm" onClick={() => selectIds(allShownOn ? [] : [...sel, ...shown.map((v) => v.id)])}>
            {allShownOn ? 'Bỏ chọn danh sách này' : filtered ? `Chọn ${shown.length} video đang lọc` : 'Chọn tất cả'}
          </Button>
          <Button size="sm" variant="ghost" disabled={n === 0} onClick={() => selectIds([])}>Bỏ chọn</Button>
          <span className="mx-1 h-5 w-px bg-line" aria-hidden />
          {[10, 50, 100].map((k) => (
            <Button key={k} size="sm" variant="secondary" aria-label={`Chọn ${k} video mới nhất`} disabled={listing.videos.length === 0} onClick={() => { setQuery(''); setSince(''); selectIds(listing.videos.slice(0, k).map((v) => v.id)); }}>
              {k} mới nhất
            </Button>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <div className="relative min-w-[200px] flex-1">
            <Search size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-muted" aria-hidden />
            <input
              type="search" aria-label="Tìm theo tiêu đề" placeholder="Tìm theo tiêu đề…" value={query}
              onChange={(e) => { setQuery(e.target.value); setPage(0); }}
              className="h-9 w-full rounded-[10px] border border-line-strong bg-surface pl-8 pr-2.5 text-sm text-fg placeholder:text-fg-muted"
            />
          </div>
          <label className="flex items-center gap-2 text-[13px] text-fg-2" title={hasDates ? undefined : 'Danh sách này không có ngày đăng'}>
            Từ ngày
            <input
              type="date" aria-label="Từ ngày" value={since} disabled={!hasDates}
              onChange={(e) => { setSince(e.target.value); setPage(0); }}
              className="h-9 rounded-[10px] border border-line-strong bg-surface px-2 text-sm text-fg disabled:opacity-50"
            />
          </label>
          {since && <Button size="sm" variant="ghost" onClick={() => setSince('')}>Xoá lọc ngày</Button>}
        </div>
        {since && hiddenNoDate > 0 && <p className="mt-1.5 text-xs text-fg-muted">{hiddenNoDate} video không rõ ngày đăng đang bị ẩn.</p>}

        <div className="mt-3 overflow-hidden rounded-xl border border-line bg-surface shadow-card">
          {rows.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-fg-muted">{listing.videos.length === 0 ? 'Kênh này chưa có video nào.' : 'Không có video nào khớp bộ lọc.'}</p>
          ) : (
            <ul className="divide-y divide-line">
              {rows.map((v) => (
                <li key={v.id}>
                  <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-surface-2">
                    <input type="checkbox" checked={sel.has(v.id)} onChange={() => toggle(v.id)} className="h-4 w-4 shrink-0 accent-[var(--vg-accent)]" aria-label={`Chọn ${v.title}`} />
                    <Thumb src={v.thumbnail} className="h-[45px] w-[80px]" />
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-fg" title={v.title}>{v.title}</span>
                    <span className="w-14 shrink-0 text-right text-xs tabular-nums text-fg-muted">{formatDuration(v.duration)}</span>
                    <span className="w-[84px] shrink-0 text-right text-xs tabular-nums text-fg-muted">{fmtUploadDate(v.uploadDate)}</span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>
        {pages > 1 && (
          <div className="mt-2 flex items-center justify-center gap-3 text-[13px] text-fg-2">
            <Button size="sm" variant="ghost" disabled={cur === 0} icon={<ChevronLeft size={15} />} onClick={() => setPage(cur - 1)}>Trước</Button>
            <span>Trang {cur + 1} / {pages} · {shown.length} video</span>
            <Button size="sm" variant="ghost" disabled={cur >= pages - 1} onClick={() => setPage(cur + 1)}>Sau <ChevronRight size={15} /></Button>
          </div>
        )}
      </div>

      <footer className="shrink-0 border-t border-line bg-surface px-6 py-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[13px] font-semibold text-fg">{n} đã chọn</span>
          <Select label="Chất lượng" value={quality} onChange={(v) => setQuality(v as Quality)} className="w-[160px]">
            {QUALITIES.map((q) => <option key={q} value={q}>{QUALITY_LABEL[q]}</option>)}
          </Select>
          <button
            type="button" onClick={pickDir} title={dir ?? 'Chọn thư mục lưu'}
            className="inline-flex h-9 min-w-[140px] flex-1 items-center gap-1.5 rounded-[10px] border border-line-strong bg-surface px-2.5 text-left text-[13px] text-fg-2 hover:bg-surface-2"
          >
            <FolderOpen size={15} className="shrink-0" aria-hidden />
            <span className="truncate">{dir ?? 'Chọn thư mục…'}</span>
          </button>
          <Button variant="primary" icon={busy ? <Spinner /> : <Download size={16} />} disabled={!dir || busy || (n === 0 && !follow)} onClick={() => void submit()}>{label}</Button>
        </div>
        <p className="mt-2 flex items-start gap-1.5 text-xs text-fg-muted">
          <Info size={13} className="mt-px shrink-0" aria-hidden />
          <span>
            {free != null && <>Ổ đĩa còn trống {formatBytes(free)}. {lowDisk && <span className="text-warning">Có thể không đủ chỗ cho {n} video. </span>}</>}
            {follow
              ? 'Video không chọn sẽ được đánh dấu là đã biết; về sau chỉ video MỚI đăng mới được tải hoặc báo cho bạn.'
              : 'Kênh sẽ không được lưu; bạn chỉ tải các video đã chọn.'}
          </span>
        </p>
      </footer>
    </div>
  );
}

export function ChannelAvatar({ src, size = 'h-12 w-12' }: { src: string | null; size?: string }) {
  return src ? <Thumb src={src} className={`${size} !rounded-full`} /> : (
    <div className={`flex ${size} shrink-0 items-center justify-center rounded-full bg-surface-2 text-fg-muted`}><Tv size={20} aria-hidden /></div>
  );
}
