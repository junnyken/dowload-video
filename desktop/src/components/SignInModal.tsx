import { useCallback, useEffect, useRef, useState } from 'react';
import { ExternalLink, Globe } from 'lucide-react';
import { Button, Modal, Spinner } from './ui';
import { cancelBrowserSignIn, signInWithBrowser } from '../lib/auth';
import { errorMessage, toAppError } from '../lib/errors';
import { WEBSITE_URL } from '../lib/config';
import { openSignIn, toast } from '../lib/ui';
import { CopyLink } from './CopyLink';

/**
 * Since 0.4.0 the app does not take a password: sign-in happens on the
 * website (which has captcha protection) and comes back over a one-shot
 * 127.0.0.1 callback. See lib/auth.ts and docs/desktop/C1-CONTRACT.md §5.
 */
export function SignInModal() {
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const waitingRef = useRef(false);
  // Each click starts a new attempt; Rust cancels the older one, and only the
  // latest attempt may update this dialog.
  const attempt = useRef(0);

  async function start() {
    const mine = ++attempt.current;
    waitingRef.current = true;
    setWaiting(true);
    setError(null);
    try {
      await signInWithBrowser();
      if (mine !== attempt.current) return;
      openSignIn(false);
      toast('success', 'Đã đăng nhập.');
    } catch (err) {
      if (mine !== attempt.current) return;
      const code = toAppError(err).code;
      if (code !== 'cancelled') {
        setError(code === 'timeout'
          ? 'Hết thời gian chờ đăng nhập trong trình duyệt (5 phút). Hãy thử lại.'
          : errorMessage(code));
      }
    } finally {
      if (mine === attempt.current) {
        waitingRef.current = false;
        setWaiting(false);
      }
    }
  }

  const close = useCallback(() => {
    if (waitingRef.current) cancelBrowserSignIn();
    openSignIn(false);
  }, []);

  // Unmounting stops the loopback listener too.
  useEffect(() => () => { if (waitingRef.current) cancelBrowserSignIn(); }, []);

  return (
    <Modal title="Đăng nhập VidGrab" onClose={close}>
      <div className="flex flex-col gap-3">
        <p className="text-[13px] text-fg-muted">Đăng nhập để xem lịch sử tải trên web và đồng bộ lịch sử từ máy này.</p>
        <p className="text-[13px] text-fg-muted">
          Ứng dụng sẽ mở trang đăng nhập VidGrab trong trình duyệt. Đăng nhập xong, trình duyệt tự chuyển phiên đăng nhập
          về ứng dụng — ứng dụng không nhận mật khẩu của bạn.
        </p>
        {error && (
          <p role="alert" className="rounded-lg bg-danger-soft px-3 py-2 text-[13px] text-danger">{error}</p>
        )}
        {waiting ? (
          <div className="flex flex-col gap-2">
            <p role="status" className="flex items-center gap-2 rounded-lg bg-surface-2 px-3 py-2 text-[13px] text-fg-2">
              <Spinner />
              Đang chờ bạn đăng nhập trong trình duyệt…
            </p>
            <div className="flex gap-2">
              <Button variant="secondary" className="flex-1" onClick={() => void start()}>Mở lại trình duyệt</Button>
              <Button variant="secondary" className="flex-1" onClick={cancelBrowserSignIn}>Huỷ</Button>
            </div>
          </div>
        ) : (
          <Button variant="primary" icon={<Globe size={16} />} onClick={() => void start()}>
            Đăng nhập qua trình duyệt
          </Button>
        )}
        <div className="flex flex-wrap items-center justify-between gap-2 text-[13px] text-fg-muted">
          <span className="inline-flex items-center gap-1"><ExternalLink size={13} aria-hidden />Tạo tài khoản / Quên mật khẩu:</span>
          <CopyLink url={WEBSITE_URL} />
        </div>
      </div>
    </Modal>
  );
}
