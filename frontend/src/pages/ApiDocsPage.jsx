import { useState, useEffect } from 'react';
import { Code, ExternalLink, CheckCircle2, AlertCircle, Key, Zap, Shield } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_URL || '';

const ENDPOINTS = [
  {
    method: 'GET', path: '/health', auth: false,
    description: 'Liveness + version check. Returns disk, Redis, yt-dlp version, app_version.',
    response: `{ "status": "ok", "app_version": "1.6.0", "disk": {...}, "redis": {...}, "ytdlp_version": "..." }`,
  },
  {
    method: 'GET', path: '/api/v1/api-info', auth: false,
    description: 'Public API metadata: endpoints, rate limits, auth guide, error format.',
    response: `{ "api_version": "v1", "app_version": "...", "rate_limits": {...}, "public_endpoints": [...] }`,
  },
  {
    method: 'POST', path: '/api/v1/fetch-link', auth: false,
    description: 'Start a download. Returns a token for polling progress.',
    request: `{ "url": "https://youtube.com/watch?v=...", "quality": "720" }`,
    response: `{ "success": true, "token": "abc123", "cache_key": "abc123" }`,
  },
  {
    method: 'GET', path: '/api/v1/progress/{token}', auth: false,
    description: 'Poll job status. Keep polling until status = "done" or "error".',
    response: `{ "status": "done", "title": "...", "direct_mp4_url": "...", "formats": [...] }`,
  },
  {
    method: 'GET', path: '/api/v1/history', auth: false,
    description: 'Recent jobs for the caller — your own when a token is sent, otherwise the anonymous pool. Supports ?limit=20&offset=0&platform=youtube&status=success.',
    response: `{ "success": true, "jobs": [ { "id": "...", "title": "...", "status": "success", ... } ] }`,
  },
  {
    method: 'GET', path: '/api/v1/user/history/export', auth: true,
    description: 'Export download history. ?format=csv|json&range_days=30 (max 365 for Pro).',
    response: `Download: vidgrab-history.csv or vidgrab-history.json`,
  },
  {
    method: 'GET', path: '/api/v1/archive', auth: true,
    description: 'Personal archive. Supports ?q=...&platform=...&is_starred=true&page=1.',
    response: `{ "items": [...], "total": 120, "page": 1 }`,
  },
  {
    method: 'GET', path: '/api/v1/error-codes', auth: false,
    description: 'Full catalogue of structured error codes with user-safe messages.',
    response: `{ "unsupported_url": { "user_message": "...", "retryable": false, ... }, ... }`,
  },
];

const ERROR_EXAMPLES = [
  { code: 'unsupported_url',     retryable: false, message: 'URL không được hỗ trợ.' },
  { code: 'provider_unavailable', retryable: true,  message: 'Nền tảng tạm thời không phản hồi.' },
  { code: 'queue_busy',          retryable: true,  message: 'Hàng đợi đang quá tải.' },
  { code: 'rate_limited',        retryable: true,  message: 'Vui lòng đợi.' },
  { code: 'job_expired',         retryable: false, message: 'Job đã hết hạn.' },
  { code: 'validation_failed',   retryable: false, message: 'Dữ liệu đầu vào không hợp lệ.' },
  { code: 'unauthorized',        retryable: false, message: 'Cần đăng nhập.' },
];

const methodColor = (m) => {
  if (m === 'GET')    return 'bg-blue-500/20 text-blue-400 border-blue-500/30';
  if (m === 'POST')   return 'bg-green-500/20 text-green-400 border-green-500/30';
  if (m === 'DELETE') return 'bg-red-500/20 text-red-400 border-red-500/30';
  return 'bg-slate-500/20 text-slate-400 border-slate-500/30';
};

export default function ApiDocsPage() {
  const [apiVersion, setApiVersion] = useState(null);
  const [expanded, setExpanded] = useState(null);

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
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-violet-500 to-purple-600 flex items-center justify-center">
            <Code className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-100 tracking-tight">VidGrab Public API</h1>
            {apiVersion && (
              <span className="text-xs font-mono text-slate-400">v{apiVersion}</span>
            )}
          </div>
        </div>
        <p className="text-sm text-slate-400 ml-13 mt-1">
          Tài liệu tham khảo cho các tích hợp bên ngoài. Chỉ bao gồm public + user-facing endpoints.
          Admin endpoints không được cung cấp tại đây.
        </p>
      </div>

      {/* ── Auth ──────────────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-slate-800/60 border border-slate-700/50 space-y-3">
        <div className="flex items-center gap-2">
          <Key className="w-4 h-4 text-violet-400" />
          <h2 className="text-base font-semibold text-slate-100">Xác thực</h2>
        </div>
        <p className="text-sm text-slate-400">
          Các endpoint công khai không cần auth nhưng bị giới hạn theo IP. Authenticated endpoints yêu cầu Bearer token.
        </p>
        <div className="font-mono text-xs bg-slate-900/80 border border-slate-700/50 rounded-xl px-4 py-3 text-violet-300">
          Authorization: Bearer &lt;your-api-key&gt;
        </div>
        <p className="text-xs text-slate-500">
          Tạo API key tại: <strong className="text-slate-300">Settings → API Access → Tạo API Key</strong>.
          Mỗi key có thể bị thu hồi bất kỳ lúc nào.
        </p>
      </div>

      {/* ── Rate Limits ───────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-slate-800/60 border border-slate-700/50 space-y-3">
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-yellow-400" />
          <h2 className="text-base font-semibold text-slate-100">Giới hạn tốc độ</h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs">
          {[
            { label: 'Default', value: '60 req/phút/IP' },
            { label: 'Fetch-link / Bulk', value: '5 req/phút/IP' },
            { label: 'Daily quota (anon)', value: 'Theo cấu hình server' },
          ].map(r => (
            <div key={r.label} className="px-3 py-2.5 rounded-xl bg-slate-900/60 border border-slate-700/40">
              <p className="text-slate-400 mb-0.5">{r.label}</p>
              <p className="font-semibold text-slate-200">{r.value}</p>
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-500">
          Khi bị rate-limited: HTTP 429 với header <code className="bg-slate-700/50 px-1 rounded">Retry-After</code>.
          Dùng exponential backoff khi retry.
        </p>
      </div>

      {/* ── Endpoints ─────────────────────────────────────── */}
      <div className="space-y-3">
        <h2 className="text-base font-semibold text-slate-100 flex items-center gap-2">
          <Shield className="w-4 h-4 text-primary" /> Endpoints
        </h2>
        {ENDPOINTS.map((ep, i) => (
          <div key={i} className="rounded-2xl bg-slate-800/60 border border-slate-700/50 overflow-hidden">
            <button
              onClick={() => setExpanded(expanded === i ? null : i)}
              className="w-full flex items-center gap-3 px-5 py-4 text-left hover:bg-slate-700/30 transition-colors cursor-pointer"
            >
              <span className={`inline-flex px-2 py-0.5 rounded border text-[11px] font-bold font-mono ${methodColor(ep.method)}`}>
                {ep.method}
              </span>
              <code className="text-sm text-slate-200 font-mono flex-1">{ep.path}</code>
              {ep.auth && (
                <span className="text-[10px] bg-violet-500/20 text-violet-300 border border-violet-500/30 px-1.5 py-0.5 rounded font-semibold flex-shrink-0">
                  auth
                </span>
              )}
            </button>
            {expanded === i && (
              <div className="px-5 pb-5 space-y-3 border-t border-slate-700/40">
                <p className="text-sm text-slate-300 pt-3">{ep.description}</p>
                {ep.request && (
                  <div>
                    <p className="text-xs text-slate-500 mb-1 font-semibold uppercase tracking-wider">Request body</p>
                    <pre className="text-xs font-mono bg-slate-900/80 border border-slate-700/50 rounded-xl px-4 py-3 text-green-300 overflow-x-auto">{ep.request}</pre>
                  </div>
                )}
                <div>
                  <p className="text-xs text-slate-500 mb-1 font-semibold uppercase tracking-wider">Response</p>
                  <pre className="text-xs font-mono bg-slate-900/80 border border-slate-700/50 rounded-xl px-4 py-3 text-blue-300 overflow-x-auto">{ep.response}</pre>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* ── Error Format ──────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-slate-800/60 border border-slate-700/50 space-y-3">
        <div className="flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-400" />
          <h2 className="text-base font-semibold text-slate-100">Format lỗi</h2>
        </div>
        <p className="text-sm text-slate-400">Mọi lỗi đều trả về cùng một cấu trúc JSON:</p>
        <pre className="text-xs font-mono bg-slate-900/80 border border-slate-700/50 rounded-xl px-4 py-3 text-red-300 overflow-x-auto">{`{
  "error_code":       "provider_unavailable",   // machine-readable code
  "user_message":     "Nền tảng tạm thời không phản hồi.",
  "retryable":        true,                     // true = retry with backoff
  "suggested_action": "Thử lại sau vài phút."
}`}</pre>
        <div className="space-y-1.5 pt-1">
          {ERROR_EXAMPLES.map(e => (
            <div key={e.code} className="flex items-center gap-3 px-3 py-2 rounded-xl bg-slate-900/50 border border-slate-700/30">
              <code className="text-[11px] font-mono text-slate-300 flex-shrink-0 w-44">{e.code}</code>
              {e.retryable
                ? <CheckCircle2 className="w-3.5 h-3.5 text-success flex-shrink-0" />
                : <AlertCircle  className="w-3.5 h-3.5 text-error flex-shrink-0" />
              }
              <span className="text-xs text-slate-400 truncate">{e.message}</span>
            </div>
          ))}
        </div>
        <a
          href="/api/v1/error-codes"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-violet-400 hover:text-violet-300 transition-colors"
        >
          Xem đầy đủ danh sách error codes <ExternalLink className="w-3 h-3" />
        </a>
      </div>

      {/* ── Retry Guidance ────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-slate-800/60 border border-slate-700/50 space-y-2">
        <h2 className="text-base font-semibold text-slate-100">Hướng dẫn retry</h2>
        <ul className="text-sm text-slate-400 space-y-1.5 list-disc list-inside">
          <li>Chỉ retry khi <code className="text-violet-300">retryable: true</code>.</li>
          <li>Dùng exponential backoff: 1s → 2s → 4s → 8s (tối đa 3 lần).</li>
          <li>HTTP 429: đợi theo <code className="text-violet-300">Retry-After</code> header.</li>
          <li>HTTP 5xx: retry với backoff; HTTP 4xx (ngoại trừ 429): không retry.</li>
          <li>Nếu <code className="text-violet-300">status = "processing"</code>: poll lại sau 2–3s.</li>
        </ul>
      </div>

    </div>
  );
}
