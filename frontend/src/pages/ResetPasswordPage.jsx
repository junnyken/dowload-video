import { useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { authLinkError } from '../lib/authRecovery';

/**
 * Landing page for the Supabase password-recovery link.
 *
 * resetPassword() has always sent users to <origin>/reset-password, but that
 * path was not in PATH_MAP, so the link fell through to the landing page and
 * nothing ever asked for a new password. The recovery token in the URL fragment
 * signs the user in silently, which looks like the reset "worked" while the old
 * password is still the only one that exists.
 */

const MIN_LENGTH = 8;

export default function ResetPasswordPage() {
  const { updatePassword, session, loading } = useAuth();
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  // Supabase reports a bad or expired link in the URL fragment rather than
  // throwing, so read it instead of leaving the user on a form that cannot work.
  const [linkError, setLinkError] = useState('');

  useEffect(() => {
    // Read the snapshot, not window.location.hash: the Supabase client clears
    // the fragment on load, well before this effect runs.
    const err = authLinkError();
    if (err) setLinkError(err);
  }, []);

  const tooShort = pw.length > 0 && pw.length < MIN_LENGTH;
  const mismatch = pw2.length > 0 && pw !== pw2;
  const canSubmit = pw.length >= MIN_LENGTH && pw === pw2 && !busy;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setError('');
    const { error } = await updatePassword(pw);
    setBusy(false);
    if (error) { setError(error.message || 'Không đổi được mật khẩu.'); return; }
    setDone(true);
    // Clear the recovery token out of the address bar once it is spent.
    window.history.replaceState({}, '', '/reset-password');
  }

  const field = 'w-full rounded-lg bg-[#0f1720] border border-slate-700 px-3 py-2.5 ' +
                'text-sm text-slate-100 placeholder-slate-500 focus:border-primary focus:outline-none';

  if (done) {
    return (
      <div className="max-w-md mx-auto text-center space-y-4">
        <div className="text-4xl">✓</div>
        <h1 className="text-xl font-bold text-white">Đã đổi mật khẩu</h1>
        <p className="text-sm text-slate-400">
          Bạn có thể dùng mật khẩu mới ngay từ bây giờ.
        </p>
        <a href="/" className="inline-block rounded-lg bg-primary px-4 py-2 text-sm font-bold text-[#012622]">
          Về trang chủ
        </a>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-bold text-white">Đặt mật khẩu mới</h1>
        <p className="mt-1 text-sm text-slate-400">
          Nhập mật khẩu mới cho tài khoản của bạn.
        </p>
      </div>

      {linkError && (
        <div className="rounded-lg border border-red-800/60 bg-red-950/40 p-3 text-sm text-red-300">
          Liên kết không dùng được: {linkError}
          <div className="mt-2 text-xs text-red-400/80">
            Liên kết đặt lại mật khẩu chỉ dùng một lần và sẽ hết hạn. Hãy yêu cầu gửi lại.
          </div>
        </div>
      )}

      {/* Without a recovery session there is nothing to update — say so instead
          of letting the user type a password that cannot be saved. */}
      {!loading && !session && !linkError && (
        <div className="rounded-lg border border-amber-800/60 bg-amber-950/30 p-3 text-sm text-amber-200">
          Không tìm thấy phiên đặt lại mật khẩu. Hãy mở trang này từ liên kết trong email.
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-semibold text-slate-400">Mật khẩu mới</label>
          <input type={show ? 'text' : 'password'} value={pw} onChange={e => setPw(e.target.value)}
                 autoComplete="new-password" className={field} placeholder={`Ít nhất ${MIN_LENGTH} ký tự`} />
          {tooShort && <p className="mt-1 text-xs text-amber-400">Cần ít nhất {MIN_LENGTH} ký tự.</p>}
        </div>

        <div>
          <label className="mb-1 block text-xs font-semibold text-slate-400">Nhập lại mật khẩu</label>
          <input type={show ? 'text' : 'password'} value={pw2} onChange={e => setPw2(e.target.value)}
                 autoComplete="new-password" className={field} placeholder="Nhập lại để xác nhận" />
          {mismatch && <p className="mt-1 text-xs text-amber-400">Hai mật khẩu chưa khớp.</p>}
        </div>

        <label className="flex items-center gap-2 text-xs text-slate-400">
          <input type="checkbox" checked={show} onChange={e => setShow(e.target.checked)} />
          Hiện mật khẩu
        </label>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button type="submit" disabled={!canSubmit}
          className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-bold text-[#012622]
                     disabled:opacity-40 disabled:cursor-not-allowed">
          {busy ? 'Đang lưu…' : 'Đổi mật khẩu'}
        </button>
      </form>
    </div>
  );
}
