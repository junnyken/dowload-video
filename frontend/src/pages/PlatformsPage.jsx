import { useState, useEffect, useMemo } from 'react';
import { CheckCircle, Clock, AlertCircle, Lock, Wifi, RefreshCw, Search, X } from 'lucide-react';

// VITE_API_BASE is not defined in any build — this fell back to ''
// and became a relative (502) URL on the split-domain deploy.
import { API_BASE } from '../lib/apiBase';

const PLATFORMS = [
  // ── Fully supported ────────────────────────────────────────────────
  {
    name: 'TikTok', status: 'full', logo: '🎵',
    features: ['video', 'audio', 'batch', 'channel', 'watermark-free'],
    description: 'Video, âm thanh, kênh, tải hàng loạt không watermark.',
    domains: ['tiktok.com'],
  },
  {
    name: 'Douyin', status: 'full', logo: '🎵',
    features: ['video', 'audio', 'channel', 'watermark-free'],
    description: 'TikTok phiên bản Trung Quốc, hỗ trợ đầy đủ qua Apify.',
    domains: ['douyin.com', 'v.douyin.com'],
  },
  {
    name: 'Facebook', status: 'full', logo: '📘',
    features: ['video', 'audio', 'batch', 'reels', 'live-vod'],
    description: 'Video, Reels, VOD live, trang/nhóm hàng loạt.',
    domains: ['facebook.com', 'fb.watch'],
  },
  {
    name: 'Threads', status: 'full', logo: '🧵',
    features: ['video', 'image', 'carousel'],
    description: 'Video và ảnh từ bài đăng Threads công khai.',
    domains: ['threads.com', 'threads.net'],
  },
  {
    name: 'Pinterest', status: 'full', logo: '📌',
    features: ['video', 'image', 'batch', 'board'],
    description: 'Video, ảnh, scrape board/bộ sưu tập.',
    domains: ['pinterest.com'],
  },
  {
    name: 'SoundCloud', status: 'full', logo: '🎧',
    features: ['audio', 'batch', 'playlist'],
    description: 'Tải nhạc, playlist, kênh nghệ sĩ.',
    domains: ['soundcloud.com'],
  },
  {
    name: 'Spotify', status: 'full', logo: '🎵',
    features: ['audio', 'batch', 'playlist', 'album'],
    description: 'Tải qua YouTube match (độ chính xác cao cho nhạc phổ biến).',
    domains: ['open.spotify.com'],
  },
  {
    name: 'YouTube', status: 'full', logo: '▶️',
    features: ['video', 'audio', 'subtitle', 'batch', 'playlist', '4K'],
    description: 'Qua residential proxy. Video, 4K, phụ đề, playlist.',
    domains: ['youtube.com', 'youtu.be'],
    note: 'Yêu cầu proxy — tốc độ thấp hơn nền tảng khác.',
  },
  // ── Upgraded in Phase 15 ───────────────────────────────────────────
  {
    name: 'Instagram', status: 'full', logo: '📸',
    features: ['video', 'image', 'carousel', 'story', 'batch', 'reels'],
    description: 'Reel, carousel, story, batch profile. Cần cookie để xem nội dung riêng tư.',
    domains: ['instagram.com', 'instagr.am'],
    isNew: true,
  },
  {
    name: 'Twitter / X', status: 'full', logo: '🐦',
    features: ['video', 'gif', 'batch', 'thread'],
    description: 'Video tweet, GIF, batch timeline. Cần cookie cho tweet giới hạn.',
    domains: ['twitter.com', 'x.com', 't.co'],
    isNew: true,
  },
  {
    name: 'Reddit', status: 'full', logo: '🤖',
    features: ['video', 'audio-merge', 'batch', 'subreddit'],
    description: 'v.redd.it với merge audio, batch subreddit, cookie cho NSFW.',
    domains: ['reddit.com', 'redd.it'],
    isNew: true,
  },
  {
    name: 'Bilibili', status: 'full', logo: '📺',
    features: ['video', 'audio', 'subtitle', 'batch', 'playlist'],
    description: 'Video, playlist, UP owner batch, phụ đề. Cookie cho nội dung VIP.',
    domains: ['bilibili.com', 'b23.tv'],
    isNew: true,
  },
  {
    name: 'Xiaohongshu', status: 'full', logo: '🌸',
    features: ['video', 'image', 'carousel', 'batch'],
    description: 'Video và ảnh carousel. Cần cookie XHS + CN proxy cho một số nội dung.',
    domains: ['xiaohongshu.com', 'xhslink.com'],
    isNew: true,
    note: 'Một số nội dung yêu cầu CN proxy.',
  },
  // ── Basic new platforms ────────────────────────────────────────────
  {
    name: 'Lemon8', status: 'basic', logo: '🍋',
    features: ['video', 'image', 'carousel'],
    description: 'Video và ảnh carousel từ bài đăng công khai.',
    domains: ['lemon8-app.com'],
    isNew: true,
  },
  {
    name: 'Snapchat', status: 'basic', logo: '👻',
    features: ['video', 'spotlight'],
    description: 'Spotlight và Story công khai. Không hỗ trợ nội dung riêng tư.',
    domains: ['snapchat.com', 'story.snapchat.com'],
    isNew: true,
  },
  {
    name: 'VK', status: 'basic', logo: '💙',
    features: ['video', 'batch', 'playlist'],
    description: 'Video đơn, playlist từ VKontakte.',
    domains: ['vk.com', 'vkvideo.ru'],
    isNew: true,
  },
  {
    name: 'Twitch', status: 'basic', logo: '🎮',
    features: ['vod', 'clip'],
    description: 'VOD và Clip (không hỗ trợ livestream).',
    domains: ['twitch.tv', 'clips.twitch.tv'],
    isNew: true,
  },
  {
    name: 'Rumble', status: 'basic', logo: '📹',
    features: ['video'],
    description: 'Video đơn từ Rumble.',
    domains: ['rumble.com'],
    isNew: true,
  },
  {
    name: 'Odysee', status: 'basic', logo: '🌊',
    features: ['video', 'audio'],
    description: 'Video và audio từ Odysee (LBRY).',
    domains: ['odysee.com', 'lbry.tv'],
    isNew: true,
  },
  {
    name: 'Dailymotion', status: 'basic', logo: '📽️',
    features: ['video', 'batch'],
    description: 'Video và playlist Dailymotion.',
    domains: ['dailymotion.com'],
    isNew: true,
  },
  {
    name: 'Podcast RSS', status: 'basic', logo: '🎙️',
    features: ['audio', 'rss', 'apple-podcasts'],
    description: 'Tải episode từ RSS feed hoặc Apple Podcasts URL.',
    domains: ['podcasts.apple.com', 'feeds.*'],
    isNew: true,
  },
];

const FEATURE_ICONS = {
  video:           { icon: '🎬', label: 'Video' },
  audio:           { icon: '🎵', label: 'Audio / MP3' },
  image:           { icon: '🖼️', label: 'Ảnh' },
  carousel:        { icon: '📚', label: 'Carousel' },
  batch:           { icon: '📦', label: 'Tải hàng loạt' },
  channel:         { icon: '📡', label: 'Kênh' },
  playlist:        { icon: '🎛️', label: 'Playlist' },
  subtitle:        { icon: '💬', label: 'Phụ đề' },
  story:           { icon: '⭕', label: 'Story' },
  reels:           { icon: '🎞️', label: 'Reels' },
  '4K':            { icon: '🔆', label: '4K' },
  'watermark-free':{ icon: '✨', label: 'Không watermark' },
  'audio-merge':   { icon: '🔊', label: 'Merge audio' },
  gif:             { icon: '🌀', label: 'GIF / MP4' },
  vod:             { icon: '📼', label: 'VOD' },
  clip:            { icon: '✂️', label: 'Clip' },
  spotlight:       { icon: '⚡', label: 'Spotlight' },
  rss:             { icon: '📡', label: 'RSS Feed' },
  'apple-podcasts':{ icon: '🎙️', label: 'Apple Podcasts' },
  thread:          { icon: '🧵', label: 'Thread' },
  subreddit:       { icon: '📋', label: 'Subreddit' },
  'live-vod':      { icon: '🔴', label: 'Live VOD' },
  album:           { icon: '💿', label: 'Album' },
  board:           { icon: '📌', label: 'Board' },
};

function StatusBadge({ status }) {
  // status may be legacy ('full','basic') or from live capability API
  const map = {
    full:                 { Icon: CheckCircle, cls: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30', label: 'Đầy đủ' },
    basic:                { Icon: Clock,       cls: 'bg-amber-500/20 text-amber-400 border-amber-500/30',       label: 'Cơ bản' },
    partial:              { Icon: AlertCircle, cls: 'bg-amber-500/20 text-amber-400 border-amber-500/30',       label: 'Một phần' },
    cookie_required:      { Icon: Lock,        cls: 'bg-blue-500/20 text-blue-400 border-blue-500/30',         label: 'Cần cookie' },
    proxy_required:       { Icon: Wifi,        cls: 'bg-purple-500/20 text-purple-400 border-purple-500/30',   label: 'Cần proxy' },
    temporarily_disabled: { Icon: Clock,       cls: 'bg-red-500/20 text-red-400 border-red-500/30',            label: 'Tạm dừng' },
    unsupported:          { Icon: AlertCircle, cls: 'bg-slate-500/20 text-slate-400 border-slate-500/30',      label: 'Chưa hỗ trợ' },
  };
  const cfg = map[status] || map.basic;
  const { Icon, cls, label } = cfg;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${cls}`}>
      <Icon size={10} />
      {label}
    </span>
  );
}

// source_type labels (matching backend)
const SOURCE_TYPE_LABELS = {
  single_video: 'Video', single_audio: 'Audio', single_post: 'Bài đăng',
  playlist: 'Playlist', album: 'Album', artist: 'Nghệ sĩ', channel: 'Kênh',
  profile: 'Profile', board: 'Board', subreddit: 'Cộng đồng', podcast_feed: 'Podcast',
};

function PlatformCard({ platform, liveData }) {
  const effectiveStatus = liveData?.overall_status || platform.status;

  const borderCls = effectiveStatus === 'full'
    ? 'border-emerald-500/20 hover:border-[#FBBF24]/30'
    : effectiveStatus === 'cookie_required'
    ? 'border-blue-500/15 hover:border-blue-400/30'
    : effectiveStatus === 'proxy_required'
    ? 'border-purple-500/15 hover:border-purple-400/30'
    : 'border-amber-500/15 hover:border-amber-400/30';

  return (
    <div className={`relative rounded-xl border p-4 transition-all bg-[#0d2821]/70 backdrop-blur-sm hover:bg-[#0d2821]/90 hover:shadow-lg hover:shadow-black/20 ${borderCls}`}>
      {platform.isNew && (
        <span className="absolute top-3 right-3 text-xs font-bold px-1.5 py-0.5 rounded bg-blue-500/25 text-blue-300 border border-blue-500/30">
          NEW
        </span>
      )}
      <div className="flex items-start gap-3">
        <span className="text-2xl leading-none flex-shrink-0">{platform.logo}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-white">{platform.name}</span>
            <StatusBadge status={effectiveStatus} />
            {liveData && (
              <span className="text-[9px] px-1 py-0.5 rounded bg-emerald-500/10 text-emerald-500/60 border border-emerald-500/15">
                LIVE
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-slate-400 leading-snug">{platform.description}</p>
          {platform.note && (
            <p className="mt-1 text-xs text-amber-400/80">⚠ {platform.note}</p>
          )}

          {/* Domains — helps users know what URLs they can paste */}
          {platform.domains?.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {platform.domains.map(d => (
                <span
                  key={d}
                  className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-slate-800/70 text-slate-400 border border-slate-700/40"
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
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] bg-slate-700/40 text-slate-400 border border-slate-600/20"
                  title={st.support_level_label}
                >
                  {SOURCE_TYPE_LABELS[st.source_type] || st.source_type}
                </span>
              ))}
            </div>
          )}

          {/* Feature tags */}
          <div className="mt-2 flex flex-wrap gap-1">
            {platform.features.map(f => {
              const fi = FEATURE_ICONS[f] || { icon: '•', label: f };
              return (
                <span
                  key={f}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs bg-slate-700/60 text-slate-300 border border-slate-600/30"
                >
                  <span className="text-[10px]">{fi.icon}</span>
                  <span>{fi.label}</span>
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

function Section({ title, platforms, liveCapMap }) {
  if (!platforms.length) return null;
  return (
    <div className="mb-8">
      <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">{title}</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        {platforms.map(p => (
          <PlatformCard
            key={p.name}
            platform={p}
            liveData={liveCapMap?.[platformKey(p.name)] || null}
          />
        ))}
      </div>
    </div>
  );
}

// Build a flat searchable text blob for each platform
function searchText(p) {
  return [
    p.name,
    p.description,
    p.note || '',
    ...(p.domains || []),
    ...p.features.map(f => FEATURE_ICONS[f]?.label || f),
  ].join(' ').toLowerCase();
}

export default function PlatformsPage() {
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

  const resolveStatus = (p) => {
    const live = liveCapMap[platformKey(p.name)];
    return live?.overall_status || p.status;
  };

  // Derive filtered list from both search query AND category filter
  const displayList = useMemo(() => {
    const q = query.trim().toLowerCase();
    return PLATFORMS.filter(p => {
      const matchesSearch = !q || searchText(p).includes(q);
      const status = resolveStatus(p);
      const matchesFilter =
        filter === 'all'             ? true
        : filter === 'full'          ? status === 'full'
        : filter === 'basic'         ? p.status === 'basic'
        : filter === 'cookie_required' ? status === 'cookie_required'
        : filter === 'proxy_required'  ? status === 'proxy_required'
        : p.isNew;
      return matchesSearch && matchesFilter;
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, filter, liveCapMap]);

  const fullCount  = PLATFORMS.filter(p => p.status === 'full').length;
  const basicCount = PLATFORMS.filter(p => p.status === 'basic').length;
  const newCount   = PLATFORMS.filter(p => p.isNew).length;
  const liveCount  = Object.keys(liveCapMap).length;

  const isSearching = query.trim().length > 0;

  // Sections for "all" view when not searching
  const full     = displayList.filter(p => p.status === 'full' && !p.isNew);
  const upgraded = displayList.filter(p => p.status === 'full' && p.isNew);
  const basic    = displayList.filter(p => p.status === 'basic');

  return (
    <div className="max-w-4xl mx-auto px-4 py-8">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <h1 className="text-2xl font-bold text-white">Nền tảng được hỗ trợ</h1>
          {liveLoading ? (
            <span className="flex items-center gap-1.5 text-xs text-slate-500">
              <RefreshCw size={12} className="animate-spin" /> Đang đồng bộ…
            </span>
          ) : liveCount > 0 ? (
            <span className="flex items-center gap-1 text-[10px] px-2 py-1 rounded-full bg-emerald-500/10 border border-emerald-500/20 text-emerald-500/70">
              ● LIVE · {liveCount} nền tảng
            </span>
          ) : null}
        </div>
        <p className="mt-1 text-slate-400">
          VidGrab hỗ trợ <strong className="text-white">{PLATFORMS.length}</strong> nền tảng —{' '}
          <span className="text-emerald-400">{fullCount} đầy đủ</span>,{' '}
          <span className="text-amber-400">{basicCount} cơ bản</span>.
        </p>

        {/* Stats row */}
        <div className="mt-4 flex gap-2.5 flex-wrap">
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-emerald-500/10 border border-emerald-500/25">
            <CheckCircle size={14} className="text-emerald-400" />
            <span className="text-sm font-semibold text-emerald-400">{fullCount} Đầy đủ</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-amber-500/10 border border-amber-500/25">
            <Clock size={14} className="text-amber-400" />
            <span className="text-sm font-semibold text-amber-400">{basicCount} Cơ bản</span>
          </div>
          <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-blue-500/10 border border-blue-500/25">
            <span className="text-sm font-semibold text-blue-400">🆕 {newCount} Mới trong Phase 15</span>
          </div>
        </div>
      </div>

      {/* Search + Filter toolbar */}
      <div className="mb-6 flex flex-col gap-3">
        {/* Search box */}
        <div className="relative">
          <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 pointer-events-none" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Tìm nền tảng, domain, tính năng… (vd: tiktok.com, playlist, 4K)"
            className="w-full pl-10 pr-10 py-2.5 rounded-xl bg-[#0d2821]/80 border border-slate-700/50 text-white placeholder-slate-500 text-sm focus:outline-none focus:border-[#FBBF24]/50 focus:ring-1 focus:ring-[#FBBF24]/20 transition-all"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery('')}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
              aria-label="Xóa tìm kiếm"
            >
              <X size={15} />
            </button>
          )}
        </div>

        {/* Category filter */}
        <div className="flex gap-2 flex-wrap">
          {[
            { key: 'all',             label: 'Tất cả' },
            { key: 'full',            label: '✅ Đầy đủ' },
            { key: 'basic',           label: '🟡 Cơ bản' },
            { key: 'cookie_required', label: '🔒 Cần cookie' },
            { key: 'proxy_required',  label: '🌐 Cần proxy' },
            { key: 'new',             label: '🆕 Mới' },
          ].map(f => (
            <button
              key={f.key}
              type="button"
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                filter === f.key
                  ? 'bg-[#FBBF24] text-[#012622] font-bold shadow-md shadow-amber-500/20'
                  : 'bg-[#0d2821]/70 text-slate-300 border border-slate-700/50 hover:border-slate-500/60 hover:text-white'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {/* Search result count */}
        {isSearching && (
          <p className="text-xs text-slate-500">
            {displayList.length > 0
              ? <>{displayList.length} nền tảng phù hợp với "<span className="text-slate-300">{query}</span>"</>
              : <>Không tìm thấy nền tảng nào cho "<span className="text-slate-300">{query}</span>"</>
            }
          </p>
        )}
      </div>

      {/* Platform grid */}
      {displayList.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <Search className="w-10 h-10 mx-auto mb-3 opacity-20" />
          <p className="text-sm">Không tìm thấy nền tảng nào.</p>
          <p className="text-xs mt-1">Thử từ khóa khác hoặc bỏ bộ lọc.</p>
        </div>
      ) : (filter === 'all' && !isSearching) ? (
        <>
          <Section title="✅ Hỗ trợ đầy đủ" platforms={full} liveCapMap={liveCapMap} />
          {upgraded.length > 0 && (
            <Section title="⬆️ Nâng cấp trong Phase 15" platforms={upgraded} liveCapMap={liveCapMap} />
          )}
          <Section title="🟡 Hỗ trợ cơ bản" platforms={basic} liveCapMap={liveCapMap} />
        </>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {displayList.map(p => (
            <PlatformCard
              key={p.name}
              platform={p}
              liveData={liveCapMap[platformKey(p.name)] || null}
            />
          ))}
        </div>
      )}

      {/* Footer note */}
      <div className="mt-8 p-4 rounded-xl bg-[#0d2821]/60 border border-slate-700/40">
        <p className="text-sm text-slate-400">
          <strong className="text-slate-300">Ghi chú:</strong> Nền tảng yêu cầu cookie cần được cấu hình bởi admin trong{' '}
          <span className="font-mono text-xs bg-slate-700/60 text-slate-300 px-1.5 py-0.5 rounded border border-slate-600/40">Admin → Cookie Pool</span>.
          {liveCount > 0 && (
            <span className="ml-1 text-emerald-400/70"> Dữ liệu status đang sync từ capability registry.</span>
          )}
        </p>
        <p className="mt-2 text-sm text-slate-400">
          Không tìm thấy nền tảng bạn cần? Gửi yêu cầu qua nút <strong className="text-slate-300">Phản hồi</strong> ở sidebar.
        </p>
      </div>
    </div>
  );
}
