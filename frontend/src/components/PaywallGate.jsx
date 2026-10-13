import { Lock, Crown } from 'lucide-react';
import { useEntitlement } from '../hooks/useEntitlement';

// Descriptions only for features the plan table (GET /api/v1/plans) really
// lists. Cloud save, chapters, logo removal, priority queue and team
// workspace were advertised here but no code enforces them, so they are gone.
// wording: BA review
const FEATURE_DESCRIPTIONS = {
  bulk_zip:      'Tải nhiều file cùng lúc dưới dạng ZIP',
  youtube_video: 'Hạn mức YouTube cao hơn',
  ai_tools:      'Phân tích thông minh với hạn mức cao hơn',
  spotify_full:  'Tải được nhiều bài hơn khi tải theo nghệ sĩ Spotify',
  api_key:       'Tích hợp qua REST API với API key riêng',
  webhook:       'Nhận thông báo tự động qua webhook',
};

function navigate(path) {
  window.history.pushState({}, '', path);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

function DefaultOverlay({ feature }) {
  const description = FEATURE_DESCRIPTIONS[feature] || 'Tính năng này dành cho gói nâng cấp';
  const planLabel   = 'Pro';

  return (
    <div
      className="absolute inset-0 z-10 flex flex-col items-center justify-center gap-3
                 bg-surface/80 backdrop-blur-sm rounded-xl px-6 text-center"
    >
      <div className="w-12 h-12 rounded-full bg-surface border border-line
                      flex items-center justify-center">
        <Lock className="w-5 h-5 text-fg-muted" aria-hidden="true" />
      </div>

      <div className="space-y-1">
        <p className="text-fg font-semibold text-sm flex items-center justify-center gap-1.5">
          <Crown className="w-4 h-4 text-accent-text" aria-hidden="true" />
          Tính năng {planLabel}
        </p>
        <p className="text-fg-muted text-xs max-w-[240px] leading-relaxed">
          {description}
        </p>
      </div>

      <button
        onClick={() => navigate('/pricing')}
        className="mt-1 px-4 py-2 rounded-lg bg-accent hover:opacity-90
                   text-accent-fg text-xs font-semibold transition-colors focus:outline-none
                   focus:ring-2 focus:ring-accent focus:ring-offset-2
                   focus:ring-offset-canvas"
      >
        Nâng cấp lên {planLabel}
      </button>
    </div>
  );
}

/**
 * PaywallGate — wraps children with a paywall overlay when the user lacks
 * the required feature. When allowed, renders children normally.
 *
 * Props:
 *   feature      — entitlement key (e.g. 'ai_tools', 'bulk_zip')
 *   requiredPlan — display label ('pro' | 'team' | 'enterprise')
 *   children     — content to gate
 *   fallback     — optional custom ReactNode instead of default overlay
 */
export default function PaywallGate({
  feature,
  requiredPlan = 'pro',
  children,
  fallback,
}) {
  const { allowed, loading } = useEntitlement();

  // While loading, render children without overlay to avoid layout shift
  if (loading) {
    return <div className="relative">{children}</div>;
  }

  const isAllowed = allowed(feature);

  if (isAllowed) {
    return <>{children}</>;
  }

  return (
    <div className="relative">
      {/* Blur + pointer-events disabled so underlying content is visible but inaccessible */}
      <div className="select-none pointer-events-none opacity-40 blur-[1px]" aria-hidden="true">
        {children}
      </div>

      {fallback !== undefined ? (
        <div className="absolute inset-0 z-10 flex items-center justify-center">
          {fallback}
        </div>
      ) : (
        <DefaultOverlay feature={feature} requiredPlan={requiredPlan} />
      )}
    </div>
  );
}
