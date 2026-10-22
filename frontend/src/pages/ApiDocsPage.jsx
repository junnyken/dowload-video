import { useState, useEffect } from 'react';
import { usePlans } from '../lib/usePlans';
import { SUPPORT_EMAIL } from '../lib/contact';
import { Code, ExternalLink, CheckCircle2, AlertCircle, Key, Zap, Shield } from 'lucide-react';
import { useTranslation, Trans } from 'react-i18next';
import { useLang } from '../i18n';

const API_BASE = import.meta.env.VITE_API_URL || '';

// Prose (descriptions, notes, error messages) lives in locales/*/apiDocs.json
// under endpoints.<id> / partner.notes.<id> / errors.<code>; code samples,
// paths and field names stay here. wording: BA review (all endpoint descriptions)
const ENDPOINTS = [
  {
    id: 'health', method: 'GET', path: '/health', auth: false,
    response: `{ "status": "ok", "app_version": "1.6.0", "disk": {...}, "redis": {...}, "ytdlp_version": "..." }`,
  },
  {
    id: 'apiInfo', method: 'GET', path: '/api/v1/api-info', auth: false,
    response: `{ "api_version": "v1", "app_version": "...", "rate_limits": {...}, "public_endpoints": [...] }`,
  },
  {
    id: 'fetchLink', method: 'POST', path: '/api/v1/fetch-link', auth: false, keyOptional: true,
    request: `{ "url": "https://youtube.com/watch?v=...", "quality": "video", "progress_token": "my-token-123" }`,
    response: `{
  "success": true,
  "title": "...",
  "thumbnail_url": "...",
  "direct_mp4_url": "...",
  "local_file_id": "...",
  "quality": "...",
  "duration": 212,
  "file_size_mb": 18.4,
  "available_formats": [...],
  "downloaded_height": 1080,
  "subtitle_url": null
}`,
  },
  {
    id: 'progress', method: 'GET', path: '/api/v1/progress/{token}', auth: false,
    response: `{ "status": "pending", "percent": 0, "speed_kbps": 0, "eta_seconds": 0 }`,
  },
  {
    id: 'history', method: 'GET', path: '/api/v1/history', auth: false, keyOptional: true,
    response: (t) => `{ "success": true, "jobs": [ { "id": "...", "title": "...", "status": "success", ... } ] }
// ${t('guestComment')}: { "success": true, "jobs": [], "local_only": true }`,
  },
  {
    id: 'historyExport', method: 'GET', path: '/api/v1/user/history/export', auth: true,
    response: `Download: vidgrab-history.csv or vidgrab-history.json`,
  },
  {
    id: 'archive', method: 'GET', path: '/api/v1/archive', auth: true,
    response: `{ "items": [...], "total": 120, "page": 1 }`,
  },
  {
    id: 'archiveSearch', method: 'GET', path: '/api/v1/archive/search', auth: true,
    response: (t) => `{ ... ${t('searchResultsPlaceholder')} ... }`,
  },
  {
    id: 'errorCodes', method: 'GET', path: '/api/v1/error-codes', auth: false,
    response: `{ "unsupported_url": { "user_message": "...", "retryable": false, ... }, ... }`,
  },
];

// wording: BA review
const PARTNER_ENDPOINTS = [
  { id: 'createJob', method: 'POST', path: '/jobs' },
  { id: 'getJob', method: 'GET', path: '/jobs/{id}' },
  { id: 'listJobs', method: 'GET', path: '/jobs?status=&limit=' },
  { id: 'cancelJob', method: 'DELETE', path: '/jobs/{id}' },
  { id: 'usage', method: 'GET', path: '/usage' },
  { id: 'addWebhook', method: 'POST', path: '/webhooks' },
  { id: 'listWebhooks', method: 'GET', path: '/webhooks' },
  { id: 'deleteWebhook', method: 'DELETE', path: '/webhooks/{id}' },
  { id: 'validateUrls', method: 'POST', path: '/validate-urls' },
  // wording: BA review
  { id: 'platformStatus', method: 'GET', path: '/platform-status' },
];

// Messages: errors.<code> in apiDocs.json. wording: BA review
const ERROR_EXAMPLES = [
  { code: 'unsupported_url',     retryable: false },
  { code: 'provider_unavailable', retryable: true },
  { code: 'extractor_broken',     retryable: true },
  { code: 'queue_busy',          retryable: true },
  { code: 'rate_limited',        retryable: true },
  { code: 'job_expired',         retryable: false },
  { code: 'validation_failed',   retryable: false },
  { code: 'unauthorized',        retryable: false },
];

const methodColor = (m) => {
  if (m === 'GET')    return 'bg-surface-2 text-fg-2 border-line';
  if (m === 'POST')   return 'bg-success-soft text-success border-success/30';
  if (m === 'DELETE') return 'bg-danger-soft text-danger border-danger/30';
  return 'bg-line-strong text-fg-muted border-line-strong';
};

export default function ApiDocsPage() {
  const [apiVersion, setApiVersion] = useState(null);
  const [expanded, setExpanded] = useState(null);
  const { free, pro } = usePlans();
  const { t } = useTranslation('apiDocs');
  const lang = useLang();
  // Daily allowance comes from /api/v1/plans; no number when it did not load.
  const dailyText = free && pro
    ? t('rate.daily', { free: free.daily_downloads, pro: pro.daily_downloads })
    : t('rate.byPlan');
  // wording: BA review
  const monthlyText = pro && pro.api_calls_per_month > 0
    ? t('rate.monthlyPro', { count: pro.api_calls_per_month.toLocaleString(lang === 'en' ? 'en-US' : 'vi-VN') })
    : pro && pro.api_calls_per_month === -1 ? t('rate.unlimited') : t('rate.byPlan');

  // Deep link /api-docs#partner: spaLink scrolls to top on navigation and the
  // section only exists after render, so scroll once it has mounted.
  useEffect(() => {
    if (window.location.hash !== '#partner') return undefined;
    const timer = setTimeout(() => {
      document.getElementById('partner')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/api-info`)
      .then(r => r.json())
      .then(d => setApiVersion(d.app_version))
      .catch(() => {});
  }, []);

  return (
    <div className="max-w-4xl mx-auto px-4 md:px-8 py-8 md:py-12 space-y-8">

      {/* ── Header ────────────────────────────────────────── */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <div className="w-10 h-10 rounded-xl bg-accent flex items-center justify-center">
            <Code className="w-5 h-5 text-accent-fg" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-fg tracking-tight">VidGrab Public API</h1>
            {apiVersion && (
              <span className="text-xs font-mono text-fg-muted">v{apiVersion}</span>
            )}
          </div>
        </div>
        <p className="text-sm text-fg-muted ml-13 mt-1">
          {t('intro')}
        </p>
      </div>

      {/* ── Auth ──────────────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-surface border border-line space-y-3">
        <div className="flex items-center gap-2">
          <Key className="w-4 h-4 text-fg-2" />
          <h2 className="text-base font-semibold text-fg">{t('auth.title')}</h2>
        </div>
        {/* wording: BA review */}
        <p className="text-sm text-fg-muted">
          <Trans t={t} i18nKey="auth.intro" components={{ code: <code className="bg-surface-2 px-1 rounded" /> }} />
        </p>
        <div className="font-mono text-xs bg-surface-2 border border-line rounded-xl px-4 py-3 text-fg-2 space-y-1">
          <div>X-API-Key: vidgrab_&lt;your-api-key&gt;</div>
          <div className="text-fg-muted">Authorization: Bearer vidgrab_&lt;your-api-key&gt;  {t('auth.accepted')}</div>
        </div>
        <p className="text-xs text-fg-muted">
          {/* wording: BA review */}
          <Trans
            t={t}
            i18nKey="auth.note"
            components={{
              a: <a href="#/api-keys" className="text-accent-text underline" />,
              b: <strong />,
              code: <code className="bg-surface-2 px-1 rounded" />,
            }}
          />
        </p>
      </div>

      {/* ── Rate Limits ───────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-surface border border-line space-y-3">
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-accent-text" />
          <h2 className="text-base font-semibold text-fg">{t('rate.title')}</h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          {[
            // wording: BA review
            { label: t('rate.otherLabel'), value: t('rate.otherValue') },
            { label: t('rate.fetchLabel'), value: t('rate.fetchValue') },
            { label: t('rate.monthlyLabel'), value: monthlyText },
            { label: t('rate.dailyLabel'), value: dailyText },
          ].map(r => (
            <div key={r.label} className="px-3 py-2.5 rounded-xl bg-surface-2 border border-line">
              <p className="text-fg-muted mb-0.5">{r.label}</p>
              <p className="font-semibold text-fg-2">{r.value}</p>
            </div>
          ))}
        </div>
        <p className="text-xs text-fg-muted">
          {/* wording: BA review */}
          {t('rate.exceeded')}
        </p>
        <p className="text-xs text-fg-muted">
          {/* wording: BA review — owner 14/10 monthly API quota */}
          <Trans
            t={t}
            i18nKey="rate.monthlyQuota"
            components={{ b: <strong />, code: <code className="bg-surface-2 px-1 rounded" /> }}
          />
        </p>
      </div>

      {/* ── Partner API ───────────────────────────────────── */}
      {/* wording: BA review (whole Partner API section) */}
      <div id="partner" className="p-5 rounded-2xl bg-surface border border-line space-y-3 scroll-mt-20">
        <div className="flex items-center gap-2">
          <Key className="w-4 h-4 text-accent-text" />
          <h2 className="text-base font-semibold text-fg">{t('partner.title')}</h2>
        </div>
        <p className="text-sm text-fg-muted">
          <Trans
            t={t}
            i18nKey="partner.intro"
            values={{ email: SUPPORT_EMAIL }}
            components={{
              code: <code className="bg-surface-2 px-1 rounded" />,
              mail: <a href={`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(t('partner.mailSubject'))}`} className="text-accent-text underline" />,
              tg: <a href="https://t.me/vidgrab_support" target="_blank" rel="noopener noreferrer" className="text-accent-text underline" />,
            }}
          />
        </p>
        <div className="font-mono text-xs bg-surface-2 border border-line rounded-xl px-4 py-3 text-fg-2 space-y-1 overflow-x-auto">
          <div>X-API-Key: vgp_&lt;key&gt;</div>
          <div className="text-fg-muted">{t('partner.basePath')}</div>
        </div>
        <div className="space-y-1.5">
          {PARTNER_ENDPOINTS.map(e => (
            <div key={e.id} className="px-3 py-2 rounded-xl bg-surface-2 border border-line">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`inline-flex px-2 py-0.5 rounded border text-[11px] font-bold font-mono ${methodColor(e.method)}`}>{e.method}</span>
                <code className="text-xs font-mono text-fg-2 break-all">{e.path}</code>
              </div>
              <p className="text-xs text-fg-muted mt-1">{t(`partner.notes.${e.id}`)}</p>
            </div>
          ))}
        </div>
        <div>
          <p className="text-xs text-fg-muted mb-1 font-semibold uppercase tracking-wider">{t('partner.exampleTitle')}</p>
          <pre className="text-xs font-mono bg-surface-2 border border-line rounded-xl px-4 py-3 text-success overflow-x-auto">{`POST /api/v1/partner/jobs
X-API-Key: vgp_<key>

{ "url": "https://youtube.com/watch?v=...", "quality": "video" }   // -> 202 + job`}</pre>
        </div>
        <ul className="text-xs text-fg-muted space-y-1.5 list-disc list-inside">
          <li><Trans t={t} i18nKey="partner.li1" components={{ code: <code className="bg-surface-2 px-1 rounded" /> }} /></li>
          <li>{t('partner.li2')}</li>
          <li><Trans t={t} i18nKey="partner.li3" components={{ code: <code className="bg-surface-2 px-1 rounded" /> }} /></li>
          {/* wording: BA review */}
          <li><Trans t={t} i18nKey="partner.li4" components={{ code: <code className="bg-surface-2 px-1 rounded" /> }} /></li>
          <li><Trans t={t} i18nKey="partner.li5" components={{ code: <code className="bg-surface-2 px-1 rounded" /> }} /></li>
        </ul>
      </div>

      {/* ── Endpoints ─────────────────────────────────────── */}
      <div className="space-y-3">
        <h2 className="text-base font-semibold text-fg flex items-center gap-2">
          <Shield className="w-4 h-4 text-accent-text" /> {t('endpointsTitle')}
        </h2>
        {ENDPOINTS.map((ep, i) => (
          <div key={i} className="rounded-2xl bg-surface border border-line overflow-hidden">
            <button
              onClick={() => setExpanded(expanded === i ? null : i)}
              className="w-full flex items-center gap-3 px-5 py-4 text-left hover:bg-surface-2 transition-colors cursor-pointer"
            >
              <span className={`inline-flex px-2 py-0.5 rounded border text-[11px] font-bold font-mono ${methodColor(ep.method)}`}>
                {ep.method}
              </span>
              <code className="text-sm text-fg-2 font-mono flex-1">{ep.path}</code>
              {ep.keyOptional && (
                <span className="text-[10px] bg-surface-2 text-fg-2 border border-line px-1.5 py-0.5 rounded font-semibold flex-shrink-0">
                  {/* wording: BA review */}
                  {t('keyOptional')}
                </span>
              )}
              {ep.auth && (
                <span className="text-[10px] bg-surface-2 text-fg-2 border border-line px-1.5 py-0.5 rounded font-semibold flex-shrink-0">
                  auth
                </span>
              )}
            </button>
            {expanded === i && (
              <div className="px-5 pb-5 space-y-3 border-t border-line">
                <p className="text-sm text-fg-2 pt-3">{t(`endpoints.${ep.id}`)}</p>
                {ep.request && (
                  <div>
                    <p className="text-xs text-fg-muted mb-1 font-semibold uppercase tracking-wider">{t('requestBody')}</p>
                    <pre className="text-xs font-mono bg-surface-2 border border-line rounded-xl px-4 py-3 text-success overflow-x-auto">{ep.request}</pre>
                  </div>
                )}
                <div>
                  <p className="text-xs text-fg-muted mb-1 font-semibold uppercase tracking-wider">{t('response')}</p>
                  <pre className="text-xs font-mono bg-surface-2 border border-line rounded-xl px-4 py-3 text-fg-2 overflow-x-auto">{typeof ep.response === 'function' ? ep.response(t) : ep.response}</pre>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Error Format ──────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-surface border border-line space-y-3">
        <div className="flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-danger" />
          <h2 className="text-base font-semibold text-fg">{t('errorFormat.title')}</h2>
        </div>
        <p className="text-sm text-fg-muted">{t('errorFormat.intro')}</p>
        <pre className="text-xs font-mono bg-surface-2 border border-line rounded-xl px-4 py-3 text-danger overflow-x-auto">{`{
  "error_code":       "provider_unavailable",   // machine-readable code
  "user_message":     "Nền tảng tạm thời không phản hồi.",
  "retryable":        true,                     // true = retry with backoff
  "suggested_action": "Thử lại sau vài phút."
}`}</pre>
        <div className="space-y-1.5 pt-1">
          {ERROR_EXAMPLES.map(e => (
            <div key={e.code} className="flex items-center gap-3 px-3 py-2 rounded-xl bg-surface-2 border border-line">
              <code className="text-[11px] font-mono text-fg-2 flex-shrink-0 w-44">{e.code}</code>
              {e.retryable
                ? <CheckCircle2 className="w-3.5 h-3.5 text-success flex-shrink-0" />
                : <AlertCircle  className="w-3.5 h-3.5 text-error flex-shrink-0" />
              }
              <span className="text-xs text-fg-muted truncate">{t(`errors.${e.code}`)}</span>
            </div>
          ))}
        </div>
        <a
          href="/api/v1/error-codes"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-fg-2 hover:text-fg-2 transition-colors"
        >
          {t('errorFormat.fullList')} <ExternalLink className="w-3 h-3" />
        </a>
      </div>

      {/* ── Retry Guidance ────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-surface border border-line space-y-2">
        <h2 className="text-base font-semibold text-fg">{t('retry.title')}</h2>
        <ul className="text-sm text-fg-muted space-y-1.5 list-disc list-inside">
          <li><Trans t={t} i18nKey="retry.li1" components={{ code: <code className="text-fg-2" /> }} /></li>
          <li>{t('retry.li2')}</li>
          {/* wording: BA review */}
          <li><Trans t={t} i18nKey="retry.li3" components={{ code: <code className="text-fg-2" /> }} /></li>
          <li><Trans t={t} i18nKey="retry.li4" components={{ code: <code className="text-fg-2" /> }} /></li>
          <li>{t('retry.li5')}</li>
          {/* wording: BA review */}
          <li><Trans t={t} i18nKey="retry.li6" components={{ code: <code className="text-fg-2" /> }} /></li>
        </ul>
      </div>

    </div>
  );
}
