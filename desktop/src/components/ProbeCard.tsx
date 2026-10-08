import { AlertCircle, Clock, Download, FolderOpen, RotateCw, Server, User, X } from 'lucide-react';
import { Badge, Button, IconButton, PlatformBadge, Select, Thumb } from './ui';
import { type Card, removeCard, retryCard, serverCanTry, setCard, switchToServer } from '../lib/probes';
import { qualityOptions, resolveQuality } from '../lib/quality';
import { settings, type Quality } from '../lib/settings';
import { auth } from '../lib/auth';
import { formatBytes, formatDuration } from '../lib/format';
import { errorMessage } from '../lib/errors';
import { api } from '../lib/tauri';

export function cardSelection(card: Card, defaultQuality: Quality, defaultDir: string | null) {
  const r = card.result!;
  const opts = qualityOptions(r.formats, { guest: auth.get().status !== 'in' });
  const value = card.quality && opts.some((o) => o.value === card.quality) ? card.quality : resolveQuality(opts, defaultQuality);
  const option = opts.find((o) => o.value === value) ?? opts[0];
  return { opts, option, outDir: card.outDir ?? defaultDir };
}

export function ProbeCard({ card, onAdd }: { card: Card; onAdd: (c: Card) => void }) {
  const st = settings.use();
  auth.use(); // signing in unlocks 2K/4K on this card
  if (card.status === 'loading') {
    return (
      <article className="flex gap-4 rounded-xl border border-line bg-surface p-3 shadow-card" aria-busy="true" aria-label="Đang phân tích liên kết">
        <div className="vg-skeleton h-[81px] w-[144px] shrink-0 rounded-lg" />
        <div className="flex min-w-0 flex-1 flex-col gap-2 py-1">
          <div className="vg-skeleton h-4 w-3/4 rounded" />
          <div className="vg-skeleton h-3 w-1/3 rounded" />
          <p className="mt-auto truncate text-xs text-fg-muted">Đang phân tích: {card.url}</p>
        </div>
        <IconButton label="Bỏ liên kết này" onClick={() => removeCard(card.url)}><X size={16} /></IconButton>
      </article>
    );
  }
  if (card.status === 'error') {
    return (
      <article className="flex items-start gap-3 rounded-xl border border-danger/40 bg-danger-soft p-3" role="alert">
        <AlertCircle size={20} className="mt-0.5 shrink-0 text-danger" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="truncate text-[13px] font-medium text-fg select-text">{card.url}</p>
          <p className="mt-0.5 text-[13px] text-danger">{errorMessage(card.errorCode)}</p>
        </div>
        {serverCanTry(card.url, card.errorCode) && (
          // wording: BA review
          <Button size="sm" icon={<Server size={14} />} onClick={() => switchToServer(card.url)}>Tải qua máy chủ VidGrab</Button>
        )}
        <Button size="sm" icon={<RotateCw size={14} />} onClick={() => retryCard(card.url)}>Thử lại</Button>
        <IconButton label="Bỏ liên kết này" onClick={() => removeCard(card.url)}><X size={16} /></IconButton>
      </article>
    );
  }
  const r = card.result!;
  const { opts, option, outDir } = cardSelection(card, st.defaultQuality, st.outDir);
  const est = option.format?.filesize ?? null;
  return (
    <article className="flex gap-4 rounded-xl border border-line bg-surface p-3 shadow-card">
      <Thumb src={r.thumbnail} className="h-[81px] w-[144px]" />
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex items-start gap-2">
          <h3 className="line-clamp-2 min-w-0 flex-1 text-sm font-semibold leading-snug text-fg select-text" title={r.title}>{r.title}</h3>
          <IconButton label="Bỏ liên kết này" onClick={() => removeCard(card.url)} className="-mr-1 -mt-1"><X size={16} /></IconButton>
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted">
          <PlatformBadge platform={r.platform} />
          {card.serverOnly && <Badge tone="neutral">Qua máy chủ VidGrab</Badge>}{/* wording: BA review */}
          {r.uploader && <span className="inline-flex min-w-0 items-center gap-1"><User size={12} aria-hidden /><span className="truncate">{r.uploader}</span></span>}
          {r.duration != null && <span className="inline-flex items-center gap-1"><Clock size={12} aria-hidden />{formatDuration(r.duration)}</span>}
        </div>
        <div className="mt-auto flex flex-wrap items-center gap-2">
          <Select label="Chất lượng" value={option.value} onChange={(v) => setCard(card.url, { quality: v as Quality })} className="w-[170px]">
            {opts.map((o) => <option key={o.value} value={o.value} disabled={o.locked}>{o.label}</option>)}
          </Select>
          <span className="w-[72px] text-[13px] text-fg-2" title="Dung lượng ước tính">{est ? `~${formatBytes(est)}` : 'Chưa rõ'}</span>
          <button
            type="button"
            title={outDir ?? 'Chọn thư mục lưu'}
            onClick={async () => {
              try {
                const d = await api.pickFolder();
                if (d) setCard(card.url, { outDir: d });
              } catch { /* dialog unavailable */ }
            }}
            className="order-last inline-flex h-9 min-w-0 flex-1 basis-full items-center min-[1100px]:order-none min-[1100px]:basis-[140px] gap-1.5 rounded-[10px] border border-line-strong bg-surface px-2.5 text-left text-[13px] text-fg-2 hover:bg-surface-2"
          >
            <FolderOpen size={15} className="shrink-0" aria-hidden />
            <span className="truncate">{outDir ?? 'Chọn thư mục…'}</span>
          </button>
          <Button variant="primary" className="max-[1099px]:ml-auto" icon={<Download size={16} />} onClick={() => onAdd(card)} disabled={!outDir}>Tải xuống</Button>
        </div>
      </div>
    </article>
  );
}
