import { useState, useEffect } from 'react';
import { trackEvent, EVENT } from '../utils/trackEvent';
import { Bot, Check, X, Link2, Unlink } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';
import { useTranslation, Trans } from 'react-i18next';

export default function LinkBotPage() {
  const { t } = useTranslation('linkBot');
  const { session, isAuthenticated } = useAuth();
  const token   = session?.access_token || localStorage.getItem('vg_token') || '';
  const apiBase = API_BASE;

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
      trackEvent(EVENT.TELEGRAM_BOT_LINKED, {});
      setStatus('success');
      setLinkInfo({ linked: true, telegram_user_id: data.telegram_user_id });
    } catch (e) {
      setError(e.message);
      setStatus('error');
    }
  };

  const handleUnlink = async () => {
    if (!window.confirm(t('unlinkConfirm'))) return;
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
        <div className="w-12 h-12 rounded-2xl bg-accent flex items-center justify-center mb-4 shadow-lg">
          <Bot className="w-6 h-6 text-accent-fg" />
        </div>
        <h1 className="text-fg text-xl font-bold mb-2">{t('title')}</h1>
        <p className="text-fg-muted text-sm text-center mb-6">
          {t('loginPrompt')}
        </p>
        <button
          onClick={() => window.dispatchEvent(new CustomEvent('vg:open-auth'))}
          className="px-6 py-2.5 rounded-xl bg-accent text-accent-fg text-sm font-semibold hover:opacity-90 transition"
        >
          {t('login')}
        </button>
      </div>
    );
  }

  return (
    <div className="min-h-[60vh] flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-sm">
        {/* Icon */}
        <div className="flex justify-center mb-5">
          <div className="w-14 h-14 rounded-2xl bg-accent flex items-center justify-center shadow-lg">
            <Bot className="w-7 h-7 text-accent-fg" />
          </div>
        </div>

        <div className="bg-canvas border border-line rounded-2xl p-6 text-center">

          {/* Already linked */}
          {linkInfo?.linked && status === 'idle' && (
            <>
              <div className="w-9 h-9 rounded-full bg-success-soft flex items-center justify-center mx-auto mb-3">
                <Check className="w-5 h-5 text-success" />
              </div>
              <h2 className="text-fg font-bold text-base mb-1">{t('linked.title')}</h2>
              {linkInfo.telegram_username && (
                <p className="text-fg-muted text-xs mb-1">
                  Telegram: @{linkInfo.telegram_username}
                </p>
              )}
              <p className="text-fg-muted text-xs mb-5">
                {t('linked.desc')}
              </p>

              {linkToken && (
                <div className="mb-4 p-3 rounded-xl bg-surface-2 border border-line text-left">
                  <p className="text-fg-2 text-xs">
                    {t('linked.relinkHint')}
                  </p>
                </div>
              )}

              <div className="flex gap-2">
                {linkToken && (
                  <button
                    onClick={handleConfirmLink}
                    disabled={status === 'linking'}
                    className="flex-1 py-2.5 rounded-xl bg-accent hover:bg-accent-hover text-accent-fg text-sm font-semibold disabled:opacity-60 transition-colors"
                  >
                    {status === 'linking' ? t('linked.updating') : t('linked.update')}
                  </button>
                )}
                <button
                  onClick={handleUnlink}
                  disabled={status === 'linking'}
                  className="flex-1 flex items-center justify-center gap-1.5 py-2.5 rounded-xl bg-surface hover:bg-surface-2 text-fg-2 text-sm font-semibold disabled:opacity-60 transition-colors"
                >
                  <Unlink className="w-3.5 h-3.5" />
                  {t('unlink')}
                </button>
              </div>
            </>
          )}

          {/* Ready to link */}
          {!linkInfo?.linked && status === 'idle' && linkToken && (
            <>
              <div className="w-9 h-9 rounded-full bg-surface-2 flex items-center justify-center mx-auto mb-3">
                <Link2 className="w-5 h-5 text-fg-2" />
              </div>
              <h2 className="text-fg font-bold text-base mb-1">{t('ready.title')}</h2>
              <p className="text-fg-muted text-xs mb-5">
                {t('ready.desc')}
              </p>
              <button
                onClick={handleConfirmLink}
                className="w-full py-3 rounded-xl bg-accent hover:opacity-90 text-accent-fg text-sm font-semibold transition-all active:scale-[0.98]"
              >
                {t('ready.confirm')}
              </button>
            </>
          )}

          {/* No token, not linked */}
          {!linkInfo?.linked && status === 'idle' && !linkToken && (
            <>
              <div className="w-9 h-9 rounded-full bg-surface-2 flex items-center justify-center mx-auto mb-3">
                <Bot className="w-5 h-5 text-fg-muted" />
              </div>
              <h2 className="text-fg font-bold text-base mb-1">{t('none.title')}</h2>
              <p className="text-fg-muted text-xs mb-5">
                <Trans t={t} i18nKey="none.desc" components={{ code: <code className="text-fg-2" /> }} />
              </p>
            </>
          )}

          {/* Linking in progress */}
          {status === 'linking' && (
            <>
              <div className="w-6 h-6 border-2 border-line border-t-line rounded-full animate-spin mx-auto mb-3" />
              <p className="text-fg-muted text-sm">{t('processing')}</p>
            </>
          )}

          {/* Success */}
          {status === 'success' && (
            <>
              <div className="w-9 h-9 rounded-full bg-success-soft flex items-center justify-center mx-auto mb-3">
                <Check className="w-5 h-5 text-success" />
              </div>
              <h2 className="text-fg font-bold text-base mb-1">{t('success.title')}</h2>
              <p className="text-fg-muted text-xs mb-5">
                {t('success.desc')}
              </p>
              <button
                onClick={() => window.history.pushState({}, '', '/')}
                className="w-full py-2.5 rounded-xl bg-surface hover:bg-surface-2 text-fg text-sm font-semibold transition-colors"
              >
                {t('success.home')}
              </button>
            </>
          )}

          {/* Unlinked */}
          {status === 'unlinked' && (
            <>
              <div className="w-9 h-9 rounded-full bg-surface-2 flex items-center justify-center mx-auto mb-3">
                <Unlink className="w-5 h-5 text-fg-muted" />
              </div>
              <h2 className="text-fg font-bold text-base mb-1">{t('unlinked.title')}</h2>
              <p className="text-fg-muted text-xs mb-5">
                {t('unlinked.desc')}
              </p>
            </>
          )}

          {/* Error */}
          {status === 'error' && (
            <>
              <div className="w-9 h-9 rounded-full bg-danger-soft flex items-center justify-center mx-auto mb-3">
                <X className="w-5 h-5 text-danger" />
              </div>
              <h2 className="text-fg font-bold text-base mb-1">{t('error.title')}</h2>
              <p className="text-danger text-xs mb-5">{error || t('error.retryHint')}</p>
              <button
                onClick={() => setStatus('idle')}
                className="w-full py-2.5 rounded-xl bg-surface hover:bg-surface-2 text-fg text-sm font-semibold transition-colors"
              >
                {t('error.retry')}
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
