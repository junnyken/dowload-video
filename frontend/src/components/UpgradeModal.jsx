import { X, Crown, Check, Zap } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';
import { trackEvent, EVENT } from '../utils/trackEvent';
import { useAuth } from '../context/AuthContext';
import { usePlans, qualityLabel } from '../lib/usePlans';
import { buildPlanRows } from '../lib/planRows';

// Every number below comes from GET /api/v1/plans (src/lib/usePlans.js), the
// same table the server enforces. When it has not loaded, the subtitle drops
// the numbers instead of guessing them.
// wording: BA review
const perDay = (t, n) => (n === -1 ? t('unlimitedPerDay') : t('perDay', { count: n }));

// Copy lives in upgrade.json under <error code>.title / .subtitle / .fallback.
function buildCopy(t, code, { guest, free, pro }, signedIn) {
  const have = signedIn ? free : guest;
  const both = !!(have && pro);
  switch (code) {
    case 'quota_exceeded_daily':
      return {
        title: t('quota_exceeded_daily.title'),
        subtitle: both
          ? t('quota_exceeded_daily.subtitle', { have: perDay(t, have.daily_downloads), pro: perDay(t, pro.daily_downloads) })
          : t('quota_exceeded_daily.fallback'),
      };
    case 'tier_limit_quality':
      return {
        title: t('tier_limit_quality.title'),
        subtitle: both
          ? t('tier_limit_quality.subtitle', { have: qualityLabel(have.max_height), pro: qualityLabel(pro.max_height) })
          : t('tier_limit_quality.fallback'),
      };
    case 'tier_limit_batch':
      return {
        title: t('tier_limit_batch.title'),
        subtitle: both
          ? t('tier_limit_batch.subtitle', { have: have.batch_links, pro: pro.batch_links })
          : t('tier_limit_batch.fallback'),
      };
    case 'tier_required_feature':
      return { title: t('tier_required_feature.title'), subtitle: t('tier_required_feature.subtitle') };
    case 'youtube_pro_only':
      return {
        title: t('youtube_pro_only.title'),
        subtitle: both
          ? t('youtube_pro_only.subtitle', {
              limit: have.youtube_per_day === -1 ? t('youtube_pro_only.unlimited') : have.youtube_per_day,
              extra: have.youtube_max_height > 0 ? t('youtube_pro_only.upTo', { quality: qualityLabel(have.youtube_max_height) }) : '',
            })
          : t('youtube_pro_only.fallback'),
      };
    case 'spotify_artist_full':
      return {
        title: t('spotify_artist_full.title'),
        subtitle: both
          ? t('spotify_artist_full.subtitle', { have: have.spotify_artist_tracks, pro: pro.spotify_artist_tracks })
          : t('spotify_artist_full.fallback'),
      };
    case 'bulk_zip_pro':
      return { title: t('bulk_zip_pro.title'), subtitle: t('bulk_zip_pro.subtitle') };
    default:
      return { title: t('default.title'), subtitle: t('default.subtitle') };
  }
}

function CellValue({ value }) {
  const { t } = useTranslation('upgrade');
  if (value === true)  return <Check className="w-4 h-4 text-fg-2 mx-auto" aria-label={t('yes')} />;
  if (value === false) return <X     className="w-4 h-4 text-fg-muted   mx-auto" aria-label={t('no')} />;
  return <span>{value}</span>;
}

export default function UpgradeModal({ isOpen, onClose, errorCode = null }) {
  const { t } = useTranslation('upgrade');
  const lp = useLocalePath();
  const { isAuthenticated } = useAuth();
  const { guest, free, pro } = usePlans();

  if (!isOpen) return null;

  const copy = buildCopy(t, errorCode, { guest, free, pro }, isAuthenticated);
  const rows = free && pro ? buildPlanRows([free, pro]) : [];

  const handleBackdrop = (e) => {
    if (e.target === e.currentTarget) onClose();
  };

  const handleUpgrade = () => {
    // paywall_seen fires when this modal opens; this records the intent to climb it.
    trackEvent(EVENT.UPGRADE_CLICKED, { trigger: errorCode || 'unknown' });
    onClose();
    window.history.pushState({}, '', lp('/pricing'));
    window.dispatchEvent(new PopStateEvent('popstate'));
    window.scrollTo(0, 0);
  };

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm"
      onClick={handleBackdrop}
      aria-modal="true"
      role="dialog"
      aria-labelledby="upgrade-modal-title"
    >
      {/* Panel */}
      <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto bg-canvas border border-line rounded-2xl shadow-2xl">

        {/* Top accent bar */}
        <div className="h-1 w-full bg-accent" />

        {/* Close */}
        <button
          onClick={onClose}
          aria-label={t('close')}
          className="absolute top-4 right-4 p-1.5 rounded-lg text-fg-muted hover:text-fg hover:bg-surface transition-colors"
        >
          <X className="w-4 h-4" />
        </button>

        <div className="px-6 pt-6 pb-7">
          {/* Header */}
          <div className="flex items-center gap-3 mb-1">
            <div className="flex-shrink-0 w-9 h-9 rounded-xl bg-accent flex items-center justify-center shadow-lg">
              <Crown className="w-4 h-4 text-accent-fg" />
            </div>
            <h2
              id="upgrade-modal-title"
              className="text-lg font-bold text-fg leading-snug"
            >
              {copy.title}
            </h2>
          </div>
          <p className="text-sm text-fg-muted mb-5 pl-12">{copy.subtitle}</p>

          {/* Comparison table */}
          {rows.length > 0 && <div className="rounded-xl border border-line overflow-hidden mb-5">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-surface">
                  <th className="text-left px-3 py-2.5 text-fg-muted font-medium w-2/5">{t('feature')}</th>
                  <th className="text-center px-3 py-2.5 text-fg-muted font-medium w-[30%]">Free</th>
                  <th className="text-center px-3 py-2.5 text-fg-2 font-semibold w-[30%]">Pro</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr
                    key={row.label}
                    className={i % 2 === 0 ? 'bg-canvas' : 'bg-surface-2'}
                  >
                    <td className="px-3 py-2 text-fg-2">{row.label}</td>
                    <td className="px-2 py-2 text-center text-xs text-fg-muted">
                      <CellValue value={row.get(free)} />
                    </td>
                    <td className="px-2 py-2 text-center text-xs text-fg font-medium">
                      <CellValue value={row.get(pro)} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}

          <button
            onClick={handleUpgrade}
            className="w-full flex items-center justify-center gap-2 py-3 px-6 rounded-xl bg-accent text-accent-fg font-semibold text-sm shadow-lg hover:opacity-90 active:scale-[0.98] transition-all mb-2"
          >
            <Zap className="w-4 h-4" />
            {t('viewPricing')}
          </button>

          <button
            onClick={onClose}
            className="w-full py-2.5 px-6 rounded-xl text-fg-muted hover:text-fg hover:bg-surface text-sm transition-colors"
          >
            {t('later')}
          </button>
        </div>
      </div>
    </div>
  );
}
