/**
 * SuccessCard — Phase 21
 * =======================
 * Rich post-download success card replacing the plain toast.
 * Shows file info + next best actions based on what was just downloaded.
 *
 * Props:
 *   jobInfo         — { title, platform, format, quality, file_url, file_size,
 *                        duration, thumbnail_url, source_url, job_id }
 *   onRetry(url)    — paste URL back into dashboard
 *   onTrim()        — open trim panel
 *   onGif()         — open GIF panel
 *   onCloudSave()   — trigger cloud save
 *   onSavePreset()  — open preset save dialog
 *   onClose()       — dismiss card
 *   userTier        — 'free'|'pro'|... for feature hints
 */

import { useState } from 'react';
import {
  CheckCircle2, Download, Copy, ExternalLink, Scissors,
  Film, CloudUpload, Bookmark, X, Image, RotateCcw, Zap, Sparkles,
} from 'lucide-react';

function fmt_size(bytes) {
  if (!bytes) return '';
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fmt_duration(s) {
  if (!s || s < 1) return '';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return m > 0 ? `${m}m${sec}s` : `${sec}s`;
}

function CopyButton({ text, label, className = '' }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {}
  };
  return (
    <button onClick={copy} className={`flex items-center gap-1.5 ${className}`}>
      {copied ? <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
      {copied ? 'Đã sao chép' : label}
    </button>
  );
}

/** Decide what "next best actions" to show based on job metadata */
function getNextActions(jobInfo) {
  if (!jobInfo) return [];
  const { platform, format, duration, quality } = jobInfo;
  const actions = [];

  const isVideo = format && !['mp3', 'm4a', 'wav', 'ogg', 'flac'].includes(format);
  const isShort = duration && duration < 90;

  if (isVideo && isShort) actions.push('gif');
  if (isVideo)           actions.push('trim');
  if (platform === 'spotify') actions.push('playlist_zip');
  if (['tiktok', 'instagram', 'youtube'].includes(platform)) actions.push('thumbnail');
  actions.push('new_url');

  return actions.slice(0, 3);
}

export default function SuccessCard({
  jobInfo,
  onRetry,
  onTrim,
  onGif,
  onProcessing,
  onCloudSave,
  onSavePreset,
  onClose,
  userTier = 'free',
}) {
  const [showPresetForm, setShowPresetForm] = useState(false);
  const [presetName, setPresetName] = useState('');

  if (!jobInfo) return null;

  const {
    title, platform, format, quality, file_url, file_size,
    duration, thumbnail_url, source_url, job_id,
  } = jobInfo;

  const nextActions = getNextActions(jobInfo);
  const isPro = ['pro', 'team', 'enterprise', 'api'].includes(userTier);
  const fileLabel = quality === 'thumbnail_only' ? 'Thumbnail' : (format?.toUpperCase() || 'File');

  return (
    <div className="bg-[#0d2e29] border border-emerald-700/40 rounded-2xl overflow-hidden shadow-xl shadow-emerald-950/50">
      {/* Header */}
      <div className="flex items-start gap-3 p-4 border-b border-white/8">
        {thumbnail_url ? (
          <img
            src={thumbnail_url}
            alt=""
            className="w-14 h-10 object-cover rounded-lg shrink-0 bg-black/30"
          />
        ) : (
          <div className="w-14 h-10 rounded-lg bg-emerald-900/40 flex items-center justify-center shrink-0">
            <CheckCircle2 className="w-5 h-5 text-emerald-400" />
          </div>
        )}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-white truncate leading-tight">
            {title || 'Tải hoàn tất'}
          </p>
          <div className="flex items-center gap-2 mt-1 flex-wrap">
            {platform && (
              <span className="text-[10px] font-semibold uppercase tracking-wide text-emerald-400/80 bg-emerald-900/30 px-1.5 py-0.5 rounded">
                {platform}
              </span>
            )}
            {fileLabel && (
              <span className="text-[10px] text-white/50">{fileLabel}</span>
            )}
            {fmt_duration(duration) && (
              <span className="text-[10px] text-white/40">{fmt_duration(duration)}</span>
            )}
            {fmt_size(file_size) && (
              <span className="text-[10px] text-white/40">{fmt_size(file_size)}</span>
            )}
          </div>
        </div>
        <button onClick={onClose} className="text-white/25 hover:text-white/60 transition-colors p-1 cursor-pointer shrink-0">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Primary actions */}
      <div className="flex items-center gap-2 p-3 flex-wrap border-b border-white/6">
        {file_url && (
          <a
            href={file_url}
            download={title || 'download'}
            className="flex items-center gap-1.5 px-3 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold rounded-lg transition-colors"
          >
            <Download className="w-3.5 h-3.5" />
            Tải file
          </a>
        )}
        {file_url && (
          <CopyButton
            text={file_url}
            label="Copy link"
            className="px-3 py-2 bg-white/8 hover:bg-white/15 text-white/80 text-xs font-semibold rounded-lg transition-colors border border-white/10 cursor-pointer"
          />
        )}
        {source_url && (
          <a
            href={source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-3 py-2 bg-white/5 hover:bg-white/10 text-white/50 text-xs rounded-lg transition-colors border border-white/8 cursor-pointer"
          >
            <ExternalLink className="w-3 h-3" />
            Nguồn
          </a>
        )}
        {onProcessing && (
          <button
            onClick={onProcessing}
            className="flex items-center gap-1.5 px-3 py-2 bg-indigo-600/20 hover:bg-indigo-600/35 text-indigo-300 text-xs font-semibold rounded-lg transition-colors border border-indigo-500/30 cursor-pointer"
          >
            <Sparkles className="w-3.5 h-3.5" />
            Xử lý thêm
          </button>
        )}
      </div>

      {/* Next best actions */}
      {nextActions.length > 0 && (
        <div className="p-3 border-b border-white/6">
          <p className="text-[10px] font-semibold text-white/35 uppercase tracking-wider mb-2">Làm gì tiếp?</p>
          <div className="flex items-center gap-2 flex-wrap">
            {nextActions.includes('gif') && (
              <button
                onClick={onGif}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer bg-white/8 hover:bg-white/15 text-white/80 border border-white/10"
                title="Tạo GIF từ video vừa tải"
              >
                <Film className="w-3.5 h-3.5" />
                Tạo GIF
              </button>
            )}
            {nextActions.includes('trim') && (
              <button
                onClick={onTrim}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer bg-white/8 hover:bg-white/15 text-white/80 border border-white/10"
                title="Cắt đoạn video"
              >
                <Scissors className="w-3.5 h-3.5" />
                Cắt clip
              </button>
            )}
            {nextActions.includes('thumbnail') && (
              <button
                onClick={() => onRetry && source_url && onRetry(source_url, { quality: 'thumbnail_only' })}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-white/8 hover:bg-white/15 text-white/80 border border-white/10 transition-colors cursor-pointer"
              >
                <Image className="w-3.5 h-3.5" />
                Lấy thumbnail
              </button>
            )}
            {nextActions.includes('playlist_zip') && (
              <button
                onClick={() => onRetry && source_url && onRetry(source_url, { zip: true })}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-white/8 hover:bg-white/15 text-white/80 border border-white/10 transition-colors cursor-pointer"
              >
                <Zap className="w-3.5 h-3.5" />
                ZIP playlist
              </button>
            )}
            {nextActions.includes('new_url') && (
              <button
                onClick={() => onClose?.()}
                className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-white/5 hover:bg-white/10 text-white/45 border border-white/8 transition-colors cursor-pointer"
              >
                <RotateCcw className="w-3 h-3" />
                Tải link khác
              </button>
            )}
          </div>
        </div>
      )}

      {/* Save preset prompt */}
      <div className="px-3 py-2.5 flex items-center gap-3">
        {!showPresetForm ? (
          <button
            onClick={() => setShowPresetForm(true)}
            className="flex items-center gap-1.5 text-xs text-white/30 hover:text-white/60 transition-colors cursor-pointer"
          >
            <Bookmark className="w-3 h-3" />
            Lưu preset này
          </button>
        ) : (
          <form
            className="flex items-center gap-2 flex-1"
            onSubmit={(e) => {
              e.preventDefault();
              onSavePreset?.(presetName);
              setShowPresetForm(false);
              setPresetName('');
            }}
          >
            <input
              autoFocus
              value={presetName}
              onChange={(e) => setPresetName(e.target.value)}
              placeholder="Tên preset..."
              className="flex-1 bg-white/8 border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-white/30 outline-none focus:border-emerald-500/60"
              maxLength={60}
            />
            <button
              type="submit"
              disabled={!presetName.trim()}
              className="px-2.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white text-xs font-semibold rounded-lg transition-colors cursor-pointer"
            >
              Lưu
            </button>
            <button
              type="button"
              onClick={() => setShowPresetForm(false)}
              className="text-white/30 hover:text-white/60 transition-colors cursor-pointer"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
