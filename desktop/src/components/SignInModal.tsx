import { useState, type FormEvent } from 'react';
import { ExternalLink } from 'lucide-react';
import { Button, Modal, Spinner } from './ui';
import { signIn } from '../lib/auth';
import { errorMessage, toAppError } from '../lib/errors';
import { WEBSITE_URL } from '../lib/config';
import { openSignIn, toast } from '../lib/ui';
import { CopyLink } from './CopyLink';

export function SignInModal() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
      openSignIn(false);
      toast('success', 'Đã đăng nhập.');
    } catch (err) {
      setError(errorMessage(toAppError(err).code));
    } finally {
      setBusy(false);
    }
  }

  const field = 'h-10 w-full rounded-[10px] border border-line-strong bg-canvas px-3 text-sm text-fg placeholder:text-fg-muted';
  return (
    <Modal title="Đăng nhập VidGrab" onClose={() => openSignIn(false)}>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <p className="text-[13px] text-fg-muted">Đăng nhập để xem lịch sử tải trên web và đồng bộ lịch sử từ máy này.</p>
        <label className="flex flex-col gap-1 text-[13px] font-medium text-fg-2">
          Email
          <input type="email" required autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} className={field} placeholder="ban@example.com" />
        </label>
        <label className="flex flex-col gap-1 text-[13px] font-medium text-fg-2">
          Mật khẩu
          <input type="password" required autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className={field} />
        </label>
        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{error}</p>
        )}
        <Button type="submit" variant="primary" disabled={busy || !email || !password} icon={busy ? <Spinner /> : undefined}>
          {busy ? 'Đang đăng nhập…' : 'Đăng nhập'}
        </Button>
        <div className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-fg-muted">
          <span className="inline-flex items-center gap-1"><ExternalLink size={13} aria-hidden />Tạo tài khoản / Quên mật khẩu:</span>
          <CopyLink url={WEBSITE_URL} />
        </div>
      </form>
    </Modal>
  );
}
