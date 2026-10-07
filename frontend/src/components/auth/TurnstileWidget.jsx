import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { TURNSTILE_SITE_KEY, loadTurnstile } from '../../lib/turnstile';
import { MSG } from '../../lib/authErrors';

/**
 * Cloudflare Turnstile widget. Renders nothing when VITE_TURNSTILE_SITE_KEY is
 * empty. Reports the token through onToken ('' when there is none / it
 * expired). A token is single-use, so the form calls ref.reset() after every
 * attempt, successful or not.
 */
const TurnstileWidget = forwardRef(function TurnstileWidget({ onToken }, ref) {
  const box = useRef(null);
  const widgetId = useRef(null);
  const onTokenRef = useRef(onToken);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => { onTokenRef.current = onToken; }, [onToken]);

  useImperativeHandle(ref, () => ({
    reset() {
      onTokenRef.current?.('');
      if (widgetId.current != null && window.turnstile) {
        try { window.turnstile.reset(widgetId.current); } catch { /* widget gone */ }
      }
    },
  }), []);

  useEffect(() => {
    if (!TURNSTILE_SITE_KEY) return undefined;
    let cancelled = false;
    loadTurnstile()
      .then((ts) => {
        if (cancelled || !box.current) return;
        const dark = document.documentElement.getAttribute('data-theme') === 'dark';
        widgetId.current = ts.render(box.current, {
          sitekey: TURNSTILE_SITE_KEY,
          language: 'vi',
          theme: dark ? 'dark' : 'light',
          size: 'flexible',
          callback: (token) => onTokenRef.current?.(token),
          'expired-callback': () => onTokenRef.current?.(''),
          'timeout-callback': () => onTokenRef.current?.(''),
          'error-callback': () => onTokenRef.current?.(''),
        });
      })
      .catch(() => { if (!cancelled) setLoadError(true); });
    return () => {
      cancelled = true;
      if (widgetId.current != null && window.turnstile) {
        try { window.turnstile.remove(widgetId.current); } catch { /* already removed */ }
      }
      widgetId.current = null;
    };
  }, []);

  if (!TURNSTILE_SITE_KEY) return null;
  return (
    <div>
      <div ref={box} className="min-h-[65px]" data-testid="turnstile" />
      {loadError && <p className="mt-1 text-xs text-danger">{MSG.captchaLoadFailed}</p>}
    </div>
  );
});

export default TurnstileWidget;
