import { useState, useEffect, useCallback, useRef } from 'react';
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
  ClipboardPaste
} from 'lucide-react';

import UpgradeModal from './UpgradeModal';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

// ── Countdown Timer Component ──────────────────────────────────────
const JobActionCell = ({ job, onDownload }) => {
  const [expired, setExpired] = useState(false);
  const [timeLeft, setTimeLeft] = useState(null);

  useEffect(() => {
    if (job.status !== 'success' || !job.created_at) return;
    
    // expiry is created_at + 15 minutes
    const expiryTime = new Date(job.created_at).getTime() + 15 * 60 * 1000;
    
    const tick = () => {
      const diff = expiryTime - Date.now();
      if (diff <= 0) {
        setExpired(true);
        setTimeLeft(0);
      } else {
        setTimeLeft(diff);
      }
    };
    
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [job]);

  if (job.status === 'failed') return <span className="text-xs text-text-muted">-</span>;
  if (job.status !== 'success') return <span className="text-xs text-text-muted">Đang chờ...</span>;

  const hasLink = job.direct_mp4_url || job.local_mp3_path || job.local_file_path;
  if (!hasLink) return <span className="text-xs text-text-muted">-</span>;

  if (expired) {
    return <span className="text-[11px] text-error italic border border-error/20 bg-error/5 px-2 py-1 rounded">Link đã hết hạn - Vui lòng tải lại</span>;
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
      {timeLeft !== null && (
        <span className="text-[10px] text-error flex items-center gap-1 font-mono bg-error/10 px-1.5 py-0.5 rounded border border-error/20">
          <Clock className="w-2.5 h-2.5" />
          {m}:{s < 10 ? '0' + s : s}
        </span>
      )}
    </div>
  );
};

export default function BulkContent() {
  const [urls, setUrls] = useState('');
  const [batchId, setBatchId] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('batch') || null;
  });
  const [jobs, setJobs] = useState([]);
  const [summary, setSummary] = useState(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [channelMode, setChannelMode] = useState(false);
  const [showUpgradeModal, setShowUpgradeModal] = useState(false);
  const [isZipping, setIsZipping] = useState(false);
  
  // New States
  const [maxVideos, setMaxVideos] = useState(20);
  const [minViews, setMinViews] = useState(0);
  const [autoDownload, setAutoDownload] = useState(false);
  const [quality, setQuality] = useState('video');
  const [quotaInfo, setQuotaInfo] = useState(null);
  const [selectedJobIds, setSelectedJobIds] = useState(new Set());
  const autoDownloadedRefs = useRef(new Set());
  const [refreshTrigger, setRefreshTrigger] = useState(0);

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
            const spData = await spRes.json();
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

      const res = await fetch(`${API}/bulk-download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          urls: urlList, 
          channel_mode: channelMode,
          max_videos: parseInt(maxVideos) || 20,
          min_views: parseInt(minViews) || 0,
          quality: quality
        }),
      });
      const data = await res.json();
      
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

  // ── Polling ────────────────────────────────────────────────────────
  useEffect(() => {
    let intervalId;

    if (batchId) {
      const fetchJobs = async () => {
        try {
          const res = await fetch(`${API}/jobs/${batchId}`);
          const data = await res.json();
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
              clearInterval(intervalId);
            }
          }
        } catch (e) {
          console.error('Polling error', e);
        }
      };

      fetchJobs();
      intervalId = setInterval(fetchJobs, 3000);
    }

    return () => clearInterval(intervalId);
  }, [batchId, autoDownload, handleSmartDownload, refreshTrigger]);

  // ── Status Badge ───────────────────────────────────────────────────
  const getStatusBadge = (status) => {
    switch (status) {
      case 'pending':
        return (
          <span className="inline-flex items-center gap-1 text-text-muted bg-surface/50 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Clock className="w-3 h-3" /> Chờ xử lý
          </span>
        );
      case 'processing':
        return (
          <span className="inline-flex items-center gap-1 text-warning bg-warning/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Loader2 className="w-3 h-3 animate-spin" /> Đang xử lý
          </span>
        );
      case 'success':
        return (
          <span className="inline-flex items-center gap-1 text-success bg-success/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <CheckCircle2 className="w-3 h-3" /> Thành công
          </span>
        );
      case 'failed':
        return (
          <span className="inline-flex items-center gap-1 text-error bg-error/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <XCircle className="w-3 h-3" /> Thất bại
          </span>
        );
      default:
        return status;
    }
  };

  const displayJobs = jobs.filter(j => j.original_url !== 'batch_zip');
  const zipJob = jobs.find(j => j.original_url === 'batch_zip');
  
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
  
  const handleCreateZip = async () => {
    if (!batchId) return;
    setIsZipping(true);
    try {
      const res = await fetch(`${API}/bulk-zip`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ batch_id: batchId })
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

  return (
    <div className="space-y-8">
      <UpgradeModal isOpen={showUpgradeModal} onClose={() => setShowUpgradeModal(false)} />
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

      {/* ── Input Area ────────────────────────────────── */}
      <div className="p-6 rounded-2xl bg-surface-card border border-border shadow-lg">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between mb-4 gap-4">
          <div>
            <h3 className="text-base font-semibold text-text-primary">
              Dán URL vào đây
            </h3>
            <p className="text-xs text-text-muted mt-0.5">
              Link video, kênh, hoặc playlist — mỗi dòng một link
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
            
            {/* Channel Mode Toggle */}
            <button
              onClick={() => setChannelMode(!channelMode)}
              className={`
                flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold
                border transition-all duration-200 cursor-pointer
                ${
                  channelMode
                    ? 'bg-accent/15 border-accent/30 text-accent-light'
                    : 'bg-surface border-border text-text-muted hover:text-text-secondary'
                }
              `}
            >
              {channelMode ? (
                <ToggleRight className="w-4 h-4" />
              ) : (
                <ToggleLeft className="w-4 h-4" />
              )}
              <Tv className="w-3.5 h-3.5" />
              Channel Mode
            </button>
          </div>
        </div>

        {channelMode && (
          <div className="mb-4 p-4 rounded-xl bg-accent/5 border border-accent/20">
            <p className="text-xs text-accent-light flex items-center gap-2 mb-3">
              <Tv className="w-4 h-4" />
              <strong>Channel Mode ON:</strong> Tự động bóc tách và lọc video trong kênh
            </p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-[10px] font-semibold text-text-muted uppercase mb-1">
                  Giới hạn video
                </label>
                <input 
                  type="number" 
                  min="1"
                  value={maxVideos}
                  onChange={(e) => setMaxVideos(e.target.value)}
                  className="w-full px-3 py-2 bg-surface border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-primary/50"
                />
              </div>
              <div>
                <label className="block text-[10px] font-semibold text-text-muted uppercase mb-1">
                  Lượt xem tối thiểu
                </label>
                <input 
                  type="number" 
                  min="0"
                  value={minViews}
                  onChange={(e) => setMinViews(e.target.value)}
                  className="w-full px-3 py-2 bg-surface border border-border rounded-lg text-sm text-text-primary focus:outline-none focus:border-primary/50"
                  placeholder="Ví dụ: 10000"
                />
              </div>
            </div>
          </div>
        )}

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
              className="absolute top-2 right-2 p-1.5 bg-surface-lighter text-text-muted hover:text-primary border border-border rounded-lg transition-colors shadow-sm"
              title="Dán từ bộ nhớ tạm"
            >
              <ClipboardPaste className="w-4 h-4" />
            </button>
          )}
        </div>

        <div className="flex justify-end mt-4">
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
          </div>

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
                        <div className="flex flex-col items-start gap-1.5">
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
              
              <button
                onClick={handleDownloadAll}
                className="
                  flex items-center gap-2 px-4 py-2.5 rounded-xl
                  bg-success/10 hover:bg-success/20 border border-success/20
                  text-success text-xs font-semibold
                  transition-all duration-200 cursor-pointer
                "
              >
                <FileDown className="w-4 h-4" />
                Tải từng file
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
        </div>
      )}

      {/* ── Jobs Table ────────────────────────────────── */}
      {(batchId || jobs.length > 0) && (
        <div className="rounded-2xl bg-surface-card border border-border shadow-lg overflow-hidden">
          <div className="px-6 py-4 border-b border-border bg-surface-lighter/30 flex justify-between items-center">
            <h3 className="font-semibold text-text-primary">
              Danh sách ({jobs.length})
            </h3>
            <span className="text-xs text-text-muted bg-surface px-2.5 py-1 rounded-md font-mono">
              {batchId ? batchId.slice(0, 8) + '...' : 'Loading...'}
            </span>
          </div>

          {displayJobs.length === 0 ? (
            <div className="p-10 text-center text-text-muted text-sm flex flex-col items-center gap-2">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
              {channelMode
                ? 'Discovering videos from channel...'
                : 'Initializing batch jobs...'}
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
                      <td className="px-6 py-3">{getStatusBadge(job.status)}</td>
                      <td className="px-6 py-3 text-right whitespace-nowrap">
                        <JobActionCell job={job} onDownload={handleSmartDownload} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
