import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
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
} from 'lucide-react';

import UpgradeModal from './UpgradeModal';
import QuickGuideSection from './QuickGuideSection';
import ContainerPreviewPanel from './ContainerPreviewPanel';
import { useContainerDiscover } from '../hooks/useContainerDiscover';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

async function safeJson(response) {
  const text = await response.text();
  try {
    return JSON.parse(text);
  } catch {
    if (response.status === 502 || response.status === 503 || response.status === 504) {
      throw new Error('🔧 Máy chủ đang bảo trì hoặc khởi động lại. Vui lòng thử lại sau vài giây.');
    }
    if (response.status === 429) {
      throw new Error('⏳ Bạn đã gửi quá nhiều yêu cầu. Vui lòng chờ 1 phút rồi thử lại.');
    }
    throw new Error(`Lỗi máy chủ (${response.status}). Vui lòng thử lại.`);
  }
}

// ── Countdown Timer + Recovery Action Cell ─────────────────────────
const JobActionCell = ({ job, onDownload, onRefresh }) => {
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
          Thử lại
        </button>
      );
    }
    return <span className="text-xs text-text-muted">-</span>;
  }

  if (job.status !== 'success') return <span className="text-xs text-text-muted">Đang chờ...</span>;

  const hasLink = job.direct_mp4_url || job.local_mp3_path || job.local_file_path;
  if (!hasLink) return <span className="text-xs text-text-muted">-</span>;

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
            {refreshing ? 'Đang gia hạn...' : 'Link đã hết hạn — Gia hạn'}
          </button>
        ) : (
          <span className="text-[11px] text-error italic border border-error/20 bg-error/5 px-2 py-1 rounded">
            Link đã hết hạn
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
        onClick={() => onDownload(job.local_mp3_path || job.local_file_path || job.direct_mp4_url, job.slugified_name)}
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
  const [selectedJobIds, setSelectedJobIds] = useState(new Set());
  const autoDownloadedRefs = useRef(new Set());
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const fileInputRef = useRef(null);

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

    // Phase 21: silent deduplication before submit
    const uniqueUrlList = [...new Set(urlList)];
    const dupCount = urlList.length - uniqueUrlList.length;
    if (dupCount > 0) urlList = uniqueUrlList;

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
              spData.tracks.forEach(t => expandedUrls.push(t.query));
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

      const res = await fetch(`${API}/bulk-download`,
        withAuth({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          urls: urlList,
          channel_mode: channelMode,
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
          return;
        }
        throw new Error(data.detail || 'Failed to submit bulk jobs');
      }
      
      if (data.batch_id) {
        setBatchId(data.batch_id);
      }
    } catch (err) {
      console.error(err);
      alert('Failed to submit bulk jobs');
    } finally {
      setIsSubmitting(false);
    }
  };

  // ── Push: ask permission after first successful batch ──────────────
  const requestPushSubscription = async () => {
    try {
      if (!('Notification' in window) || !('serviceWorker' in navigator)) return;
      if (Notification.permission === 'granted' || Notification.permission === 'denied') return;
      // Only ask after a batch completes (not on every poll tick)
      if (sessionStorage.getItem('push-asked')) return;
      sessionStorage.setItem('push-asked', '1');

      // Short delay so user sees the batch completion first
      await new Promise(r => setTimeout(r, 3000));

      const permission = await Notification.requestPermission();
      if (permission !== 'granted') return;

      const reg = await navigator.serviceWorker.ready;
      const vapidRes = await fetch(`${API}/push/vapid-key`);
      if (!vapidRes.ok) return;
      const { public_key: rawKey } = await vapidRes.json();
      if (!rawKey) return;

      // Convert VAPID public key from base64url to Uint8Array
      const padding = '='.repeat((4 - (rawKey.length % 4)) % 4);
      const base64 = (rawKey + padding).replace(/-/g, '+').replace(/_/g, '/');
      const rawData = atob(base64);
      const appServerKey = new Uint8Array(rawData.length);
      for (let i = 0; i < rawData.length; i++) appServerKey[i] = rawData.charCodeAt(i);

      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: appServerKey,
      });

      // Send subscription to backend
      const token = session?.access_token;
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
        downloadUrl = `${API}/download-local?filepath=${encodeURIComponent(url)}&filename=${encodeURIComponent(slug || 'video')}.${fileExt}`;
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
      const active = jobs.filter(j => j.status !== 'success' && j.status !== 'failed').length;
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

            // Auto-Download trigger
            if (autoDownload) {
              data.jobs.forEach(job => {
                const dlPath = job.local_mp3_path || job.local_file_path || job.direct_mp4_url;
                if (job.status === 'success' && dlPath && !autoDownloadedRefs.current.has(job.id)) {
                  autoDownloadedRefs.current.add(job.id);
                  handleSmartDownload(dlPath, job.slugified_name);
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
              (j) => j.status === 'success' || j.status === 'failed'
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
      const label = stage === 'queued' && job.recovery_attempts > 0 ? 'Đang khôi phục...' : 'Chờ xử lý';
      return (
        <span className="inline-flex items-center gap-1 text-text-muted bg-surface/50 px-2.5 py-1 rounded-lg text-xs font-medium">
          <Clock className="w-3 h-3" /> {label}
        </span>
      );
    }
    if (status === 'processing') {
      const stageLabel = stage === 'extracting' ? 'Đang trích xuất' : stage === 'downloading' ? 'Đang tải' : stage === 'zipping' ? 'Đang nén ZIP' : 'Đang xử lý';
      const isStuck = recoveryState === 'stuck';
      return (
        <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium ${isStuck ? 'text-error bg-error/10' : 'text-warning bg-warning/10'}`}>
          {isStuck ? <AlertTriangle className="w-3 h-3" /> : <Loader2 className="w-3 h-3 animate-spin" />}
          {isStuck ? 'Bị kẹt' : stageLabel}
        </span>
      );
    }
    if (status === 'success') {
      if (recoveryState === 'expired_refreshable') {
        return (
          <span className="inline-flex items-center gap-1 text-error bg-error/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Clock className="w-3 h-3" /> Hết hạn
          </span>
        );
      }
      return (
        <span className="inline-flex items-center gap-1 text-success bg-success/10 px-2.5 py-1 rounded-lg text-xs font-medium">
          <CheckCircle2 className="w-3 h-3" /> Thành công
        </span>
      );
    }
    if (status === 'failed') {
      const isAbandoned = stage === 'abandoned' || recoveryState === 'abandoned';
      return (
        <span className="inline-flex items-center gap-1 text-error bg-error/10 px-2.5 py-1 rounded-lg text-xs font-medium">
          <XCircle className="w-3 h-3" /> {isAbandoned ? 'Đã dừng' : 'Thất bại'}
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

  const successJobs = displayJobs.filter(j => j.status === 'success' && (j.direct_mp4_url || j.local_mp3_path || j.local_file_path));
  
  // Progress computation (excluding zip job)
  let totalData = summary ? { ...summary } : { total: 0, success: 0, failed: 0, pending: 0, processing: 0 };
  if (zipJob && summary) {
      totalData.total -= 1;
      totalData[zipJob.status] -= 1;
  }
  
  const progressPct =
    totalData.total > 0
      ? Math.round(((totalData.success + totalData.failed) / totalData.total) * 100)
      : 0;

  // ── Handlers for bulk actions ──────────────────────────────────────────

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
      alert('Không thể tạo file ZIP: ' + e.message);
    } finally {
      setIsZipping(false);
    }
  };
  const handleDownloadAll = () => {
    const toDownload = successJobs.filter(j => selectedJobIds.has(j.id));
    toDownload.forEach((job, index) => {
      setTimeout(() => {
        handleSmartDownload(job.local_mp3_path || job.local_file_path || job.direct_mp4_url, job.slugified_name);
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
    if (kw.trim().length < 3) { setKwError('Từ khóa phải có ít nhất 3 ký tự.'); return; }
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
        if (code === 'search_rate_limited') setKwError('Bạn đã tìm kiếm quá nhiều. Thử lại sau.');
        else if (code === 'search_no_results') setKwError(`Không tìm thấy video nào cho "${kw}".`);
        else setKwError(data?.detail?.message || data?.detail || 'Tìm kiếm thất bại.');
        return;
      }
      setKwResults(data.results || []);
      setKwCached(data.cached || false);
      if ((data.results || []).length === 0) setKwError('Không tìm thấy kết quả nào.');
    } catch (e) {
      setKwError(e.message || 'Lỗi kết nối.');
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
      if (!res.ok) { setRenameError(data?.detail || 'Tạo ZIP thất bại.'); return; }
      setRenameResult(data);
    } catch (e) {
      setRenameError(e.message || 'Lỗi kết nối.');
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
            <Layers className="w-5 h-5 text-accent-light" />
            <h2 className="text-2xl font-bold text-text-primary tracking-tight">
              Tải Hàng Loạt
            </h2>
          </div>
          <p className="text-sm text-text-muted ml-8">
            Xử lý đồng thời nhiều video hoặc toàn bộ kênh
          </p>
        </div>
        {quotaInfo && ['pro', 'vip'].includes(quotaInfo.plan) && (
          <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-primary/20 to-accent/20 border border-primary/30 rounded-xl shadow-lg shadow-primary/10 animate-in fade-in zoom-in slide-in-from-right-8 duration-500">
            <Crown className="w-4 h-4 text-primary" />
            <span className="text-xs font-bold text-primary uppercase tracking-wider">{quotaInfo.plan} ACTIVE</span>
          </div>
        )}
      </div>

      {/* ── Quick Guide ────────────────────────────────── */}
      <QuickGuideSection activeToolTab={channelMode ? 'channel' : 'bulk'} />

      {/* ── Input Area ────────────────────────────────── */}
      <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-4 gap-4">
          <div>
            <h3 className="text-base font-semibold text-text-primary">
              Dán URL vào đây
            </h3>
            <p className="text-xs text-text-muted mt-0.5">
              Mỗi dòng một link. Có thể dán nhiều URL hoặc import file .txt / .csv.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {/* Quality Selector */}
            <select
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              className="px-4 py-2 rounded-xl text-xs font-semibold border bg-surface border-border text-text-primary focus:outline-none focus:ring-2 focus:ring-primary/50"
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
                    : 'bg-surface border-border text-text-muted hover:text-text-secondary'
                }
              `}
            >
              {autoDownload ? (
                <ToggleRight className="w-4 h-4" />
              ) : (
                <ToggleLeft className="w-4 h-4" />
              )}
              Tự động tải khi xong
            </button>
            
            {/* Mode Tabs */}
            <div className="flex flex-col items-start gap-1">
              <div className="flex gap-1.5 p-1 bg-slate-800/60 rounded-xl border border-slate-700/40">
                {[
                  { key: 'bulk',      label: '📋 Bulk URL',    active: !channelMode && !searchMode && !containerMode },
                  { key: 'channel',   label: '📡 Channel',      active: channelMode && !searchMode && !containerMode },
                  { key: 'search',    label: '🔍 Từ khóa',     active: searchMode && !containerMode },
                  { key: 'container', label: '📂 Browse',       active: containerMode },
                ].map(tab => (
                  <button
                    key={tab.key}
                    onClick={() => {
                      setChannelMode(tab.key === 'channel');
                      setSearchMode(tab.key === 'search');
                      setContainerMode(tab.key === 'container');
                    }}
                    className={`flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                      tab.active
                        ? 'bg-[#FBBF24] text-slate-900'
                        : 'text-slate-400 hover:text-white'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              <p className="text-xs text-text-muted mt-1">
                {channelMode ? 'Bật chế độ này khi bạn dán link kênh/profile thay vì link video đơn.'
                  : searchMode ? 'Tìm video theo từ khóa và chọn để tải.'
                  : containerMode ? 'Xem trước và chọn nội dung trước khi queue tải.'
                  : 'Dán link video trực tiếp để tải hàng loạt.'}
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
                        ? 'opacity-40 cursor-not-allowed bg-surface border-border text-text-muted'
                        : removeWatermark && hasTikTok
                          ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-400 cursor-pointer'
                          : 'bg-surface border-border text-text-muted hover:text-text-secondary cursor-pointer'
                      }
                    `}
                  >
                    {removeWatermark && !isOtherPlatform ? (
                      <ToggleRight className="w-4 h-4" />
                    ) : (
                      <ToggleLeft className="w-4 h-4" />
                    )}
                    <Sparkles className="w-3.5 h-3.5" />
                    Bỏ watermark
                    {hasTikTok && (
                      <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-emerald-500/20 text-emerald-400">TikTok/Douyin</span>
                    )}
                    {isOtherPlatform && (
                      <span className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-bold bg-surface border border-border text-text-muted">Chỉ TikTok</span>
                    )}
                  </button>
                  <p className="text-xs text-text-muted mt-1">
                    {isOtherPlatform ? 'Chỉ áp dụng cho TikTok và Douyin.' : 'Tải bản không có logo/watermark (TikTok, Douyin).'}
                  </p>
                </div>
              );
            })()}
          </div>
        </div>

        {channelMode && (
          <div className="mb-4 p-4 rounded-xl bg-accent/5 border border-accent/20">
            <p className="text-xs text-accent-light flex items-center gap-2 mb-3">
              <Tv className="w-4 h-4" />
              <strong>Channel Mode ON:</strong> Tự động bóc tách và lọc video trong kênh
            </p>
            {/* Platform-specific hint */}
            {(() => {
              const allUrls = urls.toLowerCase();
              if (allUrls.includes('twitter.com') || allUrls.includes('x.com')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-sky-500/10 border border-sky-500/20 text-xs text-sky-300">
                  <strong>Twitter/X:</strong> Nhập @username hoặc link trang cá nhân. Tải video từ Media tab công khai. Giới hạn 100 video/lần — Twitter rate-limit rất chặt với scrape lớn.
                </div>
              );
              if (allUrls.includes('reddit.com')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-orange-500/10 border border-orange-500/20 text-xs text-orange-300">
                  <strong>Reddit:</strong> Nhập link subreddit (r/videos) hoặc user page. Hỗ trợ sort hot/new/top. Chỉ lấy các bài có video. Tối đa 50 video/lần.
                </div>
              );
              if (allUrls.includes('pinterest.com') || allUrls.includes('pin.it')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-xs text-red-300">
                  <strong>Pinterest:</strong> Nhập link board (pinterest.com/user/board-name). Chỉ Video Pin được tải. Board riêng tư sẽ báo lỗi.
                </div>
              );
              if (allUrls.includes('threads.com') || allUrls.includes('threads.net')) return (
                <div className="mb-3 p-2.5 rounded-lg bg-purple-500/10 border border-purple-500/20 text-xs text-purple-300">
                  <strong>Threads:</strong> Nhập @username hoặc link trang cá nhân công khai. Lấy tối đa 100 bài gần đây có media.
                </div>
              );
              return null;
            })()}
            <div>
              <label className="block text-[10px] font-semibold text-text-muted uppercase mb-2">
                Số lượng video muốn tải
              </label>
              <p className="text-xs text-text-muted mb-2">Chọn số video muốn quét từ kênh. Nếu mới dùng, hãy thử 10–50 video trước.</p>
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
                        ? 'bg-accent/20 border-accent/40 text-accent-light shadow-sm'
                        : 'bg-surface border-border text-text-muted hover:text-text-secondary hover:border-border/80'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              {(urls.toLowerCase().includes('twitter.com') || urls.toLowerCase().includes('x.com')) && (
                <p className="text-xs text-yellow-400/80 mt-1">⚠ Twitter rate-limit: &gt;50 video có thể chậm hoặc bị gián đoạn.</p>
              )}
              <input
                type="number"
                min="1"
                max="1000"
                value={customMaxVideos}
                onChange={(e) => { setCustomMaxVideos(e.target.value); setMaxVideos(0); }}
                className="w-full px-3 py-2 bg-surface border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-primary/50"
                placeholder="Hoặc nhập số lượng tuỳ chỉnh..."
              />
              <p className="text-[10px] text-text-muted mt-1.5">
                💡 Video được xử lý theo đợt (10 video/đợt) để tối ưu hiệu suất
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
                <label className="text-[10px] text-slate-500 uppercase font-semibold mb-1 block">Từ khóa</label>
                <input
                  value={kw}
                  onChange={e => setKw(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleKeywordSearch()}
                  placeholder="Nhập từ khóa tìm kiếm..."
                  className="w-full bg-slate-800/60 border border-slate-700/50 rounded-xl px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-[#FBBF24]/40"
                />
              </div>
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-semibold mb-1 block">Nền tảng</label>
                <select value={kwPlatform} onChange={e => setKwPlatform(e.target.value)}
                  className="w-full bg-slate-800/60 border border-slate-700/50 rounded-xl px-2 py-2 text-sm text-white focus:outline-none cursor-pointer">
                  <option value="youtube">YouTube</option>
                  <option value="tiktok">TikTok</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-semibold mb-1 block">Số lượng</label>
                <select value={kwCount} onChange={e => setKwCount(Number(e.target.value))}
                  className="w-full bg-slate-800/60 border border-slate-700/50 rounded-xl px-2 py-2 text-sm text-white focus:outline-none cursor-pointer">
                  {[10, 20, 50].map(n => <option key={n} value={n}>{n} video</option>)}
                </select>
              </div>
            </div>
            <div className="flex gap-2 flex-wrap items-end">
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-semibold mb-1 block">Sắp xếp</label>
                <select value={kwSort} onChange={e => setKwSort(e.target.value)}
                  className="bg-slate-800/60 border border-slate-700/50 rounded-xl px-2 py-2 text-sm text-white focus:outline-none cursor-pointer">
                  <option value="relevance">Liên quan</option>
                  <option value="date">Mới nhất</option>
                  <option value="view_count">Nhiều xem</option>
                </select>
              </div>
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-semibold mb-1 block">Thời lượng tối thiểu (giây)</label>
                <input type="number" value={kwMinDur} onChange={e => setKwMinDur(e.target.value)}
                  placeholder="0"
                  className="w-24 bg-slate-800/60 border border-slate-700/50 rounded-xl px-2 py-2 text-sm text-white focus:outline-none"
                />
              </div>
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-semibold mb-1 block">Thời lượng tối đa (giây)</label>
                <input type="number" value={kwMaxDur} onChange={e => setKwMaxDur(e.target.value)}
                  placeholder="không giới hạn"
                  className="w-32 bg-slate-800/60 border border-slate-700/50 rounded-xl px-2 py-2 text-sm text-white focus:outline-none"
                />
              </div>
              <button
                onClick={handleKeywordSearch}
                disabled={kwLoading || kw.trim().length < 3}
                className="flex items-center gap-2 px-5 py-2 rounded-xl bg-[#FBBF24]/20 text-[#FBBF24] text-sm font-bold border border-[#FBBF24]/30 hover:bg-[#FBBF24]/30 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {kwLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                {kwLoading ? 'Đang tìm...' : 'Tìm kiếm'}
              </button>
            </div>

            {kwError && <p className="text-sm text-red-400">{kwError}</p>}

            {/* Results */}
            {kwResults.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs text-slate-400">
                    Tìm thấy {kwResults.length} video cho "{kw}"{kwCached ? ' (từ cache)' : ''}
                  </p>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setKwSelected(new Set(kwResults.map(r => r.url)))}
                      className="text-[10px] text-slate-400 hover:text-white cursor-pointer"
                    >
                      Chọn tất cả
                    </button>
                    <button
                      onClick={() => setKwSelected(new Set())}
                      className="text-[10px] text-slate-400 hover:text-white cursor-pointer"
                    >
                      Bỏ chọn
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
                            ? 'bg-[#FBBF24]/10 border-[#FBBF24]/40'
                            : 'bg-slate-800/40 border-slate-700/40 hover:border-slate-600/60'
                        }`}
                      >
                        <input type="checkbox" checked={selected} onChange={() => {}} className="w-3.5 h-3.5 accent-[#FBBF24] cursor-pointer flex-shrink-0" />
                        {r.thumbnail_url && (
                          <img src={r.thumbnail_url} alt="" className="w-14 h-9 object-cover rounded-lg flex-shrink-0 bg-slate-700" />
                        )}
                        <div className="flex-1 min-w-0">
                          <p className="text-xs text-white font-medium truncate">{r.title}</p>
                          <div className="flex gap-2 mt-0.5 flex-wrap">
                            {r.creator && <span className="text-[10px] text-slate-400">@{r.creator}</span>}
                            {durStr && <span className="text-[10px] text-slate-500">⏱ {durStr}</span>}
                            {views && <span className="text-[10px] text-slate-500">👁 {views}</span>}
                            {r.upload_date && <span className="text-[10px] text-slate-600">{r.upload_date}</span>}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <button
                  onClick={handleKwAddSelected}
                  disabled={kwSelected.size === 0}
                  className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-gradient-to-r from-[#FBBF24] to-yellow-500 text-slate-900 text-sm font-bold disabled:opacity-50 disabled:cursor-not-allowed hover:opacity-90 transition-opacity cursor-pointer"
                >
                  <Download className="w-4 h-4" />
                  Tải {kwSelected.size} video đã chọn →
                </button>
                <p className="text-[10px] text-center text-slate-500">Các URL sẽ được chuyển sang tab Bulk URL để tải.</p>
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
              bg-surface border border-border
              text-text-primary placeholder-text-muted
              text-sm leading-relaxed cursor-text
              resize-none
              focus:outline-none focus:ring-2 focus:ring-primary/50 focus:border-primary/50
              transition-all duration-200
            "
            disabled={isSubmitting}
          />
          <div className="absolute top-2 right-2 flex gap-1">
            <button
              onClick={() => fileInputRef.current?.click()}
              className="p-1.5 bg-surface-lighter text-text-muted hover:text-accent-light border border-border rounded-lg transition-colors shadow-sm"
              title="Import URL từ file .txt hoặc .csv"
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
                className="p-1.5 bg-surface-lighter text-text-muted hover:text-primary border border-border rounded-lg transition-colors shadow-sm"
                title="Dán từ bộ nhớ tạm"
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
              <span className="text-xs text-amber-400/80 flex items-center gap-1">
                ⚠ {dups} URL trùng sẽ bị bỏ qua
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
                bg-gradient-to-r from-purple-600 to-violet-600
                text-white text-sm font-semibold cursor-pointer
                shadow-lg shadow-purple-500/25
                hover:shadow-xl hover:shadow-purple-500/30
                active:scale-[0.98]
                transition-all duration-200
                disabled:opacity-50 disabled:cursor-not-allowed
              "
            >
              <FolderOpen className="w-4 h-4" />
              Xem trước &amp; chọn nội dung
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={isSubmitting || !urls.trim()}
              className="
                flex items-center gap-2
                px-5 py-2.5 rounded-xl
                bg-gradient-to-r from-primary to-primary-dark
                text-white text-sm font-semibold cursor-pointer
                shadow-lg shadow-primary/25
                hover:shadow-xl hover:shadow-primary/30
                active:scale-[0.98]
                transition-all duration-200
                disabled:opacity-50 disabled:cursor-not-allowed
              "
            >
              {isSubmitting ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Play className="w-4 h-4" />
              )}
              {isSubmitting ? 'Đang gửi...' : 'Bắt Đầu Tải Hàng Loạt'}
            </button>
          )}
        </div>
      </div>

      {/* ── Progress Summary Bar ──────────────────────── */}
      {summary && totalData.total > 0 && (
        <div className="p-5 rounded-2xl bg-surface-card border border-border shadow-lg">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-text-primary">
              Tiến độ xử lý
            </h3>
            <span className="text-xs text-text-muted">
              {totalData.success + totalData.failed} / {totalData.total} hoàn tất
            </span>
          </div>

          {/* Progress Bar */}
          <div className="w-full h-2.5 bg-surface rounded-full overflow-hidden mb-4">
            <div
              className="h-full bg-gradient-to-r from-primary to-accent transition-all duration-500 rounded-full"
              style={{ width: `${progressPct}%` }}
            />
          </div>

          {/* Stat Pills */}
          <div className="flex flex-wrap gap-3">
            <span className="text-xs px-3 py-1.5 rounded-lg bg-surface font-medium text-text-secondary">
              Tổng: <strong className="text-text-primary">{totalData.total}</strong>
            </span>
            {totalData.pending > 0 && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-surface font-medium text-text-muted">
                Chờ: {totalData.pending}
              </span>
            )}
            {totalData.processing > 0 && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-warning/10 font-medium text-warning">
                Đang xử lý: {totalData.processing}
              </span>
            )}
            <span className="text-xs px-3 py-1.5 rounded-lg bg-success/10 font-medium text-success">
              Thành công: {totalData.success}
            </span>
            {totalData.failed > 0 && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-error/10 font-medium text-error">
                Thất bại: {totalData.failed}
              </span>
            )}
            {(summary?.recoverable > 0) && (
              <span className="text-xs px-3 py-1.5 rounded-lg bg-accent/10 font-medium text-accent">
                Có thể khôi phục: {summary.recoverable}
              </span>
            )}
          </div>

          {/* Retry Failed / Recovery Buttons */}
          {totalData.failed > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                onClick={handleRetryFailed}
                disabled={isRetrying}
                className="flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-bold bg-error/10 text-error border border-error/30 hover:bg-error/20 transition-all disabled:opacity-50"
              >
                {isRetrying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                {isRetrying ? 'Đang thêm lại hàng đợi...' : `Thử lại ${totalData.failed} job lỗi`}
              </button>
            </div>
          )}

          {/* Download All Buttons */}
          {successJobs.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 mt-4 pt-4 border-t border-border/50">
              {totalData.total === totalData.success && (
                <div className="mr-auto">
                    {zipJob && zipJob.status === "success" ? (
                       <div className="flex flex-col items-start gap-1.5">
                         <JobActionCell job={zipJob} onDownload={handleSmartDownload} />
                         {zipJob.file_size_mb > 0 && (
                           <span className="text-[11px] font-semibold text-accent-light bg-accent/10 px-2.5 py-1 rounded-lg border border-accent/20 flex items-center gap-1.5">
                             <FileDown className="w-3 h-3" />
                             ZIP: {zipJob.file_size_mb} MB
                             {zipJob.title && zipJob.title.includes('files') && (
                               <span className="text-text-muted">• {zipJob.title.split('—')[1]?.trim()}</span>
                             )}
                           </span>
                         )}
                       </div>
                    ) : (zipJob && (zipJob.status === "processing" || zipJob.status === "pending")) || isZipping ? (
                        <button disabled className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-primary/50 text-white text-xs font-bold uppercase tracking-wider cursor-not-allowed">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          Đang tạo file ZIP...
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
                            className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-gradient-to-r from-primary to-primary-dark shadow-lg hover:shadow-xl text-white text-xs font-bold uppercase tracking-wider transition-all duration-200 cursor-pointer"
                          >
                            <FileDown className="w-4 h-4" />
                            Tải tất cả (ZIP) {zipJob?.error_message ? " (Thử lại)" : ""}
                          </button>
                          {/* Organize by channel toggle */}
                          <label className="flex items-center gap-1.5 cursor-pointer select-none ml-1">
                            <input
                              type="checkbox"
                              checked={organizeByChannel}
                              onChange={e => setOrganizeByChannel(e.target.checked)}
                              className="w-3.5 h-3.5 accent-primary rounded"
                            />
                            <span className="text-[10px] text-text-muted">Chia thư mục theo kênh</span>
                          </label>
                          {/* Show estimated total size before zipping */}
                          {successJobs.length > 0 && (
                            <span className="text-[10px] text-text-muted ml-1">
                              Ước tính: {successJobs.reduce((sum, j) => sum + (j.file_size_mb || 0), 0).toFixed(1)} MB • {successJobs.length} files
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
                        ? 'bg-violet-500/20 text-violet-300 border-violet-500/40'
                        : 'bg-slate-800/60 text-slate-400 border-slate-700/40 hover:text-violet-300 hover:border-violet-500/40'
                    }`}
                  >
                    ✏️ Rename & ZIP
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
                    : 'bg-surface border-border text-text-muted opacity-50 cursor-not-allowed'}
                `}
              >
                <FileDown className="w-4 h-4" />
                Tải {selectedJobIds.size} đã chọn
              </button>
              <button
                onClick={handleExportCSV}
                className="
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/20
                  text-emerald-500 text-xs font-semibold
                  transition-all duration-200 cursor-pointer
                "
              >
                <TableIcon className="w-4 h-4" />
                Xuất CSV
              </button>
              <button
                onClick={handleCopyAll}
                className="
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  bg-accent/10 hover:bg-accent/20 border border-accent/20
                  text-accent-light text-xs font-semibold
                  transition-all duration-200 cursor-pointer
                "
              >
                <Layers className="w-4 h-4" />
                Sao chép link
              </button>
              <button
                onClick={handleOpenAll}
                className="
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  bg-primary/10 hover:bg-primary/20 border border-primary/20
                  text-primary-light text-xs font-semibold
                  transition-all duration-200 cursor-pointer
                "
              >
                <ExternalLink className="w-4 h-4" />
                Mở trong trình duyệt ({successJobs.length})
              </button>
            </div>
          )}

          {/* ── Rename Panel ────────────────────────────────────── */}
          {showRename && batchId && (
            <div className="mt-3 p-4 rounded-xl bg-violet-500/5 border border-violet-500/20 space-y-3">
              <h3 className="text-sm font-bold text-violet-300">✏️ Đổi tên & Tải ZIP</h3>

              {/* Token chips */}
              <div>
                <p className="text-[10px] text-slate-500 mb-1.5">Click token để chèn vào pattern:</p>
                <div className="flex flex-wrap gap-1.5">
                  {TOKEN_CHIPS.map(tok => (
                    <button
                      key={tok}
                      onClick={() => handleInsertToken(tok)}
                      className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-[10px] text-slate-300 hover:border-violet-500/50 hover:text-violet-300 transition-colors cursor-pointer font-mono"
                    >
                      {tok}
                    </button>
                  ))}
                </div>
              </div>

              {/* Pattern input */}
              <div>
                <label className="text-[10px] text-slate-500 uppercase font-semibold mb-1 block">Pattern</label>
                <input
                  value={renamePattern}
                  onChange={e => setRenamePattern(e.target.value)}
                  className="w-full bg-slate-900/60 border border-slate-700 rounded-lg px-3 py-2 text-xs text-slate-200 font-mono focus:outline-none focus:border-violet-500/50"
                />
              </div>

              {/* Live preview */}
              {successJobs.length > 0 && (
                <div>
                  <p className="text-[10px] text-slate-500 uppercase font-semibold mb-1">Xem trước (3 file đầu):</p>
                  <div className="space-y-1">
                    {renamePreview.map((name, i) => (
                      <p key={i} className="text-[11px] text-slate-300 font-mono bg-slate-900/40 px-2 py-1 rounded truncate">
                        {name}
                      </p>
                    ))}
                  </div>
                  <p className="text-[10px] text-slate-600 mt-1">? = giá trị sẽ được điền khi tạo ZIP</p>
                </div>
              )}

              {renameError && <p className="text-xs text-red-400">{renameError}</p>}

              {renameResult && (
                <div className="p-3 rounded-lg bg-violet-500/10 border border-violet-500/30">
                  <p className="text-xs text-violet-300 font-semibold">ZIP đã sẵn sàng! ({renameResult.file_count} files, {renameResult.file_size_mb} MB)</p>
                  <a
                    href={`${API}${renameResult.zip_url.replace('/api/v1', '')}`}
                    download={renameResult.filename}
                    className="mt-2 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-500/20 text-violet-300 text-xs font-bold hover:bg-violet-500/30 transition-colors"
                  >
                    <Download className="w-3.5 h-3.5" /> Tải ZIP đã đổi tên
                  </a>
                </div>
              )}

              <button
                onClick={handleRenameZip}
                disabled={renameLoading || !renamePattern.trim()}
                className="flex items-center gap-2 px-4 py-2 rounded-xl bg-violet-500/20 text-violet-300 text-xs font-bold border border-violet-500/30 hover:bg-violet-500/30 transition-colors cursor-pointer disabled:opacity-50"
              >
                {renameLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : null}
                {renameLoading ? 'Đang tạo ZIP...' : 'Tạo ZIP đổi tên'}
              </button>
            </div>
          )}
        </div>
      )}

      {/* ── Jobs Table ────────────────────────────────── */}
      {(batchId || jobs.length > 0) && (
        <div className="rounded-2xl bg-surface-card border border-border shadow-lg overflow-hidden">
          <div className="px-6 py-4 border-b border-border bg-surface-lighter/30 flex justify-between items-center">
            <div className="flex items-center gap-3">
              <h3 className="font-semibold text-text-primary">
                Danh sách ({isDiscovering ? '...' : displayJobs.length})
              </h3>
              {selectedJobIds.size > 0 && (
                <span className="inline-flex items-center gap-1.5 text-xs font-bold px-3 py-1 rounded-lg bg-primary/15 text-primary border border-primary/25 animate-in fade-in duration-200">
                  <CheckSquare className="w-3 h-3" />
                  Đã chọn {selectedJobIds.size}
                </span>
              )}
            </div>
            <span className="text-xs text-text-muted bg-surface px-2.5 py-1 rounded-md font-mono">
              {batchId ? batchId.slice(0, 8) + '...' : 'Loading...'}
            </span>
          </div>

          {isDiscovering ? (
            <div className="p-8 flex flex-col items-center gap-5">
              {/* Animated radar icon */}
              <div className="relative flex items-center justify-center w-16 h-16">
                <span className="absolute w-16 h-16 rounded-full bg-primary/10 animate-ping" />
                <span className="absolute w-10 h-10 rounded-full bg-primary/15 animate-ping [animation-delay:0.3s]" />
                <Radio className="w-7 h-7 text-primary relative z-10" />
              </div>

              <div className="text-center">
                <p className="text-sm font-semibold text-text-primary mb-1">
                  Đang quét danh sách video từ kênh...
                </p>
                <p className="text-xs text-text-muted">
                  {discoveryJob?.error_message || 'Đang kết nối tới YouTube...'}
                </p>
              </div>

              {/* Elapsed timer */}
              <div className="flex items-center gap-3 text-xs text-text-muted bg-surface border border-border rounded-xl px-5 py-3">
                <Clock className="w-4 h-4 text-primary" />
                <span>Thời gian đã chờ:</span>
                <span className="font-mono font-bold text-text-primary text-sm tabular-nums">
                  {String(Math.floor(discoverElapsed / 60)).padStart(2, '0')}:{String(discoverElapsed % 60).padStart(2, '0')}
                </span>
              </div>

              <p className="text-[11px] text-text-muted text-center max-w-xs leading-relaxed">
                YouTube có thể mất 30–90 giây để tải toàn bộ danh sách video của kênh. Vui lòng chờ trong giây lát.
              </p>
            </div>
          ) : displayJobs.length === 0 ? (
            <div className="p-10 text-center text-text-muted text-sm flex flex-col items-center gap-2">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
              {channelMode ? 'Đang khởi tạo...' : 'Đang tạo jobs...'}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-lighter/50 text-xs uppercase text-text-muted">
                  <tr>
                    <th className="px-6 py-3 font-medium w-8">
                      <input 
                        type="checkbox" 
                        className="rounded border-border bg-surface text-primary focus:ring-primary/50"
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
                    <th className="px-6 py-3 font-medium">URL gốc</th>
                    <th className="px-6 py-3 font-medium">Trạng thái</th>
                    <th className="px-6 py-3 font-medium text-right">Hành động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {displayJobs.map((job, idx) => (
                    <tr
                      key={job.id}
                      className="hover:bg-surface-lighter/20 transition-colors"
                    >
                      <td className="px-6 py-3">
                        <input 
                          type="checkbox" 
                          disabled={job.status !== 'success'}
                          className="rounded border-border bg-surface text-primary focus:ring-primary/50 disabled:opacity-50"
                          checked={selectedJobIds.has(job.id)}
                          onChange={(e) => {
                            const next = new Set(selectedJobIds);
                            if (e.target.checked) next.add(job.id);
                            else next.delete(job.id);
                            setSelectedJobIds(next);
                          }}
                        />
                      </td>
                      <td className="px-6 py-3 text-text-muted text-xs">
                        {idx + 1}
                      </td>
                      <td className="px-6 py-3 max-w-[320px]">
                        <div className="flex items-start gap-2">
                          <div className="mt-0.5">
                            {job.local_mp3_path ? (
                              <Music className="w-4 h-4 text-accent-light flex-shrink-0" />
                            ) : (
                              <Video className="w-4 h-4 text-primary flex-shrink-0" />
                            )}
                          </div>
                          <div className="flex-1 overflow-hidden">
                            <span className="block truncate text-text-primary" title={job.original_url}>
                              {job.original_url}
                            </span>
                            {job.file_size_mb > 0 && job.status === 'success' && (
                              <span className="text-[10px] text-text-muted mt-1 font-medium px-1.5 py-0.5 bg-surface rounded inline-block">
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
          <div className="flex items-center gap-3 px-5 py-3 bg-[#0a1a17]/95 border border-primary/30 rounded-2xl shadow-2xl shadow-black/40 backdrop-blur-xl">
            <div className="flex items-center gap-2 pr-3 border-r border-border">
              <CheckSquare className="w-4 h-4 text-primary" />
              <span className="text-sm font-bold text-white">{selectedJobIds.size}</span>
              <span className="text-xs text-text-muted">video đã chọn</span>
            </div>

            <button
              onClick={handleDownloadAll}
              className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-extrabold shadow-lg hover:shadow-xl hover:scale-105 active:scale-95 transition-all duration-200 cursor-pointer"
            >
              <Download className="w-4 h-4" />
              Tải {selectedJobIds.size} video
            </button>

            <button
              onClick={() => setSelectedJobIds(new Set())}
              className="p-2 rounded-xl text-text-muted hover:text-white hover:bg-white/10 transition-colors cursor-pointer"
              title="Bỏ chọn tất cả"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
