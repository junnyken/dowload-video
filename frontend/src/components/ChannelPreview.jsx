// Channel preview on the bulk page (owner 21/10/2026): list a Chinese
// channel (POST /channel/preview, nothing is counted), pick any number of
// videos, and one press downloads as many as today's allowance still covers;
// the rest stay selected. Douyin: the server lists the newest ~41 for free;
// the whole channel needs the browser extension (or, signed in, the paid
// "Lấy thêm qua máy chủ").
// wording: BA review (every sentence in this file)
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, Loader2, Puzzle, RefreshCw, Server, X } from 'lucide-react';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

const CN_CHANNEL = [
  /^https?:\/\/([a-z0-9-]+\.)*douyin\.com\/user\//i,
  /^https?:\/\/v\.douyin\.com\//i,
  /^https?:\/\/([a-z0-9-]+\.)*(xiaohongshu|rednote)\.com\/user\/profile\//i,
  /^https?:\/\/xhslink\.com\//i,
  /^https?:\/\/([a-z0-9-]+\.)*kuaishou\.(com|cn)\/profile\//i,
  /^https?:\/\/space\.bilibili\.com\/\d+/i,
];

/** True for a channel link the preview can list (Douyin / Xiaohongshu / Kuaishou / Bilibili). */
export function isPreviewableChannel(url) {
  const u = (url || '').trim();
  return CN_CHANNEL.some((re) => re.test(u));
}

/** Downloads left today for `platform` from GET /quota (null = no limit / unknown). */
export function remainingFor(quota, platform) {
  const snap = quota && quota.platform_quota;
  if (!snap || snap.unlimited) return null;
  if (snap.scope === 'platform') {
    const p = (snap.platforms || []).find((x) => x.platform === platform);
    if (p) return p.remaining < 0 ? null : p.remaining;
  }
  return typeof snap.remaining_total === 'number' && snap.remaining_total >= 0 ? snap.remaining_total : null;
}

/** The first `cap` selected (list order) now, the rest later; cap null = all now. */
export function splitByCap(items, cap) {
  const now = cap == null ? items.slice() : items.slice(0, Math.max(0, cap));
  return { now, later: items.slice(now.length) };
}

function fmtDuration(s) {
  if (!s) return null;
  const n = Math.round(s);
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}

export default function ChannelPreview({ url, withAuth, signedIn, onDownload, onClose }) {
  const [phase, setPhase] = useState('loading');
  const [listing, setListing] = useState(null);
  const [error, setError] = useState('');
  const [sel, setSel] = useState(() => new Set());
  const [quota, setQuota] = useState(null);
  const [deepBusy, setDeepBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState('');
  // 21/10: the whole Douyin channel through the extension in this browser (POST /channel-relay)
  const [relay, setRelay] = useState({ state: 'idle', count: 0 });
  const relayTimer = useRef(null);
  useEffect(() => () => { if (relayTimer.current) clearInterval(relayTimer.current); }, []);

  const refreshQuota = useCallback(() => {
    fetch(`${API}/quota`, withAuth({}))
      .then((r) => r.json())
      .then((d) => { if (d && d.success) setQuota(d.quota); })
      .catch(() => {});
  }, [withAuth]);

  const list = useCallback(async (deep) => {
    const res = await fetch(`${API}/channel/preview`, withAuth({
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, limit: 500, deep: !!deep }),
    }));
    let data = null;
    try { data = await res.json(); } catch { /* not JSON */ }
    if (!res.ok) {
      const d = data && data.detail;
      throw new Error(typeof d === 'string' && d ? d : res.status === 429
        ? 'Bạn lấy danh sách quá nhanh. Vui lòng chờ khoảng một phút rồi thử lại.'
        : 'Không lấy được danh sách video của kênh. Vui lòng thử lại sau.');
    }
    return data;
  }, [url, withAuth]);

  const load = useCallback(async () => {
    setPhase('loading'); setError(''); setNote(''); setSel(new Set());
    try {
      setListing(await list(false));
      setPhase('ready');
    } catch (e) {
      setError(e.message); setPhase('error');
    }
  }, [list]);

  useEffect(() => { void load(); refreshQuota(); }, [load, refreshQuota]);

  const loadDeep = async () => {
    setDeepBusy(true); setNote('');
    try {
      const deep = await list(true);
      if ((deep.items || []).length > (listing.items || []).length) setListing({ ...deep, moreHint: null, hasMore: false });
      else setNote('Máy chủ không lấy thêm được video nào cho kênh này. Hãy dùng tiện ích VidGrab để lấy đủ.');
    } catch (e) {
      setNote(e.message);
    } finally {
      setDeepBusy(false);
    }
  };

  const startRelay = async () => {
    if (relayTimer.current) clearInterval(relayTimer.current);
    setRelay({ state: 'waiting', count: 0 }); setNote('');
    // Open the tab first (inside the click), then point it at the channel: popup blockers allow that.
    const tab = window.open('about:blank', '_blank');
    let start;
    try {
      const res = await fetch(`${API}/channel-relay`, withAuth({
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: listing.channelUrl || url }),
      }));
      start = await res.json();
      if (!res.ok || !start.openUrl) throw new Error((start && typeof start.detail === 'string' && start.detail) || 'Không tạo được phiên lấy danh sách.');
    } catch (e) {
      if (tab) tab.close();
      setRelay({ state: 'error', count: 0, msg: e.message });
      return;
    }
    if (tab) tab.location.href = start.openUrl; else window.open(start.openUrl, '_blank');
    const until = Date.now() + 15 * 60_000;
    relayTimer.current = setInterval(async () => {
      if (Date.now() > until) {
        clearInterval(relayTimer.current);
        setRelay({ state: 'error', count: 0, msg: 'Chưa nhận được danh sách sau 15 phút. Kiểm tra tiện ích VidGrab (bản 5.11.0 trở lên) đã cài trong trình duyệt này rồi bấm lại.' });
        return;
      }
      let doc = null;
      try {
        const r = await fetch(`${API}/channel-relay/${encodeURIComponent(start.token)}`);
        if (r.status === 404) { clearInterval(relayTimer.current); setRelay({ state: 'error', count: 0, msg: 'Phiên lấy danh sách đã hết hạn. Hãy bấm lại.' }); return; }
        if (r.ok) doc = await r.json();
      } catch { /* try again next tick */ }
      if (!doc) return;
      if (doc.status !== 'done') { setRelay({ state: doc.status === 'scrolling' ? 'scrolling' : 'waiting', count: (doc.items || []).length }); return; }
      clearInterval(relayTimer.current);
      setListing((prev) => {
        const byId = new Map(((prev && prev.items) || []).map((v) => [v.id, v]));
        const merged = (doc.items || []).map((i) => byId.get(i.id) || { id: i.id, url: i.url, title: i.title || `Douyin ${i.id.slice(-6)}`, thumbnail: null, durationSec: null });
        const seen = new Set(merged.map((v) => v.id));
        for (const v of (prev && prev.items) || []) if (!seen.has(v.id)) merged.push(v);
        return { ...prev, channelTitle: doc.channelTitle || (prev && prev.channelTitle), items: merged, moreHint: null, hasMore: false };
      });
      setRelay({ state: 'idle', count: 0 });
      setNote(`Đã lấy ${(doc.items || []).length} video của kênh qua tiện ích${doc.skipped ? ` (bỏ qua ${doc.skipped} bài ảnh)` : ''}.`);
    }, 3000);
  };

  const items = useMemo(() => (listing && listing.items) || [], [listing]);
  const cap = listing ? remainingFor(quota, listing.platform) : null;
  const selected = items.filter((v) => sel.has(v.id));
  const { now, later } = splitByCap(selected, cap);
  const toggle = (id) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const submit = async () => {
    if (!now.length || sending) return;
    setSending(true);
    try {
      const ok = await onDownload(now.map((v) => v.url));
      if (ok) {
        setSel(new Set(later.map((v) => v.id)));
        setNote(later.length
          ? `Đã gửi tải ${now.length} video. Còn ${later.length} video đã chọn chưa tải vì hết lượt hôm nay${quota && quota.platform_quota && quota.platform_quota.reset_time_vn ? ` — lượt mới lúc ${quota.platform_quota.reset_time_vn}` : ''}; danh sách vẫn được giữ chọn ở đây.`
          : `Đã gửi tải ${now.length} video.`);
        refreshQuota();
      }
    } finally {
      setSending(false);
    }
  };

  const openExtensionPage = (e) => {
    e.preventDefault();
    window.history.pushState({}, '', '/extension');
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  return (
    <div className="mb-4 p-4 rounded-xl border border-line bg-surface space-y-3" aria-label="Danh sách video của kênh">
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-fg truncate">{listing ? listing.channelTitle : 'Danh sách video của kênh'}</p>
          <p className="text-[11px] text-fg-muted truncate">{url}</p>
        </div>
        <button type="button" onClick={onClose} className="p-1 text-fg-muted hover:text-fg cursor-pointer" aria-label="Đóng danh sách">
          <X className="w-4 h-4" />
        </button>
      </div>

      {phase === 'loading' && (
        <p className="flex items-center gap-2 text-xs text-fg-muted"><Loader2 className="w-4 h-4 animate-spin" /> Đang lấy danh sách video của kênh…</p>
      )}
      {phase === 'error' && (
        <div className="flex items-start gap-2 text-xs text-danger">
          <span className="flex-1">{error}</span>
          <button type="button" onClick={load} className="inline-flex items-center gap-1 text-fg-2 hover:text-fg cursor-pointer"><RefreshCw className="w-3.5 h-3.5" /> Thử lại</button>
        </div>
      )}

      {phase === 'ready' && listing && (
        <>
          {listing.moreHint === 'douyin_extension' && (
            <div className="p-2.5 rounded-lg border border-line bg-surface-2 text-[11px] text-fg-2 space-y-2">
              <p>
                Kênh này còn nhiều video hơn; máy chủ chỉ lấy được {items.length} video mới nhất (Douyin chỉ cho trình duyệt thật xem hết).
                Bấm “Lấy đủ kênh bằng tiện ích”: kênh mở ở tab mới, tiện ích VidGrab tự cuộn hết rồi gửi danh sách về đây.
              </p>
              {(relay.state === 'waiting' || relay.state === 'scrolling') && (
                <p className="flex items-center gap-2 text-fg"><Loader2 className="w-3.5 h-3.5 animate-spin" />
                  {relay.state === 'waiting' ? 'Đã mở kênh ở tab mới — chờ tiện ích VidGrab bắt đầu cuộn (đừng đóng tab đó)…' : `Tiện ích đang cuộn kênh: đã nhận ${relay.count} video…`}
                </p>
              )}
              {relay.state === 'error' && relay.msg && <p className="text-danger">{relay.msg}</p>}
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={startRelay} disabled={relay.state === 'waiting' || relay.state === 'scrolling'} className="inline-flex items-center gap-1.5 px-2.5 py-1 font-semibold text-accent-fg bg-accent hover:bg-accent-hover rounded-lg cursor-pointer disabled:opacity-60">
                  <Puzzle className="w-3.5 h-3.5" /> Lấy đủ kênh bằng tiện ích
                </button>
                <a href="/extension" onClick={openExtensionPage} className="inline-flex items-center gap-1.5 px-2.5 py-1 font-medium text-accent-text border border-line hover:border-line-strong rounded-lg">
                  <Puzzle className="w-3.5 h-3.5" /> Cài tiện ích VidGrab
                </a>
                {signedIn && (
                  <button type="button" onClick={loadDeep} disabled={deepBusy} className="inline-flex items-center gap-1.5 px-2.5 py-1 font-medium text-fg-2 border border-line hover:border-line-strong rounded-lg cursor-pointer disabled:opacity-60">
                    {deepBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Server className="w-3.5 h-3.5" />} Lấy thêm qua máy chủ (tối đa 200 video)
                  </button>
                )}
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span className="text-fg-muted">{items.length} video</span>
            <button type="button" onClick={() => setSel(new Set(items.map((v) => v.id)))} className="px-2 py-1 rounded-lg border border-line text-fg-2 hover:text-fg cursor-pointer">Chọn tất cả</button>
            <button type="button" onClick={() => setSel(new Set())} disabled={!sel.size} className="px-2 py-1 rounded-lg text-fg-muted hover:text-fg cursor-pointer disabled:opacity-50">Bỏ chọn</button>
            {[10, 50].map((k) => (
              <button key={k} type="button" onClick={() => setSel(new Set(items.slice(0, k).map((v) => v.id)))} className="px-2 py-1 rounded-lg border border-line text-fg-2 hover:text-fg cursor-pointer">{k} mới nhất</button>
            ))}
            {cap != null && <span className={`ml-auto ${cap === 0 ? 'text-danger' : 'text-fg-muted'}`}>Còn {cap} lượt tải hôm nay</span>}
          </div>

          <div className="max-h-96 overflow-y-auto space-y-1.5 pr-1">
            {items.map((v) => {
              const on = sel.has(v.id);
              const dur = fmtDuration(v.durationSec);
              return (
                <label key={v.id} className={`flex items-center gap-3 p-2 rounded-xl border cursor-pointer transition-colors ${on ? 'bg-accent-soft border-accent/40' : 'bg-surface-2 border-line hover:border-line-strong'}`}>
                  <input type="checkbox" checked={on} onChange={() => toggle(v.id)} className="w-3.5 h-3.5 accent-accent flex-shrink-0" aria-label={`Chọn ${v.title}`} />
                  {v.thumbnail && <img src={v.thumbnail} alt="" referrerPolicy="no-referrer" className="w-14 h-9 object-cover rounded-lg flex-shrink-0 bg-surface" />}
                  <span className="flex-1 min-w-0 text-xs text-fg font-medium truncate" title={v.title}>{v.title}</span>
                  {dur && <span className="text-[10px] text-fg-muted tabular-nums">{dur}</span>}
                </label>
              );
            })}
          </div>

          {later.length > 0 && (
            <p className="text-[11px] text-warning">Bạn chọn {selected.length} video nhưng hôm nay còn {cap} lượt: lần này tải {now.length} video đầu, {later.length} video còn lại vẫn được giữ chọn.</p>
          )}
          {note && <p className="text-[11px] text-fg-2">{note}</p>}

          <button
            type="button" onClick={submit} disabled={!now.length || sending}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent text-accent-fg border border-transparent text-sm font-semibold disabled:bg-surface-2 disabled:text-fg-muted disabled:border disabled:border-line disabled:cursor-not-allowed hover:bg-accent-hover transition-colors cursor-pointer"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            {selected.length === 0 ? 'Chọn video để tải' : cap === 0 ? 'Đã hết lượt tải hôm nay' : `Tải ${later.length ? `${now.length}/${selected.length}` : now.length} video đã chọn`}
          </button>
        </>
      )}
    </div>
  );
}
