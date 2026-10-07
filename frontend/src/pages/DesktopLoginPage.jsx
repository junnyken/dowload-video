import { useMemo, useRef, useState } from 'react';
import { Monitor, Loader2, CheckCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import AuthModal from '../components/auth/AuthModal';
import TurnstileWidget from '../components/auth/TurnstileWidget';
import { TURNSTILE_SITE_KEY } from '../lib/turnstile';
import { authErrorMessage, MSG } from '../lib/authErrors';
import { desktopCallbackUrl, parseDesktopLoginParams, signInForDesktop } from '../lib/desktopHandoff';

/**
 * /desktop-login?port=<1024-65535>&state=<64 hex> — opened by the VidGrab
 * Windows app ("Đăng nhập qua trình duyệt"). See lib/desktopHandoff.js for the
 * protocol and docs/desktop/C1-CONTRACT.md §5.
 */
export default function DesktopLoginPage() {
  const params = useMemo(() => parseDesktopLoginParams(window.location.search), []);
  const { user } = useAuth();
  const [email, setEmail] = useState(user?.email || '');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);
  const [modal, setModal] = useState(null);   // 'signup' | 'reset' | null
  const [captcha, setCaptcha] = useState('');
  const turnstile = useRef(null);

  const field = 'w-full rounded-lg bg-canvas border border-line px-3 py-2.5 ' +
                'text-sm text-fg placeholder:text-fg-muted focus:border-accent focus:outline-none';

  if (!params) {
    return (
      <div className="max-w-md mx-auto space-y-4">
        <h1 className="text-xl font-bold text-fg">Đăng nhập ứng dụng VidGrab</h1>
        <div className="rounded-lg border border-danger/60 bg-danger-soft p-3 text-sm text-danger">
          Liên kết đăng nhập không hợp lệ hoặc đã bị sửa. Hãy mở ứng dụng VidGrab trên máy tính và bấm
          “Đăng nhập qua trình duyệt” lần nữa.
        </div>
      </div>
    );
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (busy) return;
    if (TURNSTILE_SITE_KEY && !captcha) { setError(MSG.captchaNeeded); return; }
    setBusy(true); setError('');
    const { refreshToken, error: err } = await signInForDesktop(email.trim(), password, captcha);
    turnstile.current?.reset();          // tokens are single-use
    if (err || !refreshToken) {
      setBusy(false);
      setError(err ? authErrorMessage(err) : MSG.generic);
      return;
    }
    let target;
    try {
      target = desktopCallbackUrl(params, refreshToken);
    } catch {
      setBusy(false);
      setError(MSG.generic);
      return;
    }
    setPassword('');
    setSent(true);
    // Top-level navigation to the app's loopback listener; replace() keeps
    // this page (with its state) out of the back button.
    window.location.replace(target);
  }

  if (sent) {
    return (
      <div className="max-w-md mx-auto text-center space-y-3">
        <CheckCircle className="w-10 h-10 mx-auto text-success" />
        <h1 className="text-xl font-bold text-fg">Đang chuyển về ứng dụng VidGrab…</h1>
        <p className="text-sm text-fg-muted">
          Nếu trình duyệt báo không kết nối được, hãy mở lại ứng dụng VidGrab và bấm
          “Đăng nhập qua trình duyệt” lần nữa.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto space-y-5">
      <div className="flex items-start gap-3">
        <div className="w-10 h-10 shrink-0 rounded-xl bg-accent flex items-center justify-center">
          <Monitor className="w-5 h-5 text-accent-fg" />
        </div>
        <div>
          <h1 className="text-xl font-bold text-fg">Đăng nhập ứng dụng VidGrab</h1>
          <p className="mt-1 text-sm text-fg-muted">
            Đăng nhập để kết nối ứng dụng VidGrab trên máy tính với tài khoản của bạn.
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-line bg-surface p-3 text-xs text-fg-muted">
        Chỉ dùng trang này khi bạn vừa bấm “Đăng nhập qua trình duyệt” trong ứng dụng VidGrab.
        Sau khi đăng nhập, trình duyệt sẽ chuyển thông tin đăng nhập thẳng về ứng dụng trên chính máy này.
      </div>

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-semibold text-fg-muted" htmlFor="dl-email">Email</label>
          <input id="dl-email" type="email" required autoComplete="email" value={email}
                 onChange={(e) => setEmail(e.target.value)} className={field} placeholder="ban@example.com" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold text-fg-muted" htmlFor="dl-pw">Mật khẩu</label>
          <input id="dl-pw" type="password" required autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} className={field} />
        </div>

        <TurnstileWidget ref={turnstile} onToken={setCaptcha} />

        {error && (
          <p role="alert" className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-sm text-danger">{error}</p>
        )}

        <button type="submit" disabled={busy || !email || !password}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-bold text-accent-fg flex items-center justify-center gap-2
                     disabled:opacity-40 disabled:cursor-not-allowed">
          {busy && <Loader2 className="w-4 h-4 animate-spin" />}
          {busy ? 'Đang đăng nhập…' : 'Đăng nhập và quay lại ứng dụng'}
        </button>
      </form>

      <div className="flex flex-wrap justify-between gap-2 text-xs text-fg-muted">
        <span>
          Chưa có tài khoản?{' '}
          <button type="button" onClick={() => setModal('signup')} className="font-semibold text-accent-text hover:underline">
            Đăng ký
          </button>
        </span>
        <button type="button" onClick={() => setModal('reset')} className="hover:text-accent-text">
          Quên mật khẩu?
        </button>
      </div>

      {modal && <AuthModal initialView={modal} onClose={() => setModal(null)} />}
    </div>
  );
}
