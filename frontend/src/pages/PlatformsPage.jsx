import { useState, useEffect, useMemo } from 'react';
import { Trans, useTranslation } from 'react-i18next';
import { CheckCircle, Clock, AlertCircle, Lock, Wifi, RefreshCw, Search, X } from 'lucide-react';

// VITE_API_BASE is not defined in any build — this fell back to ''
// and became a relative (502) URL on the split-domain deploy.
import { API_BASE } from '../lib/apiBase';
import { usePlatformHealth } from '../lib/usePlatformHealth';

// Names/domains stay here; descriptions and notes are in locales/*/platforms.json
// (items.<platformKey(name)>.description / .note). // wording: BA review
const PLATFORMS = [
  // ── Fully supported ────────────────────────────────────────────────
  {
    name: 'TikTok', status: 'full', logo: '🎵',
    features: ['video', 'audio', 'batch', 'channel', 'watermark-free'],
    domains: ['tiktok.com'],
  },
  {
    name: 'Douyin', status: 'full', logo: '🎵',
    features: ['video', 'audio', 'channel', 'watermark-free'],
    domains: ['douyin.com', 'v.douyin.com'],
  },
  {
    name: 'Facebook', status: 'full', logo: '📘',
    features: ['video', 'audio', 'batch', 'reels', 'live-vod'],
    domains: ['facebook.com', 'fb.watch'],
  },
  {
    name: 'Threads', status: 'full', logo: '🧵',
    features: ['video', 'image', 'carousel'],
    domains: ['threads.com', 'threads.net'],
  },
  {
    name: 'Pinterest', status: 'full', logo: '📌',
    features: ['video', 'image', 'batch', 'board'],
    domains: ['pinterest.com'],
  },
  {
    name: 'SoundCloud', status: 'full', logo: '🎧',
    features: ['audio', 'batch', 'playlist'],
    domains: ['soundcloud.com'],
  },
  {
    name: 'Spotify', status: 'full', logo: '🎵',
    features: ['audio', 'batch', 'playlist', 'album'],
    domains: ['open.spotify.com'],
  },
  {
    name: 'YouTube', status: 'full', logo: '▶️',
    features: ['video', 'audio', 'subtitle', 'batch', 'playlist', '4K'],
    domains: ['youtube.com', 'youtu.be'],
    note: true, // text: locales/*/platforms.json items.youtube.note — wording: BA review
  },
  // ── Upgraded in Phase 15 ───────────────────────────────────────────
  {
    name: 'Instagram', status: 'full', logo: '📸',
    features: ['video', 'image', 'carousel', 'story', 'batch', 'reels'],
    domains: ['instagram.com', 'instagr.am'],
    isNew: true,
  },
  {
    name: 'Twitter / X', status: 'full', logo: '🐦',
    features: ['video', 'gif', 'batch', 'thread'],
    domains: ['twitter.com', 'x.com', 't.co'],
    isNew: true,
  },
  {
    name: 'Reddit', status: 'full', logo: '🤖',
    features: ['video', 'audio-merge', 'batch', 'subreddit'],
    domains: ['reddit.com', 'redd.it'],
    isNew: true,
  },
  {
    name: 'Bilibili', status: 'full', logo: '📺',
    features: ['video', 'audio', 'subtitle', 'batch', 'playlist'],
    domains: ['bilibili.com', 'b23.tv'],
    isNew: true,
  },
  {
    name: 'Xiaohongshu', status: 'full', logo: '🌸',
    features: ['video', 'image', 'carousel', 'batch'],
    domains: ['xiaohongshu.com', 'xhslink.com', 'rednote.com'],
    isNew: true,
    note: true, // text: locales/*/platforms.json items.xiaohongshu.note — wording: BA review
  },
  // ── Basic new platforms ────────────────────────────────────────────
  {
    name: 'Lemon8', status: 'basic', logo: '🍋',
    features: ['video', 'image', 'carousel'],
    domains: ['lemon8-app.com'],
    isNew: true,
  },
  {
    name: 'Snapchat', status: 'basic', logo: '👻',
    features: ['video', 'spotlight'],
    domains: ['snapchat.com', 'story.snapchat.com'],
    isNew: true,
  },
  {
    name: 'VK', status: 'basic', logo: '💙',
    features: ['video', 'batch', 'playlist'],
    domains: ['vk.com', 'vkvideo.ru'],
    isNew: true,
  },
  {
    name: 'Twitch', status: 'basic', logo: '🎮',
    features: ['vod', 'clip'],
    domains: ['twitch.tv', 'clips.twitch.tv'],
    isNew: true,
  },
  {
    name: 'Rumble', status: 'basic', logo: '📹',
    features: ['video'],
    domains: ['rumble.com'],
    isNew: true,
  },
  {
    name: 'Odysee', status: 'basic', logo: '🌊',
    features: ['video', 'audio'],
    domains: ['odysee.com', 'lbry.tv'],
    isNew: true,
  },
  {
    name: 'Dailymotion', status: 'basic', logo: '📽️',
    features: ['video', 'batch'],
    domains: ['dailymotion.com'],
    isNew: true,
  },
  {
    name: 'Podcast RSS', status: 'basic', logo: '🎙️',
    features: ['audio', 'rss', 'apple-podcasts'],
    domains: ['podcasts.apple.com', 'feeds.*'],
    isNew: true,
  },
];

const FEATURE_ICONS = {
  video:           { icon: '🎬' },
  audio:           { icon: '🎵' },
  image:           { icon: '🖼️' },
  carousel:        { icon: '📚' },
  batch:           { icon: '📦' },
  channel:         { icon: '📡' },
  playlist:        { icon: '🎛️' },
  subtitle:        { icon: '💬' },
  story:           { icon: '⭕' },
  reels:           { icon: '🎞️' },
  '4K':            { icon: '🔆' },
  'watermark-free':{ icon: '✨' },
  'audio-merge':   { icon: '🔊' },
  gif:             { icon: '🌀' },
  vod:             { icon: '📼' },
  clip:            { icon: '✂️' },
  spotlight:       { icon: '⚡' },
  rss:             { icon: '📡' },
  'apple-podcasts':{ icon: '🎙️' },
  thread:          { icon: '🧵' },
  subreddit:       { icon: '📋' },
  'live-vod':      { icon: '🔴' },
  album:           { icon: '💿' },
  board:           { icon: '📌' },
};

function StatusBadge({ status }) {
  const { t } = useTranslation('platforms');
  // status may be legacy ('full','basic') or from live capability API
  const map = {
    full:                 { Icon: CheckCircle, cls: 'bg-success-soft text-success border-success/30' },
    basic:                { Icon: Clock,       cls: 'bg-accent-soft text-accent-text border-accent/30' },
    partial:              { Icon: AlertCircle, cls: 'bg-warning-soft text-warning border-warning/30' },
    cookie_required:      { Icon: Lock,        cls: 'bg-surface-2 text-fg-2 border-line' },
    proxy_required:       { Icon: Wifi,        cls: 'bg-surface-2 text-fg-2 border-line' }, // wording: BA review
    temporarily_disabled: { Icon: Clock,       cls: 'bg-danger-soft text-danger border-danger/30' },
    unsupported:          { Icon: AlertCircle, cls: 'bg-line-strong text-fg-muted border-line-strong' },
    // Phase 34D: health-probe driven, deliberately neutral (no "full support" claim).
    degraded:             { Icon: AlertCircle, cls: 'bg-surface-2 text-fg-2 border-line' }, // wording: BA review
    experimental:         { Icon: Clock,       cls: 'bg-surface-2 text-fg-2 border-line' }, // wording: BA review
  };
  const cfg = map[status] || map.basic;
  const { Icon, cls } = cfg;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${cls}`}>
      <Icon size={10} />
      {t(`status.${map[status] ? status : 'basic'}`)}
    </span>
  );
}

// source_type labels (matching backend): locales/*/platforms.json sourceTypes.<source_type>

function PlatformCard({ platform, liveData, health }) {
  const { t } = useTranslation('platforms');
  const itemKey = platformKey(platform.name);
  const effectiveStatus = health === 'degraded' ? 'degraded'
    : health === 'unknown' ? 'experimental'
    : (liveData?.overall_status || platform.status);

  const borderCls = effectiveStatus === 'full'
    ? 'border-success/20 hover:border-accent/30'
    : effectiveStatus === 'cookie_required'
    ? 'border-line hover:border-line'
    : effectiveStatus === 'proxy_required'
    ? 'border-line hover:border-line'
    : 'border-accent/15 hover:border-accent/30';

  return (
    <div className={`relative rounded-xl border p-4 transition-all bg-surface/70 backdrop-blur-sm hover:bg-surface/90 hover:shadow-lg hover:shadow-black/20 ${borderCls}`}>
      {platform.isNew && (
        <span className="absolute top-3 right-3 text-xs font-bold px-1.5 py-0.5 rounded bg-surface-2 text-fg-2 border border-line">
          {t('newBadge')}
        </span>
      )}
      <div className="flex items-start gap-3">
        <span className="text-2xl leading-none flex-shrink-0">{platform.logo}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-fg">{platform.name}</span>
            <StatusBadge status={effectiveStatus} />
            {liveData && health === 'healthy' && (
              <span className="text-[9px] px-1 py-0.5 rounded bg-success-soft text-success border border-success/15">
                {t('liveBadge')}
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-fg-muted leading-snug">{t(`items.${itemKey}.description`)}</p>
          {platform.known_limitation && (
            <p className="mt-1 text-xs text-fg-2">ℹ {platform.known_limitation}</p>
          )}
          {platform.note && (
            <p className="mt-1 text-xs text-accent-text">⚠ {t(`items.${itemKey}.note`)}</p>
          )}

          {/* Domains — helps users know what URLs they can paste */}
          {platform.domains?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {platform.domains.map(d => (
                <span
                  key={d}
                  className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-surface text-fg-muted border border-line"
                >
                  {d}
                </span>
              ))}
            </div>
          )}

          {/* Live source types */}
          {liveData?.source_types?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {liveData.source_types.map(st => (
                <span
                  key={st.source_type}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-surface-2 text-fg-muted border border-line-strong"
                  title={st.support_level_label}
                >
                  {t(`sourceTypes.${st.source_type}`, { defaultValue: st.source_type })}
                </span>
              ))}
            </div>
          )}

          {/* Feature tags */}
          <div className="mt-2 flex flex-wrap gap-1">
            {platform.features.map(f => {
              const fi = FEATURE_ICONS[f] || { icon: '•' };
              return (
                <span
                  key={f}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-surface-2 text-fg-2 border border-line-strong"
                >
                  <span className="text-[10px]">{fi.icon}</span>
                  <span>{t(`features.${f}`, { defaultValue: f })}</span>
                </span>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

function platformKey(name) {
  return name.toLowerCase().replace(/\s.*/, '').replace(/[^a-z0-9]/g, '');
}

function Section({ title, platforms, liveCapMap, classify, info }) {
  if (!platforms.length) return null;
  return (
    <div className="mb-8">
      <h2 className="text-sm font-semibold text-fg-muted uppercase tracking-wider mb-3">{title}</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {platforms.map(p => (
          <PlatformCard
            key={p.name}
            platform={{ ...p, known_limitation: info(p.name)?.known_limitation }}
            liveData={liveCapMap?.[platformKey(p.name)] || null}
            health={classify(p.name)}
          />
        ))}
      </div>
    </div>
  );
}

// Build a flat searchable text blob for each platform
function searchText(p, t) {
  return [
    p.name,
    t(`items.${platformKey(p.name)}.description`),
    p.note ? t(`items.${platformKey(p.name)}.note`) : '',
    ...(p.domains || []),
    ...p.features.map(f => t(`features.${f}`, { defaultValue: f })),
  ].join(' ').toLowerCase();
}

export default function PlatformsPage() {
  const { t } = useTranslation('platforms');
  const [filter, setFilter]           = useState('all');
  const [query,  setQuery]            = useState('');
  const [liveCapMap, setLiveCapMap]   = useState({});
  const [liveLoading, setLiveLoading] = useState(true);

  useEffect(() => {
    setLiveLoading(true);
    fetch(`${API_BASE}/api/v1/platforms/capabilities`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (!data?.platforms) return;
        const map = {};
        data.platforms.forEach(p => { map[p.platform] = p; });
        setLiveCapMap(map);
      })
      .catch(() => {})
      .finally(() => setLiveLoading(false));
  }, []);

  const { classify, info } = usePlatformHealth();

  const resolveStatus = (p) => {
    const h = classify(p.name);
    if (h === 'degraded') return 'degraded';
    if (h === 'unknown') return 'experimental';
    const live = liveCapMap[platformKey(p.name)];
    return live?.overall_status || p.status;
  };

  // Derive filtered list from both search query AND category filter
  const displayList = useMemo(() => {
    const q = query.trim().toLowerCase();
    return PLATFORMS.filter(p => {
      const matchesSearch = !q || searchText(p, t).includes(q);
      const status = resolveStatus(p);
      const matchesFilter =
        filter === 'all'             ? true
        : filter === 'full'          ? status === 'full'
        : filter === 'basic'         ? (p.status === 'basic' && status !== 'degraded' && status !== 'experimental')
        : filter === 'cookie_required' ? status === 'cookie_required'
        : filter === 'proxy_required'  ? status === 'proxy_required'
        : p.isNew;
      return matchesSearch && matchesFilter;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, filter, liveCapMap, classify, t]);

  const healthyList = PLATFORMS.filter(p => classify(p.name) === 'healthy');
  const fullCount  = healthyList.filter(p => p.status === 'full').length;
  const basicCount = healthyList.filter(p => p.status === 'basic').length;
  const degradedCount = PLATFORMS.filter(p => classify(p.name) === 'degraded').length;
  const experimentalCount = PLATFORMS.filter(p => classify(p.name) === 'unknown').length;
  const newCount   = PLATFORMS.filter(p => p.isNew).length;
  const liveCount  = Object.keys(liveCapMap).length;

  const isSearching = query.trim().length > 0;

  // Sections for "all" view when not searching
  const okList   = displayList.filter(p => classify(p.name) === 'healthy');
  const full     = okList.filter(p => p.status === 'full' && !p.isNew);
  const upgraded = okList.filter(p => p.status === 'full' && p.isNew);
  const basic    = okList.filter(p => p.status === 'basic');
  const degraded = displayList.filter(p => classify(p.name) === 'degraded');
  const experimental = displayList.filter(p => classify(p.name) === 'unknown');

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h1 className="text-2xl font-bold text-fg">{t('title')}</h1>
          {liveLoading ? (
            <span className="flex items-center gap-1.5 text-xs text-fg-muted">
              <RefreshCw size={12} className="animate-spin" /> {t('syncing')}
            </span>
          ) : liveCount > 0 ? (
            <span className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-full bg-success-soft border border-success/20 text-success">
              {t('liveCount', { count: liveCount })}
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-fg-muted">
          <Trans
            t={t}
            i18nKey="summary"
            count={healthyList.length}
            values={{ full: fullCount, basic: basicCount }}
            components={{ b: <strong className="text-fg" />, full: <span className="text-success" />, basic: <span className="text-accent-text" /> }}
          />
          {(degradedCount + experimentalCount) > 0 && (
            <> {[
              degradedCount > 0 && t('degradedCount', { n: degradedCount }),
              experimentalCount > 0 && t('experimentalCount', { n: experimentalCount }),
            ].filter(Boolean).join(', ')}.</> /* wording: BA review */
          )}
        </p>

        {/* Stats row */}
        <div className="mt-4 flex gap-2.5 flex-wrap">
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-success-soft border border-success/25">
            <CheckCircle size={14} className="text-success" />
            <span className="text-sm font-semibold text-success">{t('statFull', { n: fullCount })}</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-accent-soft border border-accent/25">
            <Clock size={14} className="text-accent-text" />
            <span className="text-sm font-semibold text-accent-text">{t('statBasic', { n: basicCount })}</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-surface-2 border border-line">
            <span className="text-sm font-semibold text-fg-2">{t('statNew', { n: newCount })}</span>
          </div>
        </div>
      </div>

      {/* Search + Filter toolbar */}
      <div className="mb-6 flex flex-col gap-3">
        {/* Search box */}
        <div className="relative">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-fg-muted pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder={t('searchPlaceholder')}
            className="w-full pl-10 pr-10 py-2.5 rounded-xl bg-surface/80 border border-line text-fg placeholder:text-fg-muted text-sm focus:outline-none focus:border-accent/50 focus:ring-1 focus:ring-accent/20 transition-all"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-fg-muted hover:text-fg-2 transition-colors"
              aria-label={t('clearSearch')}
            >
              <X size={15} />
            </button>
          )}
        </div>

        {/* Category filter */}
        <div className="flex gap-2 flex-wrap">
          {[
            { key: 'all' },
            { key: 'full' },
            { key: 'basic' },
            { key: 'cookie_required' },
            { key: 'proxy_required' }, // wording: BA review
            { key: 'new' },
          ].map(f => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                filter === f.key
                  ? 'bg-accent text-accent-fg font-bold shadow-md'
                  : 'bg-surface/70 text-fg-2 border border-line hover:border-line-strong hover:text-fg'
              }`}
            >
              {t(`filters.${f.key}`)}
            </button>
          ))}
        </div>

        {/* Search result count */}
        {isSearching && (
          <p className="text-xs text-fg-muted">
            {displayList.length > 0
              ? <Trans t={t} i18nKey="resultsMatch" count={displayList.length} values={{ query }} components={{ q: <span className="text-fg-2" /> }} />
              : <Trans t={t} i18nKey="resultsNone" values={{ query }} components={{ q: <span className="text-fg-2" /> }} />
            }
          </p>
        )}
      </div>

      {/* Platform grid */}
      {displayList.length === 0 ? (
        <div className="text-center py-16 text-fg-muted">
          <Search className="w-10 h-10 mx-auto mb-3 opacity-20" />
          <p className="text-sm">{t('emptyTitle')}</p>
          <p className="text-xs mt-1">{t('emptyHint')}</p>
        </div>
      ) : (filter === 'all' && !isSearching) ? (
        <>
          <Section title={t('sections.full')} platforms={full} liveCapMap={liveCapMap} classify={classify} info={info} />
          {upgraded.length > 0 && (
            <Section title={t('sections.upgraded')} platforms={upgraded} liveCapMap={liveCapMap} classify={classify} info={info} />
          )}
          <Section title={t('sections.basic')} platforms={basic} liveCapMap={liveCapMap} classify={classify} info={info} />
          <Section title={t('sections.degraded')} platforms={degraded} liveCapMap={liveCapMap} classify={classify} info={info} /> {/* wording: BA review */}
          <Section title={t('sections.experimental')} platforms={experimental} liveCapMap={liveCapMap} classify={classify} info={info} /> {/* wording: BA review */}
        </>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {displayList.map(p => (
            <PlatformCard
              key={p.name}
              platform={{ ...p, known_limitation: info(p.name)?.known_limitation }}
              liveData={liveCapMap[platformKey(p.name)] || null}
              health={classify(p.name)}
            />
          ))}
        </div>
      )}

      {/* Footer note */}
      <div className="mt-8 p-4 rounded-xl bg-surface/60 border border-line">
        <p className="text-sm text-fg-muted">
          {/* wording: BA review — internal names (Admin → Cookie Pool, capability registry) removed 13/10 */}
          <strong className="text-fg-2">{t('noteLabel')}</strong> {t('noteBody')}
          {liveCount > 0 && (
            <span className="ml-1 text-success">{' '}{t('autoUpdated')}</span>
          )}
        </p>
        <p className="mt-2 text-sm text-fg-muted">
          <Trans t={t} i18nKey="requestMissing" components={{ b: <strong className="text-fg-2" /> }} />
        </p>
      </div>
    </div>
  );
}
