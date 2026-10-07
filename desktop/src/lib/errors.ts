// Error code -> Vietnamese. Covers every code in C1-CONTRACT.md plus auth/API cases.

export type AppError = { code: string; message: string };

const MESSAGES: Record<string, string> = {
  invalid_url: 'Liên kết không hợp lệ. Hãy kiểm tra lại đường dẫn.',
  unsupported: 'Trang web này chưa được hỗ trợ hoặc liên kết không chứa video.',
  private_or_login: 'Video riêng tư hoặc cần đăng nhập mới xem được.',
  geo_blocked: 'Video bị chặn ở khu vực của bạn.',
  not_found: 'Không tìm thấy video. Có thể đã bị xoá hoặc liên kết sai.',
  forbidden: 'Máy chủ từ chối cho tải video này (liên kết tải có thể đã hết hạn). Hãy thử lại.',
  network: 'Lỗi kết nối mạng. Hãy kiểm tra Internet rồi thử lại.',
  disk_full: 'Ổ đĩa đã hết dung lượng. Hãy giải phóng chỗ trống hoặc chọn thư mục khác.',
  tool_missing: 'Thiếu thành phần tải video của ứng dụng. Hãy cài đặt lại VidGrab.',
  tool_tampered: 'Thành phần tải video không còn nguyên vẹn nên bị chặn. Hãy cài đặt lại VidGrab từ nguồn chính thức.',
  timeout: 'Quá thời gian chờ. Hãy thử lại.',
  cancelled: 'Đã huỷ.',
  unknown: 'Đã xảy ra lỗi không xác định. Hãy thử lại.',
  // UI / auth / API
  invalid_credentials: 'Email hoặc mật khẩu không đúng.',
  email_not_confirmed: 'Email chưa được xác nhận. Hãy mở email xác nhận trước khi đăng nhập.',
  rate_limited: 'Thao tác quá nhanh. Vui lòng chờ một lát rồi thử lại.',
  unauthorized: 'Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.',
  server: 'Máy chủ đang gặp sự cố. Hãy thử lại sau.',
  not_in_app: 'Tính năng này chỉ chạy trong ứng dụng VidGrab trên máy tính.',
  // Douyin through the VidGrab server (lib/douyin.ts). The server's own Vietnamese
  // text wins when we have it (rememberServerMessage); these are the fallbacks.
  'api:unsupported_url': 'Liên kết Douyin này chưa được hỗ trợ. Hãy dán liên kết kênh hoặc video Douyin.',
  'api:quota_exceeded': 'Bạn đã hết lượt tải còn lại trong hôm nay.',
  'api:quota_exceeded_daily': 'Bạn đã hết lượt tải trong hôm nay. Hãy thử lại vào ngày mai hoặc đăng nhập để có thêm lượt.',
  'api:budget_exceeded': 'Dịch vụ Douyin đã hết hạn mức hôm nay. Hãy thử lại sau.',
  'api:already_processing': 'Liên kết này đang được xử lý. Hãy đợi một lát.',
  'api:no_media_found': 'Không tìm thấy video nào ở liên kết Douyin này.',
  'api:platform_disabled': 'Tính năng Douyin đang tạm tắt. Hãy thử lại sau.',
  'api:provider_unavailable': 'Dịch vụ Douyin tạm thời không dùng được. Hãy thử lại sau.',
  'api:other': 'Không lấy được dữ liệu Douyin. Hãy thử lại sau.',
  // Platform accounts (PLAN-32D §3). wording: BA review
  cookie_required: 'Video này cần đăng nhập. Hãy kết nối tài khoản của nền tảng này trong Cài đặt → Tài khoản nền tảng rồi tải lại.',
  cookie_expired: 'Phiên đăng nhập đã lưu có thể đã hết hạn. Hãy kết nối lại tài khoản trong Cài đặt → Tài khoản nền tảng rồi tải lại.',
  login_link_invalid: 'Phiên đăng nhập từ trình duyệt không dùng được hoặc đã hết hạn. Hãy bấm “Đăng nhập qua trình duyệt” lần nữa.',
};

/** A button shown next to the error text (QueueScreen). wording: BA review */
export type ErrorAction = { kind: 'reconnect_cookies'; label: string };
export function errorAction(code: string | null | undefined): ErrorAction | null {
  return code === 'cookie_required' || code === 'cookie_expired' ? { kind: 'reconnect_cookies', label: 'Kết nối lại' } : null;
}

// Vietnamese text the server sent with an error code (latest one per code).
const serverMessages = new Map<string, string>();
export function rememberServerMessage(code: string, text: string) {
  if (code in MESSAGES && text.trim()) serverMessages.set(code, text.trim());
}

export function errorMessage(code: string | null | undefined): string {
  return serverMessages.get(code ?? '') ?? MESSAGES[code ?? 'unknown'] ?? MESSAGES.unknown;
}

/** Normalises whatever invoke()/fetch/supabase threw into { code, message }. */
export function toAppError(e: unknown): AppError {
  if (e && typeof e === 'object') {
    const o = e as Record<string, unknown>;
    if (typeof o.code === 'string' && o.code in MESSAGES) {
      return { code: o.code, message: typeof o.message === 'string' ? o.message : '' };
    }
    if (typeof o.code === 'string' && typeof o.message === 'string' && !('status' in o)) {
      // Rust CommandError with a code we do not know yet.
      return { code: 'unknown', message: o.message };
    }
  }
  return { code: 'unknown', message: e instanceof Error ? e.message : String(e) };
}

/** Supabase auth errors -> our codes (message strings are English from GoTrue). */
export function authErrorCode(e: unknown): string {
  const o = (e ?? {}) as { message?: string; status?: number; name?: string; code?: string };
  const msg = (o.message ?? '').toLowerCase();
  if (o.code === 'invalid_credentials' || msg.includes('invalid login credentials')) return 'invalid_credentials';
  if (o.code === 'email_not_confirmed' || msg.includes('email not confirmed')) return 'email_not_confirmed';
  if (o.code === 'refresh_token_not_found' || o.code === 'refresh_token_already_used' || o.code === 'session_not_found'
      || msg.includes('refresh token')) return 'login_link_invalid';
  if (o.status === 429 || msg.includes('rate limit')) return 'rate_limited';
  if (o.name === 'AuthRetryableFetchError' || msg.includes('fetch') || msg.includes('network') || o.status === 0) return 'network';
  if ((o.status ?? 0) >= 500) return 'server';
  return 'unknown';
}
