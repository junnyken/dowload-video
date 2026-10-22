// Channel preview on the bulk page (owner 21/10/2026): list a Chinese
// channel (POST /channel/preview, nothing is counted), pick any number of
// videos, and one press downloads as many as today's allowance still covers;
// the rest stay selected. Douyin: the server lists the newest ~41 for free;
// the whole channel needs the browser extension (signed in) or, signed in,
// the paid "Lấy thêm qua máy chủ". From the Download button (want) the
// choice is preselected and confirmed first; nothing is sent without it.
// wording: BA review (every sentence in this file)
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, Loader2, Puzzle, RefreshCw, Server, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

const CN_CHANNEL = [
  /^https?:\/\/([a-z0-9-]+\.)*douyin\.com\/user\//i,
  /^https?:\/\/v\.douyin\.com\//i,
  /^https?:\/\/([a-z0-9-]+\.)*(xiaohongshu|rednote)\.com\/user\/profile\//i,
  /^https?:\/\/xhslink\.com\//i,
  /^https?:\/\/([a-z0-9-]+\.)*kuaishou\.(com|cn)\/profile\//i,
  /^https?:\/\/space\.bilibili\.com\/\d+/i,
];

/**
 * 22/10: a Facebook profile (or its Reels / Videos tab). Only the VidGrab
 * extension in this browser can list it (Facebook shows a signed-out
 * visitor ~10 reels; the server gets nothing), so it goes straight to the
 * relay — signed in only, like the whole Douyin channel.
 */
export const isFacebookChannel = (url) => {
  const u = (url || '').trim();
  return /^https?:\/\/(www\.|web\.|m\.)?facebook\.com\/(?!(reel|reels|watch|groups|share|events|marketplace|pages|gaming|login|photo|photos|hashtag|story\.php|profile\.php)(\/|\?|#|$))[A-Za-z0-9.-]{1,80}(\/(reels|videos))?\/?([?#].*)?$/i.test(u)
    || /^https?:\/\/(www\.|web\.|m\.)?facebook\.com\/profile\.php\?(.*&)?id=\d{5,25}/i.test(u);
};

/** True for a channel link the preview can list (Douyin / Xiaohongshu / Kuaishou / Bilibili / Facebook). */
export function isPreviewableChannel(url) {
  const u = (url || '').trim();
  return CN_CHANNEL.some((re) => re.test(u)) || isFacebookChannel(u);
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

// POST /bulk-download takes at most 200 links per request (MAX_URLS_PER_REQUEST).
export const PER_PRESS = 200;

/** Ids to preselect: 'all' = every video, a number = that many newest (list order). */
export function pickIds(items, want) {
  return new Set((want === 'all' ? items : items.slice(0, Math.max(0, want))).map((v) => v.id));
}

/** True for a douyin.com/user/… link (the extension relay also takes Facebook: isFacebookChannel). */
export const isDouyinProfile = (url) => /^https?:\/\/([a-z0-9-]+\.)*douyin\.com\/user\//i.test((url || '').trim());

function fmtDuration(s) {
  if (!s) return null;
  const n = Math.round(s);
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
}

// `want` (from the Download button): 'all' (Bulk URL tab) or N newest (Channel
// tab) is preselected and confirmed before anything is sent; undefined = the
// plain "Xem danh sách" list. `autoTab`: a tab opened inside that click, used
// to fetch the whole Douyin channel through the extension straight away.
// `serverWhole`: the server reads the whole Douyin channel itself (slower first answer).
export default function ChannelPreview({ url, withAuth, signedIn, onDownload, onClose, want, autoTab, serverWhole }) {
  const { t } = useTranslation('channelPreview');
  const lp = useLocalePath();
  const [phase, setPhase] = useState('loading');
  const [listing, setListing] = useState(null);
  const [error, setError] = useState('');
  const [sel, setSel] = useState(() => new Set());
  const [quota, setQuota] = useState(null);
  const [deepBusy, setDeepBusy] = useState(false);
  const [sending, setSending] = useState(false);
  const [note, setNote] = useState('');
  const [confirm, setConfirm] = useState(false);
  // 21/10: the whole Douyin channel through the extension in this browser (POST /channel-relay)
  const [relay, setRelay] = useState({ state: autoTab ? 'waiting' : 'idle', count: 0 });
  const relayTimer = useRef(null);
  const relayed = useRef(false); // the list already holds the whole channel
  const relayFailed = useRef(false); // the automatic fetch failed: fall back to the server's list
  const listingRef = useRef(null);
  useEffect(() => { listingRef.current = listing; }, [listing]);
  useEffect(() => () => { if (relayTimer.current) clearInterval(relayTimer.current); }, []);

  const preselect = useCallback((list) => {
    if (want === undefined) return;
    setSel(pickIds(list, want));
    setConfirm(true);
  }, [want]);

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
        ? t('errTooFast')
        : t('errListFail'));
    }
    return data;
  }, [url, withAuth, t]);

  const load = useCallback(async () => {
    if (relayed.current) return;
    setPhase('loading'); setError(''); setNote(''); setSel(new Set()); setConfirm(false);
    if (isFacebookChannel(url)) {
      // no server listing for Facebook: the extension relay is the list
      if (!signedIn) {
        setError(t('errFbSignIn'));
        setPhase('error');
      }
      return;
    }
    try {
      const data = await list(false);
      if (relayed.current) return; // the whole channel arrived first: keep it
      setListing(data);
      setPhase('ready');
      if (!autoTab || relayFailed.current) preselect(data.items || []);
    } catch (e) {
      if (relayed.current) return;
      setError(e.message); setPhase('error');
    }
  }, [list, autoTab, preselect, url, signedIn, t]);

  const loadDeep = async () => {
    setDeepBusy(true); setNote('');
    try {
      const deep = await list(true);
      if ((deep.items || []).length > (listing.items || []).length) setListing({ ...deep, moreHint: null, hasMore: false });
      else setNote(t('noMoreFromServer'));
    } catch (e) {
      setNote(e.message);
    } finally {
      setDeepBusy(false);
    }
  };

  const failRelay = (msg) => {
    if (relayTimer.current) clearInterval(relayTimer.current);
    setRelay({ state: 'error', count: 0, msg });
    if (isFacebookChannel(url) && !relayed.current) { setError(msg); setPhase('error'); }
    if (autoTab && !relayFailed.current) {
      relayFailed.current = true;
      if (listingRef.current) preselect(listingRef.current.items || []);
    }
  };

  // `given`: a tab already opened inside the user's click (automatic start);
  // otherwise open one now (this is the click), then point it at the channel:
  // popup blockers allow that.
  const startRelay = async (given) => {
    if (relayTimer.current) clearInterval(relayTimer.current);
    setRelay({ state: 'waiting', count: 0 }); setNote(''); setConfirm(false);
    const tab = given || window.open('about:blank', '_blank');
    let start;
    try {
      const res = await fetch(`${API}/channel-relay`, withAuth({
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url: (listingRef.current && listingRef.current.channelUrl) || url }),
      }));
      start = await res.json();
      if (!res.ok || !start.openUrl) throw new Error((start && typeof start.detail === 'string' && start.detail) || t('errRelaySession'));
    } catch (e) {
      if (tab) tab.close();
      failRelay(e.message);
      return;
    }
    if (tab) tab.location.href = start.openUrl; else window.open(start.openUrl, '_blank');
    const opened = Date.now();
    const until = opened + 15 * 60_000;
    relayTimer.current = setInterval(async () => {
      if (Date.now() > until) {
        failRelay(t('errRelayTimeout'));
        return;
      }
      let doc = null;
      try {
        const r = await fetch(`${API}/channel-relay/${encodeURIComponent(start.token)}`);
        if (r.status === 404) { failRelay(t('errRelayExpired')); return; }
        if (r.ok) doc = await r.json();
      } catch { /* try again next tick */ }
      if (!doc) return;
      if (doc.status !== 'done') {
        // nothing from the extension after 20 s: most likely it is not in this browser
        const state = doc.status === 'waiting' && Date.now() - opened > 20_000 ? 'noext' : doc.status;
        setRelay({ state: ['scrolling', 'login', 'noext'].includes(state) ? state : 'waiting', count: (doc.items || []).length });
        return;
      }
      clearInterval(relayTimer.current);
      const prev = listingRef.current;
      const byId = new Map(((prev && prev.items) || []).map((v) => [v.id, v]));
      const pname = doc.platform === 'facebook' ? 'Facebook' : 'Douyin';
      const merged = (doc.items || []).map((i) => byId.get(i.id) || { id: i.id, url: i.url, title: i.title || `${pname} ${i.id.slice(-6)}`, thumbnail: null, durationSec: null });
      const seen = new Set(merged.map((v) => v.id));
      for (const v of (prev && prev.items) || []) if (!seen.has(v.id)) merged.push(v);
      relayed.current = true;
      setListing({ platform: doc.platform || 'douyin', ...prev, channelTitle: doc.channelTitle || (prev && prev.channelTitle), items: merged, moreHint: null, hasMore: false, whole: true });
      setPhase('ready'); setError('');
      setRelay({ state: 'idle', count: 0 });
      setNote(doc.skipped
        ? t('relayDoneSkipped', { count: (doc.items || []).length, skipped: doc.skipped })
        : t('relayDone', { count: (doc.items || []).length }));
      preselect(merged);
    }, 3000);
  };

  useEffect(() => {
    void load(); refreshQuota();
    if (autoTab) void startRelay(autoTab);
    // once per mount: `key` in BulkContent remounts for a new link
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const items = useMemo(() => (listing && listing.items) || [], [listing]);
  const cap = listing ? remainingFor(quota, listing.platform) : null;
  const selected = items.filter((v) => sel.has(v.id));
  const quotaBound = cap != null && cap <= PER_PRESS; // what is left today stops this press, not the 200 per request
  const { now, later } = splitByCap(selected, cap == null ? PER_PRESS : Math.min(cap, PER_PRESS));
  // wording: BA review
  // `pre`: 'warn' = "You selected N videos but …", 'note' = "Note: …"
  const laterText = (pre) => t(`${pre}${quotaBound ? 'Quota' : 'Cap'}`, { count: now.length, later: later.length, selected: selected.length, cap, per: PER_PRESS });
  const toggle = (id) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });

  const submit = async () => {
    if (!now.length || sending) return;
    setSending(true); setConfirm(false);
    try {
      const ok = await onDownload(now.map((v) => v.url));
      if (ok) {
        setSel(new Set(later.map((v) => v.id)));
        const resetAt = quota && quota.platform_quota && quota.platform_quota.reset_time_vn;
        setNote(!later.length
          ? t('sent', { count: now.length })
          : quotaBound
            ? t(resetAt ? 'sentQuotaReset' : 'sentQuota', { count: now.length, later: later.length, time: resetAt })
            : t('sentCap', { count: now.length, later: later.length, per: PER_PRESS }));
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
    <div className="mb-4 p-4 rounded-xl border border-line bg-surface space-y-3" aria-label={t('listLabel')}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-fg truncate">{listing ? listing.channelTitle : t('listLabel')}</p>
          <p className="text-[11px] text-fg-muted truncate">{url}</p>
        </div>
        <button type="button" onClick={onClose} className="p-1 text-fg-muted hover:text-fg cursor-pointer" aria-label={t('closeList')}>
          <X className="w-4 h-4" />
        </button>
      </div>

      {(relay.state === 'waiting' || relay.state === 'scrolling') && (
        <p className="flex items-center gap-2 text-xs text-fg"><Loader2 className="w-3.5 h-3.5 animate-spin" />
          {relay.state === 'waiting' ? t('relayWaiting') : t('relayScrolling', { count: relay.count })}
        </p>
      )}
      {relay.state === 'login' && (
        <p className="flex items-start gap-2 text-xs text-warning"><Loader2 className="w-3.5 h-3.5 animate-spin flex-shrink-0 mt-0.5" />
          {t('relayLogin', { platform: isFacebookChannel(url) ? 'Facebook' : 'Douyin', count: relay.count })}
        </p>
      )}
      {relay.state === 'noext' && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-warning">
          <Loader2 className="w-3.5 h-3.5 animate-spin flex-shrink-0" />
          <span className="flex-1 min-w-0">{t('relayNoExt', { ver: isFacebookChannel(url) ? '5.15.0' : '5.11.1' })}</span>
          {/* new tab: leaving this page would stop the wait */}
          <a href={lp('/extension')} target="_blank" rel="noopener" className="inline-flex items-center gap-1.5 px-2.5 py-1 font-medium text-accent-text border border-line hover:border-line-strong rounded-lg">
            <Puzzle className="w-3.5 h-3.5" /> {t('installExt')}
          </a>
        </div>
      )}
      {relay.state === 'error' && relay.msg && <p className="text-xs text-danger">{relay.msg}</p>}
      {phase === 'loading' && (relay.state === 'idle' || relay.state === 'error') && (
        <p className="flex items-center gap-2 text-xs text-fg-muted"><Loader2 className="w-4 h-4 animate-spin" /> {serverWhole ? t('loadingWhole') : t('loadingList')}</p>
      )}
      {phase === 'error' && (
        <div className="flex items-start gap-2 text-xs text-danger">
          <span className="flex-1">{error}</span>
          <button type="button" onClick={load} className="inline-flex items-center gap-1 text-fg-2 hover:text-fg cursor-pointer"><RefreshCw className="w-3.5 h-3.5" /> {t('retry')}</button>
        </div>
      )}

      {phase === 'ready' && listing && (
        <>
          {listing.moreHint === 'douyin_extension' && (
            <div className="p-2.5 rounded-lg border border-line bg-surface-2 text-[11px] text-fg-2 space-y-2">
              <p>
                {t('moreHint', { count: items.length })}
                {' '}{signedIn ? t('moreHintSignedIn') : t('moreHintGuest')}
              </p>
              <div className="flex flex-wrap gap-2">
                {signedIn && (
                  <button type="button" onClick={() => startRelay()} disabled={['waiting', 'scrolling', 'login', 'noext'].includes(relay.state)} className="inline-flex items-center gap-1.5 px-2.5 py-1 font-semibold text-accent-fg bg-accent hover:bg-accent-hover rounded-lg cursor-pointer disabled:opacity-60">
                    <Puzzle className="w-3.5 h-3.5" /> {t('fetchWholeExt')}
                  </button>
                )}
                <a href={lp('/extension')} onClick={openExtensionPage} className="inline-flex items-center gap-1.5 px-2.5 py-1 font-medium text-accent-text border border-line hover:border-line-strong rounded-lg">
                  <Puzzle className="w-3.5 h-3.5" /> {t('installExt')}
                </a>
                {signedIn && (
                  <button type="button" onClick={loadDeep} disabled={deepBusy} className="inline-flex items-center gap-1.5 px-2.5 py-1 font-medium text-fg-2 border border-line hover:border-line-strong rounded-lg cursor-pointer disabled:opacity-60">
                    {deepBusy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Server className="w-3.5 h-3.5" />} {t('fetchMoreServer')}
                  </button>
                )}
              </div>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2 text-[11px]">
            <span className="text-fg-muted">{t('videoCount', { count: items.length })}</span>
            <button type="button" onClick={() => setSel(new Set(items.map((v) => v.id)))} className="px-2 py-1 rounded-lg border border-line text-fg-2 hover:text-fg cursor-pointer">{t('selectAll')}</button>
            <button type="button" onClick={() => setSel(new Set())} disabled={!sel.size} className="px-2 py-1 rounded-lg text-fg-muted hover:text-fg cursor-pointer disabled:opacity-50">{t('deselect')}</button>
            {[10, 50].map((k) => (
              <button key={k} type="button" onClick={() => setSel(new Set(items.slice(0, k).map((v) => v.id)))} className="px-2 py-1 rounded-lg border border-line text-fg-2 hover:text-fg cursor-pointer">{t('newestN', { count: k })}</button>
            ))}
            {cap != null && <span className={`ml-auto ${cap === 0 ? 'text-danger' : 'text-fg-muted'}`}>{t('capLeft', { count: cap })}</span>}
          </div>

          <div className="max-h-96 overflow-y-auto space-y-1.5 pr-1">
            {items.map((v) => {
              const on = sel.has(v.id);
              const dur = fmtDuration(v.durationSec);
              return (
                <label key={v.id} className={`flex items-center gap-3 p-2 rounded-xl border cursor-pointer transition-colors ${on ? 'bg-accent-soft border-accent/40' : 'bg-surface-2 border-line hover:border-line-strong'}`}>
                  <input type="checkbox" checked={on} onChange={() => toggle(v.id)} className="w-3.5 h-3.5 accent-accent flex-shrink-0" aria-label={t('selectVideo', { title: v.title })} />
                  {v.thumbnail && <img src={v.thumbnail} alt="" referrerPolicy="no-referrer" className="w-14 h-9 object-cover rounded-lg flex-shrink-0 bg-surface" />}
                  <span className="flex-1 min-w-0 text-xs text-fg font-medium truncate" title={v.title}>{v.title}</span>
                  {dur && <span className="text-[10px] text-fg-muted tabular-nums">{dur}</span>}
                </label>
              );
            })}
          </div>

          {later.length > 0 && !confirm && (
            <p className="text-[11px] text-warning">{laterText('warn')}</p>
          )}
          {note && <p className="text-[11px] text-fg-2">{note}</p>}

          {confirm && selected.length > 0 && (
            // wording: BA review
            <div className="p-3 rounded-xl border border-accent/40 bg-accent-soft text-xs text-fg space-y-2" role="alertdialog" aria-label={t('confirmLabel')}>
              <p className="font-semibold">
                {t(`confirmHead${listing.whole ? 'Whole' : 'Server'}${selected.length === items.length ? 'All' : 'N'}`, { total: items.length, count: selected.length })}
              </p>
              <p className="text-fg-2">
                {cap === 0
                  ? t('confirmNoQuota')
                  : later.length ? laterText('note') : t('confirmAsk', { count: now.length })}
              </p>
              <div className="flex flex-wrap gap-2">
                <button type="button" onClick={submit} disabled={!now.length || sending} className="inline-flex items-center gap-1.5 px-3 py-1.5 font-semibold text-accent-fg bg-accent hover:bg-accent-hover rounded-lg cursor-pointer disabled:opacity-60">
                  {sending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />} {t('downloadN', { count: now.length })}
                </button>
                <button type="button" onClick={() => setConfirm(false)} className="px-3 py-1.5 font-medium text-fg-2 border border-line hover:border-line-strong rounded-lg cursor-pointer">
                  {t('reselect')}
                </button>
              </div>
            </div>
          )}

          <button
            type="button" onClick={submit} disabled={!now.length || sending}
            className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent text-accent-fg border border-transparent text-sm font-semibold disabled:bg-surface-2 disabled:text-fg-muted disabled:border disabled:border-line disabled:cursor-not-allowed hover:bg-accent-hover transition-colors cursor-pointer"
          >
            {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            {selected.length === 0 ? t('pickToDownload') : cap === 0 ? t('noQuotaToday') : later.length ? t('downloadPartial', { count: now.length, selected: selected.length }) : t('downloadSelected', { count: now.length })}
          </button>
        </>
      )}
    </div>
  );
}
