import { useState, useEffect } from 'react';
import { Clock, AlertTriangle, XCircle, RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';

/** Shows expiry countdown for a processed artifact. */
export default function ArtifactExpiry({ expiresAt, onReprocess }) {
  const { t } = useTranslation('common');
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(timer);
  }, []);

  if (!expiresAt) return null;

  const msLeft = new Date(expiresAt).getTime() - now;
  const minLeft = Math.floor(msLeft / 60000);

  if (msLeft <= 0) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-danger">
        <XCircle className="w-3 h-3" />
        {t('expiry.expired')}
        {onReprocess && (
          <button
            onClick={onReprocess}
            className="ml-1 flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-danger-soft hover:bg-danger/20 text-danger transition-colors cursor-pointer"
          >
            <RotateCcw className="w-2.5 h-2.5" /> {t('expiry.recreate')}
          </button>
        )}
      </span>
    );
  }

  if (minLeft < 5) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-accent-text animate-pulse">
        <AlertTriangle className="w-3 h-3" />
        {t('expiry.minutesLeft', { count: minLeft })}
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1 text-xs text-success">
      <Clock className="w-3 h-3" />
      {t('expiry.minutesLeft', { count: minLeft })}
    </span>
  );
}
