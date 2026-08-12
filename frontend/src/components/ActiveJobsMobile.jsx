import { useState, useEffect, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import { RefreshCw, Download, RotateCcw, Home, Loader2 } from 'lucide-react';

const PLATFORM_MAP = {
  tiktok:    { emoji: '🎵', label: 'TikTok' },
  instagram: { emoji: '📸', label: 'Instagram' },
  youtube:   { emoji: '▶️', label: 'YouTube' },
  facebook:  { emoji: '🎬', label: 'Facebook' },
  spotify:   { emoji: '🎵', label: 'Spotify' },
  soundcloud:{ emoji: '🎶', label: 'SoundCloud' },
  twitter:   { emoji: '🐦', label: 'X/Twitter' },
  x:         { emoji: '🐦', label: 'X/Twitter' },
};

function getPlatform(platform) {
  const key = (platform || '').toLowerCase();
  return PLATFORM_MAP[key] || { emoji: '🌐', label: 'Video' };
}

function getElapsed(createdAt) {
  const diff = Date.now() - new Date(createdAt).getTime();
  const secs = Math.floor(diff / 1000);
  if (secs < 10) return 'vừa xong';
  if (secs < 60) return `${secs} giây trước`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins} phút trước`;
  const hrs = Math.floor(mins / 60);
  return `${hrs} giờ trước`;
}

function formatBytes(bytes) {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function StatusChip({ status }) {
  if (status === 'processing' || status === 'pending') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-amber-500/20 text-amber-400 text-[10px] font-semibold">
        <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
        {status === 'pending' ? 'Chờ xử lý' : 'Đang xử lý'}
      </span>
    );
  }
  if (status === 'completed') {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-400 text-[10px] font-semibold">
        <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
        Hoàn thành
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-red-500/20 text-red-400 text-[10px] font-semibold">
      <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
      Thất bại
    </span>
  );
}

function JobCard({ job, onRetry }) {
  const plat = getPlatform(job.platform);
  const isActive = job.status === 'processing' || job.status === 'pending';
  const isDone = job.status === 'completed';
  const isFailed = job.status === 'failed';

  return (
    <div className="bg-[#0d2821]/80 border border-slate-700/40 rounded-2xl p-4 flex gap-3">
      {/* Platform icon */}
      <div className="flex-shrink-0 w-10 h-10 rounded-xl bg-slate-700/40 flex items-center justify-center text-xl">
        {plat.emoji}
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <p className="text-slate-100 text-sm font-medium line-clamp-2 leading-snug">
          {job.title || job.original_url || 'Video không có tiêu đề'}
        </p>

        <div className="flex items-center gap-2 mt-1.5 flex-wrap">
          <StatusChip status={job.status} />
          {job.file_size > 0 && isDone && (
            <span className="text-slate-500 text-[10px]">{formatBytes(job.file_size)}</span>
          )}
          <span className="text-slate-500 text-[10px]">{getElapsed(job.created_at)}</span>
        </div>

        {/* Progress bar for active jobs */}
        {isActive && (
          <div className="mt-2 h-1 rounded-full bg-slate-700/60 overflow-hidden">
            <div className="h-full bg-gradient-to-r from-[#FBBF24] to-[#FB923C] rounded-full animate-[indeterminate_1.5s_ease-in-out_infinite]"
              style={{ width: '40%', animation: 'indeterminate 1.5s ease-in-out infinite' }} />
          </div>
        )}

        {/* Actions */}
        <div className="mt-2 flex gap-2">
          {isDone && job.download_url && (
            <a
              href={job.download_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#FBBF24]/15 border border-[#FBBF24]/30 text-[#FBBF24] text-xs font-semibold hover:bg-[#FBBF24]/25 transition-colors"
            >
              <Download className="w-3.5 h-3.5" />
              Tải về
            </a>
          )}
          {isFailed && job.original_url && (
            <button
              onClick={() => onRetry(job.original_url)}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-700/50 border border-slate-600/40 text-slate-300 text-xs font-semibold hover:bg-slate-700 transition-colors"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              Thử lại
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({ title, jobs, onRetry }) {
  if (!jobs.length) return null;
  return (
    <div>
      <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider px-1 mb-2">{title}</h3>
      <div className="flex flex-col gap-3">
        {jobs.map((job) => (
          <JobCard key={job.id} job={job} onRetry={onRetry} />
        ))}
      </div>
    </div>
  );
}

export default function ActiveJobsMobile({ onNavigate, onActiveCountChange }) {
  const { isAuthenticated, session } = useAuth();
  const [jobs, setJobs] = useState([]);
  const [loadingState, setLoadingState] = useState('idle'); // idle | loading | loaded | error
  const [refreshing, setRefreshing] = useState(false);

  const fetchJobs = useCallback(async (opts = {}) => {
    if (!isAuthenticated || !session?.access_token) return;
    if (!opts.silent) setLoadingState('loading');
    try {
      const res = await fetch('/api/v1/history?limit=30', {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      if (!res.ok) throw new Error('fetch failed');
      const data = await res.json();
      setJobs(Array.isArray(data) ? data : []);
      setLoadingState('loaded');
    } catch {
      setLoadingState((prev) => (prev === 'loading' ? 'error' : prev));
    } finally {
      setRefreshing(false);
    }
  }, [isAuthenticated, session]);

  // Initial fetch
  useEffect(() => {
    fetchJobs();
  }, [fetchJobs]);

  // Poll every 8 seconds when there are active jobs
  useEffect(() => {
    const hasActive = jobs.some(
      (j) => j.status === 'processing' || j.status === 'pending'
    );
    if (!hasActive) return;
    const timer = setInterval(() => fetchJobs({ silent: true }), 8000);
    return () => clearInterval(timer);
  }, [jobs, fetchJobs]);

  const handleRefresh = () => {
    setRefreshing(true);
    fetchJobs({ silent: true });
  };

  const handleRetry = (url) => {
    window.dispatchEvent(new CustomEvent('vidgrab:share-url', { detail: { url } }));
    onNavigate('landing', '/');
  };

  // Segment jobs
  const now = Date.now();
  const TWO_HOURS = 2 * 60 * 60 * 1000;
  const ONE_DAY = 24 * 60 * 60 * 1000;

  const activeJobs = jobs.filter(
    (j) => j.status === 'processing' || j.status === 'pending'
  );

  // Bubble active count up to App.jsx for MobileTabBar badge
  useEffect(() => {
    onActiveCountChange?.(activeJobs.length);
  }, [activeJobs.length, onActiveCountChange]);
  const recentDone = jobs
    .filter(
      (j) => j.status === 'completed' && now - new Date(j.created_at).getTime() < TWO_HOURS
    )
    .slice(0, 5);
  const failedJobs = jobs.filter(
    (j) => j.status === 'failed' && now - new Date(j.created_at).getTime() < ONE_DAY
  );

  const totalVisible = activeJobs.length + recentDone.length + failedJobs.length;

  // Auth guard
  if (!isAuthenticated) {
    return (
      <div className="min-h-screen bg-[#012622] flex flex-col items-center justify-center gap-4 px-6">
        <div className="text-4xl">🔐</div>
        <p className="text-slate-300 text-center text-sm">Đăng nhập để xem lịch sử tải</p>
        <button
          onClick={() => onNavigate('landing', '/')}
          className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-bold"
        >
          Về trang chủ
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[#012622] px-4 py-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-bold text-slate-100">Tác vụ tải</h1>
          {activeJobs.length > 0 && (
            <p className="text-xs text-slate-400 mt-0.5">{activeJobs.length} đang xử lý</p>
          )}
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-[#0d2821]/80 border border-slate-700/40 text-slate-300 text-xs font-medium hover:bg-slate-700/30 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          Làm mới
        </button>
      </div>

      {/* Loading skeleton */}
      {loadingState === 'loading' && (
        <div className="flex flex-col gap-3">
          {[1, 2, 3].map((i) => (
            <div
              key={i}
              className="bg-[#0d2821]/80 border border-slate-700/40 rounded-2xl p-4 flex gap-3 animate-pulse"
            >
              <div className="w-10 h-10 rounded-xl bg-slate-700/50 flex-shrink-0" />
              <div className="flex-1 space-y-2">
                <div className="h-3.5 bg-slate-700/50 rounded w-3/4" />
                <div className="h-3 bg-slate-700/40 rounded w-1/3" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Error */}
      {loadingState === 'error' && (
        <div className="text-center py-12 text-slate-400 text-sm">
          <p>Không thể tải dữ liệu. Vui lòng thử lại.</p>
          <button
            onClick={handleRefresh}
            className="mt-3 px-4 py-2 rounded-xl border border-slate-600 text-xs hover:bg-slate-700/30 transition-colors"
          >
            Thử lại
          </button>
        </div>
      )}

      {/* Empty state */}
      {loadingState === 'loaded' && totalVisible === 0 && (
        <div className="flex flex-col items-center justify-center py-16 gap-4 text-center">
          <div className="text-5xl">📭</div>
          <div>
            <p className="text-slate-300 font-medium">Chưa có tác vụ nào đang xử lý</p>
            <p className="text-slate-500 text-sm mt-1">Tải video để xem tiến trình ở đây</p>
          </div>
          <button
            onClick={() => onNavigate('landing', '/')}
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] text-sm font-bold shadow-md"
          >
            <Home className="w-4 h-4" />
            Về trang chủ
          </button>
        </div>
      )}

      {/* Job sections */}
      {loadingState === 'loaded' && totalVisible > 0 && (
        <div className="flex flex-col gap-6">
          <Section title="Đang xử lý" jobs={activeJobs} onRetry={handleRetry} />
          <Section title="Vừa xong" jobs={recentDone} onRetry={handleRetry} />
          <Section title="Thất bại" jobs={failedJobs} onRetry={handleRetry} />
        </div>
      )}
    </div>
  );
}
