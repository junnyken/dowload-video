import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../context/AuthContext';
import {
  Upload, Download, Trash2, Loader2, CheckCircle2, XCircle,
  FileText, Languages, X, AlertCircle, Film, Pencil, Save,
} from 'lucide-react';
import EmptyState from '../components/shared/EmptyState';
import { localFileId } from '../lib/localFile';
import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';

const NS = 'transcriptTranslate';

const API = import.meta.env.VITE_API_URL || '';

// Stable per-browser identity for anonymous-ownership scoping — mirrors the
// existing `vg_session_id` convention in UserHistoryContent.jsx. Sent
// alongside Authorization (not instead of) so that if a Supabase access
// token expires mid-session, the backend's identity fallback resolves to
// this same session id instead of silently orphaning the request under a
// null identity — see app.api.transcript_translate.resolve_identity.
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

// Must match backend's `_TARGET_LANGS` allowlist exactly (key + order).
// Display names live in locales/*/transcriptTranslate.json under langs.<code>.
const TARGET_LANG_CODES = ['vi', 'en', 'ja', 'ko', 'zh', 'fr', 'de', 'es', 'th', 'id'];

// Backend's detect_source_language() returns the language name in English
// (e.g. "Vietnamese", "Korean") — map it back to a language code so the UI
// shows it in the user's language and doesn't mix "Korean → German". Falls
// back to the raw LLM answer for a detected language outside our 10-language
// target list (still correct, just not translated for display).
const SOURCE_LANG_CODES = {
  Vietnamese: 'vi',
  English: 'en',
  Japanese: 'ja',
  Korean: 'ko',
  Chinese: 'zh',
  French: 'fr',
  German: 'de',
  Spanish: 'es',
  Thai: 'th',
  Indonesian: 'id',
};

function sourceLangLabel(sourceLang, t) {
  if (!sourceLang) return t('detecting');
  const code = SOURCE_LANG_CODES[sourceLang];
  return code ? t(`langs.${code}`) : sourceLang;
}

const ACTIVE_STATUSES = ['queued', 'detecting', 'translating'];

const STATUS_CONFIG = {
  queued:      { labelKey: 'status.queued',              cls: 'bg-line text-fg-muted border-line-strong' },
  detecting:   { labelKey: 'status.detecting', cls: 'bg-surface-2 text-fg-2 border-line', spin: true },
  translating: { labelKey: 'status.translating',              cls: 'bg-surface-2 text-fg-2 border-line', spin: true },
  done:        { labelKey: 'status.done',               cls: 'bg-success-soft text-success border-success/30' },
  failed:      { labelKey: 'status.failed',               cls: 'bg-danger-soft text-danger border-danger/30' },
};

function StatusBadge({ status }) {
  const { t } = useTranslation(NS);
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.queued;
  return (
    <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[11px] font-semibold whitespace-nowrap ${cfg.cls}`}>
      {cfg.spin && <Loader2 className="w-3 h-3 animate-spin" />}
      {status === 'done' && <CheckCircle2 className="w-3 h-3" />}
      {status === 'failed' && <XCircle className="w-3 h-3" />}
      {t(cfg.labelKey)}
    </span>
  );
}

function Dropzone({ files, onFilesAdded, onRemoveFile }) {
  const { t } = useTranslation(NS);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef(null);

  function handleDrop(e) {
    e.preventDefault();
    setDragOver(false);
    onFilesAdded(Array.from(e.dataTransfer.files || []));
  }

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        role="button"
        tabIndex={0}
        aria-label={t('dropzoneAria')}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click(); } }}
        className={`cursor-pointer rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragOver ? 'border-accent bg-accent-soft' : 'border-line hover:border-line-strong'
        }`}
      >
        <Upload className="w-8 h-8 mx-auto text-fg-muted mb-2" />
        <p className="text-sm text-fg-2 font-medium">{t('dropzoneText')}</p>
        <p className="text-xs text-fg-muted mt-1">{t('dropzoneHint')}</p>
        <input
          ref={inputRef}
          type="file"
          accept=".srt,.vtt"
          multiple
          className="hidden"
          onChange={(e) => { onFilesAdded(Array.from(e.target.files || [])); e.target.value = ''; }}
        />
      </div>

      {files.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {files.map((f, i) => (
            <li
              key={`${f.name}-${i}`}
              className="flex items-center justify-between gap-2 bg-surface-2 border border-line rounded-lg px-3 py-2 text-sm text-fg-2"
            >
              <span className="flex items-center gap-2 min-w-0">
                <FileText className="w-4 h-4 text-fg-muted shrink-0" />
                <span className="truncate">{f.name}</span>
              </span>
              <button
                onClick={() => onRemoveFile(i)}
                title={t('removeFileTitle')}
                aria-label={t('removeFileAria', { name: f.name })}
                className="text-fg-muted hover:text-danger cursor-pointer shrink-0"
              >
                <X className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function JobCard({ job, onDownload, onDelete, onBurn, onEdit }) {
  const { t } = useTranslation(NS);
  const langLabel = TARGET_LANG_CODES.includes(job.target_lang) ? t(`langs.${job.target_lang}`) : job.target_lang;
  const isDone = job.status === 'done';
  const pct = Math.min(100, Math.max(0, job.progress_pct ?? 0));

  return (
    <div className="bg-surface border border-line rounded-xl p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg flex items-center gap-2 min-w-0">
            <FileText className="w-4 h-4 text-fg-muted shrink-0" />
            <span className="truncate">{job.filename}</span>
          </p>
          <p className="text-xs text-fg-muted mt-1">
            {sourceLangLabel(job.source_lang, t)} → {langLabel}
          </p>
        </div>
        <StatusBadge status={job.status} />
      </div>

      {/* Progress */}
      <div>
        <div className="h-1.5 rounded-full bg-surface-2 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              job.status === 'failed' ? 'bg-danger' : job.status === 'done' ? 'bg-success' : 'bg-accent'
            }`}
            style={{ width: `${pct}%` }}
          />
        </div>
        {job.cue_count != null && (
          <p className="text-[11px] text-fg-muted mt-1">
            {t('translatedCount', { done: job.translated_cue_count ?? 0, total: job.cue_count })}
          </p>
        )}
      </div>

      {job.status === 'failed' && job.error && (
        <p className="text-xs text-danger">{job.error}</p>
      )}

      {job.skipped_block_count > 0 && (
        <p className="text-[11px] text-accent-text flex items-center gap-1">
          <AlertCircle className="w-3 h-3 shrink-0" />
          {t('skipped', { count: job.skipped_block_count })}
        </p>
      )}

      {isDone && job.reading_speed_warning_count > 0 && (
        <p className="text-[11px] text-accent-text flex items-center gap-1" title={t('readingWarnTitle')}>
          <AlertCircle className="w-3 h-3 shrink-0" />
          {t('readingWarn', { count: job.reading_speed_warning_count })}
        </p>
      )}

      <div className="flex items-center gap-2 justify-end">
        <button
          onClick={() => onEdit(job)}
          disabled={!isDone}
          title={t('editTitle')}
          aria-label={t('editAria', { filename: job.filename })}
          className="p-2 rounded-lg border border-line text-fg-2 hover:text-accent-text hover:border-accent/40 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-fg-2 disabled:hover:border-line cursor-pointer transition-colors"
        >
          <Pencil className="w-4 h-4" />
        </button>
        <button
          onClick={() => onDownload(job)}
          disabled={!isDone}
          title={t('downloadTitle')}
          aria-label={t('downloadAria', { filename: job.filename })}
          className="p-2 rounded-lg border border-line text-fg-2 hover:text-accent-text hover:border-accent/40 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-fg-2 disabled:hover:border-line cursor-pointer transition-colors"
        >
          <Download className="w-4 h-4" />
        </button>
        <button
          onClick={() => onBurn(job)}
          disabled={!isDone}
          title={t('burnTitle')}
          aria-label={t('burnAria', { filename: job.filename })}
          className="p-2 rounded-lg border border-line text-fg-2 hover:text-accent-text hover:border-accent/40 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:text-fg-2 disabled:hover:border-line cursor-pointer transition-colors"
        >
          <Film className="w-4 h-4" />
        </button>
        <button
          onClick={() => onDelete(job.id)}
          title={t('deleteTitle')}
          aria-label={t('deleteAria', { filename: job.filename })}
          className="p-2 rounded-lg border border-line text-fg-2 hover:text-danger hover:border-danger/40 cursor-pointer transition-colors"
        >
          <Trash2 className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

export default function TranscriptTranslatePage({ embedded = false, onOpenAsr } = {}) {
  const { session } = useAuth();
  const { t } = useTranslation(NS);
  const lp = useLocalePath();
  const [files, setFiles]           = useState([]);
  const [targetLang, setTargetLang] = useState('');
  const [jobs, setJobs]             = useState([]);
  const [submitting, setSubmitting] = useState(false);
  const [rejected, setRejected]     = useState([]);
  const [errorMsg, setErrorMsg]     = useState('');
  const submittingRef = useRef(false); // synchronous double-submit guard — `submitting` state can lag a fast double-click by a render

  // "Ghép vào video" picker — burnJob is the transcript job being burned;
  // burnHistory/burnLoading/burnError/burnResult all reset per-picker-open.
  const [burnJob, setBurnJob]         = useState(null);
  const [burnHistory, setBurnHistory] = useState([]);
  const [burnLoading, setBurnLoading] = useState(false);
  const [burnError, setBurnError]     = useState('');
  const [burnResult, setBurnResult]   = useState(null);

  // Cue editor — editJob is the job being reviewed; editCues holds the
  // live-editable copy (only .text ever changes locally, index/start/end
  // are display-only and never sent back).
  const [editJob, setEditJob]         = useState(null);
  const [editCues, setEditCues]       = useState([]);
  const [editLoading, setEditLoading] = useState(false);
  const [editSaving, setEditSaving]   = useState(false);
  const [editError, setEditError]     = useState('');
  const [editDirty, setEditDirty]     = useState(new Set());

  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs`, {
        headers: authHeaders(session),
      });
      if (!r.ok) return;
      const d = await r.json();
      setJobs(d.jobs || []);
    } catch { /* keep last known state on transient errors */ }
  }, [session]);

  // Initial fetch
  useEffect(() => { fetchJobs(); }, [fetchJobs]);

  // Poll every 5s only while at least one job is non-terminal
  useEffect(() => {
    const hasActive = jobs.some((j) => ACTIVE_STATUSES.includes(j.status));
    if (!hasActive) return;
    const timer = setInterval(() => fetchJobs(), 5000);
    return () => clearInterval(timer);
  }, [jobs, fetchJobs]);

  function addFiles(newFiles) {
    const valid = newFiles.filter((f) => /\.(srt|vtt)$/i.test(f.name));
    if (valid.length) setFiles((prev) => [...prev, ...valid]);
  }

  function removeFile(idx) {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }

  async function handleSubmit() {
    if (files.length === 0 || !targetLang || submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setErrorMsg('');
    try {
      const form = new FormData();
      files.forEach((f) => form.append('files', f));
      form.append('target_lang', targetLang);

      const r = await fetch(`${API}/api/v1/transcript-translate/upload`, {
        method: 'POST',
        headers: authHeaders(session),
        body: form,
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail?.message || d.detail || t('uploadFailed'));

      if (Array.isArray(d.jobs) && d.jobs.length > 0) {
        setJobs((prev) => [...d.jobs, ...prev]);
      }
      setRejected(Array.isArray(d.rejected) ? d.rejected : []);
      setFiles([]);
    } catch (e) {
      setErrorMsg(e.message || t('uploadError'));
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  async function handleDownload(job) {
    setErrorMsg('');
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${job.id}/download`, {
        headers: authHeaders(session),
      });
      if (!r.ok) throw new Error(t('downloadFailedRetry'));
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = job.filename || 'transcript-translated';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setErrorMsg(e.message || t('downloadFailed'));
    }
  }

  async function handleDelete(id) {
    if (!window.confirm(t('confirmDelete'))) return;
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${id}`, {
        method: 'DELETE',
        headers: authHeaders(session),
      });
      if (r.ok) setJobs((prev) => prev.filter((j) => j.id !== id));
    } catch { /* non-critical */ }
  }

  async function openBurnPicker(job) {
    setBurnJob(job);
    setBurnResult(null);
    setBurnError('');
    setBurnLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/history?limit=10&status=success`, {
        headers: authHeaders(session),
      });
      const d = await r.json();
      // Only videos whose server-side file is still plausibly on disk are
      // useful here — burning needs the real local file, not just a record.
      const withLocalFile = (d.jobs || []).filter((j) => localFileId(j));
      setBurnHistory(withLocalFile);
      if (withLocalFile.length === 0) {
        setBurnError(t('noVideos'));
      }
    } catch {
      setBurnError(t('historyLoadError'));
    } finally {
      setBurnLoading(false);
    }
  }

  function closeBurnPicker() {
    setBurnJob(null);
    setBurnHistory([]);
    setBurnError('');
    setBurnResult(null);
  }

  async function handleBurnPick(historyItem) {
    if (!burnJob) return;
    setBurnLoading(true);
    setBurnError('');
    try {
      const r = await fetch(
        `${API}/api/v1/transcript-translate/jobs/${burnJob.id}/burn-into-video`,
        {
          method: 'POST',
          headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
          body: JSON.stringify({ video_local_path: localFileId(historyItem) }),
        }
      );
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || t('burnFailed'));
      setBurnResult(d);
    } catch (e) {
      setBurnError(e.message || t('burnFailed'));
    } finally {
      setBurnLoading(false);
    }
  }

  async function openEditor(job) {
    setEditJob(job);
    setEditCues([]);
    setEditDirty(new Set());
    setEditError('');
    setEditLoading(true);
    try {
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${job.id}/cues`, {
        headers: authHeaders(session),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || t('editLoadFailed'));
      setEditCues(d.cues || []);
    } catch (e) {
      setEditError(e.message || t('editLoadFailed'));
    } finally {
      setEditLoading(false);
    }
  }

  function closeEditor() {
    setEditJob(null);
    setEditCues([]);
    setEditDirty(new Set());
    setEditError('');
  }

  function editCueText(index, text) {
    setEditCues((prev) => prev.map((c) => (c.index === index ? { ...c, text } : c)));
    setEditDirty((prev) => new Set(prev).add(index));
  }

  async function saveEdits() {
    if (!editJob || editDirty.size === 0) return;
    setEditSaving(true);
    setEditError('');
    try {
      const changed = editCues
        .filter((c) => editDirty.has(c.index))
        .map((c) => ({ index: c.index, text: c.text }));
      const r = await fetch(`${API}/api/v1/transcript-translate/jobs/${editJob.id}/cues`, {
        method: 'PUT',
        headers: { ...authHeaders(session), 'Content-Type': 'application/json' },
        body: JSON.stringify({ cues: changed }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || t('saveFailed'));

      setEditDirty(new Set());
      // Reflect the recomputed warning count in the job list right away,
      // without waiting for the next 5s poll.
      setJobs((prev) => prev.map((j) => (
        j.id === editJob.id ? { ...j, reading_speed_warning_count: d.reading_speed_warning_count } : j
      )));
    } catch (e) {
      setEditError(e.message || t('saveFailed'));
    } finally {
      setEditSaving(false);
    }
  }

  return (
    <div className={embedded ? '' : 'max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12'}>
      {/* Header */}
      <div className="mb-6">
        {embedded ? (
          <p className="text-sm text-fg-muted leading-relaxed">
            {t('intro')}
          </p>
        ) : (
          <>
            <h1 className="text-2xl font-bold text-fg flex items-center gap-2">
              <Languages className="w-6 h-6 text-accent-text" />
              {t('title')}
            </h1>
            <p className="text-sm text-fg-muted mt-1.5 leading-relaxed">
              {t('intro')}
            </p>
          </>
        )}
        <a
          href={lp('/transcript-asr')}
          onClick={(e) => {
            e.preventDefault();
            if (onOpenAsr) { onOpenAsr(); return; }
            window.history.pushState({}, '', '/transcript-asr');
            window.dispatchEvent(new PopStateEvent('popstate'));
          }}
          className="inline-flex items-center gap-1.5 text-xs text-accent-text hover:underline mt-2 cursor-pointer"
        >
          {t('asrLink')}
        </a>
      </div>

      {/* Error banner */}
      {errorMsg && (
        <div className="mb-4 flex items-start gap-2 px-4 py-3 bg-danger-soft border border-danger/30 rounded-xl text-sm text-danger">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <p className="flex-1">{errorMsg}</p>
          <button onClick={() => setErrorMsg('')} className="text-danger hover:text-danger cursor-pointer shrink-0" aria-label={t('closeError')}>
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Rejected files banner */}
      {rejected.length > 0 && (
        <div className="mb-4 px-4 py-3 bg-danger-soft border border-danger/30 rounded-xl text-sm text-danger">
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <p className="font-semibold flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" /> {t('rejectedTitle')}
            </p>
            <button onClick={() => setRejected([])} className="text-danger hover:text-danger cursor-pointer shrink-0" aria-label={t('closeRejected')}>
              <X className="w-4 h-4" />
            </button>
          </div>
          <ul className="space-y-0.5 text-xs text-danger">
            {rejected.map((r, i) => (
              <li key={`${r.filename}-${i}`}>• {r.filename}: {r.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Upload form */}
      <div className="bg-surface border border-line rounded-xl p-5 mb-8">
        <Dropzone files={files} onFilesAdded={addFiles} onRemoveFile={removeFile} />

        <div className="mt-4 flex flex-col sm:flex-row gap-3 sm:items-end">
          <div className="flex-1">
            <label htmlFor="target-lang-select" className="text-xs text-fg-muted mb-1 block">{t('targetLang')}</label>
            <select
              id="target-lang-select"
              value={targetLang}
              onChange={(e) => setTargetLang(e.target.value)}
              className="w-full sm:w-auto bg-surface border border-line rounded-lg px-3 py-2 text-sm text-fg focus:outline-none focus:border-accent/50 cursor-pointer"
            >
              <option value="" disabled>{t('chooseTargetLang')}</option>
              {TARGET_LANG_CODES.map((key) => (
                <option key={key} value={key}>{t(`langs.${key}`)}</option>
              ))}
            </select>
          </div>

          <button
            onClick={handleSubmit}
            disabled={files.length === 0 || !targetLang || submitting}
            className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {submitting ? t('uploading') : t('startTranslate')}
          </button>
        </div>
      </div>

      {/* Queue */}
      <div>
        <h2 className="text-xs font-bold text-fg-muted uppercase tracking-wider mb-3">
          {jobs.length > 0 ? t('queueCount', { n: jobs.length }) : t('queue')}
        </h2>

        {jobs.length === 0 ? (
          <EmptyState
            icon={<Languages className="w-12 h-12" />}
            title={t('emptyTitle')}
            body={t('emptyBody')}
          />
        ) : (
          <div className="flex flex-col gap-3">
            {jobs.map((job) => (
              <JobCard key={job.id} job={job} onDownload={handleDownload} onDelete={handleDelete} onBurn={openBurnPicker} onEdit={openEditor} />
            ))}
          </div>
        )}
      </div>

      {/* Clears the fixed feedback button + mobile bottom nav so the last
          job card's action icons stay tappable (main's global pb-20 only
          clears the bottom nav bar, not the feedback button floating above it). */}
      <div className="h-16 sm:h-0" aria-hidden="true" />

      {burnJob && (
        <div
          className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4"
          onClick={closeBurnPicker}
        >
          <div
            className="bg-surface border border-line rounded-xl w-full max-w-md max-h-[80vh] overflow-y-auto p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-sm font-bold text-fg flex items-center gap-2">
                <Film className="w-4 h-4 text-accent-text" />
                {t('burnHeading')}
              </h3>
              <button onClick={closeBurnPicker} className="text-fg-muted hover:text-fg cursor-pointer" aria-label={t('close')}>
                <X className="w-4 h-4" />
              </button>
            </div>

            <p className="text-xs text-fg-muted mb-3 truncate">
              {t('subtitleLabel')} <span className="text-fg-2">{burnJob.filename}</span>
            </p>

            {burnResult ? (
              <div className="text-center py-4">
                <CheckCircle2 className="w-8 h-8 text-success mx-auto mb-2" />
                <p className="text-sm text-fg mb-3">{t('burnSuccess')}</p>
                <a
                  href={`${API}${burnResult.download_url}`}
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-accent-fg text-sm font-bold hover:opacity-90 transition"
                >
                  <Download className="w-4 h-4" />
                  {t('burnDownload')}
                </a>
              </div>
            ) : burnLoading ? (
              <div className="flex items-center justify-center gap-2 py-8 text-fg-muted text-sm">
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('processing')}
              </div>
            ) : (
              <>
                {burnError && (
                  <p className="text-xs text-danger mb-3 flex items-start gap-1.5">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    {burnError}
                  </p>
                )}
                <div className="flex flex-col gap-2">
                  {burnHistory.map((item) => (
                    <button
                      key={item.id}
                      onClick={() => handleBurnPick(item)}
                      className="text-left px-3 py-2.5 rounded-lg border border-line hover:border-accent/40 hover:bg-accent-soft transition-colors cursor-pointer"
                    >
                      <p className="text-sm text-fg truncate">{item.title || item.original_url}</p>
                      {item.platform && (
                        <p className="text-[11px] text-fg-muted mt-0.5">{item.platform}</p>
                      )}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {editJob && (
        <div className="fixed inset-0 z-[60] bg-black/60 flex items-center justify-center p-4">
          <div className="bg-surface border border-line rounded-xl w-full max-w-2xl max-h-[85vh] flex flex-col">
            <div className="flex items-center justify-between px-5 py-4 border-b border-line shrink-0">
              <h3 className="text-sm font-bold text-fg flex items-center gap-2 min-w-0">
                <Pencil className="w-4 h-4 text-accent-text shrink-0" />
                <span className="truncate">{t('editHeading', { filename: editJob.filename })}</span>
              </h3>
              <button onClick={closeEditor} className="text-fg-muted hover:text-fg cursor-pointer shrink-0" aria-label={t('close')}>
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-5 py-4">
              {editLoading ? (
                <div className="flex items-center justify-center gap-2 py-8 text-fg-muted text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  {t('loading')}
                </div>
              ) : editError && editCues.length === 0 ? (
                <p className="text-sm text-danger flex items-start gap-1.5">
                  <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                  {editError}
                </p>
              ) : (
                <div className="flex flex-col gap-3">
                  {editCues.map((cue) => (
                    <div key={cue.index} className="flex gap-3">
                      <div className="w-24 shrink-0 pt-2 text-[10px] text-fg-muted font-mono leading-tight">
                        <div>#{cue.index}</div>
                        <div>{cue.start.split(',')[0].split('.')[0]}</div>
                      </div>
                      <textarea
                        value={cue.text}
                        onChange={(e) => editCueText(cue.index, e.target.value)}
                        rows={1}
                        className={`flex-1 bg-surface-2 border rounded-lg px-3 py-2 text-sm text-fg resize-y focus:outline-none focus:border-accent/50 ${
                          editDirty.has(cue.index) ? 'border-accent/40' : 'border-line'
                        }`}
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center justify-between gap-3 px-5 py-4 border-t border-line shrink-0">
              <div className="text-xs min-w-0">
                {editError && editCues.length > 0 && (
                  <span className="text-danger flex items-center gap-1"><AlertCircle className="w-3.5 h-3.5 shrink-0" />{editError}</span>
                )}
                {!editError && editDirty.size > 0 && (
                  <span className="text-fg-muted">{t('unsaved', { count: editDirty.size })}</span>
                )}
              </div>
              <button
                onClick={saveEdits}
                disabled={editDirty.size === 0 || editSaving}
                className="shrink-0 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-accent-fg text-sm font-bold hover:opacity-90 transition disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
              >
                {editSaving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {editSaving ? t('saving') : t('save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
