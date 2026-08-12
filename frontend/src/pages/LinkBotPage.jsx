import { useState, useEffect } from 'react';
import { Bot, Check, X, Link2, Unlink } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export default function LinkBotPage() {
  const { session, isAuthenticated } = useAuth();
  const token   = session?.access_token || localStorage.getItem('vg_token') || '';
  const apiBase = localStorage.getItem('vg_api_base') || '';

  // Extract token from URL: /link-bot?token=XXX
  const searchParams = new URLSearchParams(window.location.search);
  const linkToken    = searchParams.get('token') || '';

  const [status, setStatus]   = useState('idle');   // idle | linking | success | error | already | unlinked
  const [linkInfo, setLinkInfo] = useState(null);   // {linked, telegram_user_id, telegram_username}
  const [error, setError]     = useState('');

  // Load current link status
  useEffect(() => {
    if (!token) return;
    fetch(`${apiBase}/api/v1/telegram/link-status`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.json())
      .then(data => setLinkInfo(data))
      .catch(() => {});
  }, [apiBase, token]);

  const handleConfirmLink = async () => {
    if (!linkToken) return;
    setStatus('linking');
    setError('');
    try {
      const res = await fetch(
        `${apiBase}/api/v1/telegram/link-confirm?token=${encodeURIComponent(linkToken)}`,
        {
          method: 'POST',
          headers: { Authorization: `Bearer ${token}` },
        }
      );
      const data = await res.json();
      if (!res.ok) {
        setError(data?.detail?.user_message || data?.detail || `HTTP ${res.status}`);
        setStatus('error');
        return;
      }
      setStatus('success');
      setLinkInfo({ linked: true, telegram_user_id: data.telegram_user_id });
    } catch (e) {
      setError(e.message);
      setStatus('error');
    }
  };

  const handleUnlink = async () => {
    if (!window.confirm('Xác nhận huỷ kết nối Telegram?')) return;
    setStatus('linking');
    try {
      const res = await fetch(`${apiBase}/api/v1/telegram/unlink`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setLinkInfo({ linked: false });
      setStatus('unlinked');
    } catch (e) {
      setError(e.message);
      setStatus('error');
    }
  };

  if (!isAuthenticated) {
    return (
      <div className="min-h-[60vh] flex flex-col items-center justify-center px-4">
        <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center mb-4 shadow-lg">
          <Bot className="w-6 h-6 text-white" />
        </div>
        <h1 className="text-white text-xl font-bold mb-2">Kết nối Telegram Bot</h1>
        <p className="text-zinc-400 text-sm text-center mb-6">
          Đăng nhập VidGrab để kết nối tài khoản với Telegram Bot.
        </p>
        <button
          onClick={() => window.dispatchEvent(new CustomEvent('vg:open-auth'))}
          className="px-6 py-2.5 rounded-xl bg-gradient-to-r from-violet-500 to-indigo-500 text-white text-sm font-semibold hover:opacity-90 transition"
        >
          Đăng nhập
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Icon */}
        <div className="flex justify-center mb-5">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center shadow-lg">
            <Bot className="w-7 h-7 text-white" />
          </div>
        </div>

        <div className="bg-zinc-900 border border-zinc-700 rounded-2xl p-6 text-center">

          {/* Already linked */}
          {linkInfo?.linked && status === 'idle' && (
            <>
              <div className="w-9 h-9 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-3">
                <Check className="w-5 h-5 text-emerald-400" />
              </div>
              <h2 className="text-white font-bold text-base mb-1">Đã kết nối</h2>
              {linkInfo.telegram_username && (
                <p className="text-zinc-400 text-xs mb-1">
                  Telegram: @{linkInfo.telegram_username}
                </p>
              )}
              <p className="text-zinc-500 text-xs mb-5">
                Tài khoản VidGrab của bạn đã được liên kết với Telegram Bot.
              </p>

              {linkToken && (
                <div className="mb-4 p-3 rounded-xl bg-blue-950/30 border border-blue-700/40 text-left">
                  <p className="text-blue-300 text-xs">
                    Có vẻ bạn đang muốn kết nối lại. Nhấn "Cập nhật kết nối" để liên kết với phiên Telegram mới.
                  </p>
                </div>
              )}

              <div className="flex gap-2">
                {linkToken && (
                  <button
                    onClick={handleConfirmLink}
                    disabled={status === 'linking'}
                    className="flex-1 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-500 text-white text-sm font-semibold disabled:opacity-60 transition-colors"
                  >
                    {status === 'linking' ? 'Đang cập nhật...' : 'Cập nhật kết nối'}
                  </button>
                )}
                <button
                  onClick={handleUnlink}
                  disabled={status === 'linking'}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-sm font-semibold disabled:opacity-60 transition-colors"
                >
                  <Unlink className="w-3.5 h-3.5" />
                  Huỷ kết nối
                </button>
              </div>
            </>
          )}

          {/* Ready to link */}
          {!linkInfo?.linked && status === 'idle' && linkToken && (
            <>
              <div className="w-9 h-9 rounded-full bg-blue-500/20 flex items-center justify-center mx-auto mb-3">
                <Link2 className="w-5 h-5 text-blue-400" />
              </div>
              <h2 className="text-white font-bold text-base mb-1">Kết nối tài khoản VidGrab</h2>
              <p className="text-zinc-400 text-xs mb-5">
                Kết nối để đồng bộ quota và tính năng Pro với Telegram Bot.
              </p>
              <button
                onClick={handleConfirmLink}
                className="w-full py-3 rounded-xl bg-gradient-to-r from-blue-600 to-blue-500 hover:opacity-90 text-white text-sm font-semibold transition-all active:scale-[0.98]"
              >
                Xác nhận kết nối
              </button>
            </>
          )}

          {/* No token, not linked */}
          {!linkInfo?.linked && status === 'idle' && !linkToken && (
            <>
              <div className="w-9 h-9 rounded-full bg-zinc-700 flex items-center justify-center mx-auto mb-3">
                <Bot className="w-5 h-5 text-zinc-400" />
              </div>
              <h2 className="text-white font-bold text-base mb-1">Chưa kết nối Telegram</h2>
              <p className="text-zinc-400 text-xs mb-5">
                Mở Telegram Bot và dùng lệnh <code className="text-blue-400">/link</code> để nhận link kết nối.
              </p>
            </>
          )}

          {/* Linking in progress */}
          {status === 'linking' && (
            <>
              <div className="w-6 h-6 border-2 border-blue-500/30 border-t-blue-500 rounded-full animate-spin mx-auto mb-3" />
              <p className="text-zinc-400 text-sm">Đang xử lý...</p>
            </>
          )}

          {/* Success */}
          {status === 'success' && (
            <>
              <div className="w-9 h-9 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-3">
                <Check className="w-5 h-5 text-emerald-400" />
              </div>
              <h2 className="text-white font-bold text-base mb-1">Kết nối thành công!</h2>
              <p className="text-zinc-400 text-xs mb-5">
                Tài khoản VidGrab đã được liên kết với Telegram Bot. Quota và tính năng Pro sẽ đồng bộ tự động.
              </p>
              <button
                onClick={() => window.history.pushState({}, '', '/')}
                className="w-full py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white text-sm font-semibold transition-colors"
              >
                Về trang chủ
              </button>
            </>
          )}

          {/* Unlinked */}
          {status === 'unlinked' && (
            <>
              <div className="w-9 h-9 rounded-full bg-zinc-700 flex items-center justify-center mx-auto mb-3">
                <Unlink className="w-5 h-5 text-zinc-400" />
              </div>
              <h2 className="text-white font-bold text-base mb-1">Đã huỷ kết nối</h2>
              <p className="text-zinc-500 text-xs mb-5">
                Telegram Bot đã bị ngắt kết nối khỏi tài khoản VidGrab của bạn.
              </p>
            </>
          )}

          {/* Error */}
          {status === 'error' && (
            <>
              <div className="w-9 h-9 rounded-full bg-red-500/20 flex items-center justify-center mx-auto mb-3">
                <X className="w-5 h-5 text-red-400" />
              </div>
              <h2 className="text-white font-bold text-base mb-1">Đã xảy ra lỗi</h2>
              <p className="text-red-400 text-xs mb-5">{error || 'Vui lòng thử lại.'}</p>
              <button
                onClick={() => setStatus('idle')}
                className="w-full py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white text-sm font-semibold transition-colors"
              >
                Thử lại
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
