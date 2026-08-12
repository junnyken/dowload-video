import { useState, useRef, useCallback } from 'react';
import { Search, Loader2, TrendingUp, Clock, Eye, Play, Download } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE || '';

function formatDuration(secs) {
  if (!secs) return '';
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    return `${h}:${String(m % 60).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatViews(n) {
  if (!n) return '';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`;
  return String(n);
}

const SORT_OPTIONS = [
  { key: 'relevance',  label: 'Liên quan', Icon: TrendingUp },
  { key: 'date',       label: 'Mới nhất',  Icon: Clock },
  { key: 'view_count', label: 'Nhiều xem', Icon: Eye },
];

const DURATION_OPTIONS = [
  { key: null,     label: 'Tất cả',       min: null, max: null },
  { key: 'short',  label: '< 4 phút',     min: null, max: 240 },
  { key: 'medium', label: '4 – 20 phút',  min: 240,  max: 1200 },
  { key: 'long',   label: '> 20 phút',    min: 1200, max: null },
];

function VideoCard({ video, onDownload }) {
  const dur   = formatDuration(video.duration_seconds);
  const views = formatViews(video.view_count);

  return (
    <div className="group rounded-xl bg-[#0d2821]/70 border border-slate-700/40 overflow-hidden hover:border-[#FBBF24]/30 hover:shadow-lg hover:shadow-black/20 transition-all">
      <div className="relative aspect-video bg-slate-800/50 overflow-hidden">
        {video.thumbnail_url ? (
          <img
            src={video.thumbnail_url}
            alt={video.title}
            loading="lazy"
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onError={e => { e.currentTarget.parentElement.innerHTML = `<div class="w-full h-full flex items-center justify-center"><svg xmlns='http://www.w3.org/2000/svg' class='w-10 h-10 text-slate-600' fill='none' viewBox='0 0 24 24' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z'/><path stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M21 12a9 9 0 11-18 0 9 9 0 0118 0z'/></svg></div>`; }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Play className="w-10 h-10 text-slate-600" />
          </div>
        )}
        {dur && (
          <span className="absolute bottom-1.5 right-1.5 bg-black/80 text-white text-[10px] font-mono px-1.5 py-0.5 rounded">
            {dur}
          </span>
        )}
      </div>

      <div className="p-3">
        <p className="text-white text-sm font-semibold line-clamp-2 leading-snug">{video.title}</p>
        {video.creator && (
          <p className="mt-1 text-slate-400 text-xs truncate">{video.creator}</p>
        )}
        <div className="mt-1.5 flex items-center gap-3 text-[10px] text-slate-500">
          {views && (
            <span className="flex items-center gap-0.5">
              <Eye className="w-3 h-3" />{views}
            </span>
          )}
          {video.upload_date && <span>{video.upload_date.slice(0, 7)}</span>}
        </div>
        <button
          type="button"
          onClick={() => onDownload(video.url)}
          className="mt-2.5 w-full flex items-center justify-center gap-2 py-2 rounded-lg bg-[#FBBF24]/10 border border-[#FBBF24]/30 text-[#FBBF24] text-xs font-bold hover:bg-[#FBBF24]/20 hover:border-[#FBBF24]/50 active:scale-95 transition-all"
        >
          <Download className="w-3.5 h-3.5" />
          Tải về
        </button>
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="rounded-xl bg-[#0d2821]/70 border border-slate-700/40 overflow-hidden animate-pulse">
      <div className="aspect-video bg-slate-700/40" />
      <div className="p-3 space-y-2">
        <div className="h-3.5 bg-slate-700/60 rounded w-full" />
        <div className="h-3.5 bg-slate-700/60 rounded w-3/4" />
        <div className="h-3 bg-slate-700/40 rounded w-1/2 mt-1" />
        <div className="h-8 bg-slate-700/40 rounded-lg mt-2.5" />
      </div>
    </div>
  );
}

export default function SearchPage({ onNavigate }) {
  const [keyword,     setKeyword]     = useState('');
  const [platform,    setPlatform]    = useState('youtube');
  const [sort,        setSort]        = useState('relevance');
  const [duration,    setDuration]    = useState(null);
  const [results,     setResults]     = useState([]);
  const [loading,     setLoading]     = useState(false);
  const [error,       setError]       = useState('');
  const [cached,      setCached]      = useState(false);
  const [searched,    setSearched]    = useState(false);
  const [rateLimited, setRateLimited] = useState(false);
  const inputRef = useRef(null);

  const handleSearch = useCallback(async (kwOverride) => {
    // kwOverride: explicit keyword passed by chip buttons to bypass stale closure.
    // For normal button clicks the event object is passed, which is not a string,
    // so we fall back to the keyword state correctly.
    const kw = (typeof kwOverride === 'string' ? kwOverride : keyword).trim();
    if (kw.length < 3) { setError('Từ khóa phải có ít nhất 3 ký tự.'); return; }

    setLoading(true);
    setError('');
    setResults([]);
    setSearched(false);
    setRateLimited(false);

    const durOpt = DURATION_OPTIONS.find(d => d.key === duration) || DURATION_OPTIONS[0];

    try {
      const body = {
        keyword: kw,
        platform,
        count: 12,
        sort,
        ...(durOpt.min  != null ? { min_duration: durOpt.min }  : {}),
        ...(durOpt.max  != null ? { max_duration: durOpt.max }  : {}),
      };

      const res = await fetch(`${API_BASE}/api/v1/search-videos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (res.status === 429) {
        setRateLimited(true);
        setError('Đã tìm 5 lần trong giờ này. Thử lại sau.');
        return;
      }

      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail?.message || data?.detail || 'Tìm kiếm thất bại.');
        return;
      }

      setResults(data.results || []);
      setCached(!!data.cached);
      setSearched(true);
    } catch {
      setError('Không thể kết nối. Kiểm tra mạng và thử lại.');
    } finally {
      setLoading(false);
    }
  }, [keyword, platform, sort, duration]);

  const handleDownload = (url) => {
    if (!url) {
      setError('Video này không có link để tải về.');
      return;
    }
    // DashboardContent is not mounted while Search is active, so dispatching
    // vidgrab:share-url here would fire into the void (no listener yet).
    // Use the same sessionStorage handoff that ShareTargetHandler uses —
    // DashboardContent reads vg_pending_share_url on mount and pre-fills the URL.
    try { sessionStorage.setItem('vg_pending_share_url', url); } catch {}
    onNavigate?.('landing', '/');
  };

  const activeDuration = duration;

  return (
    <div className="max-w-5xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-white">Tìm kiếm video</h1>
        <p className="mt-1 text-slate-400 text-sm">
          Tìm video YouTube và TikTok để tải về — không cần link sẵn.
        </p>
      </div>

      {/* Search input */}
      <div className="flex gap-2 mb-5">
        <div className="relative flex-1">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-slate-500 pointer-events-none" />
          <input
            ref={inputRef}
            type="text"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="Nhập từ khóa tìm kiếm..."
            className="w-full pl-12 pr-4 py-3.5 rounded-xl bg-[#0d2821]/80 border border-slate-700/50 text-white placeholder-slate-500 text-base font-medium focus:outline-none focus:border-[#FBBF24]/50 focus:ring-1 focus:ring-[#FBBF24]/20 transition-all"
          />
        </div>
        <button
          type="button"
          onClick={handleSearch}
          disabled={loading || keyword.trim().length < 3}
          className="px-5 sm:px-7 rounded-xl bg-[#FBBF24] text-[#012622] font-bold hover:bg-[#F59E0B] active:scale-95 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 flex-shrink-0"
        >
          {loading
            ? <Loader2 className="w-4 h-4 animate-spin" />
            : <Search className="w-4 h-4" />
          }
          <span className="hidden sm:inline">Tìm</span>
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2.5 mb-6">
        {/* Platform toggle */}
        <div className="flex gap-0.5 p-1 rounded-lg bg-[#0d2821]/80 border border-slate-700/40">
          {[
            { key: 'youtube', label: '▶️ YouTube' },
            { key: 'tiktok',  label: '🎵 TikTok'  },
          ].map(p => (
            <button
              key={p.key}
              type="button"
              onClick={() => setPlatform(p.key)}
              className={`px-3 py-1.5 rounded-md text-xs font-semibold transition-all ${
                platform === p.key
                  ? 'bg-[#FBBF24] text-[#012622] shadow'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Sort */}
        <div className="flex gap-1 p-1 rounded-lg bg-[#0d2821]/60 border border-slate-700/40">
          {SORT_OPTIONS.map(({ key, label, Icon }) => (
            <button
              key={key}
              type="button"
              onClick={() => setSort(key)}
              className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-all ${
                sort === key
                  ? 'bg-slate-600/80 text-white'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              <Icon className="w-3 h-3" />
              {label}
            </button>
          ))}
        </div>

        {/* Duration */}
        <div className="flex gap-1 p-1 rounded-lg bg-[#0d2821]/60 border border-slate-700/40">
          {DURATION_OPTIONS.map(d => (
            <button
              key={d.key ?? 'all'}
              type="button"
              onClick={() => setDuration(d.key)}
              className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-all ${
                activeDuration === d.key
                  ? 'bg-slate-600/80 text-white'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {d.label}
            </button>
          ))}
        </div>
      </div>

      {/* Rate limit banner */}
      {rateLimited && (
        <div className="mb-5 flex items-start gap-3 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30">
          <span className="text-xl flex-shrink-0">⏱</span>
          <div>
            <p className="text-amber-400 font-semibold text-sm">Giới hạn tìm kiếm</p>
            <p className="text-amber-300/70 text-xs mt-0.5">
              Bạn đã tìm 5 lần trong giờ này. Chờ ~60 phút hoặc dán link trực tiếp vào ô tải về.
            </p>
          </div>
        </div>
      )}

      {/* Error */}
      {error && !rateLimited && (
        <div className="mb-5 px-4 py-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Cached indicator */}
      {cached && results.length > 0 && (
        <p className="mb-3 text-xs text-slate-500 flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-400/60 inline-block" />
          Kết quả từ cache (10 phút)
        </p>
      )}

      {/* Loading */}
      {loading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <SkeletonCard key={i} />)}
        </div>
      )}

      {/* Results */}
      {!loading && results.length > 0 && (
        <>
          <p className="mb-3 text-xs text-slate-500">
            {results.length} kết quả cho "
            <span className="text-slate-300">{keyword}</span>"
          </p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {results.map((v, i) => (
              <VideoCard key={`${v.url}-${i}`} video={v} onDownload={handleDownload} />
            ))}
          </div>
        </>
      )}

      {/* Searched, no results */}
      {!loading && searched && results.length === 0 && !error && (
        <div className="text-center py-16 text-slate-500">
          <Search className="w-12 h-12 mx-auto mb-3 opacity-20" />
          <p className="text-sm">Không tìm thấy video nào cho từ khóa này.</p>
          <p className="text-xs mt-1">Thử từ khóa khác hoặc đổi sang nền tảng khác.</p>
        </div>
      )}

      {/* Initial empty state */}
      {!loading && !searched && !error && (
        <div className="text-center py-16">
          <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-[#0d2821]/80 border border-slate-700/40 flex items-center justify-center">
            <Search className="w-7 h-7 text-slate-600" />
          </div>
          <p className="text-slate-400 text-sm font-medium">Nhập từ khóa để bắt đầu tìm kiếm</p>
          <p className="text-slate-600 text-xs mt-1.5">
            Hỗ trợ YouTube và TikTok · Giới hạn 5 lần/giờ · Cache 10 phút
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-2">
            {['nhạc chill', 'hướng dẫn nấu ăn', 'top trending tiktok', 'lo-fi music'].map(q => (
              <button
                key={q}
                type="button"
                onClick={() => { setKeyword(q); handleSearch(q); }}
                className="px-3 py-1.5 rounded-full bg-[#0d2821]/80 border border-slate-700/40 text-slate-400 text-xs hover:text-white hover:border-slate-500/60 transition-all"
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
