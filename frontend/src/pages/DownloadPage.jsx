import { useEffect, useState } from 'react';
import {
  Download, Loader2, RefreshCw, Monitor, Laptop, Smartphone,
  Zap, ExternalLink, Share2,
} from 'lucide-react';
import { apiUrl } from '../lib/apiBase';
import { Trans, useTranslation } from 'react-i18next';
import { usePWAInstall } from '../hooks/usePWAInstall';

// Public multi-platform download hub (Windows, macOS, Mobile PWA).
// The desktop installers are ~100 MB, so download buttons are plain <a href> links.

function safeHttpsUrl(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  try {
    const u = new URL(value.trim());
    return u.protocol === 'https:' ? u.href : null;
  } catch {
    return null;
  }
}

function detectInitialPlatform() {
  if (typeof navigator === 'undefined') return 'windows';
  const ua = navigator.userAgent || '';
  if (/Android|iPhone|iPad|iPod/i.test(ua)) return 'mobile';
  if (/Macintosh|Mac OS X/i.test(ua)) return 'macos';
  return 'windows';
}

const MACOS_RELEASES_FALLBACK = 'https://github.com/junnyken/vidgrab-releases/releases/latest';

export default function DownloadPage() {
  const { t } = useTranslation('downloadApp');
  const [platform, setPlatform] = useState(detectInitialPlatform);
  const [state, setState] = useState('loading'); // loading | ready | error
  const [info, setInfo] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const { isInstallable, triggerInstall } = usePWAInstall();

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

  const winUrl = safeHttpsUrl(info?.downloadUrl);
  const macUrl = safeHttpsUrl(info?.macosDownloadUrl);
  const notes = typeof info?.notes === 'string' ? info.notes.trim() : '';
  const androidSteps = t('mobileAndroidSteps', { returnObjects: true });
  const iosSteps = t('mobileIosSteps', { returnObjects: true });

  const tabs = [
    { id: 'windows', label: t('tabs.windows'), Icon: Monitor },
    { id: 'macos', label: t('tabs.macos'), Icon: Laptop },
    { id: 'mobile', label: t('tabs.mobile'), Icon: Smartphone },
  ];

  const HeaderIcon = platform === 'macos' ? Laptop : platform === 'mobile' ? Smartphone : Monitor;

  return (
    <div className="max-w-2xl mx-auto px-4 md:px-8 py-8 md:py-12 text-fg">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 shrink-0 rounded-xl bg-accent flex items-center justify-center">
          <HeaderIcon size={20} className="text-accent-fg" aria-hidden="true" />
        </div>
        <h1 className="text-2xl md:text-3xl font-extrabold">{t('title')}</h1>
      </div>
      <p className="text-fg-2 mb-6">{t('intro')}</p>

      {/* Platform Switcher */}
      <div className="flex flex-wrap gap-2 mb-5" role="tablist" aria-label="Platform">
        {tabs.map(({ id, label, Icon }) => {
          const active = platform === id;
          return (
            <button
              key={id}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setPlatform(id)}
              className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold border transition-colors cursor-pointer ${
                active
                  ? 'bg-accent text-accent-fg border-accent shadow-sm'
                  : 'bg-surface border-line text-fg-2 hover:bg-surface-2'
              }`}
            >
              <Icon size={16} aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>

      {platform !== 'mobile' ? (
        <>
          <section className="rounded-xl border border-line bg-surface p-4 md:p-6 mb-6" aria-live="polite">
            {state === 'loading' && (
              <p className="flex items-center gap-2 text-fg-muted">
                <Loader2 size={18} className="animate-spin" aria-hidden="true" />
                {t('checking')}
              </p>
            )}

            {state === 'error' && (
              <div role="alert">
                <p className="text-danger mb-3">{t('versionError')}</p>
                <button
                  type="button"
                  onClick={retry}
                  className="inline-flex items-center gap-2 rounded-lg border border-line px-4 py-2 text-sm font-semibold hover:bg-surface-2 transition-colors cursor-pointer"
                >
                  <RefreshCw size={16} aria-hidden="true" />
                  {t('retry')}
                </button>
              </div>
            )}

            {state === 'ready' && platform === 'windows' && (
              <div>
                {info?.latest && (
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <p className="font-semibold">{t('latest', { version: String(info.latest) })}</p>
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-accent-soft text-accent-text">
                      <Zap size={11} aria-hidden="true" /> aria2c 16x
                    </span>
                  </div>
                )}
                {notes && <p className="text-sm text-fg-muted whitespace-pre-line mb-3">{notes}</p>}
                {winUrl ? (
                  <a
                    href={winUrl}
                    rel="noopener noreferrer"
                    className="mt-2 inline-flex w-full sm:w-auto items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:bg-accent-hover transition-colors"
                  >
                    <Download size={18} aria-hidden="true" />
                    {t('downloadExe')}
                  </a>
                ) : (
                  <p className="mt-2 text-fg-muted">{t('updating')}</p>
                )}
                <p className="mt-3 text-xs text-fg-2 flex items-center gap-1.5">
                  <Zap size={13} className="text-accent-text shrink-0" aria-hidden="true" />
                  {t('windowsTurboNote')}
                </p>
                {winUrl && /^[0-9a-f]{64}$/.test(String(info?.sha256 || '')) && (
                  <div className="mt-4 text-xs text-fg-muted">
                    <p className="mb-1"><Trans t={t} i18nKey="shaNote" components={{ code: <code /> }} /></p>
                    <code className="block break-all rounded-lg bg-surface-2 px-2 py-1.5 text-fg-2 select-all">{info.sha256}</code>
                  </div>
                )}
              </div>
            )}

            {state === 'ready' && platform === 'macos' && (
              <div>
                {info?.latest && (
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <p className="font-semibold">{t('latest', { version: String(info.latest) })}</p>
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-bold bg-accent-soft text-accent-text">
                      Apple Silicon (arm64)
                    </span>
                  </div>
                )}
                {notes && <p className="text-sm text-fg-muted whitespace-pre-line mb-3">{notes}</p>}
                {macUrl ? (
                  <a
                    href={macUrl}
                    rel="noopener noreferrer"
                    className="mt-2 inline-flex w-full sm:w-auto items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:bg-accent-hover transition-colors"
                  >
                    <Download size={18} aria-hidden="true" />
                    {t('downloadDmg')}
                  </a>
                ) : (
                  <div className="mt-2 space-y-2">
                    <p className="text-sm text-fg-muted">{t('macosPending')}</p>
                    <a
                      href={MACOS_RELEASES_FALLBACK}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex w-full sm:w-auto items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:bg-accent-hover transition-colors"
                    >
                      <ExternalLink size={17} aria-hidden="true" />
                      {t('macosReleasesLink')}
                    </a>
                  </div>
                )}
                <p className="mt-3 text-xs text-fg-2 flex items-center gap-1.5">
                  <Zap size={13} className="text-accent-text shrink-0" aria-hidden="true" />
                  {t('macosEngineNote')}
                </p>
                {macUrl && /^[0-9a-f]{64}$/.test(String(info?.macosSha256 || '')) && (
                  <div className="mt-4 text-xs text-fg-muted">
                    <p className="mb-1"><Trans t={t} i18nKey="shaNoteMacos" components={{ code: <code /> }} /></p>
                    <code className="block break-all rounded-lg bg-surface-2 px-2 py-1.5 text-fg-2 select-all">{info.macosSha256}</code>
                  </div>
                )}
              </div>
            )}
          </section>

          <section className="mb-6">
            <h2 className="text-lg font-bold mb-2">{t('reqTitle')}</h2>
            <p className="text-fg-2">{platform === 'macos' ? t('macosReqBody') : t('reqBody')}</p>
          </section>

          <section className="mb-6">
            <h2 className="text-lg font-bold mb-2">{t('installTitle')}</h2>
            <ul className="list-disc list-inside space-y-2 text-fg-2">
              <li>{platform === 'macos' ? t('macosGatekeeper') : t('installSmartScreen')}</li>
              <li>{t('installKeepData')}</li>
            </ul>
          </section>
        </>
      ) : (
        <div className="space-y-5 mb-6">
          <section className="rounded-xl border border-line bg-surface p-4 md:p-6">
            <div className="flex items-start justify-between gap-3 mb-2">
              <h2 className="text-lg font-bold flex items-center gap-2">
                <Share2 size={18} className="text-accent-text shrink-0" aria-hidden="true" />
                {t('mobileTitle')}
              </h2>
            </div>
            <p className="text-sm text-fg-2 mb-4">{t('mobileIntro')}</p>
            <div className="flex flex-wrap gap-3">
              {isInstallable && (
                <button
                  type="button"
                  onClick={triggerInstall}
                  className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:bg-accent-hover transition-colors cursor-pointer"
                >
                  <Download size={18} aria-hidden="true" />
                  {t('mobileInstallBtn')}
                </button>
              )}
              <a
                href="/"
                className="inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl border border-line bg-surface-2 text-fg font-semibold text-sm hover:border-accent/40 transition-colors"
              >
                {t('openWebApp')}
              </a>
            </div>
          </section>

          <section className="rounded-xl border border-line bg-surface p-4 md:p-5">
            <h3 className="text-base font-bold mb-2">{t('mobileAndroidTitle')}</h3>
            <ol className="list-decimal list-inside space-y-1.5 text-sm text-fg-2">
              {Array.isArray(androidSteps) && androidSteps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </section>

          <section className="rounded-xl border border-line bg-surface p-4 md:p-5">
            <h3 className="text-base font-bold mb-2">{t('mobileIosTitle')}</h3>
            <ol className="list-decimal list-inside space-y-1.5 text-sm text-fg-2">
              {Array.isArray(iosSteps) && iosSteps.map((step, i) => (
                <li key={i}>{step}</li>
              ))}
            </ol>
          </section>
        </div>
      )}

      <p className="text-sm text-fg-muted">{t('sharedAccount')}</p>
    </div>
  );
}
