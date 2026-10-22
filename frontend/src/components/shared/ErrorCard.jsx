import { AlertCircle, RefreshCw, ExternalLink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import i18n from '../../i18n';

/**
 * Maps backend error_code values to friendly display info (label text:
 * results.json errorCard.codes.<error_code>, translated at render time).
 * Used by ErrorCard to render actionable failure messages.
 */
const ERROR_DISPLAY = {
  unsupported_url:           { icon: '🚫' },
  private_or_login_required: { icon: '🔒' },
  rate_limited:              { icon: '⏳' },
  temporary_blocked:         { icon: '🚧' },
  no_media_found:            { icon: '🔍' },
  processing_failed:         { icon: '⚙️' },
  drm_protected:             { icon: '🔐' },
  geo_blocked:               { icon: '🌐' },
  file_expired:              { icon: '⌛' },
  storage_limit_reached:     { icon: '💾' },
  quota_exceeded_daily:      { icon: '📊' },
  tier_limit_quality:        { icon: '⭐' },
  tier_limit_batch:          { icon: '📦' },
  tier_required_feature:     { icon: '🔓' },
  archive_sync_failed:       { icon: '🔄' },
  schedule_failed:           { icon: '📅' },
  invalid_url:               { icon: '🔗' },
  concurrency_limit:         { icon: '⏱️' },
  server_error:              { icon: '🛠️' },
};

/**
 * Parse a raw error response (HTTPException detail) into a normalised shape.
 * Accepts structured {error_code, user_message, retryable, suggested_action}
 * or a plain string for legacy compatibility.
 */
export function parseApiError(detail) {
  if (!detail) return { error_code: 'server_error', user_message: i18n.t('errorCard.unknownError', { ns: 'results' }), retryable: true, suggested_action: '' };
  if (typeof detail === 'string') {
    return {
      error_code: 'server_error',
      user_message: detail,
      retryable: true,
      suggested_action: '',
    };
  }
  return {
    error_code:       detail.error_code       || 'server_error',
    user_message:     detail.user_message     || detail.message || i18n.t('errorCard.unknownError', { ns: 'results' }),
    retryable:        detail.retryable        ?? true,
    suggested_action: detail.suggested_action || '',
  };
}

/**
 * ErrorCard — renders an API error with optional retry button.
 *
 * Props:
 *   error     {object|string}  raw API error detail (from JSON body)
 *   onRetry   {function}       called when user clicks Retry (only shown if retryable=true)
 *   compact   {boolean}        minimal single-line variant
 *   className {string}
 */
export default function ErrorCard({ error, onRetry, compact = false, className = '' }) {
  const { t } = useTranslation('results');
  if (!error) return null;
  const { error_code, user_message, retryable, suggested_action } = parseApiError(error);
  const display = ERROR_DISPLAY[error_code] || { icon: '⚠️' };
  const displayLabel = ERROR_DISPLAY[error_code] ? t(`errorCard.codes.${error_code}`) : t('errorCard.unknown');

  if (compact) {
    return (
      <div className={`flex items-center gap-2 px-3 py-2 rounded-lg bg-danger-soft border border-danger/25 text-xs text-danger ${className}`}>
        <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
        <span className="flex-1 truncate">{user_message}</span>
        {retryable && onRetry && (
          <button onClick={onRetry} className="flex-shrink-0 flex items-center gap-1 px-2 py-1 rounded bg-danger-soft hover:bg-danger/20 text-danger font-bold transition-colors cursor-pointer">
            <RefreshCw className="w-3 h-3" /> {t('errorCard.retry')}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`rounded-xl border border-danger/25 bg-danger-soft p-4 space-y-2 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-base leading-none">{display.icon}</span>
        <span className="text-danger font-bold text-sm">{displayLabel}</span>
        <span className="ml-auto text-[10px] text-fg-muted font-mono">{error_code}</span>
      </div>

      {/* Message */}
      <p className="text-fg-2 text-sm leading-relaxed">{user_message}</p>

      {/* Suggested action */}
      {suggested_action && (
        <p className="text-fg-muted text-xs">{suggested_action}</p>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1">
        {retryable && onRetry && (
          <button
            onClick={onRetry}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-danger-soft hover:bg-danger/20 border border-danger/30 text-danger text-xs font-bold transition-colors cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5" /> {t('errorCard.retry')}
          </button>
        )}
        {!retryable && (
          <span className="text-xs text-fg-muted flex items-center gap-1">
            <ExternalLink className="w-3 h-3" /> {t('errorCard.noRetry')}
          </span>
        )}
      </div>
    </div>
  );
}
