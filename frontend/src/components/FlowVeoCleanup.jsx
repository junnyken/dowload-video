import { useState, useRef, useCallback, useEffect } from 'react';
import { useTranslation, Trans } from 'react-i18next';
import {
  Upload, Wand2, Download, AlertCircle,
  CheckCircle, Loader2, RotateCcw, Info, Eye,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';

// A FastAPI HTTPException detail is either a plain string or a structured
// object. `new Error(object)` stringifies to the literal "[object Object]",
// which is exactly what the red error card used to show for every gated or
// structured failure — including the 402 telling a user to upgrade.
const errMessage = (detail, fallback) =>
  (typeof detail === 'string' ? detail : detail?.user_message || detail?.message) || fallback;

// Corner positions — percentages match backend preset_map exactly
const PRESETS = [
  { id: 'lower-right', labelKey: 'presets.lowerRight', arrow: '↘', xPct: 0.82, yPct: 0.88, wPct: 0.16, hPct: 0.10 },
  { id: 'lower-left',  labelKey: 'presets.lowerLeft', arrow: '↙', xPct: 0.02, yPct: 0.88, wPct: 0.16, hPct: 0.10 },
  { id: 'upper-right', labelKey: 'presets.upperRight', arrow: '↗', xPct: 0.82, yPct: 0.02, wPct: 0.16, hPct: 0.10 },
  { id: 'upper-left',  labelKey: 'presets.upperLeft', arrow: '↖', xPct: 0.02, yPct: 0.02, wPct: 0.16, hPct: 0.10 },
];

const METHODS = [
  {
    id: 'natural',
    labelKey: 'methods.natural.label',
    descKey: 'methods.natural.desc',
  },
  {
    id: 'crop',
    labelKey: 'methods.crop.label',
    descKey: 'methods.crop.desc',
  },
  {
    id: 'blur',
    labelKey: 'methods.blur.label',
    descKey: 'methods.blur.desc',
  },
];

const ACCEPT = '.mp4,.mov,.webm,.mkv,video/mp4,video/quicktime,video/webm,video/x-matroska';

// Mirror backend crop logic (percentage coords, not pixels).
// Returns the surviving frame rect {cx,cy,cw,ch} as 0–1 fractions.
function computeCropBounds(activePreset) {
  if (!activePreset) return null;
  const { xPct, yPct, wPct, hPct } = activePreset;
  let cx = 0, cy = 0, cw = 1, ch = 1;
  if (xPct + wPct >= 0.6)      { cw = xPct; }
  else if (xPct <= 0.4)        { cx = xPct + wPct; cw = 1 - cx; }
  if (yPct + hPct >= 0.6)      { ch = yPct; }
  else if (yPct <= 0.4)        { cy = yPct + hPct; ch = 1 - cy; }
  return { cx, cy, cw, ch };
}

// ── Suitability model ────────────────────────────────────────────────
// Deterministic scoring from video metadata only (duration, fps, resolution).
// Levels: good | manual | crop | low
function computeSuitability(info) {
  if (!info) return { level: 'manual', flags: [], cropSuggested: false };
  const { duration = 0, fps = 30, width = 1920, height = 1080 } = info;
  let score = 100;
  const flags = [];

  if (duration > 300)              { score -= 35; flags.push('very_long'); }
  else if (duration > 120)         { score -= 15; flags.push('long'); }
  else if (duration < 2)           { score -= 20; flags.push('very_short'); }
  if (fps > 48)                    { score -= 25; flags.push('high_fps'); }
  else if (fps > 30)               { score -= 8;  flags.push('medium_fps'); }
  if (width < 640 || height < 360) { score -= 20; flags.push('low_res'); }

  // Crop is cleaner when clip is long (FFmpeg delogo has more frames to drift)
  // and motion is not high-fps (crop keeps spatial quality better).
  const cropSuggested =
    (flags.includes('very_long') || flags.includes('long')) &&
    !flags.includes('high_fps') &&
    !flags.includes('low_res');

  let level;
  if (score >= 80)      level = 'good';
  else if (score >= 60) level = 'manual';
  else if (cropSuggested) level = 'crop';
  else                  level = 'low';

  return { level, flags, cropSuggested };
}

const SUIT_CONFIG = {
  good: {
    noteKey: 'suitability.good',
    cls: 'bg-success-soft border-success/25 text-success',
    Icon: CheckCircle,
  },
  manual: {
    noteKey: 'suitability.manual',
    cls: 'bg-accent-soft border-accent/25 text-accent-text',
    Icon: Info,
  },
  crop: {
    noteKey: 'suitability.crop',
    cls: 'bg-surface-2 border-line text-fg-2',
    Icon: Info,
  },
  low: {
    noteKey: 'suitability.low',
    cls: 'bg-accent-soft border-accent/25 text-accent-text',
    Icon: AlertCircle,
  },
};

function SuitabilityBanner({ suitability }) {
  const { t } = useTranslation('logoRemover');
  if (!suitability) return null;
  const cfg = SUIT_CONFIG[suitability.level];
  if (!cfg) return null;
  const { noteKey, cls, Icon } = cfg;
  return (
    <div className={`flex items-start gap-2 px-3 py-2 rounded-xl border ${cls}`}>
      <Icon className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
      <p className="text-[11px] leading-snug">{t(noteKey)}</p>
    </div>
  );
}

// Canvas-based zoomed before/after preview of the cleaned region.
// beforeUrl = original first frame; afterUrl = TELEA-cleaned first frame.
function CornerZoom({ beforeUrl, afterUrl, region }) {
  const { t } = useTranslation('logoRemover');
  const beforeRef = useRef(null);
  const afterRef  = useRef(null);
  const SCALE = 2.5;

  useEffect(() => {
    const draw = (url, ref) => {
      if (!url || !region || !ref.current) return;
      const img = new Image();
      img.onload = () => {
        const iw = img.naturalWidth, ih = img.naturalHeight;
        const rx = Math.round(region.xPct * iw), ry = Math.round(region.yPct * ih);
        const rw = Math.round(region.wPct * iw), rh = Math.round(region.hPct * ih);
        const c = ref.current;
        c.width  = Math.round(rw * SCALE);
        c.height = Math.round(rh * SCALE);
        const ctx = c.getContext('2d');
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(img, rx, ry, rw, rh, 0, 0, c.width, c.height);
      };
      img.src = url;
    };
    draw(beforeUrl, beforeRef);
    draw(afterUrl,  afterRef);
  }, [beforeUrl, afterUrl, region]);

  if (!beforeUrl && !afterUrl) return null;

  return (
    <div className="grid grid-cols-2 gap-2">
      {[
        { ref: beforeRef, labelKey: 'zoom.before', url: beforeUrl, cls: 'border-line',    lCls: 'text-fg-muted' },
        { ref: afterRef,  labelKey: 'zoom.after', url: afterUrl,  cls: 'border-success/30', lCls: 'text-success' },
      ].map(({ ref, labelKey, url, cls, lCls }) => (
        <div key={labelKey} className={`rounded-xl overflow-hidden bg-surface-2 border ${cls}`}>
          <p className={`text-[9px] font-semibold text-center py-1 ${lCls}`}>{t(labelKey)}</p>
          {url ? (
            <canvas ref={ref} className="w-full h-auto block" />
          ) : (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-4 h-4 animate-spin text-fg-muted" />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// Draggable/resizable region editor overlay — renders over the preview image.
// Pointer Events API works for both mouse and touch without extra handlers.
function RegionEditor({ region, onChange }) {
  const containerRef = useRef(null);
  const dragRef = useRef(null);
  const MIN = 0.04;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const p = (v) => `${(v * 100).toFixed(2)}%`;

  const startDrag = useCallback((e, type) => {
    e.preventDefault();
    e.stopPropagation();
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    dragRef.current = { type, startX: e.clientX, startY: e.clientY, sr: { ...region }, rect };

    const onMove = (ev) => {
      const d = dragRef.current;
      if (!d) return;
      const dx = (ev.clientX - d.startX) / d.rect.width;
      const dy = (ev.clientY - d.startY) / d.rect.height;
      const { sr } = d;
      let n = { ...sr };
      switch (d.type) {
        case 'move':
          n.xPct = clamp(sr.xPct + dx, 0, 1 - sr.wPct);
          n.yPct = clamp(sr.yPct + dy, 0, 1 - sr.hPct);
          break;
        case 'TL': { const nx = clamp(sr.xPct+dx, 0, sr.xPct+sr.wPct-MIN), ny = clamp(sr.yPct+dy, 0, sr.yPct+sr.hPct-MIN); n={xPct:nx,yPct:ny,wPct:sr.xPct+sr.wPct-nx,hPct:sr.yPct+sr.hPct-ny}; break; }
        case 'T':  { const ny = clamp(sr.yPct+dy, 0, sr.yPct+sr.hPct-MIN); n={...sr,yPct:ny,hPct:sr.yPct+sr.hPct-ny}; break; }
        case 'TR': { const ny = clamp(sr.yPct+dy, 0, sr.yPct+sr.hPct-MIN); n={...sr,yPct:ny,wPct:clamp(sr.wPct+dx,MIN,1-sr.xPct),hPct:sr.yPct+sr.hPct-ny}; break; }
        case 'L':  { const nx = clamp(sr.xPct+dx, 0, sr.xPct+sr.wPct-MIN); n={...sr,xPct:nx,wPct:sr.xPct+sr.wPct-nx}; break; }
        case 'R':  n={...sr,wPct:clamp(sr.wPct+dx,MIN,1-sr.xPct)}; break;
        case 'BL': { const nx = clamp(sr.xPct+dx, 0, sr.xPct+sr.wPct-MIN); n={...sr,xPct:nx,wPct:sr.xPct+sr.wPct-nx,hPct:clamp(sr.hPct+dy,MIN,1-sr.yPct)}; break; }
        case 'B':  n={...sr,hPct:clamp(sr.hPct+dy,MIN,1-sr.yPct)}; break;
        case 'BR': n={...sr,wPct:clamp(sr.wPct+dx,MIN,1-sr.xPct),hPct:clamp(sr.hPct+dy,MIN,1-sr.yPct)}; break;
        default: break;
      }
      onChange(n);
    };
    const onUp = () => { dragRef.current = null; window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', onUp); };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  }, [region, onChange]);

  if (!region) return null;

  const HANDLES = [
    { id:'TL', l:'0%',   t:'0%',   cur:'nw-resize' },
    { id:'T',  l:'50%',  t:'0%',   cur:'n-resize'  },
    { id:'TR', l:'100%', t:'0%',   cur:'ne-resize' },
    { id:'L',  l:'0%',   t:'50%',  cur:'w-resize'  },
    { id:'R',  l:'100%', t:'50%',  cur:'e-resize'  },
    { id:'BL', l:'0%',   t:'100%', cur:'sw-resize' },
    { id:'B',  l:'50%',  t:'100%', cur:'s-resize'  },
    { id:'BR', l:'100%', t:'100%', cur:'se-resize' },
  ];

  return (
    <div ref={containerRef} className="absolute inset-0 select-none" style={{ touchAction: 'none' }}>
      {/* Dim strips outside the selected region */}
      <div className="absolute inset-x-0 top-0 bg-black/50 pointer-events-none" style={{ height: p(region.yPct) }} />
      <div className="absolute inset-x-0 bg-black/50 pointer-events-none" style={{ top: p(region.yPct + region.hPct), bottom: 0 }} />
      <div className="absolute bg-black/50 pointer-events-none" style={{ top: p(region.yPct), height: p(region.hPct), left: 0, width: p(region.xPct) }} />
      <div className="absolute bg-black/50 pointer-events-none" style={{ top: p(region.yPct), height: p(region.hPct), left: p(region.xPct + region.wPct), right: 0 }} />

      {/* Selection box */}
      <div
        className="absolute border-2 border-accent cursor-move"
        style={{ left: p(region.xPct), top: p(region.yPct), width: p(region.wPct), height: p(region.hPct) }}
        onPointerDown={(e) => startDrag(e, 'move')}
      >
        {HANDLES.map(({ id, l, t, cur }) => (
          <div
            key={id}
            className="absolute w-3.5 h-3.5 bg-accent border-2 border-line-strong rounded-sm z-10"
            style={{ left: l, top: t, transform: 'translate(-50%,-50%)', cursor: cur, touchAction: 'none' }}
            onPointerDown={(e) => startDrag(e, id)}
          />
        ))}
      </div>
    </div>
  );
}

// Shared scope note — used in header and no-logo state
function SynthIDNote({ className = '' }) {
  const { t } = useTranslation('logoRemover');
  return (
    <div className={`flex items-start gap-2 px-3 py-2 rounded-xl bg-surface border border-line ${className}`}>
      <Info className="w-3.5 h-3.5 text-fg-muted flex-shrink-0 mt-0.5" />
      <p className="text-[11px] text-fg-muted leading-snug">
        <Trans t={t} i18nKey="synthIdNote" components={{ b: <strong className="text-fg-muted" /> }} />
      </p>
    </div>
  );
}

export default function FlowVeoCleanup() {
  const { t } = useTranslation('logoRemover');
  const { withAuth } = useAuth();
  // idle | uploading | preview | no-logo | processing | done | error
  const [step, setStep] = useState('idle');
  const [tempId, setTempId] = useState(null);
  const [videoInfo, setVideoInfo] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [preset, setPreset] = useState('lower-right');
  const [method, setMethod] = useState('natural');
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [suitability, setSuitability] = useState(null);
  const [isDragging, setIsDragging] = useState(false);
  const [region, setRegion] = useState(null);           // { xPct, yPct, wPct, hPct }
  const [regionAdjusted, setRegionAdjusted] = useState(false);
  const [expandPx, setExpandPx] = useState(8);
  const [delogoVariant, setDelogoVariant] = useState('standard'); // 'standard' | 'soft'
  const [framePreviewUrl, setFramePreviewUrl] = useState(null);  // cleaned first-frame JPEG from /preview-frame
  const [previewInfo, setPreviewInfo] = useState(null);          // decision layer: strategy/quality/recommend_crop
  const [durationWarning, setDurationWarning] = useState(null);
  const lastRegionRef = useRef(null); // snapshot at process-start for done-step overlay

  const fileInputRef = useRef(null);

  // Duration guard (free: 30s, pro: 2min)
  useEffect(() => {
    if (!videoInfo) { setDurationWarning(null); return; }
    const duration = videoInfo.duration || 0;
    const isPro = false; // TODO: wire to auth context if needed
    const maxSec = isPro ? 120 : 30;
    if (duration > maxSec) {
      setDurationWarning({
        duration,
        maxSec,
        isPro,
      });
    } else {
      setDurationWarning(null);
    }
  }, [videoInfo]);

  const reset = () => {
    setStep('idle');
    setTempId(null);
    setVideoInfo(null);
    setPreviewUrl(null);
    setResult(null);
    setError(null);
    setSuitability(null);
    setMethod('natural');
    setRegion(null);
    setRegionAdjusted(false);
    setExpandPx(8);
    setDelogoVariant('standard');
    setFramePreviewUrl(null);
    setDurationWarning(null);
    lastRegionRef.current = null;
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const handleFile = async (file) => {
    if (!file) return;
    const ext = (file.name || '').split('.').pop().toLowerCase();
    if (!['mp4', 'mov', 'webm', 'mkv'].includes(ext)) {
      setError(t('errors.unsupportedFormat'));
      setStep('error');
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      setError(t('errors.tooLarge'));
      setStep('error');
      return;
    }
    setStep('uploading');
    setError(null);
    const formData = new FormData();
    formData.append('file', file);
    try {
      const res = await fetch(`${API_BASE}/api/v1/flow-cleanup/upload`, withAuth({ method: 'POST', body: formData }));
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errMessage(body.detail, t('errors.uploadFailed', { status: res.status })));
      }
      const data = await res.json();
      setTempId(data.temp_id);
      setVideoInfo(data.video_info);
      setPreviewUrl(`${API_BASE}${data.preview_url}?t=${Date.now()}`);
      const suit = computeSuitability(data.video_info);
      setSuitability(suit);
      if (suit.level === 'crop') setMethod('crop');
      // Init region editor from the active preset
      const initP = PRESETS.find((x) => x.id === preset) ?? PRESETS[0];
      setRegion({ xPct: initP.xPct, yPct: initP.yPct, wPct: initP.wPct, hPct: initP.hPct });
      setRegionAdjusted(false);
      setStep('preview');
    } catch (e) {
      setError(e.message);
      setStep('error');
    }
  };

  // Mode the suitability model originally recommended (before user may have overridden)
  const recommendedMode =
    suitability?.level === 'crop' || suitability?.level === 'low' ? 'crop' : 'natural';

  const handleNoLogo = async () => {
    setStep('no-logo');
    // Best-effort record — non-blocking, fire-and-forget
    if (tempId) {
      fetch(`${API_BASE}/api/v1/flow-cleanup/no-logo`, withAuth({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ temp_id: tempId, suitability_level: suitability?.level ?? null }),
      })).catch(() => {});
    }
  };

  // Preview-first: process only the first frame with TELEA before the full job.
  // For crop mode we skip this — the region overlay already shows the crop boundary.
  const handlePreview = async () => {
    lastRegionRef.current = region ? { ...region } : null;
    setFramePreviewUrl(null);
    setPreviewInfo(null);
    setStep('frame-preview');
    const vw = videoInfo?.width ?? 1920;
    const vh = videoInfo?.height ?? 1080;
    const pixelRegion = region ? {
      x: Math.round(region.xPct * vw),
      y: Math.round(region.yPct * vh),
      w: Math.round(region.wPct * vw),
      h: Math.round(region.hPct * vh),
    } : null;
    try {
      const res = await fetch(`${API_BASE}/api/v1/flow-cleanup/preview-frame`, withAuth({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          temp_id: tempId,
          preset: 'custom',
          region: pixelRegion,
          expand_padding: expandPx,
        }),
      }));
      if (!res.ok) { setFramePreviewUrl('error'); return; }
      const data = await res.json();
      setFramePreviewUrl(`${API_BASE}${data.preview_clean_url}?t=${Date.now()}`);
      setPreviewInfo(data);  // { strategy, motion, quality, recommend_crop, reason, ... }
    } catch {
      setFramePreviewUrl('error');
    }
  };

  const handleProcess = async () => {
    setStep('processing');
    setError(null);
    // Snapshot region now — used later by the done-step overlay
    lastRegionRef.current = region ? { ...region } : null;
    // Convert pct → pixels for backend
    const vw = videoInfo?.width ?? 1920;
    const vh = videoInfo?.height ?? 1080;
    const pixelRegion = region && method !== 'crop' ? {
      x: Math.round(region.xPct * vw),
      y: Math.round(region.yPct * vh),
      w: Math.round(region.wPct * vw),
      h: Math.round(region.hPct * vh),
    } : null;
    const actualMethod = method === 'telea' && delogoVariant === 'soft' ? 'telea_soft' : method;
    try {
      const res = await fetch(`${API_BASE}/api/v1/flow-cleanup/process`, withAuth({
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          temp_id: tempId,
          preset: method !== 'crop' ? 'custom' : preset,
          region: pixelRegion,
          method: actualMethod,
          expand_padding: method !== 'crop' ? expandPx : 0,
          region_adjusted: regionAdjusted,
          suitability_level: suitability?.level ?? null,
          requested_mode: recommendedMode,
        }),
      }));
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(errMessage(body.detail, t('errors.processFailed', { status: res.status })));
      }
      const data = await res.json();
      setResult(data);
      setStep('done');
    } catch (e) {
      setError(e.message);
      setStep('error');
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setIsDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  const activePreset = PRESETS.find((p) => p.id === preset);
  const presetLabel = (id) => {
    const found = PRESETS.find((p) => p.id === id);
    return found ? t(found.labelKey) : id;
  };
  const foundMethod = METHODS.find((m) => m.id === method);
  const methodLabel = method === 'telea' && delogoVariant === 'soft'
    ? t('methods.teleaSoft')
    : (foundMethod ? t(foundMethod.labelKey) : method);

  // CTA label follows selected method; style follows suitability level
  const ctaLabel = {
    natural: t('cta.preview'),
    crop:  t('cta.useCrop'),
    blur:  t('cta.preview'),
  }[method] ?? t('cta.preview');
  const ctaGradient = suitability?.level !== 'low';

  // Crop mode: compute surviving frame boundaries (mirrors backend logic)
  const cropBounds = method === 'crop' ? computeCropBounds(activePreset) : null;

  // Header shown in idle/uploading/preview — hidden in step-specific states that carry their own context
  const showHeader = ['idle', 'uploading', 'preview', 'frame-preview'].includes(step);

  return (
    <div className="w-full max-w-3xl mx-auto px-4">

      {/* ── Shared header (idle / uploading / preview) ──────────── */}
      {showHeader && (
        <div className="mb-6 text-center">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 mb-4 rounded-full bg-surface border border-line text-xs font-mono text-fg-2 tracking-wide">
            <Wand2 className="w-3 h-3" />
            <span>{t('header.badge')}</span>
          </div>
          <h2 className="text-2xl sm:text-3xl font-black text-fg tracking-tight mb-2">
            {t('header.title')}
            <span style={{fontSize:'9px', fontWeight:800, padding:'2px 6px', borderRadius:'4px', background:'var(--vg-accent-soft)', color:'var(--vg-accent-text)', border:'1px solid var(--vg-line-strong)', marginLeft:'6px', letterSpacing:'0.05em', verticalAlign:'middle'}}>EXPERIMENTAL</span>
          </h2>
          <p className="text-sm text-fg-muted max-w-md mx-auto mb-3">
            <Trans t={t} i18nKey="header.subtitle" components={{ b: <strong className="text-fg-2" /> }} />
          </p>
          <div className="flex justify-center">
            <SynthIDNote className="max-w-md text-left" />
          </div>
        </div>
      )}

      {/* ── Step: idle ─────────────────────────────────────────── */}
      {step === 'idle' && (
        <div
          onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-4 w-full px-6 py-12 rounded-2xl border-2 border-dashed cursor-pointer transition-all select-none ${
            isDragging
              ? 'border-accent/60 bg-accent-soft'
              : 'border-line bg-surface-2 hover:border-line-strong hover:bg-surface'
          }`}
        >
          <div className="w-14 h-14 rounded-2xl bg-surface-2 border border-line flex items-center justify-center">
            <Upload className="w-6 h-6 text-accent-text" />
          </div>
          <div className="text-center">
            <p className="text-base font-bold text-fg mb-1">
              {t('idle.drop')}
            </p>
            <p className="text-xs text-fg-muted mb-1">{t('idle.formats')}</p>
            <p className="text-[11px] text-fg-muted">
              {t('idle.hint')}
            </p>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
        </div>
      )}

      {/* ── Step: uploading ────────────────────────────────────── */}
      {step === 'uploading' && (
        <div className="flex flex-col items-center gap-3 py-14">
          <Loader2 className="w-8 h-8 text-success animate-spin" />
          <div className="text-center">
            <p className="text-sm font-semibold text-fg-2">{t('uploading.title')}</p>
            <p className="text-xs text-fg-muted mt-1">{t('uploading.subtitle')}</p>
          </div>
        </div>
      )}

      {/* ── Step: preview ──────────────────────────────────────── */}
      {step === 'preview' && (
        <div className="space-y-5">

          {/* Frame preview — logo box in cleanup mode, crop boundary in crop mode */}
          <div className="relative w-full rounded-xl overflow-hidden bg-surface-2 border border-line">
            <img src={previewUrl} alt={t('preview.firstFrameAlt')} className="w-full h-auto block" />
            {/* Cleanup / blur modes: draggable/resizable region editor */}
            {!cropBounds && region && (
              <RegionEditor
                region={region}
                onChange={(r) => { setRegion(r); setRegionAdjusted(true); }}
              />
            )}
            {/* Crop mode: shade removed strips + outline surviving frame */}
            {cropBounds && (
              <>
                {(cropBounds.cx + cropBounds.cw) < 0.98 && (
                  <div className="absolute inset-y-0 bg-black/60 pointer-events-none"
                    style={{ left: `${(cropBounds.cx + cropBounds.cw) * 100}%`, width: `${(1 - cropBounds.cx - cropBounds.cw) * 100}%` }} />
                )}
                {(cropBounds.cy + cropBounds.ch) < 0.98 && (
                  <div className="absolute inset-x-0 bg-black/60 pointer-events-none"
                    style={{ top: `${(cropBounds.cy + cropBounds.ch) * 100}%`, height: `${(1 - cropBounds.cy - cropBounds.ch) * 100}%` }} />
                )}
                <div
                  className="absolute border-2 border-accent pointer-events-none"
                  style={{
                    left:   `${cropBounds.cx * 100}%`,
                    top:    `${cropBounds.cy * 100}%`,
                    width:  `${cropBounds.cw * 100}%`,
                    height: `${cropBounds.ch * 100}%`,
                  }}
                />
                <div className="absolute bottom-2 left-2 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-accent-text font-semibold">
                  {t('preview.keptAfterCrop')}
                </div>
              </>
            )}
            {videoInfo && (
              <div className={`absolute px-2 py-0.5 rounded-md bg-black/60 text-[10px] text-fg-muted font-mono ${cropBounds ? 'bottom-2 right-2' : 'bottom-2 left-2'}`}>
                {videoInfo.width}×{videoInfo.height} · {videoInfo.duration}s
              </div>
            )}
          </div>

          {/* Suitability assessment — shown when we have a signal */}
          <SuitabilityBanner suitability={suitability} />

          {/* Eligibility prompt — method-aware guidance */}
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-surface-2 border border-line">
            <Eye className="w-3.5 h-3.5 text-fg-muted flex-shrink-0 mt-0.5" />
            <p className="text-[11px] text-fg-muted leading-snug">
              {method === 'crop'
                ? t('preview.cropHint')
                : t('preview.dragHint')}
            </p>
          </div>

          {/* Corner selector */}
          <div>
            <p className="text-xs font-bold text-fg-muted mb-2">{t('preview.whichCorner')}</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {PRESETS.map((p) => (
                <button
                  key={p.id}
                  onClick={() => {
                    setPreset(p.id);
                    setRegion({ xPct: p.xPct, yPct: p.yPct, wPct: p.wPct, hPct: p.hPct });
                    setRegionAdjusted(false);
                  }}
                  className={`flex items-center justify-center gap-1.5 px-3 py-3 rounded-xl text-xs font-bold border transition-all cursor-pointer ${
                    preset === p.id
                      ? 'bg-accent-soft border-accent/50 text-accent-text'
                      : 'bg-surface border-line text-fg-muted hover:border-line-strong hover:text-fg'
                  }`}
                >
                  <span className="text-base leading-none">{p.arrow}</span>
                  <span>{t(p.labelKey)}</span>
                </button>
              ))}
            </div>
          </div>

          {/* No-logo escape — real option, not a tiny link */}
          <button
            onClick={handleNoLogo}
            className="w-full flex items-center justify-center gap-2 py-2.5 text-xs text-fg-muted border border-line rounded-xl hover:text-fg-2 hover:border-line-strong transition-colors cursor-pointer"
          >
            <Eye className="w-3.5 h-3.5" />
            {t('preview.noLogo')}
          </button>

          {/* Expand padding control — only for delogo/blur modes */}
          {method !== 'crop' && (
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-surface-2 border border-line">
              <span className="text-[11px] text-fg-muted">{t('preview.expand')}</span>
              <div className="flex gap-1">
                {[0, 4, 8, 12, 16].map((px) => (
                  <button
                    key={px}
                    onClick={() => setExpandPx(px)}
                    className={`text-[10px] px-2 py-1 rounded-lg font-mono transition-colors cursor-pointer border ${
                      expandPx === px
                        ? 'bg-accent-soft border-accent/50 text-accent-text'
                        : 'bg-surface-2 border-line text-fg-muted hover:text-fg'
                    }`}
                  >
                    {px === 0 ? t('preview.tight') : `+${px}px`}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Method selector — recommendation badge per suitability level */}
          <div>
            <p className="text-xs font-bold text-fg-muted mb-2">{t('preview.chooseMethod')}</p>
            <p style={{fontSize:'10px', color:'var(--vg-fg-muted)', lineHeight:1.5, marginBottom:'8px'}}>
              {t('preview.methodTip')}
            </p>
            <div className="space-y-1.5">
              {METHODS.map((m) => {
                const isSelected = method === m.id;
                const isRecommended =
                  (suitability?.level === 'good'   && m.id === 'natural') ||
                  (suitability?.level === 'manual'  && m.id === 'natural') ||
                  (suitability?.level === 'crop'    && m.id === 'crop')  ||
                  (suitability?.level === 'low'     && m.id === 'crop');
                return (
                  <button
                    key={m.id}
                    onClick={() => setMethod(m.id)}
                    className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl border transition-all cursor-pointer text-left ${
                      isSelected
                        ? 'bg-surface-2 border-line-strong text-fg'
                        : 'bg-surface-2 border-line text-fg-muted hover:border-line-strong'
                    }`}
                  >
                    <div className={`w-4 h-4 rounded-full border-2 flex-shrink-0 flex items-center justify-center ${
                      isSelected ? 'border-accent' : 'border-line-strong'
                    }`}>
                      {isSelected && <div className="w-2 h-2 rounded-full bg-accent" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="font-bold text-sm leading-none">{t(m.labelKey)}</p>
                        {isRecommended && (
                          <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-bold border ${
                            isSelected
                              ? 'bg-success-soft border-success/30 text-success'
                              : 'bg-accent-soft border-accent/25 text-accent-text'
                          }`}>
                            {isSelected ? t('preview.recommendedSelected') : t('preview.recommended')}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-fg-muted leading-snug mt-0.5">{t(m.descKey)}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* TELEA variant sub-toggle — only when TELEA is selected */}
          {method === 'telea' && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-surface-2 border border-line">
              <span className="text-[11px] text-fg-muted">{t('preview.pixelMode')}</span>
              {[
                { id: 'standard', labelKey: 'preview.variantStandard', hint: 'TELEA thuần' },
                { id: 'soft',     labelKey: 'preview.variantSoft',   hint: 'TELEA + gblur' },
              ].map((v) => (
                <button
                  key={v.id}
                  onClick={() => setDelogoVariant(v.id)}
                  className={`text-[10px] px-2.5 py-1 rounded-md border transition-colors cursor-pointer ${
                    delogoVariant === v.id
                      ? 'bg-surface-2 border-line-strong text-fg'
                      : 'border-line text-fg-muted hover:text-fg-2'
                  }`}
                >
                  {t(v.labelKey)}
                </button>
              ))}
              {delogoVariant === 'soft' && (
                <span className="ml-1 text-[10px] text-fg-muted">{t('preview.softNote')}</span>
              )}
            </div>
          )}

          {/* Rule-based guidance — deterministic, no fake confidence */}
          <div className="px-3 py-2 rounded-xl bg-surface-2 border border-line text-[11px] text-fg-muted space-y-1 leading-snug">
            {method !== 'crop' && <p>{t('guidance.telea')}</p>}
            {method === 'crop' && <p>{t('guidance.crop')}</p>}
            {(suitability?.level === 'crop' || suitability?.level === 'low') && method !== 'crop' && (
              <p className="text-accent-text">{t('guidance.longClip')}</p>
            )}
            {regionAdjusted && <p>{t('guidance.adjusted')}</p>}
            {method !== 'crop' && expandPx === 0 && <p>{t('guidance.padding0')}</p>}
          </div>

          {/* Duration guard warning */}
          {durationWarning && (
            <div style={{background:'var(--vg-accent-soft)', border:'1px solid var(--vg-line-strong)', borderRadius:'10px', padding:'10px 12px', marginBottom:'10px'}}>
              <p style={{fontSize:'11px', fontWeight:700, color:'var(--vg-accent-text)', marginBottom:'4px'}}>{t('duration.title')}</p>
              <p style={{fontSize:'10px', color:'var(--vg-fg-muted)', lineHeight:1.5}}>{t(durationWarning.isPro ? 'duration.pro' : 'duration.free', { seconds: Math.round(durationWarning.duration) })}</p>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <button
              onClick={reset}
              className="flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-bold text-fg-muted border border-line hover:text-fg hover:border-line-strong transition-all cursor-pointer"
            >
              <RotateCcw className="w-4 h-4" />
              {t('preview.changeVideo')}
            </button>
            <button
              onClick={method === 'crop' ? handleProcess : handlePreview}
              disabled={!!durationWarning}
              className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold transition-all cursor-pointer ${
                !!durationWarning
                  ? 'cursor-not-allowed bg-surface-2 text-fg-muted border border-line'
                  : ctaGradient
                  ? 'bg-accent text-accent-fg shadow-md'
                  : 'bg-accent-soft text-accent-text border border-accent/30 hover:bg-accent/20'
              }`}
            >
              <Wand2 className="w-4 h-4" />
              {ctaLabel}
            </button>
          </div>
          {/* Level-specific secondary nudge — shown below main action row */}
          {suitability?.level === 'crop' && method === 'crop' && (
            <button
              onClick={() => setMethod('natural')}
              className="w-full flex items-center justify-center py-1.5 text-[11px] text-fg-muted hover:text-fg-muted transition-colors cursor-pointer"
            >
              {t('preview.tryNatural')}
            </button>
          )}
          {suitability?.level === 'low' && (
            <button
              onClick={() => setMethod('crop')}
              className="w-full flex items-center justify-center py-1.5 text-[11px] text-fg-muted hover:text-fg-muted transition-colors cursor-pointer"
            >
              {t('preview.tryCrop')}
            </button>
          )}
        </div>
      )}

      {/* ── Step: no-logo ──────────────────────────────────────── */}
      {step === 'no-logo' && (
        <div className="space-y-5">
          <div className="text-center">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 mb-4 rounded-full bg-surface-2 border border-line-strong text-xs font-bold text-fg-muted tracking-wide">
              <Eye className="w-3 h-3" />
              <span>{t('noLogo.badge')}</span>
            </div>
            <h2 className="text-xl sm:text-2xl font-black text-fg tracking-tight mb-2">
              {t('noLogo.title')}
            </h2>
            <p className="text-sm text-fg-muted max-w-sm mx-auto">
              {t('noLogo.body')}
            </p>
          </div>

          {/* Explanations */}
          <div className="px-4 py-4 rounded-xl bg-surface-2 border border-line text-xs text-fg-muted space-y-2">
            <p className="font-semibold text-fg-muted mb-1">{t('noLogo.commonCases')}</p>
            <p>
              <Trans t={t} i18nKey="noLogo.case1" components={{ b: <strong className="text-fg-2" /> }} />
            </p>
            <p>
              <Trans t={t} i18nKey="noLogo.case2" components={{ b: <strong className="text-fg-2" /> }} />
            </p>
            <p>
              <Trans t={t} i18nKey="noLogo.case3" components={{ b: <strong className="text-fg-2" /> }} />
            </p>
          </div>

          {/* SynthID explanation — important context */}
          <div className="flex items-start gap-2 px-4 py-3 rounded-xl bg-surface-2 border border-line">
            <Info className="w-3.5 h-3.5 text-fg-muted flex-shrink-0 mt-0.5" />
            <p className="text-[11px] text-fg-muted leading-snug">
              <Trans t={t} i18nKey="noLogo.synthId" components={{ b: <strong className="text-fg-muted" /> }} />
            </p>
          </div>

          <div className="space-y-2">
            {/* Primary exit — this is the expected outcome, not an error */}
            <button
              onClick={reset}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold bg-accent text-accent-fg shadow-md cursor-pointer"
            >
              {t('noLogo.continue')}
            </button>
            {/* Secondary — only if user thinks they still see a logo */}
            <button
              onClick={() => setStep('preview')}
              className="w-full flex items-center justify-center gap-2 py-2 text-xs text-fg-muted hover:text-fg-2 transition-colors cursor-pointer"
            >
              {t('noLogo.manual')}
            </button>
          </div>
        </div>
      )}

      {/* ── Step: frame-preview ────────────────────────────────── */}
      {step === 'frame-preview' && (
        <div className="space-y-4">
          <div className="text-center pt-2">
            <p className="text-sm font-bold text-fg">{t('frame.title')}</p>
            <p className="text-xs text-fg-muted mt-0.5">{t('frame.subtitle')}</p>
          </div>

          {/* Full cleaned frame with region overlay */}
          <div className="relative w-full rounded-xl overflow-hidden bg-surface-2 border border-line">
            {framePreviewUrl && framePreviewUrl !== 'error' ? (
              <>
                <img src={framePreviewUrl} alt={t('frame.processedAlt')} className="w-full h-auto block" />
                {lastRegionRef.current && (
                  <div
                    className="absolute border-2 border-success/60 pointer-events-none"
                    style={{
                      left:   `${lastRegionRef.current.xPct * 100}%`,
                      top:    `${lastRegionRef.current.yPct * 100}%`,
                      width:  `${lastRegionRef.current.wPct * 100}%`,
                      height: `${lastRegionRef.current.hPct * 100}%`,
                    }}
                  />
                )}
                <div className="absolute top-2 right-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-success font-semibold">
                  <CheckCircle className="w-3 h-3" />
                  {t('frame.badge')}
                </div>
              </>
            ) : framePreviewUrl === 'error' ? (
              <div className="flex flex-col items-center justify-center py-10 gap-2">
                <AlertCircle className="w-5 h-5 text-warning" />
                <p className="text-xs text-fg-muted">{t('frame.error')}</p>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-14 gap-3">
                <Loader2 className="w-6 h-6 text-accent-text animate-spin" />
                <p className="text-xs text-fg-muted">{t('frame.loading')}</p>
              </div>
            )}
          </div>

          {/* Corner zoom — before vs after */}
          {framePreviewUrl && framePreviewUrl !== 'error' && lastRegionRef.current && (
            <CornerZoom
              beforeUrl={previewUrl}
              afterUrl={framePreviewUrl}
              region={lastRegionRef.current}
            />
          )}

          {/* Decision badge — which family will process the whole clip */}
          {previewInfo?.strategy && (
            <div className="flex flex-wrap items-center gap-2 text-[11px]">
              <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg font-semibold border ${
                previewInfo.motion === 'moving'
                  ? 'text-fg-2 bg-surface-2 border-line'
                  : 'text-success bg-success-soft border-success/20'}`}>
                {previewInfo.motion === 'moving' ? t('frame.motionMode') : t('frame.staticMode')}
              </span>
              {previewInfo.quality === 'weak' && (
                <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg font-semibold text-accent-text bg-accent-soft border border-accent/20">
                  {t('frame.weak')}
                </span>
              )}
            </div>
          )}

          {/* Crop recommendation — do NOT auto-promote a weak result */}
          {previewInfo?.recommend_crop ? (
            <div className="px-3 py-3 rounded-xl bg-accent-soft border border-accent/30 space-y-2.5">
              <p className="text-xs text-accent-text font-semibold leading-snug">
                {previewInfo.reason || t('frame.weakHint')}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => { setMethod('crop'); handleProcess(); }}
                  className="flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm font-bold bg-accent text-accent-fg shadow-md transition-all cursor-pointer"
                >
                  {t('frame.switchCrop')}
                </button>
                <button
                  onClick={handleProcess}
                  className="px-3 py-2.5 rounded-lg text-xs font-semibold text-fg-muted border border-line hover:text-fg hover:border-line-strong transition-all cursor-pointer whitespace-nowrap"
                >
                  {t('frame.keepNatural')}
                </button>
              </div>
            </div>
          ) : (
            <div className="px-3 py-2.5 rounded-xl bg-surface-2 border border-line text-[11px] text-fg-muted space-y-1 leading-snug">
              <p>· {previewInfo?.reason || t('frame.defaultReason')}</p>
              <p>{t('frame.tipBlur')}</p>
              <p>{t('frame.tipFirstFrame')}</p>
            </div>
          )}

          {/* Actions */}
          {!previewInfo?.recommend_crop && (
            <div className="flex gap-3">
              <button
                onClick={() => setStep('preview')}
                className="flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-bold text-fg-muted border border-line hover:text-fg hover:border-line-strong transition-all cursor-pointer"
              >
                {t('frame.adjustRegion')}
              </button>
              <button
                onClick={handleProcess}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold bg-accent text-accent-fg shadow-md transition-all cursor-pointer"
              >
                <Wand2 className="w-4 h-4" />
                {t('frame.processAll')}
              </button>
            </div>
          )}
          {previewInfo?.recommend_crop && (
            <button
              onClick={() => setStep('preview')}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold text-fg-muted border border-line hover:text-fg hover:border-line-strong transition-all cursor-pointer"
            >
              {t('frame.adjustRegion')}
            </button>
          )}
        </div>
      )}

      {/* ── Step: processing ───────────────────────────────────── */}
      {step === 'processing' && (
        <div className="flex flex-col items-center gap-4 py-16">
          <Loader2 className="w-8 h-8 text-accent-text animate-spin" />
          <div className="text-center">
            <p className="text-sm font-semibold text-fg-2 mb-1">{t('processing.title')}</p>
            <p className="text-xs text-fg-muted">
              {method === 'natural'
                ? t('processing.natural')
                : method === 'crop'
                ? t('processing.crop')
                : t('processing.blur')}
              {' '}{t('processing.suffix')}
            </p>
          </div>
        </div>
      )}

      {/* ── Step: done ─────────────────────────────────────────── */}
      {step === 'done' && result && (
        <div className="space-y-4">

          {/* 1. Title — compact, rewarding */}
          <div className="flex items-center gap-3 px-1 pt-4">
            <div className="w-9 h-9 flex-shrink-0 rounded-xl bg-success-soft border border-success/30 flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-success" />
            </div>
            <div>
              <p className="text-sm font-bold text-fg leading-snug">
                {method === 'crop'
                  ? t('done.titleCrop')
                  : method === 'blur'
                  ? t('done.titleBlur')
                  : t('done.titleNatural')}
              </p>
              <p className="text-[11px] text-fg-muted">{t('done.sizeExpiry', { size: result.file_size_mb })}</p>
            </div>
          </div>

          {/* 2. Preview — method-aware reference visualization */}
          {previewUrl && (
            <div className="relative w-full rounded-xl overflow-hidden bg-surface-2 border border-line">
              <img src={previewUrl} alt={t('done.referenceAlt')} className="w-full h-auto block opacity-75" />
              {method !== 'crop' ? (
                /* Cleanup / blur: emerald box over the exact region that was processed */
                <>
                  {lastRegionRef.current && (
                    <div
                      className="absolute border-2 border-success bg-success-soft pointer-events-none"
                      style={{
                        left:   `${lastRegionRef.current.xPct * 100}%`,
                        top:    `${lastRegionRef.current.yPct * 100}%`,
                        width:  `${lastRegionRef.current.wPct * 100}%`,
                        height: `${lastRegionRef.current.hPct * 100}%`,
                      }}
                    />
                  )}
                  <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-success font-semibold">
                    <CheckCircle className="w-3 h-3" />
                    {t('done.processedRegion')}
                  </div>
                </>
              ) : (
                /* Crop: shade removed strips, outline surviving frame */
                cropBounds && (
                  <>
                    {(cropBounds.cx + cropBounds.cw) < 0.98 && (
                      <div className="absolute inset-y-0 bg-black/65 pointer-events-none"
                        style={{ left: `${(cropBounds.cx + cropBounds.cw) * 100}%`, width: `${(1 - cropBounds.cx - cropBounds.cw) * 100}%` }} />
                    )}
                    {(cropBounds.cy + cropBounds.ch) < 0.98 && (
                      <div className="absolute inset-x-0 bg-black/65 pointer-events-none"
                        style={{ top: `${(cropBounds.cy + cropBounds.ch) * 100}%`, height: `${(1 - cropBounds.cy - cropBounds.ch) * 100}%` }} />
                    )}
                    <div
                      className="absolute border-2 border-success pointer-events-none"
                      style={{
                        left:   `${cropBounds.cx * 100}%`,
                        top:    `${cropBounds.cy * 100}%`,
                        width:  `${cropBounds.cw * 100}%`,
                        height: `${cropBounds.ch * 100}%`,
                      }}
                    />
                    <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-success font-semibold">
                      <CheckCircle className="w-3 h-3" />
                      {t('done.afterCrop')}
                    </div>
                  </>
                )
              )}
              <div className="absolute top-2 right-2 px-2 py-0.5 rounded-md bg-black/60 text-[10px] text-fg-muted">
                {t('done.originalFrame')}
              </div>
            </div>
          )}

          {/* 3. Method summary — human-readable, mode-aware labels */}
          <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-surface-2 border border-line text-xs text-fg-muted">
            <span>{t('done.method')}</span>
            <span className="text-fg-2 font-semibold">{methodLabel}</span>
            <span className="mx-1 text-fg-muted">·</span>
            <span>{method === 'crop' ? t('done.cropEdge') : t('done.region')}</span>
            <span className="text-fg-2 font-semibold">
              {method === 'crop'
                ? presetLabel(preset)
                : regionAdjusted
                ? t('done.customRegion')
                : presetLabel(preset)
              }
            </span>
          </div>

          {/* 4. Primary CTA */}
          <a
            href={`${API_BASE}${result.download_url}`}
            download={result.filename}
            className="flex items-center justify-center gap-2.5 w-full px-5 py-4 rounded-xl text-base font-bold bg-accent text-accent-fg shadow-md transition-all"
          >
            <Download className="w-5 h-5" />
            {t('done.download')}
          </a>

          {/* 5. Secondary actions */}
          <div className="flex gap-2">
            <button
              onClick={() => setStep('preview')}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 text-sm text-fg-muted border border-line rounded-xl hover:text-fg hover:border-line-strong transition-colors cursor-pointer"
            >
              {t('done.adjust')}
            </button>
            <button
              onClick={reset}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 text-sm text-fg-muted hover:text-fg transition-colors cursor-pointer"
            >
              <RotateCcw className="w-4 h-4" />
              {t('done.another')}
            </button>
          </div>

          {/* 6. Quality + scope note — low emphasis; level-aware */}
          <SynthIDNote className="mt-1" />
          {suitability?.level === 'low' ? (
            <p className="text-[11px] text-accent-text text-center px-4 leading-snug">
              <Trans t={t} i18nKey="done.lowConfidence" components={{ b: <strong className="text-accent-text" /> }} />
            </p>
          ) : (
            <p className="text-[11px] text-fg-muted text-center px-4 leading-snug">
              {t('done.variance')}
            </p>
          )}
        </div>
      )}

      {/* ── Step: error ────────────────────────────────────────── */}
      {step === 'error' && (
        <div className="space-y-4 pt-4">
          <div className="flex items-start gap-3 px-5 py-4 rounded-2xl bg-danger-soft border border-danger/20">
            <AlertCircle className="w-5 h-5 text-danger flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-danger mb-1">{t('error.title')}</p>
              <p className="text-xs text-danger">{error || t('error.fallback')}</p>
            </div>
          </div>

          {/* Troubleshooting — honest about limitations */}
          <div className="px-4 py-3 rounded-xl bg-surface-2 border border-line text-xs text-fg-muted space-y-1">
            <p className="font-semibold text-fg-muted mb-1">{t('error.tryThese')}</p>
            <p><Trans t={t} i18nKey="error.tip1" components={{ b: <strong className="text-fg-2" /> }} /></p>
            <p><Trans t={t} i18nKey="error.tip2" components={{ b: <strong className="text-fg-2" /> }} /></p>
            <p>{t('error.tip3')}</p>
          </div>

          <div className="space-y-2">
            {/* Primary: try different region/method if we have a preview (processing error),
                or upload fresh if the error was during upload */}
            <button
              onClick={() => previewUrl ? setStep('preview') : reset()}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold text-fg-2 border border-line-strong hover:text-fg hover:border-line-strong transition-all cursor-pointer"
            >
              <RotateCcw className="w-4 h-4" />
              {previewUrl ? t('error.tryOther') : t('error.retry')}
            </button>
            {previewUrl && (
              <button
                onClick={reset}
                className="w-full flex items-center justify-center py-2 text-xs text-fg-muted hover:text-fg-2 transition-colors cursor-pointer"
              >
                {t('error.changeVideo')}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
