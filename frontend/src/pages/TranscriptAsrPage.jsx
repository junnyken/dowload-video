import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Mic, Download, Trash2, Loader2, CheckCircle2, XCircle, Film, Languages, X,
  AlertCircle, PowerOff, Clock, RefreshCw, Ban,
} from 'lucide-react';
import EmptyState from '../components/shared/EmptyState';
import { localFileId } from '../lib/localFile';

const API = import.meta.env.VITE_API_URL || '';

// Same stable per-browser identity convention as TranscriptTranslatePage.jsx
// (see that file for the full rationale) — reused verbatim, not reinvented.
function getSessionId() {
  let sid = localStorage.getItem('vg_session_id');
  if (!sid) {
    sid = crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2);
    localStorage.setItem('vg_session_id', sid);
  }
  return sid;
}
const SESSION_ID = getSessionId();

function authHeaders(session) {
  const headers = { 'X-Session-ID': SESSION_ID };
  if (session?.access_token) headers.Authorization = `Bearer ${session.access_token}`;
  return headers;
}

// Must match backend transcript_translate._TARGET_LANGS exactly.
const TARGET_LANGS = {
  vi: 'Tiếng Việt', en: 'Tiếng Anh', ja: 'Tiếng Nhật', ko: 'Tiếng Hàn',
  zh: 'Tiếng Trung', fr: 'Tiếng Pháp', de: 'Tiếng Đức', es: 'Tiếng Tây Ban Nha',
  th: 'Tiếng Thái', id: 'Tiếng Indonesia',
};

const ACTIVE_STATUSES = ['queued', 'extracting_audio', 'transcribing'];

// Every error_code the backend can answer (app.services.asr.types.ERROR_MESSAGES_VI
// plus disk_full from the shared disk guard). Kept client-side so a job row
// always reads in Vietnamese even if the stored message is missing.
export const ERROR_VI = {
  asr_disabled: 'Tính năng tạo phụ đề tự động đang tạm tắt.',
  asr_paused: 'Tính năng tạo phụ đề tự động đang tạm dừng bởi quản trị viên.',
  spend_ceiling_reached: 'Hệ thống đã dùng hết ngân sách tạo phụ đề hôm nay. Vui lòng thử lại vào ngày mai.',
  budget_unavailable: 'Không kiểm tra được ngân sách hệ thống, vui lòng thử lại sau.',
  provider_unavailable: 'Dịch vụ nhận dạng giọng nói chưa sẵn sàng, vui lòng thử lại sau.',
  provider_bad_output: 'Dịch vụ nhận dạng giọng nói trả kết quả không hợp lệ, chưa thể tạo phụ đề.',
  provider_transient: 'Dịch vụ nhận dạng giọng nói đang quá tải, vui lòng thử lại sau.',
  provider_error: 'Dịch vụ nhận dạng giọng nói gặp lỗi.',
  no_speech: 'Không nhận diện được lời thoại nào trong video (có thể video không có tiếng nói).',
  queue_unavailable: 'Không xếp được job vào hàng đợi, vui lòng thử lại sau. Hạn mức đã được hoàn lại.',
  timeout: 'Xử lý quá thời gian cho phép.',
  interrupted: 'Job bị gián đoạn giữa chừng (máy chủ khởi động lại). Hạn mức đã được hoàn lại.',
  stuck_timeout: 'Job bị treo quá thời gian cho phép. Hạn mức đã được hoàn lại.',
  source_missing: 'Video đã hết hạn hoặc bị xoá khỏi server. Vui lòng tải lại video.',
  too_long: 'Video vượt giới hạn thời lượng cho tạo phụ đề tự động.',
  audio_extract_failed: 'Không tách được âm thanh từ video.',
  internal_error: 'Lỗi hệ thống khi tạo phụ đề.',
  cancelled: 'Đã huỷ bởi người dùng.',
  disk_full: 'Hệ thống tạm hết dung lượng lưu trữ. Vui lòng thử lại sau vài phút.',
};
const GENERIC_ERROR = 'Đã xảy ra lỗi, vui lòng thử lại sau.';

/** Vietnamese message for a job's failure: known code first, then stored text. */
export function jobErrorText(job) {
  if (job?.error_code && ERROR_VI[job.error_code]) return ERROR_VI[job.error_code];
  return job?.error || GENERIC_ERROR;
}

/** Vietnamese message for a failed API response body (create / actions). */
export function apiErrorText(body, fallback = GENERIC_ERROR) {
  const d = body?.detail;
  const code = body?.error_code || (d && typeof d === 'object' ? d.error_code : null);
  if (code && ERROR_VI[code]) return ERROR_VI[code];
  if (d && typeof d === 'object') return d.user_message || d.message || fallback;
  if (typeof d === 'string' && d) return d;
  return fallback;
}

const STATUS_CONFIG = {
  queued:           { label: 'Đang chờ', cls: 'bg-surface-2 text-fg-2 border-line-strong' },
  extracting_audio: { label: 'Đang tách âm thanh', cls: 'bg-accent-soft text-accent-text border-line', spin: true },
  transcribing:     { label: 'Đang nhận diện giọng nói', cls: 'bg-accent-soft text-accent-text border-line', spin: true },
  done:             { label: 'Hoàn tất', cls: 'bg-success-soft text-success border-success/30' },
  failed:           { label: 'Thất bại', cls: 'bg-danger-soft text-danger border-danger/30' },
  cancelled:        { label: 'Đã huỷ', cls: 'bg-surface-2 text-fg-muted border-line-strong' },
};
const UNKNOWN_STATUS = { label: 'Không rõ trạng thái', cls: 'bg-surface-2 text-fg-muted border-line-strong' };

function statusKey(job) {
  if (job.status === 'failed' && job.error_code === 'cancelled') return 'cancelled';
  return job.status;
}

function StatusBadge({ job }) {
  const key = statusKey(job);
  const cfg = STATUS_CONFIG[key] || UNKNOWN_STATUS;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-semibold whitespace-nowrap ${cfg.cls}`}>
      {cfg.spin && <Loader2 className="w-3 h-3 animate-spin" />}
      {key === 'done' && <CheckCircle2 className="w-3 h-3" />}
      {key === 'failed' && <XCircle className="w-3 h-3" />}
      {key === 'cancelled' && <Ban className="w-3 h-3" />}
      {cfg.label}
    </span>
  );
}

function formatDuration(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return '—';
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

function formatMinutes(n) {
  return `${Number(n).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} phút`;
}

function formatResetTime(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || Number.isNaN(d.getTime())) return null;
  return d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });
}

const DOWNLOAD_FORMATS = [
  { key: 'srt', label: 'SRT', hint: 'Phổ biến nhất, dùng được với hầu hết trình phát' },
  { key: 'vtt', label: 'VTT', hint: 'Dùng cho phụ đề trên web' },
  { key: 'txt', label: 'TXT', hint: 'Chỉ lời thoại, không có mốc thời gian' },
];

const ROW_BTN =
  'inline-flex items-center justify-center gap-1.5 min-h-11 min-w-11 px-3 rounded-lg border border-line text-fg-2 text-xs font-medium ' +
  'hover:text-accent-text hover:border-accent/40 disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-colors ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent';

function Dialog({ title, icon, onClose, children }) {
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-[60] bg-canvas/80 backdrop-blur-sm flex items-end sm:items-center justify-center p-0 sm:p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="bg-surface border border-line rounded-t-2xl sm:rounded-xl w-full sm:max-w-md max-h-[85vh] overflow-y-auto p-5 shadow-card"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-bold text-fg flex items-center gap-2">{icon}{title}</h3>
          <button onClick={onClose} className="min-h-11 min-w-11 -mr-2 flex items-center justify-center text-fg-muted hover:text-fg cursor-pointer" aria-label="Đóng">
            <X className="w-4 h-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/**
 * Phiên âm AI tab. `onOpenTranslate` is called after a chained translate job
 * is created so the hub can switch to the "Dịch" tab.
 */
export default function TranscriptAsrPage({ onOpenTranslate }) {
  const { session } = useAuth();
  const [jobs, setJobs]                 = useState([]);
  const [jobsState, setJobsState]       = useState('loading'); // loading | ok | error
  const [quota, setQuota]               = useState(null);
  const [quotaState, setQuotaState]     = useState('loading'); // loading | ok | error
  const [errorMsg, setErrorMsg]         = useState('');
  const [picking, setPicking]           = useState(false);
  const [history, setHistory]           = useState([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [creatingId, setCreatingId]     = useState(null);
  const submittingRef = useRef(false);

  const [downloadJob, setDownloadJob]   = useState(null);
  const [downloading, setDownloading]   = useState('');
  const [confirmJob, setConfirmJob]     = useState(null);
  const [confirming, setConfirming]     = useState(false);

  const [chainJob, setChainJob]         = useState(null);
  const [chainLang, setChainLang]       = useState('');
  const [chainLoading, setChainLoading] = useState(false);
  const [chainError, setChainError]     = useState('');

  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs`, { headers: authHeaders(session) });
      if (!r.ok) throw new Error(String(r.status));
      const d = await r.json();
      setJobs(d.jobs || []);
      setJobsState('ok');
    } catch {
      // Keep the last known list on a transient error; only the first load shows an error state.
      setJobsState((s) => (s === 'ok' ? 'ok' : 'error'));
    }
  }, [session]);

  const fetchQuota = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/quota`, { headers: authHeaders(session) });
      if (!r.ok) throw new Error(String(r.status));
      setQuota(await r.json());
      setQuotaState('ok');
    } catch {
      setQuotaState((s) => (s === 'ok' ? 'ok' : 'error'));
    }
  }, [session]);

  useEffect(() => { fetchJobs(); fetchQuota(); }, [fetchJobs, fetchQuota]);

  const hasActive = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
  useEffect(() => {
    if (!hasActive) return undefined;
    const timer = setInterval(() => { fetchJobs(); fetchQuota(); }, 5000);
    return () => clearInterval(timer);
  }, [hasActive, fetchJobs, fetchQuota]);

  const remaining = quota && quota.minutes_used != null && quota.minutes_limit != null
    ? Math.max(0, quota.minutes_limit - quota.minutes_used)
    : null;
  const exhausted = quotaState === 'ok' && quota?.enabled && remaining !== null && remaining <= 0;
  const canCreate = quotaState === 'ok' && quota?.enabled === true && !exhausted;
  const resetTime = formatResetTime(quota?.reset_at_utc);

  async function openPicker() {
    setPicking(true);
    setHistoryError('');
    setHistoryLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/history?limit=10&status=success`, { headers: authHeaders(session) });
      const d = await r.json();
      const withLocalFile = (d.jobs || []).filter((j) => localFileId(j));
      setHistory(withLocalFile);
      if (withLocalFile.length === 0) {
        setHistoryError('Không tìm thấy video nào còn trên server. Tải video trước khi tạo phụ đề.');
      }
    } catch {
      setHistoryError('Không tải được danh sách video đã tải. Thử lại sau.');
    } finally {
      setHistoryLoading(false);
    }
  }

  function closePicker() {
    setPicking(false);
    setHistory([]);
    setHistoryError('');
  }

  async function handlePick(item) {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setCreatingId(item.id);
    setErrorMsg('');
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs`, {
        method: 'POST',
        headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_local_path: localFileId(item),
          video_title: item.title || item.original_url,
        }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        setErrorMsg(apiErrorText(d, 'Tạo job thất bại.'));
        closePicker();
        fetchQuota(); // the failure may mean the feature just got switched off / the quota changed
        return;
      }
      setJobs((prev) => [d, ...prev]);
      closePicker();
      fetchQuota();
    } catch {
      setErrorMsg('Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.');
    } finally {
      submittingRef.current = false;
      setCreatingId(null);
    }
  }

  async function handleDownload(job, fmt) {
    setErrorMsg('');
    setDownloading(fmt);
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs/${job.id}/download?format=${fmt}`, {
        headers: authHeaders(session),
      });
      if (!r.ok) throw new Error('Không thể tải file.');
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${job.video_title || 'transcript'}.${fmt}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      setDownloadJob(null);
    } catch (e) {
      setDownloadJob(null);
      setErrorMsg(e.message || 'Không thể tải file.');
    } finally {
      setDownloading('');
    }
  }

  async function handleConfirm() {
    if (!confirmJob) return;
    setConfirming(true);
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs/${confirmJob.id}`, {
        method: 'DELETE',
        headers: authHeaders(session),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setErrorMsg(apiErrorText(d, 'Không thực hiện được thao tác. Thử lại sau.'));
      } else {
        setJobs((prev) => prev.filter((j) => j.id !== confirmJob.id));
        fetchQuota(); // a cancel refunds the reserved minutes
      }
    } catch {
      setErrorMsg('Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại.');
    } finally {
      setConfirming(false);
      setConfirmJob(null);
    }
  }

  function openChainPicker(job) {
    setChainJob(job);
    setChainLang('');
    setChainError('');
  }

  async function handleChainTranslate() {
    if (!chainJob || !chainLang) return;
    setChainLoading(true);
    setChainError('');
    try {
      const r = await fetch(`${API}/api/v1/transcript-asr/jobs/${chainJob.id}/translate`, {
        method: 'POST',
        headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_lang: chainLang }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(apiErrorText(d, 'Tạo job dịch thất bại.'));
      setChainJob(null);
      if (onOpenTranslate) onOpenTranslate();
      else window.location.href = '/transcript-translate';
    } catch (e) {
      setChainError(e.message || 'Tạo job dịch thất bại.');
    } finally {
      setChainLoading(false);
    }
  }

  const isActive = (j) => ACTIVE_STATUSES.includes(j.status);

  function renderRowActions(job) {
    const isDone = job.status === 'done';
    return (
      <div className="flex items-center gap-2 flex-wrap">
        {isDone && (
          <>
            <button onClick={() => setDownloadJob(job)} className={ROW_BTN} aria-label={`Tải phụ đề ${job.video_title || ''}`}>
              <Download className="w-4 h-4" /> Tải
            </button>
            <button onClick={() => openChainPicker(job)} className={ROW_BTN} aria-label={`Dịch phụ đề ${job.video_title || ''}`}>
              <Languages className="w-4 h-4" /> Dịch phụ đề
            </button>
          </>
        )}
        <button
          onClick={() => setConfirmJob(job)}
          className={`${ROW_BTN} hover:!text-danger hover:!border-danger/40`}
          aria-label={`${isActive(job) ? 'Huỷ' : 'Xoá'} job ${job.video_title || ''}`}
        >
          {isActive(job) ? <><Ban className="w-4 h-4" /> Huỷ</> : <><Trash2 className="w-4 h-4" /> Xoá</>}
        </button>
      </div>
    );
  }

  function renderProgressBar(job) {
    if (!isActive(job)) return null;
    const pct = Math.min(100, Math.max(0, job.progress_pct ?? 0));
    return (
      <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
    );
  }

  function renderFailureNote(job) {
    if (job.status !== 'failed') return null;
    const cancelled = job.error_code === 'cancelled';
    return <p className={`text-xs ${cancelled ? 'text-fg-muted' : 'text-danger'}`}>{jobErrorText(job)}</p>;
  }

  return (
    <div data-testid="asr-panel">
      <p className="text-sm text-fg-muted leading-relaxed mb-4">
        Dành cho video chưa có sẵn phụ đề: chọn 1 video đã tải, hệ thống tự nhận diện giọng nói và tạo phụ đề
        kèm mốc thời gian. Có thể dịch luôn phụ đề vừa tạo sang ngôn ngữ khác.
      </p>

      {errorMsg && (
        <div role="alert" className="mb-4 flex items-start gap-2 px-4 py-3 bg-danger-soft border border-danger/30 rounded-xl text-sm text-danger">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <p className="flex-1">{errorMsg}</p>
          <button onClick={() => setErrorMsg('')} className="min-h-11 min-w-11 -my-2.5 -mr-2 flex items-center justify-center cursor-pointer shrink-0" aria-label="Đóng thông báo lỗi">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Create / quota card */}
      <section className="mb-8 bg-surface border border-line rounded-xl p-4" aria-label="Tạo phụ đề mới">
        {quotaState === 'loading' && (
          <div className="flex items-center gap-2 text-sm text-fg-muted" data-testid="quota-loading">
            <Loader2 className="w-4 h-4 animate-spin" /> Đang kiểm tra hạn mức…
          </div>
        )}

        {quotaState === 'error' && (
          <div className="flex flex-col sm:flex-row sm:items-center gap-3" data-testid="quota-error">
            <p className="text-sm text-danger flex items-start gap-2 flex-1">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              Không kiểm tra được hạn mức hôm nay nên chưa thể tạo phụ đề mới.
            </p>
            <button onClick={() => { setQuotaState('loading'); fetchQuota(); }} className={ROW_BTN}>
              <RefreshCw className="w-4 h-4" /> Thử lại
            </button>
          </div>
        )}

        {quotaState === 'ok' && quota && !quota.enabled && (
          <div className="flex items-start gap-3" data-testid="asr-disabled">
            <PowerOff className="w-5 h-5 text-fg-muted shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-semibold text-fg">Tính năng đang tạm tắt</p>
              <p className="text-xs text-fg-muted mt-1 leading-relaxed">
                {quota.unavailable_reason && ERROR_VI[quota.unavailable_reason]
                  ? ERROR_VI[quota.unavailable_reason]
                  : 'Phiên âm AI hiện chưa mở. Bạn vẫn xem và tải được các phụ đề đã tạo trước đó.'}
              </p>
            </div>
          </div>
        )}

        {quotaState === 'ok' && quota?.enabled && (
          <div className="flex flex-col gap-3">
            <div className="flex items-start gap-3" data-testid="quota-line">
              <Clock className="w-5 h-5 text-fg-muted shrink-0 mt-0.5" />
              <div className="text-sm">
                {remaining !== null ? (
                  <p className="text-fg">
                    <span className="font-semibold">Còn {formatMinutes(remaining)}</span> hôm nay
                    <span className="text-fg-muted"> (đã dùng {formatMinutes(quota.minutes_used)} / {formatMinutes(quota.minutes_limit)})</span>
                  </p>
                ) : (
                  <p className="text-fg-2">
                    Không đọc được số phút đã dùng · hạn mức {formatMinutes(quota.minutes_limit)}/ngày
                  </p>
                )}
                <p className="text-xs text-fg-muted mt-1">
                  Mỗi video tối đa {quota.per_job_max_minutes} phút
                  {resetTime ? ` · hạn mức làm mới lúc ${resetTime}` : ''}
                </p>
              </div>
            </div>

            {exhausted ? (
              <p className="text-sm text-warning-fg bg-warning-soft border border-line rounded-lg px-3 py-2.5" data-testid="quota-exhausted">
                Đã hết hạn mức phiên âm hôm nay.{resetTime ? ` Quay lại sau ${resetTime} để tạo tiếp.` : ' Quay lại vào ngày mai để tạo tiếp.'}
              </p>
            ) : (
              <button
                onClick={openPicker}
                className="w-full min-h-12 flex items-center justify-center gap-2 px-5 py-3 rounded-xl border-2 border-dashed border-line hover:border-accent/40 text-fg-2 hover:text-accent-text transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
              >
                <Mic className="w-5 h-5" />
                Chọn video đã tải để tạo phụ đề
              </button>
            )}
          </div>
        )}
      </section>

      <div>
        <h2 className="text-xs font-bold text-fg-muted uppercase tracking-wider mb-3">
          Hàng đợi{jobs.length > 0 ? ` (${jobs.length})` : ''}
        </h2>

        {jobsState === 'loading' && (
          <div className="flex flex-col gap-3" data-testid="jobs-loading" aria-busy="true">
            {[0, 1].map((i) => <div key={i} className="h-24 rounded-xl bg-surface-2 animate-pulse" />)}
          </div>
        )}

        {jobsState === 'error' && (
          <div className="bg-danger-soft border border-danger/30 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center gap-3" data-testid="jobs-error">
            <p className="text-sm text-danger flex-1">Không tải được danh sách job. Kiểm tra mạng rồi thử lại.</p>
            <button onClick={() => { setJobsState('loading'); fetchJobs(); }} className={ROW_BTN}>
              <RefreshCw className="w-4 h-4" /> Thử lại
            </button>
          </div>
        )}

        {jobsState === 'ok' && jobs.length === 0 && (
          <EmptyState
            icon={<Mic className="w-12 h-12" />}
            title="Chưa có job tạo phụ đề nào"
            body={canCreate ? 'Chọn video đã tải ở trên để bắt đầu.' : 'Các job bạn tạo sẽ hiện ở đây.'}
          />
        )}

        {jobsState === 'ok' && jobs.length > 0 && (
          <>
            {/* Desktop table */}
            <div className="hidden md:block bg-surface border border-line rounded-xl" data-testid="jobs-table">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[11px] uppercase tracking-wider text-fg-muted border-b border-line">
                    <th className="px-4 py-3 font-semibold">Video</th>
                    <th className="px-3 py-3 font-semibold">Thời lượng</th>
                    <th className="px-3 py-3 font-semibold">Trạng thái</th>
                    <th className="px-4 py-3 font-semibold text-right">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map((job) => (
                    <tr key={job.id} className="border-b border-line last:border-0 align-top">
                      <td className="px-4 py-3 max-w-[16rem]">
                        <p className="font-medium text-fg flex items-center gap-2 min-w-0">
                          <Film className="w-4 h-4 text-fg-muted shrink-0" />
                          <span className="truncate">{job.video_title || 'Video'}</span>
                        </p>
                        {job.detected_language && <p className="text-xs text-fg-muted mt-1">Ngôn ngữ: {job.detected_language}</p>}
                        <div className="mt-2 space-y-1">{renderProgressBar(job)}{renderFailureNote(job)}</div>
                      </td>
                      <td className="px-3 py-3 font-mono text-xs text-fg-2 whitespace-nowrap">{formatDuration(job.duration_sec)}</td>
                      <td className="px-3 py-3"><StatusBadge job={job} /></td>
                      <td className="px-4 py-3"><div className="flex justify-end">{renderRowActions(job)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Mobile cards */}
            <div className="md:hidden flex flex-col gap-3" data-testid="jobs-cards">
              {jobs.map((job) => (
                <div key={job.id} className="bg-surface border border-line rounded-xl p-4 flex flex-col gap-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-fg flex items-center gap-2 min-w-0">
                        <Film className="w-4 h-4 text-fg-muted shrink-0" />
                        <span className="truncate">{job.video_title || 'Video'}</span>
                      </p>
                      <p className="text-xs text-fg-muted mt-1">
                        <span className="font-mono">{formatDuration(job.duration_sec)}</span>
                        {job.detected_language ? ` · Ngôn ngữ: ${job.detected_language}` : ''}
                      </p>
                    </div>
                    <StatusBadge job={job} />
                  </div>
                  {renderProgressBar(job)}
                  {renderFailureNote(job)}
                  {renderRowActions(job)}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="h-16 sm:h-0" aria-hidden="true" />

      {picking && (
        <Dialog title="Chọn video" icon={<Mic className="w-4 h-4 text-accent-text" />} onClose={closePicker}>
          {historyLoading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-fg-muted text-sm">
              <Loader2 className="w-4 h-4 animate-spin" /> Đang tải...
            </div>
          ) : (
            <>
              {historyError && (
                <p className="text-xs text-danger mb-3 flex items-start gap-1.5">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  {historyError}
                </p>
              )}
              <div className="flex flex-col gap-2">
                {history.map((item) => (
                  <button
                    key={item.id}
                    onClick={() => handlePick(item)}
                    disabled={creatingId !== null}
                    className="text-left min-h-12 px-3 py-2.5 rounded-lg border border-line hover:border-accent/40 hover:bg-accent-soft transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-between gap-2"
                  >
                    <span className="min-w-0">
                      <span className="block text-sm text-fg truncate">{item.title || item.original_url}</span>
                      {item.platform && <span className="block text-[11px] text-fg-muted mt-0.5">{item.platform}</span>}
                    </span>
                    {creatingId === item.id && <Loader2 className="w-4 h-4 animate-spin text-accent-text shrink-0" />}
                  </button>
                ))}
              </div>
            </>
          )}
        </Dialog>
      )}

      {downloadJob && (
        <Dialog title="Tải phụ đề" icon={<Download className="w-4 h-4 text-accent-text" />} onClose={() => setDownloadJob(null)}>
          <p className="text-xs text-fg-muted mb-3 truncate">{downloadJob.video_title || 'Video'}</p>
          <div className="flex flex-col gap-2">
            {DOWNLOAD_FORMATS.map((f) => (
              <button
                key={f.key}
                onClick={() => handleDownload(downloadJob, f.key)}
                disabled={!!downloading}
                className="text-left min-h-12 px-3 py-2 rounded-lg border border-line hover:border-accent/40 hover:bg-accent-soft disabled:opacity-50 cursor-pointer flex items-center justify-between gap-3"
              >
                <span>
                  <span className="block text-sm font-semibold text-fg">{f.label}</span>
                  <span className="block text-[11px] text-fg-muted">{f.hint}</span>
                </span>
                {downloading === f.key ? <Loader2 className="w-4 h-4 animate-spin text-accent-text" /> : <Download className="w-4 h-4 text-fg-muted" />}
              </button>
            ))}
          </div>
        </Dialog>
      )}

      {confirmJob && (
        <Dialog
          title={isActive(confirmJob) ? 'Huỷ job này?' : 'Xoá job này?'}
          icon={<Trash2 className="w-4 h-4 text-danger" />}
          onClose={() => !confirming && setConfirmJob(null)}
        >
          <p className="text-sm text-fg-2 mb-4 leading-relaxed">
            {isActive(confirmJob)
              ? 'Job sẽ dừng lại và số phút đã giữ cho video này được hoàn lại hạn mức của bạn.'
              : 'Job và file phụ đề đã tạo sẽ bị xoá, không thể hoàn tác. Video gốc không bị ảnh hưởng.'}
          </p>
          <div className="flex gap-2">
            <button onClick={() => setConfirmJob(null)} disabled={confirming} className={`${ROW_BTN} flex-1`}>Giữ lại</button>
            <button
              onClick={handleConfirm}
              disabled={confirming}
              className="flex-1 inline-flex items-center justify-center gap-2 min-h-11 px-4 rounded-lg bg-danger text-accent-fg font-bold text-sm hover:opacity-90 disabled:opacity-50 cursor-pointer"
            >
              {confirming && <Loader2 className="w-4 h-4 animate-spin" />}
              {isActive(confirmJob) ? 'Huỷ job' : 'Xoá job'}
            </button>
          </div>
        </Dialog>
      )}

      {chainJob && (
        <Dialog title="Dịch phụ đề" icon={<Languages className="w-4 h-4 text-accent-text" />} onClose={() => setChainJob(null)}>
          <label htmlFor="chain-lang-select" className="text-xs text-fg-muted mb-1 block">Ngôn ngữ đích</label>
          <select
            id="chain-lang-select"
            value={chainLang}
            onChange={(e) => setChainLang(e.target.value)}
            className="w-full min-h-11 bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-none focus:border-accent/50 cursor-pointer mb-3"
          >
            <option value="" disabled>-- Chọn ngôn ngữ đích --</option>
            {Object.entries(TARGET_LANGS).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>

          {chainError && <p className="text-xs text-danger mb-3">{chainError}</p>}

          <button
            onClick={handleChainTranslate}
            disabled={!chainLang || chainLoading}
            className="w-full inline-flex items-center justify-center gap-2 min-h-11 px-4 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            {chainLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Languages className="w-4 h-4" />}
            {chainLoading ? 'Đang tạo...' : 'Bắt đầu dịch'}
          </button>
        </Dialog>
      )}
    </div>
  );
}
