// "Theo dõi kênh" — Channel Watch (Phase 33A, task #6257).
// Signed-in only; shown in the menu only when GET /watch/status says enabled.
// All user-facing strings below: wording: BA review.
import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { Eye, Plus, Trash2, Pause, Play, RefreshCw, Bell, Download, AlertCircle } from 'lucide-react';
import EmptyState from '../components/shared/EmptyState';
import { API_BASE } from '../lib/apiBase';
import { enableWebPush, pushSupported } from '../lib/webPush';

const API = `${API_BASE}/api/v1/watch`;

// wording: BA review
const T = {
  title: 'Theo dõi kênh',
  subtitle: 'Nhận thông báo khi kênh bạn theo dõi có video mới.',
  loginTitle: 'Đăng nhập để theo dõi kênh',
  loginBody: 'Theo dõi kênh chỉ dành cho tài khoản đã đăng nhập.',
  loginBtn: 'Đăng nhập',
  unavailable: 'Tính năng theo dõi kênh hiện chưa mở cho tài khoản của bạn.',
  placeholder: 'Dán liên kết kênh TikTok, YouTube hoặc Instagram (gói Pro), ví dụ https://www.youtube.com/@tenkenh',
  add: 'Theo dõi',
  adding: 'Đang kiểm tra kênh…',
  used: (u, m) => `Đang theo dõi ${u}/${m} kênh`,
  every: (h) => `Kiểm tra khoảng mỗi ${h} giờ`,
  emptyTitle: 'Chưa theo dõi kênh nào',
  emptyBody: 'Dán liên kết trang kênh ở trên để bắt đầu.',
  lastScan: 'Kiểm tra gần nhất',
  never: 'Chưa kiểm tra',
  newItems: (n) => `${n} video mới (7 ngày)`,
  paused: 'Tạm dừng',
  pause: 'Tạm dừng',
  resume: 'Tiếp tục',
  remove: 'Bỏ theo dõi',
  confirmRemove: 'Bỏ theo dõi kênh này? Bạn có thể phải chờ một thời gian trước khi thêm kênh khác.',
  baselinePending: 'Đang ghi nhận video hiện có của kênh…',
  // wording: BA review — shown while the first read failed temporarily
  retrying: (t) => (t ? `Chưa đọc được kênh, sẽ thử lại lúc ${t}.` : 'Chưa đọc được kênh, hệ thống sẽ thử lại sớm.'),
  degraded: 'Tạm thời không đọc được kênh, hệ thống sẽ thử lại.',
  modeTap: 'Thông báo + mở nhanh để tải',
  modeNotify: 'Chỉ thông báo',
  download: 'Tải',
  inboxTitle: 'Hộp thư', // wording: BA review
  unreadCount: (n) => `${n} chưa xem`, // wording: BA review
  allChannels: 'Tất cả kênh', // wording: BA review
  unreadOnly: 'Chỉ video chưa xem', // wording: BA review
  markAll: 'Đánh dấu đã xem tất cả', // wording: BA review
  inboxEmpty: 'Chưa có video mới.', // wording: BA review
  unreadDot: 'Chưa xem', // wording: BA review
  pushBtn: 'Bật thông báo',
  push: {
    ok: 'Đã bật thông báo trên trình duyệt này.',
    unsupported: 'Trình duyệt này không hỗ trợ thông báo.',
    denied: 'Bạn đã chặn thông báo. Hãy cho phép trong cài đặt trình duyệt.',
    not_configured: 'Máy chủ chưa bật thông báo.',
    failed: 'Không bật được thông báo. Vui lòng thử lại.',
  },
  genericError: 'Có lỗi xảy ra. Vui lòng thử lại.',
};

function fmt(iso) {
  if (!iso) return null;
  return new Date(iso).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

function errText(d) {
  return d?.message || d?.detail?.message || (typeof d?.detail === 'string' ? d.detail : '') || T.genericError;
}

export default function WatchPage() {
  const { isAuthenticated, session, withAuth } = useAuth();
  const [status, setStatus] = useState(null);       // null = loading, false = unavailable
  const [subs, setSubs] = useState([]);
  const [inbox, setInbox] = useState({ items: [], unread: 0, ready: true });
  const [inboxSub, setInboxSub] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [url, setUrl] = useState('');
  const [mode, setMode] = useState('one_tap');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    if (!isAuthenticated) return undefined;
    let alive = true;
    const getJson = (path) => fetch(`${API}${path}`, withAuth()).then((r) => (r.ok ? r.json() : null));
    getJson('/status')
      .then((st) => {
        if (!alive) return null;
        setStatus(st || false);
        if (!st) return null;
        return getJson('/subscriptions');
      })
      .then((s) => {
        if (!alive || !s) return;
        setSubs(s.subscriptions || []);
      })
      .catch(() => { if (alive) setStatus(false); });
    return () => { alive = false; };
  }, [isAuthenticated, withAuth, reloadKey]);

  useEffect(() => {
    if (!isAuthenticated) return undefined;
    let alive = true;
    const qs = new URLSearchParams();
    if (inboxSub) qs.set('subscription_id', inboxSub);
    if (unreadOnly) qs.set('unread', 'true');
    const q = qs.toString();
    fetch(`${API}/inbox${q ? `?${q}` : ''}`, withAuth())
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!alive || !d) return;
        setInbox({
          items: Array.isArray(d.items) ? d.items : [],
          unread: Number(d.unread) || 0,
          ready: d.ready !== false,
        });
      })
      .catch(() => {});
    return () => { alive = false; };
  }, [isAuthenticated, withAuth, reloadKey, inboxSub, unreadOnly]);

  if (!isAuthenticated) {
    return (
      <div className="max-w-3xl mx-auto px-4 md:px-8 py-8">
        <EmptyState icon={<Eye className="w-10 h-10" />} title={T.loginTitle} body={T.loginBody}
          action={(
            <button onClick={() => window.dispatchEvent(new Event('vidgrab:open-auth'))}
              className="px-4 py-2 rounded-xl bg-accent text-accent-fg font-bold text-sm cursor-pointer">
              {T.loginBtn}
            </button>
          )} />
      </div>
    );
  }

  if (status === null) {
    return <div className="max-w-3xl mx-auto px-4 py-16 flex justify-center"><RefreshCw className="w-5 h-5 animate-spin text-fg-muted" /></div>;
  }
  if (status === false) {
    return <div className="max-w-3xl mx-auto px-4 md:px-8 py-8"><EmptyState icon={<Eye className="w-10 h-10" />} title={T.title} body={T.unavailable} /></div>;
  }

  async function add(e) {
    e.preventDefault();
    if (!url.trim()) return;
    setBusy(true); setErr(''); setMsg('');
    try {
      const r = await fetch(`${API}/sources`, withAuth({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim(), mode }),
      }));
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setErr(errText(d)); return; }
      setUrl('');
      load();
    } catch { setErr(T.genericError); } finally { setBusy(false); }
  }

  async function patch(id, body) {
    const r = await fetch(`${API}/subscriptions/${id}`, withAuth({
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }));
    if (!r.ok) setErr(errText(await r.json().catch(() => ({}))));
    load();
  }

  async function remove(id) {
    if (!confirm(T.confirmRemove)) return;
    const r = await fetch(`${API}/subscriptions/${id}`, withAuth({ method: 'DELETE' }));
    if (!r.ok) setErr(errText(await r.json().catch(() => ({}))));
    load();
  }

  async function markRead(payload, optimistic) {
    const prev = inbox;
    setInbox(optimistic);
    try {
      const r = await fetch(`${API}/inbox/read`, withAuth({
        // keepalive: "Tải" navigates to /share-target right after the click.
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), keepalive: true,
      }));
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { setInbox(prev); setErr(errText(d)); return; }
      if (typeof d.unread === 'number') setInbox((cur) => ({ ...cur, unread: d.unread }));
    } catch { setInbox(prev); }
  }

  function markOne(it) {
    if (it.read) return;
    markRead({ item_ids: [it.id] }, {
      ...inbox,
      items: inbox.items.map((x) => (x.id === it.id ? { ...x, read: true } : x)),
      unread: Math.max(0, inbox.unread - 1),
    });
  }

  function markAll() {
    markRead({ all: true }, { ...inbox, items: inbox.items.map((x) => ({ ...x, read: true })), unread: 0 });
  }

  async function push() {
    const res = await enableWebPush(session?.access_token);
    setMsg(T.push[res] || T.push.failed);
  }

  const canAdd = status.enabled && status.used < status.limits.max_sources;

  return (
    <div className="max-w-3xl mx-auto px-4 md:px-8 py-8">
      <div className="flex items-start justify-between gap-3 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-fg">{T.title}</h1>
          <p className="text-sm text-fg-muted mt-1">{T.subtitle}</p>
        </div>
        {pushSupported() && (
          <button onClick={push}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl border border-line-strong text-fg-2 text-xs font-bold hover:text-fg cursor-pointer shrink-0">
            <Bell className="w-4 h-4" /> {T.pushBtn}
          </button>
        )}
      </div>

      <form onSubmit={add} className="mb-3 flex flex-col sm:flex-row gap-2">
        <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder={T.placeholder}
          disabled={!status.enabled || busy}
          className="flex-1 min-w-0 px-3 py-2 rounded-xl bg-surface-2 border border-line text-sm text-fg" />
        <select value={mode} onChange={(e) => setMode(e.target.value)} disabled={!status.enabled || busy}
          className="px-3 py-2 rounded-xl bg-surface-2 border border-line text-sm text-fg">
          <option value="one_tap">{T.modeTap}</option>
          <option value="notify_only">{T.modeNotify}</option>
        </select>
        <button type="submit" disabled={!canAdd || busy || !url.trim()}
          className="flex items-center justify-center gap-2 px-4 py-2 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 cursor-pointer disabled:opacity-50">
          {busy ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
          {busy ? T.adding : T.add}
        </button>
      </form>

      <div className="mb-4 px-4 py-2.5 bg-surface-2 border border-line rounded-xl text-xs text-fg-muted flex flex-wrap gap-x-4 gap-y-1">
        <span>{T.used(status.used, status.limits.max_sources)}</span>
        <span>{T.every(Math.round(status.limits.min_interval_sec / 3600))}</span>
      </div>

      {err && (
        <p className="mb-4 text-xs text-danger bg-danger-soft border border-danger/20 rounded-lg px-3 py-2 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 shrink-0" /> {err}
        </p>
      )}
      {msg && <p className="mb-4 text-xs text-fg-2 bg-surface-2 border border-line rounded-lg px-3 py-2">{msg}</p>}

      {subs.length === 0 ? (
        <EmptyState icon={<Eye className="w-10 h-10" />} title={T.emptyTitle} body={T.emptyBody} compact />
      ) : (
        <div className="flex flex-col gap-3">
          {subs.map((s) => {
            const paused = s.status === 'paused';
            return (
              <div key={s.id} className="p-4 rounded-2xl border border-line bg-surface flex flex-col gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <a href={s.source.canonical_url} target="_blank" rel="noreferrer noopener"
                    className="font-bold text-sm text-fg truncate hover:underline">{s.source.display_name || s.source.canonical_url}</a>
                  {paused && <span className="text-xs px-2 py-0.5 rounded bg-surface-2 text-fg-muted shrink-0">{T.paused}</span>}
                </div>
                <div className="flex gap-4 text-xs text-fg-muted flex-wrap">
                  <span>{T.lastScan}: {fmt(s.source.last_scan_at) || T.never}</span>
                  {s.new_items_7d > 0 && <span className="text-accent-text">{T.newItems(s.new_items_7d)}</span>}
                  <span>{s.mode === 'notify_only' ? T.modeNotify : T.modeTap}</span>
                </div>
                {!s.baseline_completed_at && s.source.status !== 'degraded' && (
                  <p className="text-xs text-fg-muted">
                    {s.source.last_failure_category ? T.retrying(fmt(s.source.next_scan_at)) : T.baselinePending}
                  </p>
                )}
                {s.source.status === 'degraded' && <p className="text-xs text-danger">{T.degraded}</p>}
                <div className="flex items-center gap-2 pt-0.5">
                  <button onClick={() => patch(s.id, { status: paused ? 'active' : 'paused' })}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-line-strong text-xs text-fg-muted hover:text-fg cursor-pointer">
                    {paused ? <><Play className="w-3.5 h-3.5" /> {T.resume}</> : <><Pause className="w-3.5 h-3.5" /> {T.pause}</>}
                  </button>
                  <button onClick={() => remove(s.id)} title={T.remove}
                    className="ml-auto flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs text-fg-muted hover:text-danger hover:bg-danger-soft cursor-pointer">
                    <Trash2 className="w-3.5 h-3.5" /> {T.remove}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-8">
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          <h2 className="text-sm font-bold text-fg-2">{T.inboxTitle}</h2>
          {inbox.unread > 0 && (
            <span className="text-[11px] px-2 py-0.5 rounded-full bg-accent-soft text-accent-text font-bold">{T.unreadCount(inbox.unread)}</span>
          )}
        </div>
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 mb-3">
          <select value={inboxSub} onChange={(e) => setInboxSub(e.target.value)} aria-label={T.allChannels}
            className="min-w-0 px-3 py-2 rounded-xl bg-surface-2 border border-line text-sm text-fg">
            <option value="">{T.allChannels}</option>
            {subs.map((s) => (
              <option key={s.id} value={s.id}>{s.source.display_name || s.source.canonical_url}</option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-xs text-fg-muted cursor-pointer">
            <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} />
            {T.unreadOnly}
          </label>
          <button onClick={markAll} disabled={inbox.unread === 0 || !inbox.ready}
            className="sm:ml-auto px-3 py-2 rounded-lg border border-line-strong text-xs font-bold text-fg-muted hover:text-fg cursor-pointer disabled:opacity-50">
            {T.markAll}
          </button>
        </div>
        {inbox.items.length === 0 ? (
          <p className="text-sm text-fg-muted px-1 py-4">{T.inboxEmpty}</p>
        ) : (
          <div className="flex flex-col gap-2">
            {inbox.items.map((it) => (
              <div key={it.id} className="flex items-center gap-3 px-3 py-2 rounded-xl border border-line bg-surface min-w-0">
                <span title={it.read ? undefined : T.unreadDot} aria-label={it.read ? undefined : T.unreadDot}
                  className={`w-2 h-2 rounded-full shrink-0 ${it.read ? 'bg-transparent' : 'bg-accent'}`} />
                <div className="flex-1 min-w-0">
                  <a href={it.url} target="_blank" rel="noreferrer noopener" onClick={() => markOne(it)}
                    className={`block text-sm text-fg truncate hover:underline ${it.read ? '' : 'font-bold'}`}>{it.title || it.url}</a>
                  <p className="text-[11px] text-fg-muted truncate">{it.platform} · {it.channel} · {fmt(it.discovered_at)}</p>
                </div>
                <a href={`/share-target?url=${encodeURIComponent(it.url)}`} onClick={() => markOne(it)}
                  className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-accent/30 text-accent-text text-xs font-bold hover:bg-accent-soft shrink-0">
                  <Download className="w-3.5 h-3.5" /> {T.download}
                </a>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
