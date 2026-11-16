// "Theo dõi kênh" — Channel Watch (Phase 33A–33F).
// Signed-in only; shown in the menu only when GET /watch/status says enabled.
// All user-facing strings below: wording: BA review.
import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import { useEntitlement } from '../hooks/useEntitlement';
import { Eye, Plus, Trash2, Pause, Play, RefreshCw, Bell, Download, AlertCircle, FileDown, Monitor } from 'lucide-react';
import EmptyState from '../components/shared/EmptyState';
import { API_BASE } from '../lib/apiBase';
import { localDownloadUrl, localFileId, toFileId } from '../lib/localFile';
import { buildChannelZipFilename, groupWatchItemsByChannel, canShowWatchBulkZip } from '../lib/watchZip';
import { enableWebPush, pushSupported } from '../lib/webPush';
import { useTranslation } from 'react-i18next';
import i18n, { getLang, useLocalePath } from '../i18n';

const API = `${API_BASE}/api/v1/watch`;
const CORE_API = `${API_BASE}/api/v1`;

// wording: BA review
// Text lives in locales/*/watch.json; getters read it at render time (language can change).
const w = (key, opts) => i18n.t(key, { ns: 'watch', ...opts });
const T = {
  get title() { return w('title'); },
  get subtitle() { return w('subtitle'); },
  get loginTitle() { return w('loginTitle'); },
  get loginBody() { return w('loginBody'); },
  get loginBtn() { return w('loginBtn'); },
  get unavailable() { return w('unavailable'); },
  get offlineTitle() { return w('offlineTitle'); },
  get offlineBody() { return w('offlineBody'); },
  get retry() { return w('retry'); },
  get placeholder() { return w('placeholder'); },
  get add() { return w('add'); },
  get adding() { return w('adding'); },
  get emptyTitle() { return w('emptyTitle'); },
  get emptyBody() { return w('emptyBody'); },
  get lastScan() { return w('lastScan'); },
  get never() { return w('never'); },
  get paused() { return w('paused'); },
  get pause() { return w('pause'); },
  get resume() { return w('resume'); },
  get remove() { return w('remove'); },
  get confirmRemove() { return w('confirmRemove'); },
  get baselinePending() { return w('baselinePending'); },
  get degraded() { return w('degraded'); },
  get modeTap() { return w('modeTap'); },
  get modeNotify() { return w('modeNotify'); },
  get modeAuto() { return w('modeAuto'); },
  get download() { return w('download'); },
  get inboxTitle() { return w('inboxTitle'); },
  get newVideosGroup() { return w('newVideosGroup'); },
  get newBadge() { return w('newBadge'); },
  get unknownTime() { return w('unknownTime'); },
  get allChannels() { return w('allChannels'); },
  get unreadOnly() { return w('unreadOnly'); },
  get markAll() { return w('markAll'); },
  get inboxEmpty() { return w('inboxEmpty'); },
  get unreadDot() { return w('unreadDot'); },
  get pushBtn() { return w('pushBtn'); },
  get genericError() { return w('genericError'); },
  get zipPacking() { return w('zipPacking'); },
  get zipFailed() { return w('zipFailed'); },
  get desktopHintText() { return w('desktopHintText'); },
  get desktopHintCta() { return w('desktopHintCta'); },
  used: (used, max) => w('used', { used, max }),
  every: (hours) => w('every', { hours }),
  newItems: (count) => w('newItems', { count }),
  // wording: BA review — shown while the first read failed temporarily
  retrying: (when) => (when ? w('retryingAt', { when }) : w('retrying')),
  unreadCount: (count) => w('unreadCount', { count }),
  publishedAt: (when) => w('publishedAt', { when }),
  detectedAt: (when) => w('detectedAt', { when }),
  downloadAllZip: (count) => w('downloadAllZip', { count }),
  zipPreparing: (done, total) => w('zipPreparing', { done, total }),
  zipReady: (file) => w('zipReady', { file }),
  push: {
    get ok() { return w('push.ok'); },
    get unsupported() { return w('push.unsupported'); },
    get denied() { return w('push.denied'); },
    get not_configured() { return w('push.not_configured'); },
    get failed() { return w('push.failed'); },
  },
};

function fmt(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(getLang() === 'en' ? 'en-US' : 'vi-VN', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function errText(d) {
  return d?.message || d?.detail?.message || (typeof d?.detail === 'string' ? d.detail : '') || T.genericError;
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export default function WatchPage() {
  useTranslation('watch'); // re-render on language change (T reads i18n at render)
  const lp = useLocalePath();
  const { isAuthenticated, session, withAuth } = useAuth();
  const { allowed } = useEntitlement();
  const [status, setStatus] = useState(null);       // null = loading, false = unavailable
  const [subs, setSubs] = useState([]);
  const [inbox, setInbox] = useState({ items: [], unread: 0, ready: true });
  const [inboxSub, setInboxSub] = useState('');
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [url, setUrl] = useState('');
  const [mode, setMode] = useState('one_tap');
  const [busy, setBusy] = useState(false);
  const [zipState, setZipState] = useState({});
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  const [reloadKey, setReloadKey] = useState(0);
  const load = useCallback(() => setReloadKey((k) => k + 1), []);

  useEffect(() => {
    if (!isAuthenticated) return undefined;
    let alive = true;
    const getJson = (path) => fetch(`${API}${path}`, withAuth()).then((r) => (r.ok ? r.json() : null));
    // /status: 'offline' for network errors and gateway 5xx (13/10: overloaded
    // host) — only the server's own refusal means the feature is not open.
    fetch(`${API}/status`, withAuth())
      .then(async (r) => {
        if (r.ok) return r.json();
        const d = await r.json().catch(() => null);
        if (r.status >= 500 && d?.error_code !== 'watch_disabled') return 'offline';
        return null;
      })
      .then((st) => {
        if (!alive) return null;
        setStatus(st || false);
        if (!st || st === 'offline') return null;
        return getJson('/subscriptions');
      })
      .then((s) => {
        if (!alive || !s) return;
        setSubs(s.subscriptions || []);
      })
      .catch(() => { if (alive) setStatus('offline'); });
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
  if (status === 'offline') {
    return (
      <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 flex flex-col items-center gap-3">
        <EmptyState icon={<Eye className="w-10 h-10" />} title={T.offlineTitle} body={T.offlineBody} />
        <button onClick={load} className="px-4 py-2 rounded-lg border border-line-strong text-sm text-fg-muted hover:text-fg cursor-pointer">{T.retry}</button>
      </div>
    );
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

  const bulkZipAllowed = canShowWatchBulkZip(status, allowed('bulk_zip'));

  async function downloadChannelZip(group) {
    if (!bulkZipAllowed || zipState[group.key]) return;
    const urls = [...new Set(group.items.map((x) => x.url).filter(Boolean))];
    if (urls.length === 0) return;

    setErr('');
    setMsg('');
    setZipState((s) => ({ ...s, [group.key]: { phase: 'downloading', done: 0, total: urls.length } }));

    try {
      // 1. Submit to existing /bulk-download flow (deducts daily quota normally)
      const startRes = await fetch(`${CORE_API}/bulk-download`, withAuth({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          urls,
          channel_mode: false,
          max_videos: urls.length,
          min_views: 0,
          quality: 'video',
          remove_watermark: true,
        }),
      }));
      const startData = await startRes.json().catch(() => ({}));
      if (!startRes.ok || !startData.batch_id) {
        throw new Error(errText(startData));
      }
      const batchId = startData.batch_id;

      // 2. Poll /jobs/{batchId} until all video jobs reach terminal state
      let videoJobs = [];
      for (let attempt = 0; attempt < 240; attempt += 1) {
        if (!mountedRef.current) return;
        await sleep(2500);
        const jobsRes = await fetch(`${CORE_API}/jobs/${batchId}`, withAuth());
        if (!jobsRes.ok) continue;
        const jobsData = await jobsRes.json().catch(() => ({}));
        videoJobs = (jobsData.jobs || []).filter((j) => j.original_url !== 'batch_zip');
        const doneCount = videoJobs.filter((j) => ['success', 'failed', 'cancelled'].includes(j.status)).length;
        if (mountedRef.current) {
          setZipState((s) => ({
            ...s,
            [group.key]: { phase: 'downloading', done: doneCount, total: videoJobs.length || urls.length },
          }));
        }
        if (videoJobs.length > 0 && doneCount === videoJobs.length) break;
      }

      const okJobs = videoJobs.filter((j) => j.status === 'success');
      if (okJobs.length === 0) {
        throw new Error(videoJobs[0]?.error_message || T.zipFailed);
      }

      // 3. Request archive via existing /bulk-zip endpoint
      if (mountedRef.current) {
        setZipState((s) => ({
          ...s,
          [group.key]: { phase: 'zipping', done: okJobs.length, total: videoJobs.length },
        }));
      }
      const zipRes = await fetch(`${CORE_API}/bulk-zip`, withAuth({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_id: batchId, organize_by_channel: false }),
      }));
      const zipData = await zipRes.json().catch(() => ({}));
      if (!zipRes.ok) {
        throw new Error(errText(zipData));
      }

      // 4. Poll batch_zip job until ready, then download as <tên kênh>_<yyyy-mm-dd>.zip
      let zipFileId = null;
      for (let attempt = 0; attempt < 120; attempt += 1) {
        if (!mountedRef.current) return;
        await sleep(2000);
        const jobsRes = await fetch(`${CORE_API}/jobs/${batchId}`, withAuth());
        if (!jobsRes.ok) continue;
        const jobsData = await jobsRes.json().catch(() => ({}));
        const zipJob = (jobsData.jobs || []).find((j) => j.original_url === 'batch_zip');
        if (!zipJob) continue;
        if (zipJob.status === 'failed') {
          throw new Error(zipJob.error_message || T.zipFailed);
        }
        if (zipJob.status === 'success') {
          zipFileId = toFileId(zipJob.direct_file_id || zipJob.direct_mp4_url || localFileId(zipJob));
          break;
        }
      }

      if (!zipFileId) {
        throw new Error(T.zipFailed);
      }

      const zipFilename = buildChannelZipFilename(group.channel, new Date());
      const downloadHref = localDownloadUrl(API_BASE, zipFileId, zipFilename);
      const a = document.createElement('a');
      a.href = downloadHref;
      a.setAttribute('download', zipFilename);
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);

      const unreadIds = group.items.filter((x) => !x.read).map((x) => x.id);
      if (unreadIds.length > 0 && inbox.ready) {
        const unreadSet = new Set(unreadIds);
        markRead(
          { item_ids: unreadIds },
          {
            ...inbox,
            items: inbox.items.map((x) => (unreadSet.has(x.id) ? { ...x, read: true } : x)),
            unread: Math.max(0, inbox.unread - unreadIds.length),
          },
        );
      }
      if (mountedRef.current) {
        setMsg(T.zipReady(zipFilename));
      }
    } catch (e) {
      if (mountedRef.current) {
        setErr(e?.message || T.zipFailed);
      }
    } finally {
      if (mountedRef.current) {
        setZipState((s) => {
          const next = { ...s };
          delete next[group.key];
          return next;
        });
      }
    }
  }

  const canAdd = status.enabled && status.used < status.limits.max_sources;
  const channelGroups = groupWatchItemsByChannel(inbox.items);

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

      {/* Windows desktop auto-download hint (no web auto-download) */}
      <div className="mb-4 px-4 py-3 rounded-xl border border-accent/25 bg-accent-soft/40 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
        <span className="flex items-center gap-2 text-fg-2">
          <Monitor className="w-4 h-4 text-accent-text shrink-0" />
          <span>{T.desktopHintText}</span>
        </span>
        <a href={lp('/download')}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent text-accent-fg font-bold hover:opacity-90 shrink-0 self-start sm:self-auto">
          <Download className="w-3.5 h-3.5" /> {T.desktopHintCta}
        </a>
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
        <span>{T.every(Math.max(1, Math.round(status.limits.min_interval_sec / 3600)))}</span>
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
            const modeLabel = s.mode === 'notify_only'
              ? T.modeNotify
              : s.mode === 'auto_download'
                ? T.modeAuto
                : T.modeTap;
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
                  <span>{modeLabel}</span>
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

        {channelGroups.length === 0 ? (
          <p className="text-sm text-fg-muted px-1 py-4">{T.inboxEmpty}</p>
        ) : (
          <div className="flex flex-col gap-4">
            {channelGroups.map((group) => {
              const z = zipState[group.key];
              return (
                <div key={group.key} className="rounded-2xl border border-line bg-surface p-3.5 flex flex-col gap-2.5">
                  <div className="flex items-center justify-between gap-2 flex-wrap border-b border-line/60 pb-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="font-bold text-sm text-fg truncate">{group.channel}</span>
                      {group.platform && (
                        <span className="text-[11px] px-2 py-0.5 rounded bg-surface-2 text-fg-muted uppercase shrink-0">
                          {group.platform}
                        </span>
                      )}
                      <span className="text-[11px] text-fg-muted shrink-0">
                        · {T.newVideosGroup} ({group.items.length})
                      </span>
                    </div>

                    {bulkZipAllowed && group.items.length > 0 && (
                      <button
                        type="button"
                        disabled={Boolean(z)}
                        onClick={() => downloadChannelZip(group)}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-accent text-accent-fg text-xs font-bold hover:opacity-90 cursor-pointer disabled:opacity-60 shrink-0"
                      >
                        {z ? (
                          <>
                            <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                            {z.phase === 'zipping' ? T.zipPacking : T.zipPreparing(z.done, z.total)}
                          </>
                        ) : (
                          <>
                            <FileDown className="w-3.5 h-3.5" />
                            {T.downloadAllZip(group.items.length)}
                          </>
                        )}
                      </button>
                    )}
                  </div>

                  <div className="flex flex-col gap-2">
                    {group.items.map((it) => {
                      const pubText = fmt(it.published_at) || T.unknownTime;
                      const discText = fmt(it.discovered_at) || T.unknownTime;
                      return (
                        <div key={it.id} className="flex items-center gap-3 px-3 py-2 rounded-xl border border-line bg-surface-2/40 min-w-0">
                          <span title={it.read ? undefined : T.unreadDot} aria-label={it.read ? undefined : T.unreadDot}
                            className={`w-2 h-2 rounded-full shrink-0 ${it.read ? 'bg-transparent' : 'bg-accent'}`} />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-1.5 min-w-0">
                              <span className={`text-[10px] px-1.5 py-0.5 rounded font-bold shrink-0 ${it.read ? 'bg-surface-2 text-fg-muted' : 'bg-accent-soft text-accent-text'}`}>
                                {T.newBadge}
                              </span>
                              <a href={it.url} target="_blank" rel="noreferrer noopener" onClick={() => markOne(it)}
                                className={`block text-sm text-fg truncate hover:underline ${it.read ? '' : 'font-bold'}`}>
                                {it.title || it.url}
                              </a>
                            </div>
                            <p className="text-[11px] text-fg-muted truncate mt-0.5">
                              {T.publishedAt(pubText)} · {T.detectedAt(discText)}
                            </p>
                          </div>
                          <a href={lp(`/share-target?url=${encodeURIComponent(it.url)}`)} onClick={() => markOne(it)}
                            className="flex items-center gap-1 px-3 py-1.5 rounded-lg border border-accent/30 text-accent-text text-xs font-bold hover:bg-accent-soft shrink-0">
                            <Download className="w-3.5 h-3.5" /> {T.download}
                          </a>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
