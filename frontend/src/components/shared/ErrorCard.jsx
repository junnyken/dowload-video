import { AlertCircle, RefreshCw, ExternalLink } from 'lucide-react';

/**
 * Maps backend error_code values to friendly Vietnamese display info.
 * Used by ErrorCard to render actionable failure messages.
 */
const ERROR_DISPLAY = {
  unsupported_url:           { icon: '🚫', label: 'URL không hỗ trợ' },
  private_or_login_required: { icon: '🔒', label: 'Nội dung riêng tư' },
  rate_limited:              { icon: '⏳', label: 'Giới hạn tốc độ' },
  temporary_blocked:         { icon: '🚧', label: 'Tạm thời bị chặn' },
  no_media_found:            { icon: '🔍', label: 'Không tìm thấy media' },
  processing_failed:         { icon: '⚙️', label: 'Xử lý thất bại' },
  drm_protected:             { icon: '🔐', label: 'Được bảo vệ DRM' },
  geo_blocked:               { icon: '🌐', label: 'Bị chặn theo khu vực' },
  file_expired:              { icon: '⌛', label: 'File đã hết hạn' },
  storage_limit_reached:     { icon: '💾', label: 'Hết giới hạn lưu trữ' },
  quota_exceeded_daily:      { icon: '📊', label: 'Hết giới hạn hôm nay' },
  tier_limit_quality:        { icon: '⭐', label: 'Giới hạn chất lượng' },
  tier_limit_batch:          { icon: '📦', label: 'Giới hạn số lượng' },
  tier_required_feature:     { icon: '🔓', label: 'Tính năng Pro' },
  archive_sync_failed:       { icon: '🔄', label: 'Lỗi đồng bộ Archive' },
  schedule_failed:           { icon: '📅', label: 'Lỗi lịch tải' },
  invalid_url:               { icon: '🔗', label: 'URL không hợp lệ' },
  concurrency_limit:         { icon: '⏱️', label: 'Server bận' },
  server_error:              { icon: '🛠️', label: 'Lỗi server' },
};

/**
 * Parse a raw error response (HTTPException detail) into a normalised shape.
 * Accepts structured {error_code, user_message, retryable, suggested_action}
 * or a plain string for legacy compatibility.
 */
export function parseApiError(detail) {
  if (!detail) return { error_code: 'server_error', user_message: 'Lỗi không xác định.', retryable: true, suggested_action: '' };
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
    user_message:     detail.user_message     || detail.message || 'Lỗi không xác định.',
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
  if (!error) return null;
  const { error_code, user_message, retryable, suggested_action } = parseApiError(error);
  const display = ERROR_DISPLAY[error_code] || { icon: '⚠️', label: 'Lỗi' };

  if (compact) {
    return (
      <div className={`flex items-center gap-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/25 text-xs text-red-300 ${className}`}>
        <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
        <span className="flex-1 truncate">{user_message}</span>
        {retryable && onRetry && (
          <button onClick={onRetry} className="flex-shrink-0 flex items-center gap-1 px-2 py-1 rounded bg-red-500/20 hover:bg-red-500/30 text-red-300 font-bold transition-colors cursor-pointer">
            <RefreshCw className="w-3 h-3" /> Thử lại
          </button>
        )}
      </div>
    );
  }

  return (
    <div className={`rounded-xl border border-red-500/25 bg-red-500/8 p-4 space-y-2 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2">
        <span className="text-base leading-none">{display.icon}</span>
        <span className="text-red-300 font-bold text-sm">{display.label}</span>
        <span className="ml-auto text-[10px] text-slate-600 font-mono">{error_code}</span>
      </div>

      {/* Message */}
      <p className="text-slate-300 text-sm leading-relaxed">{user_message}</p>

      {/* Suggested action */}
      {suggested_action && (
        <p className="text-slate-500 text-xs">{suggested_action}</p>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2 pt-1">
        {retryable && onRetry && (
          <button
            onClick={onRetry}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red-500/15 hover:bg-red-500/25 border border-red-500/30 text-red-300 text-xs font-bold transition-colors cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5" /> Thử lại
          </button>
        )}
        {!retryable && (
          <span className="text-xs text-slate-600 flex items-center gap-1">
            <ExternalLink className="w-3 h-3" /> Retry không khả thi với lỗi này
          </span>
        )}
      </div>
    </div>
  );
}
