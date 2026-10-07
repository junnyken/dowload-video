/**
 * Supabase Auth errors -> Vietnamese messages for the auth forms.
 * Wording is listed for BA review in docs/runbooks/antispam-signup.md.
 */
export const MSG = {
  captchaNeeded: 'Vui lòng hoàn tất bước xác minh chống spam bên dưới rồi thử lại.',
  captchaFailed: 'Xác minh chống spam không thành công hoặc đã hết hạn. Vui lòng xác minh lại rồi thử lần nữa.',
  captchaLoadFailed: 'Không tải được bước xác minh chống spam. Hãy kiểm tra kết nối, tắt chặn quảng cáo cho trang này rồi tải lại trang.',
  disposableEmail: 'Không nhận đăng ký bằng email tạm thời (email dùng một lần). Vui lòng dùng email thật như Gmail, Outlook hoặc email công ty.',
  signupRejected: 'Không tạo được tài khoản với email này. Nếu đây là email tạm thời (dùng một lần), hãy đăng ký bằng email thật như Gmail, Outlook hoặc email công ty.',
  invalidCredentials: 'Email hoặc mật khẩu không đúng.',
  emailNotConfirmed: 'Email chưa được xác nhận. Hãy mở thư xác nhận trong hộp thư trước khi đăng nhập.',
  rateLimited: 'Bạn thao tác quá nhanh. Vui lòng chờ một lát rồi thử lại.',
  network: 'Lỗi kết nối mạng. Hãy kiểm tra Internet rồi thử lại.',
  generic: 'Đã xảy ra lỗi. Vui lòng thử lại.',
};

export function authErrorMessage(error) {
  if (!error) return '';
  const code = String(error.code || '');
  const msg = String(error.message || '');
  const low = msg.toLowerCase();
  // GoTrue: 400 captcha_failed "captcha protection: request disallowed (...)"
  if (code === 'captcha_failed' || low.includes('captcha')) return MSG.captchaFailed;
  // A BEFORE INSERT trigger on auth.users (migration 036) surfaces as
  // 500 unexpected_failure "Database error saving new user".
  if (low.includes('database error saving new user')) return MSG.signupRejected;
  if (code === 'invalid_credentials' || low.includes('invalid login credentials')) return MSG.invalidCredentials;
  if (code === 'email_not_confirmed' || low.includes('email not confirmed')) return MSG.emailNotConfirmed;
  if (error.status === 429 || code.startsWith('over_') || low.includes('rate limit')) return MSG.rateLimited;
  if (error.name === 'AuthRetryableFetchError' || low.includes('failed to fetch')) return MSG.network;
  return msg || MSG.generic;
}
