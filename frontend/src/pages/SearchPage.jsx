import { useState, useRef, useCallback } from 'react';
import { Search, Loader2, TrendingUp, Clock, Eye, Play, Download } from 'lucide-react';
import { useTranslation, Trans } from 'react-i18next';

// VITE_API_BASE/VITE_API_BASE_URL are not defined in any build —
// this fell back to '' and became a relative (502) URL.
import { API_BASE } from '../lib/apiBase';

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
  { key: 'relevance',  labelKey: 'sort.relevance', Icon: TrendingUp },
  { key: 'date',       labelKey: 'sort.date',  Icon: Clock },
  { key: 'view_count', labelKey: 'sort.viewCount', Icon: Eye },
];

const DURATION_OPTIONS = [
  { key: null,     labelKey: 'duration.all',    min: null, max: null },
  { key: 'short',  labelKey: 'duration.short',  min: null, max: 240 },
  { key: 'medium', labelKey: 'duration.medium', min: 240,  max: 1200 },
  { key: 'long',   labelKey: 'duration.long',   min: 1200, max: null },
];

const SUGGESTION_KEYS = ['chill', 'cooking', 'trending', 'lofi'];

function VideoCard({ video, onDownload }) {
  const { t } = useTranslation('search');
  const dur   = formatDuration(video.duration_seconds);
  const views = formatViews(video.view_count);

  return (
    <div className="group rounded-xl bg-surface/70 border border-line overflow-hidden hover:border-accent/30 hover:shadow-lg hover:shadow-black/20 transition-all">
      <div className="relative aspect-video bg-surface overflow-hidden">
        {video.thumbnail_url ? (
          <img
            src={video.thumbnail_url}
            alt={video.title}
            loading="lazy"
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            onError={e => { e.currentTarget.parentElement.innerHTML = `<div class="w-full h-full flex items-center justify-center"><svg xmlns='http://www.w3.org/2000/svg' class='w-10 h-10 text-fg-muted' fill='none' viewBox='0 0 24 24' stroke='currentColor'><path stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z'/><path stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M21 12a9 9 0 11-18 0 9 9 0 0118 0z'/></svg></div>`; }}
          />
        ) : (
          <div className="w-full h-full flex items-center justify-center">
            <Play className="w-10 h-10 text-fg-muted" />
          </div>
        )}
        {dur && (
          <span className="absolute bottom-1.5 right-1.5 bg-black/80 text-white text-[10px] font-mono px-1.5 py-0.5 rounded">
            {dur}
          </span>
        )}
      </div>

      <div className="p-3">
        <p className="text-fg text-sm font-semibold line-clamp-2 leading-snug">{video.title}</p>
        {video.creator && (
          <p className="mt-1 text-fg-muted text-xs truncate">{video.creator}</p>
        )}
        <div className="mt-1.5 flex items-center gap-3 text-[10px] text-fg-muted">
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
          className="mt-2.5 w-full flex items-center justify-center gap-2 py-2 rounded-lg bg-accent-soft border border-accent/30 text-accent-text text-xs font-bold hover:bg-accent/20 hover:border-accent/50 active:scale-95 transition-all"
        >
          <Download className="w-3.5 h-3.5" />
          {t('download')}
        </button>
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="rounded-xl bg-surface/70 border border-line overflow-hidden animate-pulse">
      <div className="aspect-video bg-surface-2" />
      <div className="p-3 space-y-2">
        <div className="h-3.5 bg-surface-2 rounded w-full" />
        <div className="h-3.5 bg-surface-2 rounded w-3/4" />
        <div className="h-3 bg-surface-2 rounded w-1/2 mt-1" />
        <div className="h-8 bg-surface-2 rounded-lg mt-2.5" />
      </div>
    </div>
  );
}

export default function SearchPage({ onNavigate }) {
  const { t } = useTranslation('search');
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
    if (kw.length < 3) { setError(t('errors.tooShort')); return; }

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
        setError(t('errors.rateLimited'));
        return;
      }

      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail?.message || data?.detail || t('errors.failed'));
        return;
      }

      setResults(data.results || []);
      setCached(!!data.cached);
      setSearched(true);
    } catch {
      setError(t('errors.network'));
    } finally {
      setLoading(false);
    }
  }, [keyword, platform, sort, duration, t]);

  const handleDownload = (url) => {
    if (!url) {
      setError(t('errors.noLink'));
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
        <h1 className="text-2xl font-bold text-fg">{t('title')}</h1>
        <p className="mt-1 text-fg-muted text-sm">
          {t('subtitle')}
        </p>
      </div>

      {/* Search input */}
      <div className="flex gap-2 mb-5">
        <div className="relative flex-1">
          <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 text-fg-muted pointer-events-none" />
          <input
            ref={inputRef}
            type="text"
            value={keyword}
            onChange={e => setKeyword(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder={t('placeholder')}
            className="w-full pl-12 pr-4 py-3.5 rounded-xl bg-surface/80 border border-line text-fg placeholder:text-fg-muted text-base font-medium focus:outline-none focus:border-accent/50 focus:ring-1 focus:ring-accent/20 transition-all"
          />
        </div>
        <button
          type="button"
          onClick={handleSearch}
          disabled={loading || keyword.trim().length < 3}
          className="px-5 sm:px-7 rounded-xl bg-accent text-accent-fg font-bold hover:bg-accent-hover active:scale-95 transition-all disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2 flex-shrink-0"
        >
          {loading
            ? <Loader2 className="w-4 h-4 animate-spin" />
            : <Search className="w-4 h-4" />
          }
          <span className="hidden sm:inline">{t('searchBtn')}</span>
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2.5 mb-6">
        {/* Platform toggle */}
        <div className="flex gap-0.5 p-1 rounded-lg bg-surface/80 border border-line">
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
                  ? 'bg-accent text-accent-fg shadow'
                  : 'text-fg-muted hover:text-fg'
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Sort */}
        <div className="flex gap-1 p-1 rounded-lg bg-surface/60 border border-line">
          {SORT_OPTIONS.map(({ key, labelKey, Icon }) => (
            <button
              key={key}
              type="button"
              onClick={() => setSort(key)}
              className={`flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-medium transition-all ${
                sort === key
                  ? 'bg-line text-fg'
                  : 'text-fg-muted hover:text-fg-2'
              }`}
            >
              <Icon className="w-3 h-3" />
              {t(labelKey)}
            </button>
          ))}
        </div>

        {/* Duration */}
        <div className="flex gap-1 p-1 rounded-lg bg-surface/60 border border-line">
          {DURATION_OPTIONS.map(d => (
            <button
              key={d.key ?? 'all'}
              type="button"
              onClick={() => setDuration(d.key)}
              className={`px-2.5 py-1.5 rounded-md text-xs font-medium transition-all ${
                activeDuration === d.key
                  ? 'bg-line text-fg'
                  : 'text-fg-muted hover:text-fg-2'
              }`}
            >
              {t(d.labelKey)}
            </button>
          ))}
        </div>
      </div>

      {/* Rate limit banner */}
      {rateLimited && (
        <div className="mb-5 flex items-start gap-3 p-4 rounded-xl bg-accent-soft border border-accent/30">
          <span className="text-xl flex-shrink-0">⏱</span>
          <div>
            <p className="text-accent-text font-semibold text-sm">{t('rateLimit.title')}</p>
            <p className="text-accent-text text-xs mt-0.5">
              {t('rateLimit.body')}
            </p>
          </div>
        </div>
      )}

      {/* Error */}
      {error && !rateLimited && (
        <div className="mb-5 px-4 py-3 rounded-xl bg-danger-soft border border-danger/30 text-danger text-sm">
          {error}
        </div>
      )}

      {/* Cached indicator */}
      {cached && results.length > 0 && (
        <p className="mb-3 text-xs text-fg-muted flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-success/60 inline-block" />
          {t('cached')}
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
          <p className="mb-3 text-xs text-fg-muted">
            <Trans t={t} i18nKey="resultsFor" values={{ count: results.length, keyword }} components={{ kw: <span className="text-fg-2" /> }} />
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
        <div className="text-center py-16 text-fg-muted">
          <Search className="w-12 h-12 mx-auto mb-3 opacity-20" />
          <p className="text-sm">{t('noResults')}</p>
          <p className="text-xs mt-1">{t('noResultsHint')}</p>
        </div>
      )}

      {/* Initial empty state */}
      {!loading && !searched && !error && (
        <div className="text-center py-16">
          <div className="w-16 h-16 mx-auto mb-4 rounded-2xl bg-surface/80 border border-line flex items-center justify-center">
            <Search className="w-7 h-7 text-fg-muted" />
          </div>
          <p className="text-fg-muted text-sm font-medium">{t('empty.title')}</p>
          <p className="text-fg-muted text-xs mt-1.5">
            {t('empty.hint')}
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-2">
            {SUGGESTION_KEYS.map(k => t(`suggestions.${k}`)).map(q => (
              <button
                key={q}
                type="button"
                onClick={() => { setKeyword(q); handleSearch(q); }}
                className="px-3 py-1.5 rounded-full bg-surface/80 border border-line text-fg-muted text-xs hover:text-fg hover:border-line-strong transition-all"
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
