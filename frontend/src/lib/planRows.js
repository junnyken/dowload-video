import { qualityLabel } from './usePlans';

// Comparison rows built ONLY from the /api/v1/plans payload. Each row returns,
// per tier, a string (shown as text), true/false (tick / cross) or null (the
// row does not apply to that tier and is skipped). A row is dropped entirely
// when no tier has anything to show (e.g. Channel Watch switched off).
// wording: BA review
const per = (n, unit) => (n === -1 ? 'Không giới hạn' : `${n} ${unit}`);

export function buildPlanRows(tiers) {
  const rows = [
    {
      key: 'daily', label: 'Lượt tải mỗi ngày',
      get: (t) => per(t.daily_downloads, 'lượt'),
    },
    {
      key: 'quality', label: 'Chất lượng tối đa',
      get: (t) => qualityLabel(t.max_height),
    },
    {
      key: 'youtube', label: 'YouTube',
      // youtube_max_height 0 = no YouTube-specific cap (production 13/10):
      // the general quality limit applies, so say nothing about height.
      get: (t) => (t.youtube_max_height > 0
        ? `${per(t.youtube_per_day, 'video/ngày')}, đến ${qualityLabel(t.youtube_max_height)}`
        : per(t.youtube_per_day, 'video/ngày')),
    },
    {
      key: 'batch', label: 'Dán nhiều link một lần',
      get: (t) => (t.batch_links > 1 ? `Tối đa ${t.batch_links} link` : false),
    },
    { key: 'zip', label: 'Tải ZIP', get: (t) => !!t.zip },
    {
      key: 'watch', label: 'Theo dõi kênh',
      get: (t) => {
        if (!(t.watch_sources > 0)) return false;
        const extra = [t.watch_instagram && 'Instagram', t.watch_webhook && 'webhook'].filter(Boolean);
        return `${t.watch_sources} nguồn${extra.length ? ` + ${extra.join(', ')}` : ''}`;
      },
      hideIfNone: (ts) => !ts.some((t) => t.watch_sources > 0),
    },
    {
      key: 'spotify', label: 'Spotify nghệ sĩ',
      get: (t) => `Đến ${t.spotify_artist_tracks} bài`,
    },
    {
      key: 'smart', label: 'Phân tích thông minh',
      get: (t) => {
        if (!(t.smart_analysis_per_day > 0)) return false;
        const base = per(t.smart_analysis_per_day, 'lần/ngày');
        return t.smart_analysis_max_min ? `${base} (video đến ${t.smart_analysis_max_min} phút)` : base;
      },
    },
    {
      key: 'pins', label: 'Ghim tệp',
      get: (t) => (t.pins > 0 || t.pins === -1
        ? `${per(t.pins, 'tệp')}${t.pin_days > 0 ? `, giữ ${t.pin_days} ngày` : ''}` : false),
    },
    {
      key: 'schedules', label: 'Lịch tải',
      get: (t) => (t.schedules > 0 || t.schedules === -1 ? per(t.schedules, 'lịch') : false),
    },
    {
      // owner 14/10: monthly API call quota (backend app/core/api_quota.py)
      key: 'api', label: 'API key',
      get: (t) => {
        if (!t.api_key) return false;
        const n = t.api_calls_per_month;
        if (n === -1) return 'Không giới hạn lượt gọi';
        return n > 0 ? `${n.toLocaleString('vi-VN')} lượt gọi/tháng` : true;
      },
    },
  ];
  return rows.filter((r) => !(r.hideIfNone && r.hideIfNone(tiers)));
}
