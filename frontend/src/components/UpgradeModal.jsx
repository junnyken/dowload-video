import { X, Crown, Check, Zap } from 'lucide-react';
import { trackEvent, EVENT } from '../utils/trackEvent';
import { useAuth } from '../context/AuthContext';
import { usePlans, qualityLabel } from '../lib/usePlans';
import { buildPlanRows } from '../lib/planRows';

// Every number below comes from GET /api/v1/plans (src/lib/usePlans.js), the
// same table the server enforces. When it has not loaded, the subtitle drops
// the numbers instead of guessing them.
// wording: BA review
const perDay = (n) => (n === -1 ? 'không giới hạn lượt' : `${n} lượt`);

function buildCopy(code, { guest, free, pro }, signedIn) {
  const have = signedIn ? free : guest;
  switch (code) {
    case 'quota_exceeded_daily':
      return {
        title: 'Đã đạt giới hạn hôm nay',
        subtitle: have && pro
          ? `Gói hiện tại: ${perDay(have.daily_downloads)}/ngày. Pro: ${perDay(pro.daily_downloads)}/ngày.`
          : 'Nâng cấp Pro để có thêm lượt tải mỗi ngày.',
      };
    case 'tier_limit_quality':
      return {
        title: 'Chất lượng này dành cho Pro',
        subtitle: have && pro
          ? `Gói hiện tại tải tối đa ${qualityLabel(have.max_height)}. Pro tải tối đa ${qualityLabel(pro.max_height)}.`
          : 'Nâng cấp Pro để chọn chất lượng cao hơn.',
      };
    case 'tier_limit_batch':
      return {
        title: 'Đã vượt số link được dán một lần',
        subtitle: have && pro
          ? `Gói hiện tại: tối đa ${have.batch_links} link. Pro: tối đa ${pro.batch_links} link.`
          : 'Nâng cấp Pro để dán được nhiều link hơn.',
      };
    case 'tier_required_feature':
      return { title: 'Tính năng Pro', subtitle: 'Tính năng này chỉ dành cho thành viên Pro.' };
    case 'youtube_pro_only':
      return {
        title: 'Đã đạt giới hạn YouTube',
        subtitle: have && pro
          ? `Gói hiện tại: ${have.youtube_per_day === -1 ? 'không giới hạn' : have.youtube_per_day} video/ngày${have.youtube_max_height > 0 ? `, đến ${qualityLabel(have.youtube_max_height)}` : ''}.`
          : 'Nâng cấp Pro để có thêm hạn mức YouTube.',
      };
    case 'spotify_artist_full':
      return {
        title: 'Spotify nghệ sĩ: giới hạn số bài',
        subtitle: have && pro
          ? `Gói hiện tại: đến ${have.spotify_artist_tracks} bài. Pro: đến ${pro.spotify_artist_tracks} bài.`
          : 'Nâng cấp Pro để tải được nhiều bài hơn.',
      };
    case 'bulk_zip_pro':
      return {
        title: 'Tải ZIP dành cho Pro',
        subtitle: 'Đóng gói cả lượt tải thành một tệp ZIP chỉ dành cho thành viên Pro.',
      };
    default:
      return { title: 'Nâng cấp Pro', subtitle: 'Xem các gói và quyền lợi của từng gói.' };
  }
}

function CellValue({ value }) {
  if (value === true)  return <Check className="w-4 h-4 text-fg-2 mx-auto" aria-label="Có" />;
  if (value === false) return <X     className="w-4 h-4 text-fg-muted   mx-auto" aria-label="Không" />;
  return <span>{value}</span>;
}

export default function UpgradeModal({ isOpen, onClose, errorCode = null }) {
  const { isAuthenticated } = useAuth();
  const { guest, free, pro } = usePlans();

  if (!isOpen) return null;

  const copy = buildCopy(errorCode, { guest, free, pro }, isAuthenticated);
  const rows = free && pro ? buildPlanRows([free, pro]) : [];

  const handleBackdrop = (e) => {
    if (e.target === e.currentTarget) onClose();
  };

  const handleUpgrade = () => {
    // paywall_seen fires when this modal opens; this records the intent to climb it.
    trackEvent(EVENT.UPGRADE_CLICKED, { trigger: errorCode || 'unknown' });
    onClose();
    window.history.pushState({}, '', '/pricing');
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
          aria-label="Đóng"
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
                  <th className="text-left px-3 py-2.5 text-fg-muted font-medium w-2/5">Tính năng</th>
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
            Xem bảng giá Pro →
          </button>

          <button
            onClick={onClose}
            className="w-full py-2.5 px-6 rounded-xl text-fg-muted hover:text-fg hover:bg-surface text-sm transition-colors"
          >
            Để sau
          </button>
        </div>
      </div>
    </div>
  );
}
