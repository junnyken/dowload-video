import { useEffect, useState } from 'react';
import { Download, Loader2, RefreshCw, Monitor } from 'lucide-react';
import { apiUrl } from '../lib/apiBase';
import { Trans, useTranslation } from 'react-i18next';

// Public page (no login) for the VidGrab Windows app — task #6125.
// The installer is ~100 MB, so the button is a plain <a href>, never a fetch.

function safeHttpsUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const u = new URL(value.trim());
    return u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

export default function DownloadPage() {
  const { t } = useTranslation('downloadApp');
  const [state, setState] = useState('loading'); // loading | ready | error
  const [info, setInfo] = useState(null);

  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl('/api/v1/client/version'))
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json();
      })
      .then((data) => {
        if (cancelled) return;
        setInfo(data && typeof data === 'object' ? data : {});
        setState('ready');
      })
      .catch(() => { if (!cancelled) setState('error'); });
    return () => { cancelled = true; };
  }, [attempt]);

  const retry = () => { setState('loading'); setAttempt((n) => n + 1); };

  const url = safeHttpsUrl(info?.downloadUrl);
  const notes = typeof info?.notes === 'string' ? info.notes.trim() : '';

  return (
    <div className="max-w-2xl mx-auto px-4 md:px-8 py-8 md:py-12 text-fg">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 shrink-0 rounded-xl bg-accent flex items-center justify-center">
          <Monitor size={20} className="text-accent-fg" aria-hidden="true" />
        </div>
        {/* wording: BA review */}
        <h1 className="text-2xl md:text-3xl font-extrabold">{t('title')}</h1>
      </div>
      {/* wording: BA review */}
      <p className="text-fg-2 mb-6">
        {t('intro')}
      </p>

      <section className="rounded-xl border border-line bg-surface p-4 md:p-6 mb-6" aria-live="polite">
        {state === 'loading' && (
          <p className="flex items-center gap-2 text-fg-muted">
            <Loader2 size={18} className="animate-spin" aria-hidden="true" />
            {/* wording: BA review */}
            {t('checking')}
          </p>
        )}

        {state === 'error' && (
          <div role="alert">
            {/* wording: BA review */}
            <p className="text-danger mb-3">{t('versionError')}</p>
            <button
              type="button"
              onClick={retry}
              className="inline-flex items-center gap-2 rounded-lg border border-line px-4 py-2 text-sm font-semibold hover:bg-surface-2 transition-colors"
            >
              <RefreshCw size={16} aria-hidden="true" />
              {/* wording: BA review */}
              {t('retry')}
            </button>
          </div>
        )}

        {state === 'ready' && (
          <div>
            {info?.latest && (
              // wording: BA review
              <p className="font-semibold mb-1">{t('latest', { version: String(info.latest) })}</p>
            )}
            {notes && <p className="text-sm text-fg-muted whitespace-pre-line mb-3">{notes}</p>}
            {url ? (
              <a
                href={url}
                rel="noopener noreferrer"
                className="mt-2 inline-flex w-full sm:w-auto items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:bg-accent-hover transition-colors"
              >
                <Download size={18} aria-hidden="true" />
                {/* wording: BA review */}
                {t('downloadExe')}
              </a>
            ) : (
              // wording: BA review
              <p className="mt-2 text-fg-muted">{t('updating')}</p>
            )}
            {url && /^[0-9a-f]{64}$/.test(String(info?.sha256 || '')) && (
              <div className="mt-4 text-xs text-fg-muted">
                {/* wording: BA review */}
                <p className="mb-1"><Trans t={t} i18nKey="shaNote" components={{ code: <code /> }} /></p>
                <code className="block break-all rounded-lg bg-surface-2 px-2 py-1.5 text-fg-2 select-all">{info.sha256}</code>
              </div>
            )}
          </div>
        )}
      </section>

      <section className="mb-6">
        {/* wording: BA review */}
        <h2 className="text-lg font-bold mb-2">{t('reqTitle')}</h2>
        {/* wording: BA review */}
        <p className="text-fg-2">{t('reqBody')}</p>
      </section>

      <section className="mb-6">
        {/* wording: BA review */}
        <h2 className="text-lg font-bold mb-2">{t('installTitle')}</h2>
        <ul className="list-disc list-inside space-y-2 text-fg-2">
          {/* wording: BA review */}
          <li>{t('installSmartScreen')}</li>
          {/* wording: BA review */}
          <li>{t('installKeepData')}</li>
        </ul>
      </section>

      {/* wording: BA review */}
      <p className="text-sm text-fg-muted">
        {t('sharedAccount')}
      </p>
    </div>
  );
}
