import { useState, useEffect, useCallback } from 'react';
import {
  History,
  Search,
  Filter,
  FileVideo,
  Download,
  Trash2,
  CheckCircle2,
  XCircle,
  Loader2,
  Clock,
  RefreshCw,
  Music,
  Video,
  ExternalLink,
  Eraser,
  Copy,
  Check,
  FileDown,
  RotateCcw,
} from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_URL || '';

export default function HistoryContent() {
  const [jobs, setJobs] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all'); // all, success, failed
  const [platformFilter, setPlatformFilter] = useState('all');
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [copiedId, setCopiedId] = useState(null);
  const [exportLoading, setExportLoading] = useState(false);
  const [filterTab, setFilterTab] = useState('all');
  const [isOfflineCached, setIsOfflineCached] = useState(false);
  const PER_PAGE = 20;

  const PLATFORMS = ['all', 'youtube', 'tiktok', 'instagram', 'facebook', 'twitter', 'threads', 'reddit', 'other'];

  const applyTabFilter = (items) => {
    if (!items || !items.length) return items;
    const now = Date.now();
    const todayStart = new Date(); todayStart.setHours(0,0,0,0);
    const weekAgo = now - 7 * 24 * 60 * 60 * 1000;
    if (filterTab === 'today') return items.filter(i => new Date(i.created_at || i.timestamp || i.createdAt).getTime() >= todayStart.getTime());
    if (filterTab === 'recent') return items.filter(i => new Date(i.created_at || i.timestamp || i.createdAt).getTime() >= weekAgo);
    if (filterTab === 'failed') return items.filter(i => (i.status || '').toLowerCase().includes('fail') || !!i.error);
    return items;
  };

  const fetchHistory = useCallback(async (pageNum = 1, append = false) => {
    setIsLoading(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/history?limit=${PER_PAGE}&offset=${(pageNum - 1) * PER_PAGE}`);
      const data = await res.json();
      if (data.success && data.jobs) {
        if (append) {
          setJobs(prev => [...prev, ...data.jobs]);
        } else {
          setJobs(data.jobs);
          try { localStorage.setItem('vg_history_cache', JSON.stringify({ data: data, ts: Date.now() })); } catch {}
        }
        setHasMore(data.jobs.length >= PER_PAGE);
      }
    } catch (err) {
      console.error('Failed to load history', err);
      try {
        const cached = JSON.parse(localStorage.getItem('vg_history_cache') || 'null');
        if (cached?.data) {
          const items = cached.data.jobs || cached.data.items || cached.data.downloads || cached.data;
          if (Array.isArray(items)) { setJobs(items); setIsOfflineCached(true); }
        }
      } catch {}
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHistory(1);
  }, [fetchHistory]);

  const handleDeleteJob = async (jobId) => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/history/${jobId}`, { method: 'DELETE' });
      if (res.ok) {
        setJobs(prev => prev.filter(j => j.id !== jobId));
      }
    } catch { /* ignore */ }
  };

  const handleClearAll = async () => {
    if (!window.confirm('Bạn có chắc chắn muốn xóa tất cả lịch sử?')) return;
    try {
      const res = await fetch(`${API_BASE}/api/v1/history/all`, { method: 'DELETE' });
      if (res.ok) {
        setJobs([]);
      }
    } catch { /* ignore */ }
  };

  const handleLoadMore = () => {
    const next = page + 1;
    setPage(next);
    fetchHistory(next, true);
  };

  const handleSmartDownload = (url, slug) => {
    try {
      let downloadUrl = url;
      if (url.includes('proxy-download') || url.includes('download-local')) {
        downloadUrl = url;
      } else if (url.includes(':/') || url.startsWith('http')) {
        downloadUrl = `${API_BASE}/api/v1/proxy-download?url=${encodeURIComponent(url)}&filename=${encodeURIComponent(slug || 'video')}`;
      } else {
        const fileExt = url.split('.').pop() || 'mp4';
        downloadUrl = `${API_BASE}/api/v1/download-local?filepath=${encodeURIComponent(url)}&filename=${encodeURIComponent(slug || 'video')}.${fileExt}`;
      }
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.setAttribute('download', '');
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch {
      window.open(url, '_blank');
    }
  };

  const handleExport = async (format) => {
    setExportLoading(true);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/user/history/export?format=${format}&range_days=365`,
        { credentials: 'include' }
      );
      if (!res.ok) { alert('Xuất thất bại — bạn cần đăng nhập.'); return; }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `vidgrab-history.${format}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      alert('Xuất thất bại.');
    } finally {
      setExportLoading(false);
    }
  };

  const handleCopyUrl = async (jobId, url) => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = url;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    setCopiedId(jobId);
    setTimeout(() => setCopiedId(null), 2000);
  };

  const handleRerun = (url) => {
    window.dispatchEvent(new CustomEvent('vidgrab:share-url', { detail: { url } }));
    window.history.pushState({}, '', '/');
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  const getExpiryBadge = (job) => {
    const expiryDate = job.link_expires_at || job.file_expires_at;
    if (!expiryDate) return null;
    const ms = new Date(expiryDate) - Date.now();
    if (ms < 0) {
      return (
        <span className="text-[10px] bg-red-500/20 text-red-400 border border-red-500/30 px-1.5 py-0.5 rounded font-semibold">
          Hết hạn
        </span>
      );
    }
    const days = Math.floor(ms / 86400000);
    if (days <= 3) {
      return (
        <span className="text-[10px] bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 px-1.5 py-0.5 rounded font-semibold">
          Còn {days}d
        </span>
      );
    }
    return null;
  };

  // Filter & Search
  const filtered = jobs.filter(j => {
    if (filter === 'success' && j.status !== 'success') return false;
    if (filter === 'failed' && j.status !== 'failed') return false;
    if (platformFilter !== 'all' && (j.platform || '').toLowerCase() !== platformFilter) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      return (
        (j.title || '').toLowerCase().includes(q) ||
        (j.original_url || '').toLowerCase().includes(q) ||
        (j.batch_id || '').toLowerCase().includes(q)
      );
    }
    return true;
  });

  const getStatusBadge = (status) => {
    switch (status) {
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
      case 'abandoned':
        return (
          <span className="inline-flex items-center gap-1 text-error bg-error/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <XCircle className="w-3 h-3" /> Đã từ bỏ
          </span>
        );
      case 'processing':
        return (
          <span className="inline-flex items-center gap-1 text-warning bg-warning/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Loader2 className="w-3 h-3 animate-spin" /> Đang xử lý
          </span>
        );
      case 'cancelled':
        return (
          <span className="inline-flex items-center gap-1 text-text-muted bg-surface/50 px-2.5 py-1 rounded-lg text-xs font-medium">
            <XCircle className="w-3 h-3" /> Đã hủy
          </span>
        );
      case 'expired':
        return (
          <span className="inline-flex items-center gap-1 text-orange-400 bg-orange-500/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Clock className="w-3 h-3" /> Hết hạn
          </span>
        );
      case 'metadata_only':
        return (
          <span className="inline-flex items-center gap-1 text-text-muted bg-surface/50 px-2.5 py-1 rounded-lg text-xs font-medium">
            <ExternalLink className="w-3 h-3" /> Chỉ metadata
          </span>
        );
      case 'archived':
        return (
          <span className="inline-flex items-center gap-1 text-accent-light bg-accent/10 px-2.5 py-1 rounded-lg text-xs font-medium">
            <CheckCircle2 className="w-3 h-3" /> Đã lưu trữ
          </span>
        );
      case 'queued':
        return (
          <span className="inline-flex items-center gap-1 text-text-muted bg-surface/50 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Clock className="w-3 h-3" /> Trong hàng đợi
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 text-text-muted bg-surface/50 px-2.5 py-1 rounded-lg text-xs font-medium">
            <Clock className="w-3 h-3" /> {status === 'pending' ? 'Chờ xử lý' : 'Chờ'}
          </span>
        );
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '--';
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now - d;
    if (diff < 60000) return 'Vừa xong';
    if (diff < 3600000) return `${Math.floor(diff / 60000)} phút trước`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)} giờ trước`;
    return d.toLocaleDateString('vi-VN', { day: '2-digit', month: '2-digit', year: 'numeric' });
  };

  return (
    <div className="space-y-6">
      {/* ── Header ────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <History className="w-5 h-5 text-accent-light" />
            <h2 className="text-2xl font-bold text-text-primary tracking-tight">
              Lịch sử tải xuống
            </h2>
          </div>
          <p className="text-sm text-text-muted ml-8">
            Xem và quản lý tất cả các lần tải trước đây
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => { setPage(1); fetchHistory(1); }}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-surface border border-border text-text-muted hover:text-text-primary text-xs font-semibold transition-colors cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Làm mới
          </button>
          {jobs.length > 0 && (
            <>
              <div className="relative group">
                <button
                  disabled={exportLoading}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-primary/10 border border-primary/30 text-primary text-xs font-semibold hover:bg-primary/20 transition-colors cursor-pointer disabled:opacity-50"
                >
                  <FileDown className="w-3.5 h-3.5" /> Xuất
                </button>
                <div className="absolute right-0 top-full mt-1 hidden group-hover:flex group-focus-within:flex flex-col z-20 bg-surface-card border border-border rounded-xl shadow-lg overflow-hidden min-w-[100px]">
                  <button onClick={() => handleExport('csv')} className="px-4 py-2 text-xs text-text-secondary hover:bg-surface-lighter/50 text-left">CSV</button>
                  <button onClick={() => handleExport('json')} className="px-4 py-2 text-xs text-text-secondary hover:bg-surface-lighter/50 text-left">JSON</button>
                </div>
              </div>
              <button
                onClick={handleClearAll}
                className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-error/10 border border-error/20 text-error text-xs font-semibold hover:bg-error/20 transition-colors cursor-pointer"
              >
                <Trash2 className="w-3.5 h-3.5" /> Xóa tất cả
              </button>
            </>
          )}
        </div>
      </div>

      {/* ── Tab Filter ───────────────────────────────── */}
      <div className="flex gap-2 mb-4 flex-wrap">
        {[['all', 'Tất cả'], ['today', 'Hôm nay'], ['recent', '7 ngày'], ['failed', 'Thất bại']].map(([key, label]) => (
          <button key={key} onClick={() => setFilterTab(key)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${filterTab === key ? 'bg-indigo-600 text-white' : 'bg-white/5 text-white/50 hover:bg-white/10'}`}>
            {label}
          </button>
        ))}
      </div>

      {/* ── Search & Filter Bar ───────────────────────── */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Tìm theo URL, tiêu đề hoặc Batch ID..."
              className="
                w-full pl-10 pr-4 py-2.5 rounded-xl
                bg-surface border border-border
                text-text-primary placeholder-text-muted
                text-sm
                focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50
                transition-all duration-200
              "
            />
          </div>
          <div className="flex items-center gap-1 p-1 bg-surface border border-border rounded-xl">
            {[
              { key: 'all', label: 'Tất cả' },
              { key: 'success', label: 'Thành công' },
              { key: 'failed', label: 'Thất bại' },
            ].map(f => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-all cursor-pointer ${
                  filter === f.key
                    ? 'bg-primary/20 text-primary border border-primary/30'
                    : 'text-text-muted hover:text-text-secondary border border-transparent'
                }`}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        {/* Platform filter */}
        <div className="flex items-center gap-2 overflow-x-auto pb-1">
          <Filter className="w-3.5 h-3.5 text-text-muted flex-shrink-0" />
          {PLATFORMS.map(p => (
            <button
              key={p}
              onClick={() => setPlatformFilter(p)}
              className={`px-2.5 py-1 rounded-lg text-xs font-semibold whitespace-nowrap transition-all cursor-pointer ${
                platformFilter === p
                  ? 'bg-accent/20 text-accent-light border border-accent/30'
                  : 'text-text-muted hover:text-text-secondary border border-transparent'
              }`}
            >
              {p === 'all' ? 'Tất cả nền tảng' : p.charAt(0).toUpperCase() + p.slice(1)}
            </button>
          ))}
        </div>
      </div>

      {/* ── Table ──────────────────────────────────────── */}
      {isOfflineCached && (
        <div className="flex items-center gap-2 bg-yellow-500/10 border border-yellow-500/30 rounded-lg px-3 py-2 mb-3 text-xs text-yellow-400">
          <span>📵</span> Đang hiển thị dữ liệu đã lưu — kết nối lại để cập nhật
        </div>
      )}
      <div className="rounded-2xl bg-surface-card border border-border shadow-lg overflow-hidden">
        {isLoading && jobs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <Loader2 className="w-6 h-6 animate-spin text-primary mb-3" />
            <p className="text-sm text-text-muted">Đang tải lịch sử...</p>
          </div>
        ) : filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-16 h-16 rounded-2xl bg-surface flex items-center justify-center mb-4 border border-border">
              <FileVideo className="w-7 h-7 text-text-muted" />
            </div>
            <p className="text-sm text-text-secondary font-medium">
              {search ? 'Không tìm thấy kết quả' : 'Chưa có lịch sử tải xuống'}
            </p>
            <p className="text-xs text-text-muted mt-1">
              {search ? 'Thử từ khóa khác' : 'Các file đã tải xong sẽ hiển thị ở đây'}
            </p>
          </div>
        ) : (
          <>
            {/* Desktop Table */}
            <div className="hidden md:block overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-lighter/50 text-xs uppercase text-text-muted">
                  <tr>
                    <th className="px-6 py-3 font-medium w-8">#</th>
                    <th className="px-6 py-3 font-medium">Tiêu đề / URL</th>
                    <th className="px-6 py-3 font-medium">Trạng thái</th>
                    <th className="px-6 py-3 font-medium">Thời gian</th>
                    <th className="px-6 py-3 font-medium text-right">Hành động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/50">
                  {applyTabFilter(filtered).map((job, idx) => {
                    const hasDownload = job.status === 'success' && (job.direct_mp4_url || job.local_mp3_path || job.local_file_path);
                    return (
                      <tr key={job.id} className="hover:bg-surface-lighter/20 transition-colors">
                        <td className="px-6 py-3 text-text-muted text-xs">{idx + 1}</td>
                        <td className="px-6 py-3 max-w-[360px]">
                          <div className="flex items-start gap-2">
                            <div className="mt-0.5">
                              {job.local_mp3_path ? (
                                <Music className="w-4 h-4 text-accent-light flex-shrink-0" />
                              ) : job.source_surface === 'logo_inpaint' || job.job_kind === 'visible_logo_cleanup' ? (
                                <Eraser className="w-4 h-4 text-violet-400 flex-shrink-0" />
                              ) : (
                                <Video className="w-4 h-4 text-primary flex-shrink-0" />
                              )}
                            </div>
                            <div className="flex-1 overflow-hidden">
                              {job.title && (
                                <span className="block truncate text-text-primary font-medium text-sm" title={job.title}>
                                  {job.title}
                                </span>
                              )}
                              {(job.source_surface === 'logo_inpaint' || job.job_kind === 'visible_logo_cleanup') && (
                                <span className="inline-block text-[10px] bg-violet-500/20 text-violet-300 border border-violet-500/30 px-1.5 py-0.5 rounded font-semibold mr-1 mb-0.5">
                                  Inpainted
                                </span>
                              )}
                              <span className="block truncate text-text-muted text-xs" title={job.original_url}>
                                {job.original_url}
                              </span>
                              <div className="flex items-center gap-1 mt-0.5 flex-wrap">
                                {job.file_size_mb > 0 && (
                                  <span className="text-[10px] text-text-muted font-medium px-1.5 py-0.5 bg-surface rounded inline-block">
                                    {job.file_size_mb} MB
                                  </span>
                                )}
                                {getExpiryBadge(job)}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-3">{getStatusBadge(job.status)}</td>
                        <td className="px-6 py-3 text-text-muted text-xs whitespace-nowrap">
                          {formatDate(job.created_at)}
                        </td>
                        <td className="px-6 py-3 text-right whitespace-nowrap">
                          <div className="flex items-center justify-end gap-2">
                            {hasDownload && (
                              <button
                                onClick={() => handleSmartDownload(
                                  job.local_mp3_path || job.local_file_path || job.direct_mp4_url,
                                  job.slugified_name || job.title
                                )}
                                className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold rounded-lg bg-success/10 text-success hover:bg-success/20 transition-colors cursor-pointer"
                              >
                                <Download className="w-3 h-3" /> Tải lại
                              </button>
                            )}
                            {job.original_url && (
                              <button
                                onClick={() => handleRerun(job.original_url)}
                                title="Tải lại URL này"
                                className="p-1.5 rounded-lg text-text-muted hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors cursor-pointer"
                              >
                                <RotateCcw className="w-3.5 h-3.5" />
                              </button>
                            )}
                            {job.original_url && (
                              <button
                                onClick={() => handleCopyUrl(job.id, job.original_url)}
                                title="Copy URL gốc"
                                className="p-1.5 rounded-lg text-text-muted hover:text-primary hover:bg-primary/10 transition-colors cursor-pointer"
                              >
                                {copiedId === job.id
                                  ? <Check className="w-3.5 h-3.5 text-success" />
                                  : <Copy className="w-3.5 h-3.5" />
                                }
                              </button>
                            )}
                            <button
                              onClick={() => handleDeleteJob(job.id)}
                              className="p-1.5 rounded-lg text-text-muted hover:text-error hover:bg-error/10 transition-colors cursor-pointer"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* Mobile Cards */}
            <div className="md:hidden space-y-2 p-3">
              {applyTabFilter(filtered).map((job, idx) => {
                const hasDownload = job.status === 'success' && (job.direct_mp4_url || job.local_mp3_path || job.local_file_path);
                return (
                  <div key={job.id} className="p-3 rounded-xl bg-surface border border-border">
                    <div className="flex items-start gap-2 mb-2">
                      {job.local_mp3_path ? (
                        <Music className="w-4 h-4 text-accent-light flex-shrink-0 mt-0.5" />
                      ) : job.source_surface === 'logo_inpaint' || job.job_kind === 'visible_logo_cleanup' ? (
                        <Eraser className="w-4 h-4 text-violet-400 flex-shrink-0 mt-0.5" />
                      ) : (
                        <Video className="w-4 h-4 text-primary flex-shrink-0 mt-0.5" />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1 flex-wrap">
                          <p className="text-sm font-medium text-text-primary truncate">{job.title || job.original_url}</p>
                          {(job.source_surface === 'logo_inpaint' || job.job_kind === 'visible_logo_cleanup') && (
                            <span className="text-[10px] bg-violet-500/20 text-violet-300 border border-violet-500/30 px-1.5 py-0.5 rounded font-semibold flex-shrink-0">
                              Inpainted
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-text-muted truncate">{job.original_url}</p>
                      </div>
                    </div>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 flex-wrap">
                        {getStatusBadge(job.status)}
                        {getExpiryBadge(job)}
                        <span className="text-[10px] text-text-muted">{formatDate(job.created_at)}</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        {hasDownload && (
                          <button
                            onClick={() => handleSmartDownload(
                              job.local_mp3_path || job.local_file_path || job.direct_mp4_url,
                              job.slugified_name || job.title
                            )}
                            className="p-1.5 rounded-lg bg-success/10 text-success hover:bg-success/20 transition-colors"
                          >
                            <Download className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {job.original_url && (
                          <button
                            onClick={() => handleRerun(job.original_url)}
                            title="Tải lại URL này"
                            className="p-1.5 rounded-lg text-text-muted hover:text-emerald-400 hover:bg-emerald-500/10 transition-colors"
                          >
                            <RotateCcw className="w-3.5 h-3.5" />
                          </button>
                        )}
                        {job.original_url && (
                          <button
                            onClick={() => handleCopyUrl(job.id, job.original_url)}
                            className="p-1.5 rounded-lg text-text-muted hover:text-primary hover:bg-primary/10 transition-colors"
                          >
                            {copiedId === job.id
                              ? <Check className="w-3.5 h-3.5 text-success" />
                              : <Copy className="w-3.5 h-3.5" />
                            }
                          </button>
                        )}
                        <button
                          onClick={() => handleDeleteJob(job.id)}
                          className="p-1.5 rounded-lg text-text-muted hover:text-error hover:bg-error/10 transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Load More */}
            {hasMore && (
              <div className="flex justify-center py-4 border-t border-border/50">
                <button
                  onClick={handleLoadMore}
                  disabled={isLoading}
                  className="flex items-center gap-2 px-5 py-2 rounded-xl bg-surface border border-border text-text-secondary text-xs font-semibold hover:text-text-primary hover:border-primary/30 transition-all cursor-pointer disabled:opacity-50"
                >
                  {isLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                  Tải thêm
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
