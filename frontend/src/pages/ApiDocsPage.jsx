import { useState, useEffect } from 'react';
import { usePlans } from '../lib/usePlans';
import { SUPPORT_EMAIL } from '../lib/contact';
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
  // wording: BA review (all endpoint descriptions below)
  {
    method: 'POST', path: '/api/v1/fetch-link', auth: false, keyOptional: true,
    description: 'Tải một link và chờ kết quả: request giữ kết nối cho đến khi xong (đồng bộ), không trả token để poll. Gửi API key thì chạy dưới tài khoản của bạn (hạn mức, chất lượng theo gói); không gửi thì chạy như khách. quality mặc định "video" (tối đa 1080p); các giá trị: "video", "video_fast" (nhanh, chất lượng thấp hơn), "video_4k" (tốt nhất có thể), "video_<chiều cao>" (ví dụ "video_720"), "mp3_128", "mp3_320". Chất lượng thực tế bị giới hạn theo gói của tài khoản. progress_token là tuỳ chọn do bạn tự đặt: gửi kèm để theo dõi tiến độ qua GET /progress/{token} trong lúc request đang chạy.',
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
    method: 'GET', path: '/api/v1/progress/{token}', auth: false,
    description: 'Tiến độ tải theo progress_token bạn đã gửi trong POST /fetch-link, chỉ có ý nghĩa khi request đó còn đang chạy. Token chưa biết (hoặc chưa có tiến độ) trả status "pending". Không dùng để lấy kết quả cuối: kết quả nằm trong response của /fetch-link.',
    response: `{ "status": "pending", "percent": 0, "speed_kbps": 0, "eta_seconds": 0 }`,
  },
  {
    method: 'GET', path: '/api/v1/history', auth: false, keyOptional: true,
    description: 'Lịch sử tải của tài khoản khi có API key (mặc định limit=5; hỗ trợ ?limit=&offset=&platform=&status=success|failed|processing). Khách (không gửi key) nhận danh sách rỗng kèm local_only: true vì lịch sử khách chỉ nằm trên trình duyệt.',
    response: `{ "success": true, "jobs": [ { "id": "...", "title": "...", "status": "success", ... } ] }
// khách: { "success": true, "jobs": [], "local_only": true }`,
  },
  {
    method: 'GET', path: '/api/v1/user/history/export', auth: true,
    description: 'Export download history. ?format=csv|json&range_days=30 (max 365 for Pro).',
    response: `Download: vidgrab-history.csv or vidgrab-history.json`,
  },
  {
    method: 'GET', path: '/api/v1/archive', auth: true,
    description: 'Kho lưu cá nhân. Hỗ trợ ?platform=&is_starred=&collection_id=&language=&file_status=available|pinned|expired&sort=&page=1&limit=20. Không có tham số q: tìm kiếm dùng GET /api/v1/archive/search.',
    response: `{ "items": [...], "total": 120, "page": 1 }`,
  },
  {
    method: 'GET', path: '/api/v1/archive/search', auth: true,
    description: 'Tìm trong kho lưu theo tiêu đề, tác giả, hashtag, ghi chú. ?q= (tối thiểu 2 ký tự, bắt buộc) và ?limit=20.',
    response: `{ ... danh sách kết quả khớp ... }`,
  },
  {
    method: 'GET', path: '/api/v1/error-codes', auth: false,
    description: 'Full catalogue of structured error codes with user-safe messages.',
    response: `{ "unsupported_url": { "user_message": "...", "retryable": false, ... }, ... }`,
  },
];

// wording: BA review
const PARTNER_ENDPOINTS = [
  { method: 'POST', path: '/jobs', note: 'Tạo job tải. Body: { url, quality }. Trả 202 cùng thông tin job.' },
  { method: 'GET', path: '/jobs/{id}', note: 'Xem trạng thái và kết quả của một job.' },
  { method: 'GET', path: '/jobs?status=&limit=', note: 'Liệt kê job của tài khoản (mọi key), lọc theo trạng thái.' },
  { method: 'DELETE', path: '/jobs/{id}', note: 'Huỷ job đang chờ.' },
  { method: 'GET', path: '/usage', note: 'Xem mức dùng trong ngày và trong tháng so với hạn mức.' },
  { method: 'POST', path: '/webhooks', note: 'Đăng ký webhook (URL HTTPS) nhận sự kiện job.completed, job.failed.' },
  { method: 'GET', path: '/webhooks', note: 'Liệt kê webhook đã đăng ký.' },
  { method: 'DELETE', path: '/webhooks/{id}', note: 'Xoá một webhook.' },
  { method: 'POST', path: '/validate-urls', note: 'Kiểm tra trước danh sách URL, tối đa 100 URL mỗi lần.' },
  // wording: BA review
  { method: 'GET', path: '/platform-status', note: 'Nền tảng nào đang tải được. Webhook platform.degraded / platform.recovered báo khi trạng thái đổi.' },
];

const ERROR_EXAMPLES = [
  { code: 'unsupported_url',     retryable: false, message: 'URL không được hỗ trợ.' },
  { code: 'provider_unavailable', retryable: true,  message: 'Nền tảng tạm thời không phản hồi.' },
  // wording: BA review
  { code: 'extractor_broken',     retryable: true,  message: 'Nền tảng vừa thay đổi, hệ thống đang tự cập nhật. Thử lại sau 30–60 phút.' },
  { code: 'queue_busy',          retryable: true,  message: 'Hàng đợi đang quá tải.' },
  { code: 'rate_limited',        retryable: true,  message: 'Vui lòng đợi.' },
  { code: 'job_expired',         retryable: false, message: 'Job đã hết hạn.' },
  { code: 'validation_failed',   retryable: false, message: 'Dữ liệu đầu vào không hợp lệ.' },
  { code: 'unauthorized',        retryable: false, message: 'Cần đăng nhập.' },
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
  // Daily allowance comes from /api/v1/plans; no number when it did not load.
  const dailyText = free && pro
    ? `Free ${free.daily_downloads} · Pro ${pro.daily_downloads} lượt/ngày`
    : 'Theo gói tài khoản';
  // wording: BA review
  const monthlyText = pro && pro.api_calls_per_month > 0
    ? `Pro ${pro.api_calls_per_month.toLocaleString('vi-VN')} lượt/tháng`
    : pro && pro.api_calls_per_month === -1 ? 'Không giới hạn' : 'Theo gói tài khoản';

  // Deep link /api-docs#partner: spaLink scrolls to top on navigation and the
  // section only exists after render, so scroll once it has mounted.
  useEffect(() => {
    if (window.location.hash !== '#partner') return undefined;
    const t = setTimeout(() => {
      document.getElementById('partner')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 50);
    return () => clearTimeout(t);
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
          Tài liệu tham khảo cho các tích hợp bên ngoài. Chỉ bao gồm public + user-facing endpoints.
          Admin endpoints không được cung cấp tại đây.
        </p>
      </div>

      {/* ── Auth ──────────────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-surface border border-line space-y-3">
        <div className="flex items-center gap-2">
          <Key className="w-4 h-4 text-fg-2" />
          <h2 className="text-base font-semibold text-fg">Xác thực</h2>
        </div>
        {/* wording: BA review */}
        <p className="text-sm text-fg-muted">
          Một số endpoint công khai không cần key nhưng bị giới hạn theo IP. Endpoint ghi “auth” bắt buộc có key.
          Với <code className="bg-surface-2 px-1 rounded">/fetch-link</code> và <code className="bg-surface-2 px-1 rounded">/history</code>, key là tuỳ chọn:
          gửi key thì request chạy dưới tài khoản của bạn.
        </p>
        <div className="font-mono text-xs bg-surface-2 border border-line rounded-xl px-4 py-3 text-fg-2 space-y-1">
          <div>X-API-Key: vidgrab_&lt;your-api-key&gt;</div>
          <div className="text-fg-muted">Authorization: Bearer vidgrab_&lt;your-api-key&gt;  (cũng được chấp nhận)</div>
        </div>
        <p className="text-xs text-fg-muted">
          {/* wording: BA review */}
          Tạo API key tại trang <a href="#/api-keys" className="text-accent-text underline"><strong>API Keys</strong></a> (dành cho gói Pro, Team, Enterprise).
          Mỗi key có thể bị thu hồi bất kỳ lúc nào. Key sai hoặc đã thu hồi trả HTTP 401 với <code className="bg-surface-2 px-1 rounded">{'{"error_code":"invalid_api_key"}'}</code>.
        </p>
      </div>

      {/* ── Rate Limits ───────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-surface border border-line space-y-3">
        <div className="flex items-center gap-2">
          <Zap className="w-4 h-4 text-accent-text" />
          <h2 className="text-base font-semibold text-fg">Giới hạn tốc độ</h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          {[
            // wording: BA review
            { label: 'Các endpoint khác', value: '60 request/phút — theo API key (không có key: theo IP)' },
            { label: 'Fetch-link / Bulk-download', value: '30 request/phút — theo API key (không có key: theo IP)' },
            { label: 'Lượt gọi API mỗi tháng', value: monthlyText },
            { label: 'Lượt tải mỗi ngày', value: dailyText },
          ].map(r => (
            <div key={r.label} className="px-3 py-2.5 rounded-xl bg-surface-2 border border-line">
              <p className="text-fg-muted mb-0.5">{r.label}</p>
              <p className="font-semibold text-fg-2">{r.value}</p>
            </div>
          ))}
        </div>
        <p className="text-xs text-fg-muted">
          {/* wording: BA review */}
          Vượt giới hạn tốc độ: HTTP 429. Hãy đợi khoảng một phút rồi thử lại, dùng exponential backoff.
          Request có gửi key được đếm theo từng key, nên nhiều máy chủ dùng chung một IP không chia sẻ giới hạn với nhau.
        </p>
        <p className="text-xs text-fg-muted">
          {/* wording: BA review — owner 14/10 monthly API quota */}
          Mỗi request gửi kèm API key được tính 1 lượt gọi vào hạn mức <strong>tháng</strong> của tài khoản (tính theo tháng UTC,
          làm mới 00:00 UTC ngày 1). Hết lượt: HTTP 429 với{' '}
          <code className="bg-surface-2 px-1 rounded">{'{"error_code":"api_monthly_quota_exceeded","reset_at":"…"}'}</code>.
          Mọi phản hồi của request có key kèm header{' '}
          <code className="bg-surface-2 px-1 rounded">X-API-Quota-Limit</code>,{' '}
          <code className="bg-surface-2 px-1 rounded">X-API-Quota-Remaining</code>,{' '}
          <code className="bg-surface-2 px-1 rounded">X-API-Quota-Reset</code> (Unix time).
          Xem số lượt đã dùng: <code className="bg-surface-2 px-1 rounded">GET /api/v1/api-keys/usage</code>.
          Lệnh tải qua API vẫn trừ thêm vào hạn mức lượt tải mỗi ngày.
        </p>
      </div>

      {/* ── Partner API ───────────────────────────────────── */}
      {/* wording: BA review (whole Partner API section) */}
      <div id="partner" className="p-5 rounded-2xl bg-surface border border-line space-y-3 scroll-mt-20">
        <div className="flex items-center gap-2">
          <Key className="w-4 h-4 text-accent-text" />
          <h2 className="text-base font-semibold text-fg">Partner API (doanh nghiệp tích hợp từ backend)</h2>
        </div>
        <p className="text-sm text-fg-muted">
          Dành cho doanh nghiệp gọi VidGrab từ máy chủ của mình. Key dạng <code className="bg-surface-2 px-1 rounded">vgp_</code> do VidGrab cấp,
          bạn không tự tạo được. Muốn có key, hãy liên hệ:{' '}
          <a href={`mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent('Đăng ký Partner API')}`} className="text-accent-text underline">{SUPPORT_EMAIL}</a>
          {' '}hoặc Telegram{' '}
          <a href="https://t.me/vidgrab_support" target="_blank" rel="noopener noreferrer" className="text-accent-text underline">@vidgrab_support</a>.
        </p>
        <div className="font-mono text-xs bg-surface-2 border border-line rounded-xl px-4 py-3 text-fg-2 space-y-1 overflow-x-auto">
          <div>X-API-Key: vgp_&lt;key&gt;</div>
          <div className="text-fg-muted">Base path: /api/v1/partner</div>
        </div>
        <div className="space-y-1.5">
          {PARTNER_ENDPOINTS.map(e => (
            <div key={e.method + e.path} className="px-3 py-2 rounded-xl bg-surface-2 border border-line">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`inline-flex px-2 py-0.5 rounded border text-[11px] font-bold font-mono ${methodColor(e.method)}`}>{e.method}</span>
                <code className="text-xs font-mono text-fg-2 break-all">{e.path}</code>
              </div>
              <p className="text-xs text-fg-muted mt-1">{e.note}</p>
            </div>
          ))}
        </div>
        <div>
          <p className="text-xs text-fg-muted mb-1 font-semibold uppercase tracking-wider">Ví dụ tạo job</p>
          <pre className="text-xs font-mono bg-surface-2 border border-line rounded-xl px-4 py-3 text-success overflow-x-auto">{`POST /api/v1/partner/jobs
X-API-Key: vgp_<key>

{ "url": "https://youtube.com/watch?v=...", "quality": "video" }   // -> 202 + job`}</pre>
        </div>
        <ul className="text-xs text-fg-muted space-y-1.5 list-disc list-inside">
          <li>Request/phút tính theo key (mặc định 60). Vượt giới hạn: HTTP 429 kèm header <code className="bg-surface-2 px-1 rounded">Retry-After</code>.</li>
          <li>Job/ngày tính theo key (mặc định 10.000).</li>
          <li>Job/ngày hoặc job/tháng hết hạn mức: HTTP 429 với <code className="bg-surface-2 px-1 rounded">{'{"detail":{"code":"rate_limit_exceeded" | "monthly_quota_exceeded","reset_at":"…"}}'}</code>, chờ đến <code className="bg-surface-2 px-1 rounded">reset_at</code>.</li>
          <li>Poll <code className="bg-surface-2 px-1 rounded">GET /jobs/{'{id}'}</code> không bị trừ hạn mức job.</li>
        </ul>
      </div>

      {/* ── Endpoints ─────────────────────────────────────── */}
      <div className="space-y-3">
        <h2 className="text-base font-semibold text-fg flex items-center gap-2">
          <Shield className="w-4 h-4 text-accent-text" /> Endpoints
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
                  key tuỳ chọn
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
                <p className="text-sm text-fg-2 pt-3">{ep.description}</p>
                {ep.request && (
                  <div>
                    <p className="text-xs text-fg-muted mb-1 font-semibold uppercase tracking-wider">Request body</p>
                    <pre className="text-xs font-mono bg-surface-2 border border-line rounded-xl px-4 py-3 text-success overflow-x-auto">{ep.request}</pre>
                  </div>
                )}
                <div>
                  <p className="text-xs text-fg-muted mb-1 font-semibold uppercase tracking-wider">Response</p>
                  <pre className="text-xs font-mono bg-surface-2 border border-line rounded-xl px-4 py-3 text-fg-2 overflow-x-auto">{ep.response}</pre>
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
          <h2 className="text-base font-semibold text-fg">Format lỗi</h2>
        </div>
        <p className="text-sm text-fg-muted">Mọi lỗi đều trả về cùng một cấu trúc JSON:</p>
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
              <span className="text-xs text-fg-muted truncate">{e.message}</span>
            </div>
          ))}
        </div>
        <a
          href="/api/v1/error-codes"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs text-fg-2 hover:text-fg-2 transition-colors"
        >
          Xem đầy đủ danh sách error codes <ExternalLink className="w-3 h-3" />
        </a>
      </div>

      {/* ── Retry Guidance ────────────────────────────────── */}
      <div className="p-5 rounded-2xl bg-surface border border-line space-y-2">
        <h2 className="text-base font-semibold text-fg">Hướng dẫn retry</h2>
        <ul className="text-sm text-fg-muted space-y-1.5 list-disc list-inside">
          <li>Chỉ retry khi <code className="text-fg-2">retryable: true</code>.</li>
          <li>Dùng exponential backoff: 1s → 2s → 4s → 8s (tối đa 3 lần).</li>
          {/* wording: BA review */}
          <li>HTTP 429 với key <code className="text-fg-2">vidgrab_</code>: đợi khoảng một phút rồi thử lại (không có header Retry-After). Partner API (key <code className="text-fg-2">vgp_</code>): vượt request/phút thì có header <code className="text-fg-2">Retry-After</code>, đợi đúng số giây đó; hết job/ngày hoặc job/tháng thì chờ đến <code className="text-fg-2">reset_at</code>, thử lại sớm hơn vẫn bị 429.</li>
          <li>HTTP 5xx: retry với backoff; HTTP 4xx (ngoại trừ 429): không retry.</li>
          {/* wording: BA review */}
          <li>/fetch-link là đồng bộ: chờ response của chính request đó, đừng gửi lại khi chưa có kết quả (mỗi lần gửi tính một lượt tải). Muốn hiện tiến độ, tự đặt <code className="text-fg-2">progress_token</code> và poll <code className="text-fg-2">/progress/{'{token}'}</code>.</li>
        </ul>
      </div>

    </div>
  );
}
