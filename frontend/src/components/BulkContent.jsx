import { useState, useEffect, useCallback, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import i18n, { useLocalePath } from '../i18n';
import { trackEvent, EVENT } from '../utils/trackEvent';
import { useAuth } from '../context/AuthContext';
import { localAnyId, localMp3Id, toFileId } from '../lib/localFile';
import {
  Layers,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  Download,
  ToggleLeft,
  ToggleRight,
  Tv,
  FileDown,
  ExternalLink,
  Table as TableIcon,
  Music,
  Video,
  Crown,
  ClipboardPaste,
  CheckSquare,
  X,
  Search,
  Radio,
  RefreshCw,
  AlertTriangle,
  Sparkles,
  FolderOpen,
  Link2,
  Pencil,
  Eye,
  OctagonX,
  Puzzle,
} from 'lucide-react';

import UpgradeModal from './UpgradeModal';
import QuickGuideSection from './QuickGuideSection';

// The server's own sentence for a refused bulk request, whatever its shape:
// a string detail (cookie_required…), a make_error object (daily allowance,
// tier batch size…), slowapi's {"error": …} on 429, FastAPI's 422 list.
// Every refusal used to fall through to "Failed to submit bulk jobs" unless
// it was exactly quota_exceeded_daily, hiding why (20/10).
// wording: BA review
// Not a component: uses the i18n instance directly (language = current page language).
const tb = (key, opts) => i18n.t(key, { ns: 'bulk', ...opts });

export function bulkRefusalText(status, data) {
  const d = data && data.detail;
  if (typeof d === 'string' && d.trim()) return d;
  if (d && typeof d === 'object' && !Array.isArray(d)) {
    const m = d.message || d.user_message;
    if (m) return d.suggested_action && !d.message ? `${m} ${d.suggested_action}` : m;
  }
  if (status === 429) return tb('refusal.tooFast');
  if (status === 422) return tb('refusal.invalid');
  if (data && typeof data.message === 'string' && data.message) return data.message;
  return tb('refusal.generic', { status });
}

// Shown under a Douyin channel row when the server listed only the newest
// videos and Douyin has more (channel_hint from GET /jobs/{batch_id}).
// wording: BA review
function DouyinExtensionHint() {
  const { t } = useTranslation('bulk');
  const lp = useLocalePath();
  const openExtensionPage = (e) => {
    e.preventDefault();
    window.history.pushState({}, '', '/extension');
    window.dispatchEvent(new PopStateEvent('popstate'));
  };
  return (
    <div className="mt-2 p-2.5 rounded-control border border-line bg-surface text-[11px] text-fg-2 whitespace-normal">
      <p className="font-semibold text-fg mb-1">{t('douyinHint.title')}</p>
      <p className="mb-2">
        {t('douyinHint.body')}
      </p>
      <a
        href={lp('/extension')}
        onClick={openExtensionPage}
        className="inline-flex items-center gap-1.5 px-2.5 py-1 font-medium text-accent-text border border-line hover:border-line-strong rounded-control transition-colors"
      >
        <Puzzle className="w-3.5 h-3.5" />
        {t('douyinHint.install')}
      </a>
    </div>
  );
}
import ContainerPreviewPanel from './ContainerPreviewPanel';
import ChannelPreview, { isDouyinProfile, isFacebookChannel, isPreviewableChannel } from './ChannelPreview';
import { useContainerDiscover } from '../hooks/useContainerDiscover';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;
// The server lists the newest ~41 Douyin videos by itself; more needs the extension.
const DOUYIN_SERVER_PAGE = 40;

async function safeJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    if (response.status === 502 || response.status === 503 || response.status === 504) {
      throw new Error(tb('safeJson.maintenance'));
    }
    if (response.status === 429) {
      throw new Error(tb('safeJson.rateLimited'));
    }
    throw new Error(tb('safeJson.server', { status: response.status }));
  }
}

// ── Countdown Timer + Recovery Action Cell ─────────────────────────
const JobActionCell = ({ job, onDownload, onRefresh }) => {
  const { t } = useTranslation('bulk');
  const [expired, setExpired] = useState(false);
  const [timeLeft, setTimeLeft] = useState(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    if (job.status !== 'success') return;

    // Use link_expires_at from API if available; fall back to created_at + 60min
    const expirySource = job.link_expires_at
      ? new Date(job.link_expires_at).getTime()
      : job.created_at
        ? new Date(job.created_at).getTime() + 60 * 60 * 1000
        : null;

    if (!expirySource) return;

    const tick = () => {
      const diff = expirySource - Date.now();
      if (diff <= 0) { setExpired(true); setTimeLeft(0); }
      else            { setTimeLeft(diff); }
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [job]);

  if (job.status === 'failed') {
    const recoverable = job.recovery_state === 'recoverable';
    if (recoverable && onRefresh) {
      return (
        <button
          onClick={async () => { setRefreshing(true); await onRefresh(job.id); setRefreshing(false); }}
          disabled={refreshing}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-warning/10 text-warning hover:bg-warning/20 transition-colors cursor-pointer disabled:opacity-50"
        >
          {refreshing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          {t('retry')}
        </button>
      );
    }
    return <span className="text-xs text-fg-muted">-</span>;
  }

  if (job.status !== 'success') return <span className="text-xs text-fg-muted">{t('waiting')}</span>;

  const hasLink = job.direct_mp4_url || localAnyId(job) || job.direct_file_id;
  if (!hasLink) return <span className="text-xs text-fg-muted">-</span>;

  // Expired local file: show "Gia hạn link" CTA
  if (expired || job.recovery_state === 'expired_refreshable') {
    return (
      <div className="flex flex-col items-end gap-1">
        {onRefresh ? (
          <button
            onClick={async () => { setRefreshing(true); await onRefresh(job.id); setRefreshing(false); }}
            disabled={refreshing}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-error/10 text-error hover:bg-error/20 border border-error/20 transition-colors cursor-pointer disabled:opacity-50"
          >
            {refreshing ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            {refreshing ? t('renewing') : t('linkExpiredRenew')}
          </button>
        ) : (
          <span className="text-[11px] text-error italic border border-error/20 bg-error/5 px-2 py-1 rounded">
            {t('linkExpired')}
          </span>
        )}
      </div>
    );
  }

  const m = Math.floor(timeLeft / 60000);
  const s = Math.floor((timeLeft % 60000) / 1000);

  return (
    <div className="flex flex-col items-end gap-1.5">
      <button
        onClick={() => onDownload(localAnyId(job) || job.direct_file_id || job.direct_mp4_url, job.slugified_name)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg bg-success/10 text-success hover:bg-success/20 transition-colors cursor-pointer"
      >
        <Download className="w-3 h-3" />
        Download
      </button>
      {timeLeft !== null && timeLeft > 0 && (
        <span className="text-[10px] text-error flex items-center gap-1 font-mono bg-error/10 px-1.5 py-0.5 rounded border border-error/20">
          <Clock className="w-2.5 h-2.5" />
          {m}:{s < 10 ? '0' + s : s}
        </span>
      )}
    </div>
  );
};

export default function BulkContent() {
  const { t } = useTranslation('bulk');
  const { withAuth, session } = useAuth();
  const [urls, setUrls] = useState('');
  const [batchId, setBatchId] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('batch') || null;
  });
  const [jobs, setJobs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [channelMode, setChannelMode] = useState(false);

  // ── Container Browse State (Phase 25) ─────────────────────────────
  const [containerMode, setContainerMode]           = useState(false);
  const [containerPanelOpen, setContainerPanelOpen] = useState(false);
  const [containerUrl, setContainerUrl]             = useState('');
  const {
    container,
    isLoading: containerLoading,
    error: containerError,
    discover: discoverContainer,
    refresh: refreshContainer,
  } = useContainerDiscover(null, { autoStart: false });

  // ── Keyword Search State ───────────────────────────────────────────
  const [searchMode, setSearchMode]       = useState(false);
  const [kw, setKw]                       = useState('');
  const [kwPlatform, setKwPlatform]       = useState('youtube');
  const [kwCount, setKwCount]             = useState(10);
  const [kwSort, setKwSort]               = useState('relevance');
  const [kwMinDur, setKwMinDur]           = useState('');
  const [kwMaxDur, setKwMaxDur]           = useState('');
  const [kwLoading, setKwLoading]         = useState(false);
  const [kwResults, setKwResults]         = useState([]);
  const [kwError, setKwError]             = useState('');
  const [kwSelected, setKwSelected]       = useState(new Set());
  const [kwCached, setKwCached]           = useState(false);

  // ── Rename & ZIP State ────────────────────────────────────────────
  const [showRename, setShowRename]       = useState(false);
  const [renamePattern, setRenamePattern] = useState('{platform}_{creator}_{date}_{title}');
  const [renameLoading, setRenameLoading] = useState(false);
  const [renameResult, setRenameResult]   = useState(null);
  const [renameError, setRenameError]     = useState('');

  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [discoverElapsed, setDiscoverElapsed] = useState(0);
  const discoverTimerRef = useRef(null);
  const [isZipping, setIsZipping] = useState(false);
  const [organizeByChannel, setOrganizeByChannel] = useState(false);

  // New States
  const [maxVideos, setMaxVideos] = useState(20);
  const [customMaxVideos, setCustomMaxVideos] = useState('');
  const [autoDownload, setAutoDownload] = useState(false);
  const [quality, setQuality] = useState('video');
  const [removeWatermark, setRemoveWatermark] = useState(true);
  const [quotaInfo, setQuotaInfo] = useState(null);
  // 21/10: channel listed for choosing — {url, want?, tab?, n}
  const [preview, setPreview] = useState(null);
  const previewUrl = preview && preview.url;
  const [selectedJobIds, setSelectedJobIds] = useState(new Set());
  const autoDownloadedRefs = useRef(new Set());
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const fileInputRef = useRef(null);
  // 21/10: the server can read a whole Douyin channel itself (admin cookie): no extension tab
  const [douyinWhole, setDouyinWhole] = useState(false);

  useEffect(() => {
    fetch(`${API}/channel/preview/caps`)
      .then((res) => (res.ok ? res.json() : null))
      .then((d) => setDouyinWhole(!!(d && d.douyinWhole)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch(`${API}/quota`)
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          setQuotaInfo(data.quota);
        }
      })
      .catch(err => console.error(err));
  }, []);

  // ── URL Cleaner ───────────────────────────────────────────────────
  // Strips tracking query params from Douyin & TikTok profile/user URLs
  // while keeping other URLs (YouTube watch?v=, playlists, etc.) intact.
  const cleanProfileUrls = useCallback((text) => {
    return text
      .split('\n')
      .map((line) => {
        const trimmed = line.trim();
        if (!trimmed) return line;

        try {
          const url = new URL(trimmed);
          const host = url.hostname.replace('www.', '');

          // Douyin user profiles: douyin.com/user/XXXXX?junk → strip query
          if (host === 'douyin.com' && url.pathname.startsWith('/user/')) {
            return url.origin + url.pathname;
          }

          // TikTok user profiles: tiktok.com/@username?junk → strip query
          // But keep /video/ URLs intact (they rarely have harmful params)
          if (host === 'tiktok.com' && url.pathname.match(/^\/@[^/]+$/) && url.search) {
            return url.origin + url.pathname;
          }
        } catch {
          // Not a valid URL, return as-is
        }
        return line;
      })
      .join('\n');
  }, []);

  const handleUrlsChange = useCallback((e) => {
    setUrls(cleanProfileUrls(e.target.value));
  }, [cleanProfileUrls]);

  const handleFileImport = useCallback((e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const text = ev.target.result || '';
      // Extract all URLs from file (one per line, or comma-separated in CSV)
      const lines = text.split(/[\n,;]+/).map(l => l.trim()).filter(l => l.startsWith('http'));
      const cleaned = cleanProfileUrls(lines.join('\n'));
      setUrls(prev => prev ? prev + '\n' + cleaned : cleaned);
    };
    reader.readAsText(file);
    e.target.value = '';
  }, [cleanProfileUrls]);

  const handleUrlsPaste = useCallback((e) => {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text');
    const cleaned = cleanProfileUrls(pasted);

    // Insert at cursor position (or replace selection)
    const textarea = e.target;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const before = urls.substring(0, start);
    const after = urls.substring(end);
    setUrls(before + cleaned + after);
  }, [urls, cleanProfileUrls]);

  // ── Submit ─────────────────────────────────────────────────────────
  const handleSubmit = async () => {
    if (!urls.trim()) return;

    let urlList = urls
      .split('\n')
      .map((u) => u.trim())
      .filter((u) => u !== '');
    if (urlList.length === 0) return;

    // 21/10: one Chinese channel link → list it to choose from (the old path
    // queued the first 20 videos only, owner's test: "21 video"). Bulk URL
    // preselects the whole channel, Channel the N newest; both ask before
    // sending. Signed in, a Douyin profile is read whole by the server when
    // it has the admin cookie; otherwise by the extension: the tab opens here,
    // inside the click, or the popup blocker stops it.
    if (urlList.length === 1 && isPreviewableChannel(urlList[0])) {
      const want = channelMode ? (parseInt(customMaxVideos) || parseInt(maxVideos) || 20) : 'all';
      // Facebook (22/10): only the extension can list it — always the relay tab when signed in.
      const fb = !!session?.access_token && isFacebookChannel(urlList[0]);
      const whole = !!session?.access_token && isDouyinProfile(urlList[0]) && (want === 'all' || want > DOUYIN_SERVER_PAGE);
      const tab = fb || (whole && !douyinWhole) ? window.open('about:blank', '_blank') : null;
      setPreview((p) => ({ url: urlList[0], want, tab, n: ((p && p.n) || 0) + 1 }));
      return;
    }

    // Phase 21: silent deduplication before submit
    const uniqueUrlList = [...new Set(urlList)];
    const dupCount = urlList.length - uniqueUrlList.length;
    if (dupCount > 0) urlList = uniqueUrlList;

    await runBulk(urlList, channelMode);
  };

  /** Sends a list to /bulk-download (a new batch); true when the server accepted it. */
  const runBulk = async (urlList, asChannel) => {
    setIsSubmitting(true);
    setJobs([]);
    setSelectedJobIds(new Set());
    setSummary(null);
    setBatchId(null);

    try {
      // Intercept Spotify URLs to expand them into ytsearch queries
      let expandedUrls = [];
      for (const u of urlList) {
        if (u.includes('open.spotify.com')) {
          try {
            const spRes = await fetch(`${API}/fetch-spotify`, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ url: u })
            });
            const spData = await safeJson(spRes);
            if (spData.success && spData.tracks) {
              spData.tracks.forEach(trk => expandedUrls.push(trk.query));
            } else {
              expandedUrls.push(u); // fallback
            }
          } catch(e) {
            console.error('Spotify expansion failed', e);
            expandedUrls.push(u);
          }
        } else {
          expandedUrls.push(u);
        }
      }
      urlList = expandedUrls;

      trackEvent(EVENT.BULK_STARTED, {
        url_count: urlList.length,
        channel_mode: asChannel,
        quality,
      });
      const res = await fetch(`${API}/bulk-download`,
        withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          urls: urlList,
          channel_mode: asChannel,
          max_videos: parseInt(customMaxVideos) || parseInt(maxVideos) || 20,
          min_views: 0,
          quality: quality,
          remove_watermark: removeWatermark,
        }) })
      );
      const data = await safeJson(res);

      if (!res.ok) {
        if (res.status === 403 && data.detail === 'QUOTA_EXCEEDED') {
          setShowUpgradeModal(true);
          return false;
        }
        throw new Error(bulkRefusalText(res.status, data));
      }
      
      if (data.batch_id) {
        setBatchId(data.batch_id);
      }
      return true;
    } catch (err) {
      console.error(err);
      // wording: BA review
      const network = err instanceof TypeError || /failed to fetch|networkerror|load failed/i.test(err?.message || '');
      alert(network
        ? t('errNetwork')
        : (err?.message || t('errSubmit')));
      return false;
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Push: ask permission after first successful batch ──────────────
  const requestPushSubscription = async () => {
    try {
      if (!('Notification' in window) || !('serviceWorker' in navigator) || !('PushManager' in window)) return;
      // Subscriptions belong to an account; the backend answers 401 to guests,
      // so do not ask a guest for permission it cannot use.
      const token = session?.access_token;
      if (!token) return;
      if (Notification.permission === 'denied') return;
      // Only once per session, after a batch completes (not on every poll tick)
      if (sessionStorage.getItem('push-asked')) return;
      sessionStorage.setItem('push-asked', '1');

      // The server publishes its VAPID public key ('' = push not configured)
      // so a key change needs no frontend rebuild.
      const vapidRes = await fetch(`${API}/push/vapid-key`);
      if (!vapidRes.ok) return;
      const { public_key: rawKey } = await vapidRes.json();
      if (!rawKey) return;

      if (Notification.permission !== 'granted') {
        // Short delay so user sees the batch completion first
        await new Promise(r => setTimeout(r, 3000));
        const permission = await Notification.requestPermission();
        if (permission !== 'granted') return;
      }

      const reg = await navigator.serviceWorker.ready;

      // Convert VAPID public key from base64url to Uint8Array
      const padding = '='.repeat((4 - (rawKey.length % 4)) % 4);
      const base64 = (rawKey + padding).replace(/-/g, '+').replace(/_/g, '/');
      const rawData = atob(base64);
      const appServerKey = new Uint8Array(rawData.length);
      for (let i = 0; i < rawData.length; i++) appServerKey[i] = rawData.charCodeAt(i);

      // Permission granted earlier but the subscription was never stored
      // (the old backend rejected it), or it was made with a previous key:
      // reuse a matching subscription, replace a stale one.
      let sub = await reg.pushManager.getSubscription();
      if (sub) {
        const oldKey = sub.options?.applicationServerKey
          ? new Uint8Array(sub.options.applicationServerKey) : null;
        const sameKey = oldKey && oldKey.length === appServerKey.length
          && oldKey.every((b, i) => b === appServerKey[i]);
        if (!sameKey) {
          await sub.unsubscribe();
          sub = null;
        }
      }
      if (!sub) {
        sub = await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: appServerKey,
        });
      }

      // Send subscription to backend
      await fetch(`${API}/push/subscribe`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(sub.toJSON()),
      });
      console.log('[Push] Subscribed to push notifications');
    } catch (err) {
      console.warn('[Push] Subscription failed:', err);
    }
  };

  // ── Smart Download (Proxy/Local) ───────────────────────────────────
  const handleSmartDownload = useCallback((url, slug) => {
    try {
      let downloadUrl = url;
      // If it's already a full backend endpoint URL
      if (url.includes('proxy-download') || url.includes('download-local')) {
        downloadUrl = url;
      } else if (url.includes(':/') || url.startsWith('http')) {
        // External URL -> use proxy
        downloadUrl = `${API}/proxy-download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(slug || 'video')}`;
      } else {
        // Assume local path, extract extension from the path (e.g., .mp4 or .mp3)
        const fileExt = url.split('.').pop() || 'mp4';
        // A file id, or a legacy server path (old rows) reduced to its id.
        downloadUrl = `${API}/download-local?file=${encodeURIComponent(toFileId(url))}&filename=${encodeURIComponent(slug || 'video')}.${fileExt}`;
      }
      
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.setAttribute('download', '');
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (err) {
      console.warn('Smart download failed, falling back to new tab:', err);
      // Fallback
      window.open(url, '_blank');
    }
  }, []);

  // ── Polling (adaptive interval) ────────────────────────────────────
  // Use recursive setTimeout so each tick can pick the next delay based on
  // how many jobs are still active.  Speeds up during heavy processing,
  // slows down when the batch is nearly done, and stops on completion.
  useEffect(() => {
    let timeoutId = null;
    let cancelled = false;

    const _nextDelay = (jobs) => {
      if (!jobs || jobs.length === 0) return 3000;
      const active = jobs.filter(j => !['success', 'failed', 'cancelled'].includes(j.status)).length;
      const ratio = active / jobs.length;
      if (ratio > 0.5) return 2000;   // majority still running → fast
      if (ratio > 0.1) return 3000;   // some still running → normal
      return 5000;                     // nearly done → back off
    };

    if (batchId) {
      const fetchJobs = async () => {
        if (cancelled) return;
        try {
          const res = await fetch(`${API}/jobs/${batchId}`);
          const data = await safeJson(res);
          if (data.jobs) {
            setJobs(data.jobs);
            setSummary(data.summary || null);

            // Auto-Download trigger — staggered like handleDownloadAll (500ms
            // apart). Firing every just-finished download in the same tick
            // with no delay hits Chrome/Firefox's automatic-download block,
            // so most files silently never save even though the UI says
            // "success".
            if (autoDownload) {
              let _staggerIndex = 0;
              data.jobs.forEach(job => {
                const dlPath = localAnyId(job) || job.direct_file_id || job.direct_mp4_url;
                if (job.status === 'success' && dlPath && !autoDownloadedRefs.current.has(job.id)) {
                  autoDownloadedRefs.current.add(job.id);
                  const delay = _staggerIndex * 500;
                  _staggerIndex += 1;
                  setTimeout(() => handleSmartDownload(dlPath, job.slugified_name), delay);
                }
              });
            }

            // Auto-select successful jobs if not already selected
            setSelectedJobIds(prev => {
              const next = new Set(prev);
              data.jobs.forEach(j => {
                if (j.status === 'success' && j.original_url !== 'batch_zip' && !autoDownloadedRefs.current.has(j.id + '_selected')) {
                  next.add(j.id);
                  autoDownloadedRefs.current.add(j.id + '_selected');
                }
              });
              return next;
            });

            const allDone = data.jobs.every(
              (j) => ['success', 'failed', 'cancelled'].includes(j.status)
            );
            if (allDone && data.jobs.length > 0) {
              requestPushSubscription();
              return;  // all terminal — stop polling
            }

            if (!cancelled) {
              timeoutId = setTimeout(fetchJobs, _nextDelay(data.jobs));
            }
          }
        } catch (e) {
          console.error('Polling error', e);
          if (!cancelled) timeoutId = setTimeout(fetchJobs, 5000);
        }
      };

      fetchJobs();
    }

    return () => {
      cancelled = true;
      clearTimeout(timeoutId);
    };
  }, [batchId, autoDownload, handleSmartDownload, refreshTrigger]);

  // ── Status Badge ───────────────────────────────────────────────────
  const getStatusBadge = (status, job = {}) => {
    const stage = job.job_stage;
    const recoveryState = job.recovery_state;

    if (status === 'pending') {
      const label = stage === 'queued' && job.recovery_attempts > 0 ? t('status.recovering') : t('status.pending');
      return (
        <span className="inline-flex items-center gap-1 text-fg-muted bg-surface/50 px-2.5 py-1 rounded-lg text-xs font-medium">
          <Clock className="w-3 h-3" /> {label}
        </span>
      );
    }
    if (status === 'processing') {
      const stageLabel = stage === 'extracting' ? t('status.extracting') : stage === 'downloading' ? t('status.downloading') : stage === 'zipping' ? t('status.zipping') : t('status.processing');
      const isStuck = recoveryState === 'stuck';
      return (
        <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium ${isStuck ? 'text-error bg-error/10' : 'text-warning bg-warning/10'}`}>
          {isStuck ? <AlertTriangle className="w-3 h-3" /> : <Loader2 className="w-3 h-3 animate-spin" />}
          {isStuck ? t('status.stuck') : stageLabel}
        </span>
      );
    }
    if (status === 'success') {
      if (recoveryState === 'expired_refreshable') {
        return (
          <span className="inline-flex items-center gap-1 text-error bg-error/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Clock className="w-3 h-3" /> {t('status.expired')}
          </span>
        );
      }
      return (
        <span className="inline-flex items-center gap-1 text-success bg-success/10 px-2.5 py-1 rounded-lg text-xs font-medium">
          <CheckCircle2 className="w-3 h-3" /> {t('status.success')}
        </span>
      );
    }
    if (status === 'failed') {
      const isAbandoned = stage === 'abandoned' || recoveryState === 'abandoned';
      return (
        <span className="inline-flex items-center gap-1 text-error bg-error/10 px-2.5 py-1 rounded-lg text-xs font-medium">
          <XCircle className="w-3 h-3" /> {isAbandoned ? t('status.stopped') : t('status.failed')}
        </span>
      );
    }
    if (status === 'cancelled') {
      return (
        <span className="inline-flex items-center gap-1 text-fg-muted bg-surface px-2.5 py-1 rounded-lg text-xs font-medium">
          <OctagonX className="w-3 h-3" /> {t('status.stopped')}
        </span>
      );
    }
    return status;
  };

  // Exclude the batch_zip placeholder and the channel-discovery placeholder job
  // (discovery job has status=processing and is the ONLY job → replaced by discovery UI)
  const allVideoJobs = jobs.filter(j => j.original_url !== 'batch_zip');
  const displayJobs = allVideoJobs.filter(j => !(j.status === 'processing' && allVideoJobs.length === 1));
  const zipJob = jobs.find(j => j.original_url === 'batch_zip');

  // Channel discovery phase: only 1 job exists (the scraping placeholder) and it's still processing
  const discoveryJob = allVideoJobs.length === 1 && allVideoJobs[0].status === 'processing'
    ? allVideoJobs[0]
    : null;
  const isDiscovering = !!discoveryJob || (batchId && allVideoJobs.length === 0);

  // Start/stop local elapsed timer when discovery phase changes
  useEffect(() => {
    if (isDiscovering && batchId) {
      if (!discoverTimerRef.current) {
        setDiscoverElapsed(0);
        discoverTimerRef.current = setInterval(() => setDiscoverElapsed(s => s + 1), 1000);
      }
    } else {
      if (discoverTimerRef.current) {
        clearInterval(discoverTimerRef.current);
        discoverTimerRef.current = null;
      }
    }
    return () => {};
  }, [isDiscovering, batchId]);

  const successJobs = displayJobs.filter(j => j.status === 'success' && (j.direct_mp4_url || localAnyId(j) || j.direct_file_id));
  
  // Progress computation (excluding zip job)
  let totalData = summary ? { ...summary } : { total: 0, success: 0, failed: 0, pending: 0, processing: 0 };
  if (zipJob && summary) {
      totalData.total -= 1;
      totalData[zipJob.status] -= 1;
  }
  
  const progressPct =
    totalData.total > 0
      ? Math.round(((totalData.success + totalData.failed + (totalData.cancelled || 0)) / totalData.total) * 100)
      : 0;

  // ── Handlers for bulk actions ──────────────────────────────────────────

  // "Dừng": jobs not started yet are cancelled; ones already downloading finish.
  const [isStopping, setIsStopping] = useState(false);
  const handleStopBatch = async () => {
    if (!batchId || isStopping) return;
    setIsStopping(true);
    try {
      const res = await fetch(`${API}/jobs/${batchId}/cancel`, { method: 'POST' });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(data.detail || 'Stop failed');
      setRefreshTrigger(prev => prev + 1);
    } catch (e) {
      console.error(e);
    } finally {
      setIsStopping(false);
    }
  };
  const canStop = !!batchId && jobs.some(j => j.original_url !== 'batch_zip' && ['pending', 'queued', 'retrying'].includes(j.status));
  const stopButton = (label) => (
    <button
      onClick={handleStopBatch}
      disabled={isStopping}
      className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold bg-surface text-fg-2 border border-line hover:bg-error/10 hover:text-error hover:border-error/30 transition-all disabled:opacity-50"
    >
      {isStopping ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <OctagonX className="w-3.5 h-3.5" />}
      {isStopping ? t('stopping') : label}
    </button>
  );

  const [isRetrying, setIsRetrying] = useState(false);
  const handleRetryFailed = async () => {
    if (!batchId) return;
    setIsRetrying(true);
    try {
      const res = await fetch(`${API}/retry-failed/${batchId}`, { method: 'POST' });
      const data = await safeJson(res);
      if (!res.ok) throw new Error(data.detail || 'Retry failed');
      setRefreshTrigger(prev => prev + 1);
    } catch (e) {
      console.error(e);
    } finally {
      setIsRetrying(false);
    }
  };

  const handleRefreshJob = async (jobId) => {
    try {
      const res = await fetch(`${API}/jobs/${jobId}/refresh`, withAuth({ method: 'POST' }));
      if (res.ok) setRefreshTrigger(prev => prev + 1);
    } catch (e) {
      console.error('Refresh failed:', e);
    }
  };

  const handleCreateZip = async () => {
    if (!batchId) return;
    setIsZipping(true);
    try {
      const res = await fetch(`${API}/bulk-zip`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_id: batchId, organize_by_channel: organizeByChannel })
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Server error: ${res.status}`);
      }
      setRefreshTrigger(prev => prev + 1);
    } catch (e) {
      console.error(e);
      alert(t('zipFailed', { message: e.message }));
    } finally {
      setIsZipping(false);
    }
  };
  const handleDownloadAll = () => {
    const toDownload = successJobs.filter(j => selectedJobIds.has(j.id));
    toDownload.forEach((job, index) => {
      setTimeout(() => {
        handleSmartDownload(localAnyId(job) || job.direct_file_id || job.direct_mp4_url, job.slugified_name);
      }, index * 500);
    });
  };

  const handleExportCSV = () => {
    const csvContent = "data:text/csv;charset=utf-8," 
      + "Title,Original URL,Direct MP4 URL\n"
      + successJobs.map(j => `"${(j.title || j.slugified_name || 'video').replace(/"/g, '""')}","${j.original_url}","${j.direct_mp4_url}"`).join("\n");
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement("a");
    link.setAttribute("href", encodedUri);
    link.setAttribute("download", `export_${new Date().getTime()}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleCopyAll = () => {
    const links = successJobs.map(j => j.direct_mp4_url).join('\n');
    navigator.clipboard.writeText(links).then(() => {
      alert('All links copied to clipboard!');
    });
  };

  const handleOpenAll = () => {
    successJobs.forEach(job => {
      window.open(job.direct_mp4_url, '_blank');
    });
  };

  // ── Keyword Search Handler ─────────────────────────────────────────
  const handleKeywordSearch = async () => {
    if (kw.trim().length < 3) { setKwError(t('kw.tooShort')); return; }
    setKwLoading(true);
    setKwError('');
    setKwResults([]);
    setKwSelected(new Set());
    try {
      const res = await fetch(`${API}/search-videos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          keyword:      kw.trim(),
          platform:     kwPlatform,
          count:        kwCount,
          sort:         kwSort,
          min_duration: kwMinDur ? parseInt(kwMinDur) : null,
          max_duration: kwMaxDur ? parseInt(kwMaxDur) : null,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        const code = data?.detail?.error_code || '';
        if (code === 'search_rate_limited') setKwError(t('kw.rateLimited'));
        else if (code === 'search_no_results') setKwError(t('kw.noVideosFor', { kw }));
        else setKwError(data?.detail?.message || data?.detail || t('kw.failed'));
        return;
      }
      setKwResults(data.results || []);
      setKwCached(data.cached || false);
      if ((data.results || []).length === 0) setKwError(t('kw.noResults'));
    } catch (e) {
      setKwError(e.message || t('connError'));
    } finally {
      setKwLoading(false);
    }
  };

  const handleKwAddSelected = () => {
    const selectedUrls = kwResults
      .filter(r => kwSelected.has(r.url))
      .map(r => r.url)
      .join('\n');
    if (!selectedUrls) return;
    setSearchMode(false);
    setChannelMode(false);
    setUrls(prev => (prev ? prev + '\n' + selectedUrls : selectedUrls).trim());
  };

  // ── Rename & ZIP Handlers ──────────────────────────────────────────
  const TOKEN_CHIPS = [
    '{platform}', '{creator}', '{date}', '{download_date}',
    '{title}', '{index}', '{duration}', '{resolution}',
  ];

  const handleInsertToken = (token) => {
    setRenamePattern(p => p + token);
  };

  const handleRenameZip = async () => {
    if (!batchId) return;
    setRenameLoading(true);
    setRenameError('');
    setRenameResult(null);
    try {
      const res = await fetch(`${API}/bulk-zip-rename`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_id: batchId, pattern: renamePattern }),
      });
      const data = await res.json();
      if (!res.ok) { setRenameError(data?.detail || t('rename.failed')); return; }
      setRenameResult(data);
    } catch (e) {
      setRenameError(e.message || t('connError'));
    } finally {
      setRenameLoading(false);
    }
  };

  const renamePreview = successJobs.slice(0, 3).map((job, i) => {
    const dl_date = job.created_at ? job.created_at.slice(0, 10) : 'unknown';
    const sanitize = s => (s || '').replace(/[/\\:*?"<>|]/g, '').replace(/\s+/g, '_').slice(0, 80) || 'unknown';
    const ext = job.is_audio_only ? '.mp3' : '.mp4';
    let name = renamePattern
      .replace('{platform}', sanitize(job.platform || 'unknown'))
      .replace('{creator}', '?creator?')
      .replace('{date}', '?date?')
      .replace('{download_date}', dl_date)
      .replace('{title}', sanitize(job.title || 'video'))
      .replace('{index}', String(i + 1).padStart(3, '0'))
      .replace('{duration}', '?dur?')
      .replace('{resolution}', `${job.downloaded_height || 0}p`);
    return name.replace(/_+/g, '_').replace(/^_|_$/g, '') + ext;
  });

  // ── Container panel callbacks ──────────────────────────────────────
  const handleContainerQueued = useCallback((newBatchId) => {
    if (newBatchId) {
      setBatchId(newBatchId);
      setJobs([]);
      setSummary(null);
    }
    setContainerPanelOpen(false);
  }, []);

  return (
    <div className="space-y-8">
      <UpgradeModal isOpen={showUpgradeModal} onClose={() => setShowUpgradeModal(false)} />

      {/* ── Container Preview Panel ─────────────────────── */}
      {containerPanelOpen && (
        <ContainerPreviewPanel
          container={container}
          isLoading={containerLoading}
          error={containerError}
          onClose={() => setContainerPanelOpen(false)}
          onQueued={handleContainerQueued}
          onRefresh={refreshContainer}
        />
      )}
      {/* ── Header ────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <Layers className="w-5 h-5 text-accent-text" />
            <h2 className="text-2xl font-bold text-fg tracking-tight">
              {t('title')}
            </h2>
          </div>
          <p className="text-sm text-fg-muted ml-8">
            {t('subtitle')}
          </p>
        </div>
        {quotaInfo && ['pro', 'vip'].includes(quotaInfo.plan) && (
          <div className="flex items-center gap-2 px-4 py-2 bg-accent-soft border border-accent/30 rounded-xl shadow-lg animate-in fade-in zoom-in slide-in-from-right-8 duration-500">
            <Crown className="w-4 h-4 text-accent-text" />
            <span className="text-xs font-bold text-accent-text uppercase tracking-wider">{quotaInfo.plan} ACTIVE</span>
          </div>
        )}
      </div>

      {/* ── Input Area ────────────────────────────────── */}
      <div className="p-6 rounded-2xl bg-surface border border-line shadow-lg">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-4 gap-4">
          <div>
            <h3 className="text-base font-semibold text-fg">
              {t('pasteHere')}
            </h3>
            <p className="text-xs text-fg-muted mt-0.5">
              {t('pasteHint')}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Quality Selector */}
            <select
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              className="px-4 py-2 rounded-xl text-xs font-semibold border bg-surface border-line text-fg focus:outline-none focus:ring-2 focus:ring-primary/50"
            >
              <option value="video">MP4 Video</option>
              <option value="mp3_128">MP3 Audio (128kbps)</option>
              {quotaInfo && ['pro', 'vip'].includes(quotaInfo.plan) ? (
                 <option value="mp3_320">MP3 Audio (320kbps VIP)</option>
              ) : (
                 <option value="mp3_320" disabled>MP3 Audio (320kbps VIP) - PRO ONLY</option>
              )}
            </select>

            {/* Auto-Download Toggle */}
            <button
              onClick={() => setAutoDownload(!autoDownload)}
              className={`
                flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold
                border transition-all duration-200 cursor-pointer
                ${
                  autoDownload
                    ? 'bg-success/15 border-success/30 text-success'
                    : 'bg-surface border-line text-fg-muted hover:text-fg-2'
                }
              `}
            >
              {autoDownload ? (
                <ToggleRight className="w-4 h-4" />
              ) : (
                <ToggleLeft className="w-4 h-4" />
              )}
              {t('autoDownload')}
            </button>
            
            {/* Mode Tabs */}
            <div className="flex flex-col items-start gap-1">
              <div className="flex flex-wrap gap-1 p-1 bg-surface-2 rounded-control border border-line max-w-full">
                {[
                  { key: 'bulk',      label: 'Bulk URL',  icon: Link2,      active: !channelMode && !searchMode && !containerMode },
                  { key: 'channel',   label: 'Channel',   icon: Tv,         active: channelMode && !searchMode && !containerMode },
                  { key: 'search',    label: t('tabKeyword'),   icon: Search,     active: searchMode && !containerMode },
                  { key: 'container', label: 'Browse',    icon: FolderOpen, active: containerMode },
                ].map(tab => (
                  <button
                    key={tab.key}
                    type="button"
                    aria-pressed={tab.active}
                    onClick={() => {
                      setChannelMode(tab.key === 'channel');
                      setSearchMode(tab.key === 'search');
                      setContainerMode(tab.key === 'container');
                    }}
                    className={`inline-flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition-colors cursor-pointer ${
                      tab.active
                        ? 'bg-surface text-fg shadow-sm ring-1 ring-line'
                        : 'text-fg-2 hover:text-fg'
                    }`}
                  >
                    <tab.icon className="w-3.5 h-3.5" />
                    {tab.label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-fg-muted mt-1">
                {channelMode ? t('modeHint.channel')
                  : searchMode ? t('modeHint.search')
                  : containerMode ? t('modeHint.container')
                  : t('modeHint.bulk')}
              </p>
            </div>

            {/* Watermark Toggle — TikTok/Douyin platform-aware */}
            {(() => {
              const hasTikTok = urls.split('\n').some(u => {
                const l = u.toLowerCase();
                return l.includes('tiktok.com') || l.includes('vm.tiktok.com') ||
                  l.includes('douyin.com') || l.includes('v.douyin.com') || l.includes('iesdouyin.com');
              });
              const isOtherPlatform = urls.trim().length > 0 && !hasTikTok;
              return (
                <div className="flex flex-col items-start gap-1">
                  <button
                    onClick={() => !isOtherPlatform && setRemoveWatermark(!removeWatermark)}
                    disabled={isOtherPlatform}
                    className={`
                      flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold
                      border transition-all duration-200
                      ${isOtherPlatform
                        ? 'opacity-40 cursor-not-allowed bg-surface border-line text-fg-muted'
                        : removeWatermark && hasTikTok
                          ? 'bg-success-soft border-success/30 text-success cursor-pointer'
                          : 'bg-surface border-line text-fg-muted hover:text-fg-2 cursor-pointer'
                      }
                    `}
                  >
                    {removeWatermark && !isOtherPlatform ? (
                      <ToggleRight className="w-4 h-4" />
                    ) : (
                      <ToggleLeft className="w-4 h-4" />
                    )}
                    <Sparkles className="w-3.5 h-3.5" />
                    {t('removeWatermark')}
                    {hasTikTok && (
                      <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-success-soft text-success">TikTok/Douyin</span>
                    )}
                    {isOtherPlatform && (
                      <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-surface border border-line text-fg-muted">{t('tiktokOnly')}</span>
                    )}
                  </button>
                  <p className="text-xs text-fg-muted mt-1">
                    {isOtherPlatform ? t('watermarkOnlyTikTok') : t('watermarkHint')}
                  </p>
                </div>
              );
            })()}
          </div>
        </div>

        {/* 21/10: one Chinese channel link (Bulk URL or Channel tab) can be listed and chosen from */}
        <>
              {(() => {
                const lines = urls.split('\n').map((u) => u.trim()).filter(Boolean);
                if (lines.length !== 1 || !isPreviewableChannel(lines[0]) || previewUrl === lines[0]) return null;
                return (
                  // wording: BA review
                  <button type="button" onClick={() => {
                    // Facebook: the extension's tab opens inside this click (popup blockers)
                    const tab = session?.access_token && isFacebookChannel(lines[0]) ? window.open('about:blank', '_blank') : null;
                    setPreview((p) => ({ url: lines[0], tab, n: ((p && p.n) || 0) + 1 }));
                  }}
                    className="mb-3 inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold border border-accent/40 bg-accent/10 text-accent-text hover:bg-accent/20 cursor-pointer">
                    <Eye className="w-3.5 h-3.5" /> {t('listToChoose')}
                  </button>
                );
              })()}
              {previewUrl && (
                <ChannelPreview
                  key={preview.n} url={preview.url} want={preview.want} autoTab={preview.tab}
                  serverWhole={douyinWhole && !!session?.access_token && isDouyinProfile(preview.url)}
                  withAuth={withAuth} signedIn={!!session?.access_token}
                  onDownload={(list) => runBulk(list, false)} onClose={() => setPreview(null)}
                />
              )}
        </>

        {channelMode && (
          <div className="mb-4 p-4 rounded-xl bg-accent/5 border border-accent/20">
            <p className="text-xs text-accent-text flex items-center gap-2 mb-3">
              <Tv className="w-4 h-4" />
              <strong>{t('channelModeOn')}</strong> {t('channelModeDesc')}
            </p>
            {/* Platform-specific hint */}
            {(() => {
              const allUrls = urls.toLowerCase();
              if (allUrls.includes('twitter.com') || allUrls.includes('x.com')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-surface-2 border border-line text-xs text-fg-2">
                  <strong>Twitter/X:</strong> {t('hint.twitter')}
                </div>
              );
              if (allUrls.includes('reddit.com')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-accent-soft border border-accent/20 text-xs text-accent-text">
                  <strong>Reddit:</strong> {t('hint.reddit')}
                </div>
              );
              if (allUrls.includes('pinterest.com') || allUrls.includes('pin.it')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-danger-soft border border-danger/20 text-xs text-danger">
                  <strong>Pinterest:</strong> {t('hint.pinterest')}
                </div>
              );
              if (allUrls.includes('threads.com') || allUrls.includes('threads.net')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-surface-2 border border-line text-xs text-fg-2">
                  <strong>Threads:</strong> {t('hint.threads')}
                </div>
              );
              return null;
            })()}
            <div>
              <label className="block text-[10px] font-semibold text-fg-muted uppercase mb-2">
                {t('howMany')}
              </label>
              <p className="text-xs text-fg-muted mb-2">{t('howManyHint')}</p>
              {/douyin\.com/i.test(urls) && (
                // 21/10 — wording: BA review. Listing is free (~41 newest); only downloads count.
                <p className="text-xs text-fg-muted mb-2">{t('douyinChannelNote')}</p>
              )}
              {/(xiaohongshu\.com|rednote\.com|xhslink\.c|kuaishou\.c|chenzhongtech\.c)/i.test(urls) && (
                // Task #6171 — wording: BA review. Server rule: signed-in only, ≤ 20 per scan, ≤ remaining today.
                <p className="text-xs text-fg-muted mb-2">{t('xhsChannelNote')}</p>
              )}
              <div className="flex flex-wrap gap-2 mb-2">
                {[
                  { value: 10, label: '10' },
                  { value: 20, label: '20' },
                  { value: 50, label: '50' },
                  { value: 100, label: '100' },
                  { value: 200, label: '200' },
                  { value: 500, label: '500' },
                ].map(opt => (
                  <button
                    key={opt.value}
                    onClick={() => { setMaxVideos(opt.value); setCustomMaxVideos(''); }}
                    className={`px-3.5 py-1.5 rounded-lg text-xs font-bold border transition-all duration-200 cursor-pointer ${
                      !customMaxVideos && parseInt(maxVideos) === opt.value
                        ? 'bg-accent/20 border-accent/40 text-accent-text shadow-sm'
                        : 'bg-surface border-line text-fg-muted hover:text-fg-2 hover:border-line/80'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {(urls.toLowerCase().includes('twitter.com') || urls.toLowerCase().includes('x.com')) && (
                <p className="text-xs text-fg-2 mt-1 inline-flex items-center gap-1"><AlertTriangle className="w-3 h-3 flex-shrink-0" /> {t('twitterLimit')}</p>
              )}
              <input
                type="number"
                min="1"
                max="1000"
                value={customMaxVideos}
                onChange={(e) => { setCustomMaxVideos(e.target.value); setMaxVideos(0); }}
                className="w-full px-3 py-2 bg-surface border border-line rounded-lg text-sm text-fg focus:outline-none focus:border-accent/50"
                placeholder={t('customCountPlaceholder')}
              />
              <p className="text-[10px] text-fg-muted mt-1.5">
                {t('batchNote')}
              </p>
            </div>
          </div>
        )}

        {/* ── Keyword Search Panel ──────────────────────────────────── */}
        {searchMode && (
          <div className="mb-4 space-y-3">
            {/* Search controls */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              <div className="col-span-2 sm:col-span-2">
                <label className="text-[10px] text-fg-muted uppercase font-semibold mb-1 block">{t('kw.label')}</label>
                <input
                  value={kw}
                  onChange={e => setKw(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleKeywordSearch()}
                  placeholder={t('kw.placeholder')}
                  className="w-full bg-surface border border-line rounded-xl px-3 py-2 text-sm text-fg placeholder:text-fg-muted focus:outline-none focus:border-accent/40"
                />
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase font-semibold mb-1 block">{t('kw.platform')}</label>
                <select value={kwPlatform} onChange={e => setKwPlatform(e.target.value)}
                  className="w-full bg-surface border border-line rounded-xl px-2 py-2 text-sm text-fg focus:outline-none cursor-pointer">
                  <option value="youtube">YouTube</option>
                  <option value="tiktok">TikTok</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase font-semibold mb-1 block">{t('kw.count')}</label>
                <select value={kwCount} onChange={e => setKwCount(Number(e.target.value))}
                  className="w-full bg-surface border border-line rounded-xl px-2 py-2 text-sm text-fg focus:outline-none cursor-pointer">
                  {[10, 20, 50].map(n => <option key={n} value={n}>{t('kw.nVideos', { count: n })}</option>)}
                </select>
              </div>
            </div>
            <div className="flex gap-2 flex-wrap items-end">
              <div>
                <label className="text-[10px] text-fg-muted uppercase font-semibold mb-1 block">{t('kw.sort')}</label>
                <select value={kwSort} onChange={e => setKwSort(e.target.value)}
                  className="bg-surface border border-line rounded-xl px-2 py-2 text-sm text-fg focus:outline-none cursor-pointer">
                  <option value="relevance">{t('kw.sortRelevance')}</option>
                  <option value="date">{t('kw.sortNewest')}</option>
                  <option value="view_count">{t('kw.sortViews')}</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase font-semibold mb-1 block">{t('kw.minDur')}</label>
                <input type="number" value={kwMinDur} onChange={e => setKwMinDur(e.target.value)}
                  placeholder="0"
                  className="w-24 bg-surface border border-line rounded-xl px-2 py-2 text-sm text-fg focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] text-fg-muted uppercase font-semibold mb-1 block">{t('kw.maxDur')}</label>
                <input type="number" value={kwMaxDur} onChange={e => setKwMaxDur(e.target.value)}
                  placeholder={t('kw.unlimited')}
                  className="w-32 bg-surface border border-line rounded-xl px-2 py-2 text-sm text-fg focus:outline-none"
                />
              </div>
              <button
                onClick={handleKeywordSearch}
                disabled={kwLoading || kw.trim().length < 3}
                className="flex items-center gap-2 px-5 py-2 rounded-xl bg-accent text-accent-fg text-sm font-semibold border border-transparent hover:bg-accent-hover transition-colors cursor-pointer disabled:bg-surface-2 disabled:text-fg-muted disabled:border disabled:border-line disabled:shadow-none disabled:cursor-not-allowed"
              >
                {kwLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                {kwLoading ? t('kw.searching') : t('kw.search')}
              </button>
            </div>

            {kwError && <p className="text-sm text-danger">{kwError}</p>}

            {/* Results */}
            {kwResults.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-fg-muted">
                    {t(kwCached ? 'kw.foundCached' : 'kw.found', { count: kwResults.length, kw })}
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setKwSelected(new Set(kwResults.map(r => r.url)))}
                      className="text-[10px] text-fg-muted hover:text-fg cursor-pointer"
                    >
                      {t('selectAll')}
                    </button>
                    <button
                      onClick={() => setKwSelected(new Set())}
                      className="text-[10px] text-fg-muted hover:text-fg cursor-pointer"
                    >
                      {t('deselect')}
                    </button>
                  </div>
                </div>

                <div className="max-h-96 overflow-y-auto space-y-1.5 pr-1">
                  {kwResults.map(r => {
                    const selected = kwSelected.has(r.url);
                    const dur = r.duration_seconds;
                    const durStr = dur
                      ? `${Math.floor(dur / 60)}:${String(dur % 60).padStart(2, '0')}`
                      : null;
                    const views = r.view_count
                      ? (r.view_count >= 1e6 ? `${(r.view_count/1e6).toFixed(1)}M` : `${Math.round(r.view_count/1e3)}K`) + ' views'
                      : null;
                    return (
                      <div
                        key={r.url}
                        onClick={() => setKwSelected(prev => {
                          const next = new Set(prev);
                          next.has(r.url) ? next.delete(r.url) : next.add(r.url);
                          return next;
                        })}
                        className={`flex items-center gap-3 p-2.5 rounded-xl border cursor-pointer transition-colors ${
                          selected
                            ? 'bg-accent-soft border-accent/40'
                            : 'bg-surface-2 border-line hover:border-line-strong'
                        }`}
                      >
                        <input type="checkbox" checked={selected} onChange={() => {}} className="w-3.5 h-3.5 accent-accent cursor-pointer flex-shrink-0" />
                        {r.thumbnail_url && (
                          <img src={r.thumbnail_url} alt="" className="w-14 h-9 object-cover rounded-lg flex-shrink-0 bg-surface-2" />
                        )}
                        <div className="flex-1 min-w-0">
                          <p className="text-xs text-fg font-medium truncate">{r.title}</p>
                          <div className="flex gap-2 mt-0.5 flex-wrap">
                            {r.creator && <span className="text-[10px] text-fg-muted">@{r.creator}</span>}
                            {durStr && <span className="text-[10px] text-fg-muted">⏱ {durStr}</span>}
                            {views && <span className="text-[10px] text-fg-muted inline-flex items-center gap-0.5"><Eye className="w-3 h-3" /> {views}</span>}
                            {r.upload_date && <span className="text-[10px] text-fg-muted">{r.upload_date}</span>}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <button
                  onClick={handleKwAddSelected}
                  disabled={kwSelected.size === 0}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-accent text-accent-fg border border-transparent text-sm font-semibold disabled:bg-surface-2 disabled:text-fg-muted disabled:border disabled:border-line disabled:shadow-none disabled:cursor-not-allowed hover:bg-accent-hover transition-colors cursor-pointer"
                >
                  <Download className="w-4 h-4" />
                  {t('kw.downloadSelected', { count: kwSelected.size })}
                </button>
                <p className="text-[10px] text-center text-fg-muted">{t('kw.movedToBulk')}</p>
              </div>
            )}
          </div>
        )}

        {/* Hidden file input for import */}
        <input
          ref={fileInputRef}
          type="file"
          accept=".txt,.csv"
          className="hidden"
          onChange={handleFileImport}
        />

        <div className="relative">
          <textarea
            rows={5}
            value={urls}
            onChange={handleUrlsChange}
            onPaste={handleUrlsPaste}
            placeholder={
              channelMode
                ? 'https://www.douyin.com/user/MS4wLjABAAAA...\nhttps://www.tiktok.com/@username\nhttps://www.youtube.com/playlist?list=...'
                : 'https://www.youtube.com/watch?v=...\nhttps://www.tiktok.com/@user/video/...\nhttps://...'
            }
            className="
              w-full px-4 py-3 rounded-xl
              bg-surface border border-line
              text-fg placeholder-text-muted
              text-sm leading-relaxed cursor-text
              resize-none
              focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-accent/50
              transition-all duration-200
            "
            disabled={isSubmitting}
          />
          <div className="absolute top-2 right-2 flex gap-1">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-1.5 bg-surface-2 text-fg-muted hover:text-accent-text border border-line rounded-lg transition-colors shadow-sm"
              title={t('importFile')}
            >
              <FileDown className="w-4 h-4" />
            </button>
            {navigator.clipboard && (
              <button
                onClick={async () => {
                  try {
                    const text = await navigator.clipboard.readText();
                    if (text) {
                      const cleaned = cleanProfileUrls(text);
                      setUrls(prev => prev ? prev + '\n' + cleaned : cleaned);
                    }
                  } catch (err) {
                    console.error("Failed to read clipboard: ", err);
                  }
                }}
                className="p-1.5 bg-surface-2 text-fg-muted hover:text-accent-text border border-line rounded-lg transition-colors shadow-sm"
                title={t('pasteClipboard')}
              >
                <ClipboardPaste className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between mt-4">
          {(() => {
            const lines = urls.split('\n').map(u => u.trim()).filter(Boolean);
            const dups = lines.length - new Set(lines).size;
            return dups > 0 ? (
              <span className="text-xs text-accent-text flex items-center gap-1">
                {t('dupUrls', { count: dups })}
              </span>
            ) : <span />;
          })()}

          {containerMode ? (
            <button
              onClick={() => {
                const firstUrl = urls.split('\n').map(u => u.trim()).find(u => u.startsWith('http'));
                if (!firstUrl) return;
                setContainerUrl(firstUrl);
                discoverContainer(firstUrl);
                setContainerPanelOpen(true);
              }}
              disabled={!urls.trim()}
              className="
                flex items-center gap-2
                px-5 py-2.5 rounded-xl
                bg-accent border border-transparent
                text-accent-fg text-sm font-semibold cursor-pointer
                hover:bg-accent-hover
                active:scale-[0.98]
                transition-colors duration-200
                disabled:bg-surface-2 disabled:text-fg-muted disabled:border disabled:border-line disabled:shadow-none disabled:cursor-not-allowed
              "
            >
              <FolderOpen className="w-4 h-4" />
              {t('previewAndPick')}
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={isSubmitting || !urls.trim()}
              className={`
                flex items-center gap-2
                px-5 py-2.5 rounded-xl
                bg-accent border border-transparent
                text-accent-fg text-sm font-semibold cursor-pointer
                hover:bg-accent-hover
                active:scale-[0.98]
                transition-colors duration-200
                disabled:cursor-not-allowed
                ${isSubmitting ? 'disabled:opacity-80 disabled:cursor-progress' : 'disabled:bg-surface-2 disabled:text-fg-muted disabled:border-line disabled:shadow-none'}
              `}
            >
              {isSubmitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              {isSubmitting ? t('sending') : t('startBulk')}
            </button>
          )}
        </div>
      </div>

      {/* ── Progress Summary Bar ──────────────────────── */}
      {summary && totalData.total > 0 && (
        <div className="p-5 rounded-2xl bg-surface border border-line shadow-lg">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-fg">
              {t('progress')}
            </h3>
            <span className="text-xs text-fg-muted">
              {t('progressDone', { done: totalData.success + totalData.failed + (totalData.cancelled || 0), total: totalData.total })}
            </span>
          </div>

          {/* Progress Bar */}
          <div className="w-full h-2.5 bg-surface rounded-full overflow-hidden mb-4">
            <div
              className="h-full bg-accent transition-all duration-500 rounded-full"
              style={{ width: `${progressPct}%` }}
            />
          </div>

          {/* Stat Pills */}
          <div className="flex flex-wrap gap-3">
            <span className="text-xs px-3 py-1.5 rounded-lg bg-surface font-medium text-fg-2">
              {t('stat.total')} <strong className="text-fg">{totalData.total}</strong>
            </span>
            {totalData.pending > 0 && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-surface font-medium text-fg-muted">
                {t('stat.pending')} {totalData.pending}
              </span>
            )}
            {totalData.processing > 0 && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-warning/10 font-medium text-warning">
                {t('stat.processing')} {totalData.processing}
              </span>
            )}
            <span className="text-xs px-3 py-1.5 rounded-lg bg-success/10 font-medium text-success">
              {t('stat.success')} {totalData.success}
            </span>
            {totalData.failed > 0 && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-error/10 font-medium text-error">
                {t('stat.failed')} {totalData.failed}
              </span>
            )}
            {totalData.cancelled > 0 && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-surface font-medium text-fg-muted">
                {t('stat.stopped')} {totalData.cancelled}
              </span>
            )}
            {(summary?.recoverable > 0) && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-accent/10 font-medium text-accent">
                {t('stat.recoverable')} {summary.recoverable}
              </span>
            )}
          </div>

          {/* Stop: cancels the videos not started yet */}
          {canStop && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {stopButton(t('stopRest'))}
              <span className="text-[11px] text-fg-muted">{t('stopNote')}</span>
            </div>
          )}

          {/* Retry Failed / Recovery Buttons */}
          {totalData.failed > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={handleRetryFailed}
                disabled={isRetrying}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold bg-error/10 text-error border border-error/30 hover:bg-error/20 transition-all disabled:opacity-50"
              >
                {isRetrying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                {isRetrying ? t('requeueing') : t('retryFailed', { count: totalData.failed })}
              </button>
            </div>
          )}

          {/* Download All Buttons */}
          {successJobs.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 mt-4 pt-4 border-t border-line/50">
              {totalData.total === totalData.success && (
                <div className="mr-auto">
                    {zipJob && zipJob.status === "success" ? (
                       <div className="flex flex-col items-start gap-1.5">
                         <JobActionCell job={zipJob} onDownload={handleSmartDownload} />
                         {zipJob.file_size_mb > 0 && (
                           <span className="text-[11px] font-semibold text-accent-text bg-accent/10 px-2.5 py-1 rounded-lg border border-accent/20 flex items-center gap-1.5">
                             <FileDown className="w-3 h-3" />
                             ZIP: {zipJob.file_size_mb} MB
                             {zipJob.title && zipJob.title.includes('files') && (
                               <span className="text-fg-muted">• {zipJob.title.split('—')[1]?.trim()}</span>
                             )}
                           </span>
                         )}
                       </div>
                    ) : (zipJob && (zipJob.status === "processing" || zipJob.status === "pending")) || isZipping ? (
                        <button disabled className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-accent/50 text-fg text-xs font-bold uppercase tracking-wider cursor-not-allowed">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          {t('creatingZip')}
                        </button>
                    ) : (
                        <div className="flex flex-col items-start gap-2">
                          <button
                            onClick={() => {
                              if (quotaInfo && !quotaInfo.permissions?.can_zip) {
                                setShowUpgradeModal(true);
                                return;
                              }
                              handleCreateZip();
                            }}
                            className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-accent shadow-lg hover:shadow-xl text-accent-fg text-xs font-bold uppercase tracking-wider transition-all duration-200 cursor-pointer"
                          >
                            <FileDown className="w-4 h-4" />
                            {t('zipAll')} {zipJob?.error_message ? t('zipRetry') : ""}
                          </button>
                          {/* Organize by channel toggle */}
                          <label className="flex items-center gap-1.5 cursor-pointer select-none ml-1">
                            <input
                              type="checkbox"
                              checked={organizeByChannel}
                              onChange={e => setOrganizeByChannel(e.target.checked)}
                              className="w-3.5 h-3.5 accent-primary rounded"
                            />
                            <span className="text-[10px] text-fg-muted">{t('byChannel')}</span>
                          </label>
                          {/* Show estimated total size before zipping */}
                          {successJobs.length > 0 && (
                            <span className="text-[10px] text-fg-muted ml-1">
                              {t('zipEstimate', { mb: successJobs.reduce((sum, j) => sum + (j.file_size_mb || 0), 0).toFixed(1), count: successJobs.length })}
                            </span>
                          )}
                        </div>
                    )}
                    {zipJob?.status === "failed" && <p className="text-[10px] text-error mt-1">{zipJob.error_message}</p>}
                </div>
              )}

              {/* ── Rename & ZIP button ─────────────────────────────── */}
              {batchId && totalData.total === totalData.success && (
                <div className="ml-2">
                  <button
                    onClick={() => setShowRename(p => !p)}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold border transition-colors cursor-pointer ${
                      showRename
                        ? 'bg-surface-2 text-fg-2 border-line'
                        : 'bg-surface text-fg-muted border-line hover:text-fg-2 hover:border-line'
                    }`}
                  >
                    <Pencil className="w-3.5 h-3.5" /> Rename & ZIP
                  </button>
                </div>
              )}

              <button
                onClick={handleDownloadAll}
                disabled={selectedJobIds.size === 0}
                className={`
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  border text-xs font-semibold
                  transition-all duration-200
                  ${selectedJobIds.size > 0 
                    ? 'bg-success/10 hover:bg-success/20 border-success/20 text-success cursor-pointer' 
                    : 'bg-surface border-line text-fg-muted opacity-50 cursor-not-allowed'}
                `}
              >
                <FileDown className="w-4 h-4" />
                {t('downloadSelectedN', { count: selectedJobIds.size })}
              </button>
              <button
                onClick={handleExportCSV}
                className="
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  bg-success-soft hover:bg-success/20 border border-success/20
                  text-success text-xs font-semibold
                  transition-all duration-200 cursor-pointer
                "
              >
                <TableIcon className="w-4 h-4" />
                {t('exportCsv')}
              </button>
              <button
                onClick={handleCopyAll}
                className="
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  bg-accent/10 hover:bg-accent/20 border border-accent/20
                  text-accent-text text-xs font-semibold
                  transition-all duration-200 cursor-pointer
                "
              >
                <Layers className="w-4 h-4" />
                {t('copyLinks')}
              </button>
              <button
                onClick={handleOpenAll}
                className="
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  bg-accent/10 hover:bg-accent/20 border border-accent/20
                  text-primary-light text-xs font-semibold
                  transition-all duration-200 cursor-pointer
                "
              >
                <ExternalLink className="w-4 h-4" />
                {t('openAll', { n: successJobs.length })}
              </button>
            </div>
          )}

          {/* ── Rename Panel ────────────────────────────────────── */}
          {showRename && batchId && (
            <div className="mt-3 p-4 rounded-xl bg-surface-2 border border-line space-y-3">
              <h3 className="text-sm font-bold text-fg-2 inline-flex items-center gap-1.5"><Pencil className="w-3.5 h-3.5" /> {t('rename.title')}</h3>

              {/* Token chips */}
              <div>
                <p className="text-[10px] text-fg-muted mb-1.5">{t('rename.tokenHint')}</p>
                <div className="flex flex-wrap gap-1.5">
                  {TOKEN_CHIPS.map(tok => (
                    <button
                      key={tok}
                      onClick={() => handleInsertToken(tok)}
                      className="px-2 py-0.5 rounded bg-surface border border-line text-[10px] text-fg-2 hover:border-line hover:text-fg-2 transition-colors cursor-pointer font-mono"
                    >
                      {tok}
                    </button>
                  ))}
                </div>
              </div>

              {/* Pattern input */}
              <div>
                <label className="text-[10px] text-fg-muted uppercase font-semibold mb-1 block">Pattern</label>
                <input
                  value={renamePattern}
                  onChange={e => setRenamePattern(e.target.value)}
                  className="w-full bg-surface-2 border border-line rounded-lg px-3 py-2 text-xs text-fg-2 font-mono focus:outline-none focus:border-line"
                />
              </div>

              {/* Live preview */}
              {successJobs.length > 0 && (
                <div>
                  <p className="text-[10px] text-fg-muted uppercase font-semibold mb-1">{t('rename.preview')}</p>
                  <div className="space-y-1">
                    {renamePreview.map((name, i) => (
                      <p key={i} className="text-[11px] text-fg-2 font-mono bg-surface-2 px-2 py-1 rounded truncate">
                        {name}
                      </p>
                    ))}
                  </div>
                  <p className="text-[10px] text-fg-muted mt-1">{t('rename.placeholderNote')}</p>
                </div>
              )}

              {renameError && <p className="text-xs text-danger">{renameError}</p>}

              {renameResult && (
                <div className="p-3 rounded-lg bg-surface-2 border border-line">
                  <p className="text-xs text-fg-2 font-semibold">{t('rename.ready', { count: renameResult.file_count, mb: renameResult.file_size_mb })}</p>
                  <a
                    href={`${API}${renameResult.zip_url.replace('/api/v1', '')}`}
                    download={renameResult.filename}
                    className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-2 text-fg-2 text-xs font-bold hover:bg-line transition-colors"
                  >
                    <Download className="w-3.5 h-3.5" /> {t('rename.download')}
                  </a>
                </div>
              )}

              <button
                onClick={handleRenameZip}
                disabled={renameLoading || !renamePattern.trim()}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-surface-2 text-fg-2 text-xs font-bold border border-line hover:bg-line transition-colors cursor-pointer disabled:opacity-50"
              >
                {renameLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                {renameLoading ? t('rename.creating') : t('rename.create')}
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Jobs Table ────────────────────────────────── */}
      {(batchId || jobs.length > 0) && (
        <div className="rounded-2xl bg-surface border border-line shadow-lg overflow-hidden">
          <div className="px-6 py-4 border-b border-line bg-surface-2/30 flex justify-between items-center">
            <div className="flex items-center gap-3">
              <h3 className="font-semibold text-fg">
                {t('listTitle', { n: isDiscovering ? '...' : displayJobs.length })}
              </h3>
              {selectedJobIds.size > 0 && (
                <span className="inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1 rounded-lg bg-accent/15 text-accent-text border border-accent/25 animate-in fade-in duration-200">
                  <CheckSquare className="w-3 h-3" />
                  {t('selectedN', { count: selectedJobIds.size })}
                </span>
              )}
            </div>
            <span className="text-xs text-fg-muted bg-surface px-2.5 py-1 rounded-md font-mono">
              {batchId ? batchId.slice(0, 8) + '...' : 'Loading...'}
            </span>
          </div>

          {isDiscovering ? (
            <div className="p-8 flex flex-col items-center gap-5">
              {/* Animated radar icon */}
              <div className="relative flex items-center justify-center w-16 h-16">
                <span className="absolute w-16 h-16 rounded-full bg-accent/10 animate-ping" />
                <span className="absolute w-10 h-10 rounded-full bg-accent/15 animate-ping [animation-delay:0.3s]" />
                <Radio className="w-7 h-7 text-accent-text relative z-10" />
              </div>

              <div className="text-center">
                <p className="text-sm font-semibold text-fg mb-1">
                  {t('discover.scanning')}
                </p>
                <p className="text-xs text-fg-muted">
                  {discoveryJob?.error_message || t('discover.connecting')}
                </p>
              </div>

              {/* Elapsed timer */}
              <div className="flex items-center gap-3 text-xs text-fg-muted bg-surface border border-line rounded-xl px-5 py-3">
                <Clock className="w-4 h-4 text-accent-text" />
                <span>{t('discover.elapsed')}</span>
                <span className="font-mono font-bold text-fg text-sm tabular-nums">
                  {String(Math.floor(discoverElapsed / 60)).padStart(2, '0')}:{String(discoverElapsed % 60).padStart(2, '0')}
                </span>
              </div>

              <p className="text-[11px] text-fg-muted text-center max-w-xs leading-relaxed">
                {t('discover.slowNote')}
              </p>
              {discoveryJob && stopButton(t('discover.stop'))}
            </div>
          ) : displayJobs.length === 0 ? (
            <div className="p-10 text-center text-fg-muted text-sm flex flex-col items-center gap-2">
              <Loader2 className="w-5 h-5 animate-spin text-accent-text" />
              {channelMode ? t('initializing') : t('creatingJobs')}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-2/50 text-xs uppercase text-fg-muted">
                  <tr>
                    <th className="px-6 py-3 font-medium w-8">
                      <input 
                        type="checkbox" 
                        className="rounded border-line bg-surface text-accent-text focus:ring-primary/50"
                        checked={successJobs.length > 0 && selectedJobIds.size === successJobs.length}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedJobIds(new Set(successJobs.map(j => j.id)));
                          } else {
                            setSelectedJobIds(new Set());
                          }
                        }}
                      />
                    </th>
                    <th className="px-6 py-3 font-medium w-8">#</th>
                    <th className="px-6 py-3 font-medium">{t('col.originalUrl')}</th>
                    <th className="px-6 py-3 font-medium">{t('col.status')}</th>
                    <th className="px-6 py-3 font-medium text-right">{t('col.actions')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {displayJobs.map((job, idx) => (
                    <tr
                      key={job.id}
                      className="hover:bg-surface-2/20 transition-colors"
                    >
                      <td className="px-6 py-3">
                        <input 
                          type="checkbox" 
                          disabled={job.status !== 'success'}
                          className="rounded border-line bg-surface text-accent-text focus:ring-primary/50 disabled:opacity-50"
                          checked={selectedJobIds.has(job.id)}
                          onChange={(e) => {
                            const next = new Set(selectedJobIds);
                            if (e.target.checked) next.add(job.id);
                            else next.delete(job.id);
                            setSelectedJobIds(next);
                          }}
                        />
                      </td>
                      <td className="px-6 py-3 text-fg-muted text-xs">
                        {idx + 1}
                      </td>
                      <td className="px-6 py-3 max-w-[320px]">
                        <div className="flex items-start gap-2">
                          <div className="mt-0.5">
                            {localMp3Id(job) ? (
                              <Music className="w-4 h-4 text-accent-text flex-shrink-0" />
                            ) : (
                              <Video className="w-4 h-4 text-accent-text flex-shrink-0" />
                            )}
                          </div>
                          <div className="flex-1 overflow-hidden">
                            <span className="block truncate text-fg" title={job.original_url}>
                              {job.original_url}
                            </span>
                            {job.file_size_mb > 0 && job.status === 'success' && (
                              <span className="text-[10px] text-fg-muted mt-1 font-medium px-1.5 py-0.5 bg-surface rounded inline-block">
                                {job.file_size_mb} MB
                              </span>
                            )}
                            
                            {/* Error message handling */}
                        {job.error_message && job.status === 'failed' && (
                          <p className="text-[10px] text-error mt-0.5 truncate" title={job.error_message}>
                            {job.error_message}
                          </p>
                        )}
                        {/* Processing message (Scraping...) */}
                        {job.error_message && job.status === 'processing' && (
                          <p className="text-[10px] text-warning mt-0.5 truncate">
                            {job.error_message}
                          </p>
                        )}
                        {/* Success Note / Filter Summary for channels */}
                        {job.error_message && job.status === 'success' && !job.direct_mp4_url && (
                          <p className="text-[11px] font-medium text-success mt-1" title={job.error_message}>
                            <CheckCircle2 className="inline w-3 h-3 mr-1" />
                            {job.error_message}
                          </p>
                        )}
                        {job.channel_hint === 'douyin_extension' && <DouyinExtensionHint />}
                          </div>
                        </div>
                      </td>
                      <td className="px-6 py-3">{getStatusBadge(job.status, job)}</td>
                      <td className="px-6 py-3 text-right whitespace-nowrap">
                        <JobActionCell job={job} onDownload={handleSmartDownload} onRefresh={handleRefreshJob} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ── Floating Selection Action Bar ─────────────── */}
      {selectedJobIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 animate-in slide-in-from-bottom-4 fade-in duration-300">
          <div className="flex items-center gap-3 px-5 py-3 bg-surface/95 border border-accent/30 rounded-2xl shadow-2xl shadow-black/40 backdrop-blur-xl">
            <div className="flex items-center gap-2 pr-3 border-r border-line">
              <CheckSquare className="w-4 h-4 text-accent-text" />
              <span className="text-sm font-bold text-fg">{selectedJobIds.size}</span>
              <span className="text-xs text-fg-muted">{t('floatingSelected', { count: selectedJobIds.size })}</span>
            </div>

            <button
              onClick={handleDownloadAll}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-accent text-accent-fg text-sm font-extrabold shadow-lg hover:shadow-xl hover:scale-105 active:scale-95 transition-all duration-200 cursor-pointer"
            >
              <Download className="w-4 h-4" />
              {t('downloadVideosN', { count: selectedJobIds.size })}
            </button>

            <button
              onClick={() => setSelectedJobIds(new Set())}
              className="p-2 rounded-xl text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors cursor-pointer"
              title={t('deselectAll')}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
      {/* ── Quick Guide ────────────────────────────────── */}
      <QuickGuideSection activeToolTab={channelMode ? 'channel' : 'bulk'} />
    </div>
  );
}
