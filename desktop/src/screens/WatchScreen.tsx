// "Theo dõi kênh" (Phase 33B, task #6259): channels the VidGrab server
// watches for this account; new videos are downloaded on THIS machine when
// the user taps "Tải". All Vietnamese strings: wording: BA review.
import { useEffect, useRef, useState } from 'react';
import { AlertCircle, Bell, Download, Eye, Pause, Play, RefreshCw, Trash2 } from 'lucide-react';
import { Badge, Button, EmptyState, IconButton, PlatformBadge, ScreenHeader, Spinner } from '../components/ui';
import { formatDate } from '../lib/format';
import { confirmDialog, toast } from '../lib/ui';
import {
  addSource, downloadInboxItems, fetchInbox, inboxQuery, markInboxRead, refreshAll, removeSource, setPaused, watchInbox, watchLoading, watchStatus, watchSubs,
} from '../lib/watch';
import { channelLabel, type InboxItem, type WatchSubscription } from '../lib/watch-core';

// wording: BA review
const T = {
  title: 'Theo dõi kênh',
  subtitle: 'Máy chủ VidGrab kiểm tra kênh giúp bạn; video mới được tải về ngay trên máy này khi bạn bấm “Tải”.',
  unavailable: 'Tính năng theo dõi kênh hiện chưa mở cho tài khoản của bạn.',
  // wording: BA review — shown while the VidGrab server cannot be reached
  offlineTitle: 'Chưa kết nối được máy chủ VidGrab',
  offlineText: 'Danh sách kênh và video mới sẽ hiện lại khi có kết nối. Ứng dụng tự thử lại; bạn cũng có thể bấm Thử lại.',
  retry: 'Thử lại',
  addLabel: 'Theo dõi kênh mới',
  placeholder: 'https://www.tiktok.com/@tenkenh hoặc https://www.youtube.com/@tenkenh',
  add: 'Theo dõi',
  modeTap: 'Thông báo + bấm để tải',
  modeNotify: 'Chỉ thông báo',
  used: (u: number, m: number) => `Đang theo dõi ${u}/${m} kênh`,
  every: (h: number) => `Máy chủ kiểm tra khoảng mỗi ${h} giờ`,
  emptyTitle: 'Chưa theo dõi kênh nào',
  emptyText: 'Dán liên kết trang kênh TikTok, YouTube hoặc Instagram (gói Pro) ở trên để bắt đầu.',
  lastScan: 'Kiểm tra gần nhất',
  never: 'Chưa kiểm tra',
  newItems: (n: number) => `${n} video mới (7 ngày)`,
  paused: 'Đang tạm dừng',
  baseline: 'Đang ghi nhận video hiện có của kênh…',
  // wording: BA review — shown while the first read failed temporarily
  retrying: (t: string) => (t ? `Chưa đọc được kênh, máy chủ sẽ thử lại lúc ${t}.` : 'Chưa đọc được kênh, máy chủ sẽ thử lại sớm.'),
  degraded: 'Tạm thời không đọc được kênh, máy chủ sẽ thử lại.',
  pause: 'Tạm dừng',
  resume: 'Tiếp tục',
  remove: 'Bỏ theo dõi',
  confirmTitle: 'Bỏ theo dõi kênh này?',
  confirmText: 'Bạn có thể phải chờ một thời gian trước khi thêm kênh khác.',
  inboxTitle: 'Hộp thư', // wording: BA review
  unreadCount: (n: number) => `${n} chưa xem`, // wording: BA review
  allChannels: 'Tất cả kênh', // wording: BA review
  unreadOnly: 'Chỉ video chưa xem', // wording: BA review
  markAll: 'Đánh dấu đã xem tất cả', // wording: BA review
  unreadDot: 'Chưa xem', // wording: BA review
  downloadAll: (n: number) => `Tải tất cả (${n})`,
  download: 'Tải',
  noRecent: 'Chưa có video mới.', // wording: BA review
  reload: 'Làm mới',
};

function SubCard({ s }: { s: WatchSubscription }) {
  const [busy, setBusy] = useState(false);
  const paused = s.status === 'paused';
  const name = s.source.display_name ? channelLabel(s.source.display_name) : (s.source.canonical_url ?? '');

  async function run(f: () => Promise<{ ok: boolean; message?: string }>) {
    setBusy(true);
    try {
      const r = await f();
      if (!r.ok && r.message) toast('error', r.message);
    } finally { setBusy(false); }
  }
  async function remove() {
    const ok = await confirmDialog({ title: T.confirmTitle, text: T.confirmText, okLabel: T.remove, danger: true });
    if (ok) await run(() => removeSource(s.id));
  }

  return (
    <article className="rounded-xl border border-line bg-surface p-3.5 shadow-card">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="min-w-0 truncate text-[15px] font-semibold text-fg" title={s.source.canonical_url ?? ''}>{name}</h3>
            {s.new_items_7d > 0 && <Badge tone="accent">{T.newItems(s.new_items_7d)}</Badge>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {s.source.platform && <PlatformBadge platform={s.source.platform} />}
            {paused ? <Badge tone="warning">{T.paused}</Badge> : <Badge tone="neutral"><Bell size={11} aria-hidden /> {s.mode === 'notify_only' ? T.modeNotify : T.modeTap}</Badge>}
          </div>
        </div>
      </div>
      <dl className="mt-2.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-fg-muted">
        <dt>{T.lastScan}</dt><dd className="text-fg-2">{s.source.last_scan_at ? formatDate(s.source.last_scan_at) : T.never}</dd>
      </dl>
      {!s.baseline_completed_at && s.source.status !== 'degraded' && (
        <p className="mt-2 text-xs text-fg-muted">
          {s.source.last_failure_category ? T.retrying(formatDate(s.source.next_scan_at)) : T.baseline}
        </p>
      )}
      {s.source.status === 'degraded' && (
        <p role="alert" className="mt-2 flex items-start gap-1.5 rounded-lg bg-danger-soft px-2.5 py-1.5 text-xs text-danger">
          <AlertCircle size={14} className="mt-px shrink-0" aria-hidden />{T.degraded}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        <Button size="sm" variant="ghost" disabled={busy} icon={paused ? <Play size={14} /> : <Pause size={14} />}
          onClick={() => void run(() => setPaused(s.id, !paused))}>{paused ? T.resume : T.pause}</Button>
        <span className="ml-auto flex items-center">
          <IconButton label={T.remove} disabled={busy} onClick={() => void remove()} className="hover:!text-danger"><Trash2 size={15} /></IconButton>
        </span>
      </div>
    </article>
  );
}

function ItemRow({ i }: { i: InboxItem }) {
  return (
    <li className="flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-2">
      <span role={i.read ? undefined : 'img'} aria-label={i.read ? undefined : T.unreadDot} title={i.read ? undefined : T.unreadDot}
        className={`h-2 w-2 shrink-0 rounded-full ${i.read ? 'bg-transparent' : 'bg-accent'}`} />
      <div className="min-w-0 flex-1">
        <p className={`truncate text-sm text-fg ${i.read ? '' : 'font-semibold'}`} title={i.title ?? i.url ?? ''}>{i.title || i.url}</p>
        <p className="truncate text-xs text-fg-muted">{channelLabel(i.channel)}{i.discovered_at ? ` · ${formatDate(i.discovered_at)}` : ''}</p>
      </div>
      <Button size="sm" variant="primary" icon={<Download size={14} />} onClick={() => void downloadInboxItems([i])}>{T.download}</Button>
    </li>
  );
}

export function WatchScreen() {
  const st = watchStatus.use();
  const subs = watchSubs.use();
  const inbox = watchInbox.use();
  const q = inboxQuery.use();
  const loading = watchLoading.use();
  const [url, setUrl] = useState('');
  const [mode, setMode] = useState<'one_tap' | 'notify_only'>('one_tap');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { void refreshAll(); }, []);
  const subId = q.subscriptionId ?? '';
  const unreadOnly = q.unreadOnly === true;
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; } // refreshAll above already fetched
    void fetchInbox({ subscriptionId: subId, unreadOnly });
  }, [subId, unreadOnly]);

  if (!st || (st.kind !== 'ok' && loading)) {
    return <div className="flex h-full items-center justify-center"><Spinner className="text-accent" /></div>;
  }
  if (st.kind === 'error') {
    return (
      <div className="flex h-full flex-col">
        <ScreenHeader title={T.title} />
        <div className="flex flex-col items-center gap-3 px-6">
          <EmptyState icon={<AlertCircle size={24} />} title={T.offlineTitle} text={T.offlineText} />
          <Button size="sm" variant="ghost" disabled={loading} icon={<RefreshCw size={14} />}
            onClick={() => void refreshAll()}>{T.retry}</Button>
        </div>
      </div>
    );
  }
  if (st.kind !== 'ok') {
    return (
      <div className="flex h-full flex-col">
        <ScreenHeader title={T.title} />
        <div className="px-6"><EmptyState icon={<Eye size={24} />} title={T.title} text={T.unavailable} /></div>
      </div>
    );
  }

  const s = st.status;
  const pending = inbox.items.filter((i) => !i.read && !!i.url);
  const canAdd = s.enabled && s.used < s.limits.max_sources;

  async function add() {
    if (!url.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await addSource(url.trim(), mode);
      if (r.ok) { setUrl(''); if (r.message) toast('success', r.message); } else setErr(r.message); // server's text, verbatim
    } finally { setBusy(false); }
  }

  return (
    <div className="flex h-full flex-col">
      <ScreenHeader title={T.title} subtitle={T.subtitle}
        actions={<Button size="sm" icon={loading ? <Spinner /> : <RefreshCw size={14} />} disabled={loading} onClick={() => void refreshAll()}>{T.reload}</Button>} />
      <div className="min-h-0 flex-1 overflow-y-auto px-6 pb-6">
        <div className="rounded-xl border border-line bg-surface p-3 shadow-card">
          <label htmlFor="watch-url" className="text-[13px] font-semibold text-fg-2">{T.addLabel}</label>
          <div className="mt-1.5 flex items-center gap-2">
            <input id="watch-url" value={url} disabled={busy || !s.enabled} spellCheck={false}
              onChange={(e) => { setUrl(e.target.value); setErr(null); }}
              onKeyDown={(e) => { if (e.key === 'Enter' && !busy) void add(); }}
              placeholder={T.placeholder}
              className="h-9 min-w-0 flex-1 rounded-[10px] border border-line-strong bg-canvas px-3 text-sm text-fg placeholder:text-fg-muted" />
            <select aria-label="Chế độ nhận tin" value={mode} disabled={busy || !s.enabled} onChange={(e) => setMode(e.target.value as 'one_tap' | 'notify_only')}
              className="h-9 rounded-[10px] border border-line-strong bg-canvas px-2 text-sm text-fg">
              <option value="one_tap">{T.modeTap}</option>
              <option value="notify_only">{T.modeNotify}</option>
            </select>
            <Button variant="primary" icon={busy ? <Spinner /> : <Eye size={16} />} disabled={busy || !canAdd || !url.trim()} onClick={() => void add()}>{T.add}</Button>
          </div>
          <p className="mt-2 flex flex-wrap gap-x-3 text-xs text-fg-muted">
            <span>{T.used(s.used, s.limits.max_sources)}</span>
            {s.limits.min_interval_sec > 0 && <span>{T.every(Math.max(1, Math.round(s.limits.min_interval_sec / 3600)))}</span>}
          </p>
          {!s.enabled && <p className="mt-2 text-[13px] text-fg-muted">{T.unavailable}</p>}
          {err && <p role="alert" className="mt-2 text-[13px] text-danger">{err}</p>}
        </div>

        <section className="mt-4" aria-label={T.inboxTitle}>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-[13px] font-semibold text-fg-2">{T.inboxTitle}</h2>
            {inbox.unread > 0 && <Badge tone="accent">{T.unreadCount(inbox.unread)}</Badge>}
            {pending.length > 1 && (
              <span className="ml-auto flex gap-1.5">
                <Button size="sm" variant="primary" icon={<Download size={14} />} onClick={() => void downloadInboxItems(pending)}>{T.downloadAll(pending.length)}</Button>
              </span>
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <select aria-label={T.allChannels} value={subId} onChange={(e) => inboxQuery.set({ ...q, subscriptionId: e.target.value })}
              className="h-9 min-w-0 max-w-full rounded-[10px] border border-line-strong bg-canvas px-2 text-sm text-fg">
              <option value="">{T.allChannels}</option>
              {subs.map((x) => <option key={x.id} value={x.id}>{x.source.display_name || x.source.canonical_url}</option>)}
            </select>
            <label className="flex items-center gap-1.5 text-xs text-fg-muted">
              <input type="checkbox" checked={unreadOnly} onChange={(e) => inboxQuery.set({ ...q, unreadOnly: e.target.checked })} />
              {T.unreadOnly}
            </label>
            <Button size="sm" variant="ghost" className="ml-auto" disabled={inbox.unread === 0 || !inbox.ready}
              onClick={() => void markInboxRead('all', (m) => toast('error', m))}>{T.markAll}</Button>
          </div>
          {inbox.items.length === 0
            ? <p className="mt-2 text-xs text-fg-muted">{T.noRecent}</p>
            : <ul className="mt-2 flex flex-col gap-1.5">{inbox.items.map((i) => <ItemRow key={i.id} i={i} />)}</ul>}
        </section>

        {subs.length === 0 && !loading
          ? <EmptyState icon={<Eye size={24} />} title={T.emptyTitle} text={T.emptyText} />
          : (
            <section className="mt-4 flex flex-col gap-3" aria-label="Kênh đang theo dõi">
              {subs.map((x) => <SubCard key={x.id} s={x} />)}
            </section>
          )}
      </div>
    </div>
  );
}
