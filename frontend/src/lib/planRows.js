import i18n from '../i18n';
import { qualityLabel } from './usePlans';

// Comparison rows built ONLY from the /api/v1/plans payload. Each row returns,
// per tier, a string (shown as text), true/false (tick / cross) or null (the
// row does not apply to that tier and is skipped). A row is dropped entirely
// when no tier has anything to show (e.g. Channel Watch switched off).
// wording: BA review (text: locales/<lang>/plans.json)
const tp = (key, opts) => i18n.t(key, { ns: 'plans', ...opts });
const per = (n, unit) => (n === -1 ? tp('unlimited') : tp(`unit.${unit}`, { count: n }));

export function buildPlanRows(tiers) {
  const rows = [
    {
      key: 'daily', label: tp('rows.daily'),
      get: (t) => per(t.daily_downloads, 'downloads'),
    },
    {
      key: 'quality', label: tp('rows.quality'),
      get: (t) => qualityLabel(t.max_height),
    },
    {
      key: 'youtube', label: tp('rows.youtube'),
      // youtube_max_height 0 = no YouTube-specific cap (production 13/10):
      // the general quality limit applies, so say nothing about height.
      get: (t) => (t.youtube_max_height > 0
        ? tp('youtubeUpTo', { per: per(t.youtube_per_day, 'videosPerDay'), quality: qualityLabel(t.youtube_max_height) })
        : per(t.youtube_per_day, 'videosPerDay')),
    },
    {
      key: 'batch', label: tp('rows.batch'),
      get: (t) => (t.batch_links > 1 ? tp('batchMax', { n: t.batch_links }) : false),
    },
    { key: 'zip', label: tp('rows.zip'), get: (t) => !!t.zip },
    {
      key: 'watch', label: tp('rows.watch'),
      get: (t) => {
        if (!(t.watch_sources > 0)) return false;
        const extra = [t.watch_instagram && 'Instagram', t.watch_webhook && 'webhook'].filter(Boolean);
        return `${tp('watchSources', { n: t.watch_sources })}${extra.length ? ` + ${extra.join(', ')}` : ''}`;
      },
      hideIfNone: (ts) => !ts.some((t) => t.watch_sources > 0),
    },
    {
      key: 'spotify', label: tp('rows.spotify'),
      get: (t) => tp('spotifyTracks', { n: t.spotify_artist_tracks }),
    },
    {
      key: 'smart', label: tp('rows.smart'),
      get: (t) => {
        if (!(t.smart_analysis_per_day > 0)) return false;
        const base = per(t.smart_analysis_per_day, 'timesPerDay');
        return t.smart_analysis_max_min ? tp('smartMax', { base, min: t.smart_analysis_max_min }) : base;
      },
    },
    {
      key: 'pins', label: tp('rows.pins'),
      get: (t) => (t.pins > 0 || t.pins === -1
        ? (t.pin_days > 0 ? tp('pinKeep', { base: per(t.pins, 'files'), days: t.pin_days }) : per(t.pins, 'files')) : false),
    },
    {
      key: 'schedules', label: tp('rows.schedules'),
      get: (t) => (t.schedules > 0 || t.schedules === -1 ? per(t.schedules, 'schedules') : false),
    },
    {
      // owner 14/10: monthly API call quota (backend app/core/api_quota.py)
      key: 'api', label: tp('rows.api'),
      get: (t) => {
        if (!t.api_key) return false;
        const n = t.api_calls_per_month;
        if (n === -1) return tp('apiUnlimited');
        return n > 0 ? tp('apiPerMonth', { n: n.toLocaleString(i18n.language === 'en' ? 'en-US' : 'vi-VN') }) : true;
      },
    },
  ];
  return rows.filter((r) => !(r.hideIfNone && r.hideIfNone(tiers)));
}
