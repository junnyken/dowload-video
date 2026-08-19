import { useState, useRef, useCallback, useEffect } from 'react';
import {
  Upload, Wand2, Download, AlertCircle,
  CheckCircle, Loader2, RotateCcw, Info, Eye,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';

// Corner positions — percentages match backend preset_map exactly
const PRESETS = [
  { id: 'lower-right', label: 'Dưới phải', arrow: '↘', xPct: 0.82, yPct: 0.88, wPct: 0.16, hPct: 0.10 },
  { id: 'lower-left',  label: 'Dưới trái', arrow: '↙', xPct: 0.02, yPct: 0.88, wPct: 0.16, hPct: 0.10 },
  { id: 'upper-right', label: 'Trên phải', arrow: '↗', xPct: 0.82, yPct: 0.02, wPct: 0.16, hPct: 0.10 },
  { id: 'upper-left',  label: 'Trên trái', arrow: '↖', xPct: 0.02, yPct: 0.02, wPct: 0.16, hPct: 0.10 },
];

const METHODS = [
  {
    id: 'natural',
    label: 'Tự nhiên',
    desc: 'Tự nhiên hơn, ít nhòe hơn. Phù hợp hơn khi có chuyển động (lấy nền thật từ khung hình lân cận). Nếu preview còn lem, nên chuyển sang Cắt viền.',
  },
  {
    id: 'crop',
    label: 'Cắt viền',
    desc: 'Cắt bỏ cạnh chứa logo — thay đổi kích thước video, thường sạch nhất khi logo sát mép',
  },
  {
    id: 'blur',
    label: 'Che mờ',
    desc: 'Phủ mờ lên vùng logo — nhanh, nhưng để lại vệt rõ ràng',
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
    note: 'Trường hợp này phù hợp để làm sạch tự động.',
    cls: 'bg-emerald-500/10 border-emerald-500/25 text-emerald-400',
    Icon: CheckCircle,
  },
  manual: {
    note: 'Hệ thống cần bạn xác nhận vùng logo để xử lý chính xác hơn.',
    cls: 'bg-amber-500/10 border-amber-500/25 text-amber-400',
    Icon: Info,
  },
  crop: {
    note: 'Với video này, cắt khung hình có thể cho kết quả tốt hơn lấp pixel.',
    cls: 'bg-sky-500/10 border-sky-500/25 text-sky-400',
    Icon: Info,
  },
  low: {
    note: 'Vùng này chưa phù hợp để làm sạch tự động — kết quả có thể thấy vệt.',
    cls: 'bg-orange-500/10 border-orange-500/25 text-orange-400',
    Icon: AlertCircle,
  },
};

function SuitabilityBanner({ suitability }) {
  if (!suitability) return null;
  const cfg = SUIT_CONFIG[suitability.level];
  if (!cfg) return null;
  const { note, cls, Icon } = cfg;
  return (
    <div className={`flex items-start gap-2 px-3 py-2 rounded-xl border ${cls}`}>
      <Icon className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
      <p className="text-[11px] leading-snug">{note}</p>
    </div>
  );
}

// Canvas-based zoomed before/after preview of the cleaned region.
// beforeUrl = original first frame; afterUrl = TELEA-cleaned first frame.
function CornerZoom({ beforeUrl, afterUrl, region }) {
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
        { ref: beforeRef, label: 'Trước', url: beforeUrl, cls: 'border-slate-700/50',    lCls: 'text-slate-600' },
        { ref: afterRef,  label: 'Sau (tự nhiên)', url: afterUrl,  cls: 'border-emerald-500/30', lCls: 'text-emerald-500/70' },
      ].map(({ ref, label, url, cls, lCls }) => (
        <div key={label} className={`rounded-xl overflow-hidden bg-slate-900/60 border ${cls}`}>
          <p className={`text-[9px] font-semibold text-center py-1 ${lCls}`}>{label}</p>
          {url ? (
            <canvas ref={ref} className="w-full h-auto block" />
          ) : (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-4 h-4 animate-spin text-slate-500" />
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
        className="absolute border-2 border-[#FB923C] cursor-move"
        style={{ left: p(region.xPct), top: p(region.yPct), width: p(region.wPct), height: p(region.hPct) }}
        onPointerDown={(e) => startDrag(e, 'move')}
      >
        {HANDLES.map(({ id, l, t, cur }) => (
          <div
            key={id}
            className="absolute w-3.5 h-3.5 bg-[#FB923C] border-2 border-white rounded-sm z-10"
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
  return (
    <div className={`flex items-start gap-2 px-3 py-2 rounded-xl bg-slate-800/60 border border-slate-700/40 ${className}`}>
      <Info className="w-3.5 h-3.5 text-slate-500 flex-shrink-0 mt-0.5" />
      <p className="text-[11px] text-slate-500 leading-snug">
        Chỉ áp dụng cho logo{' '}
        <strong className="text-slate-400">hiển thị trên khung hình</strong>.{' '}
        Watermark vô hình{' '}
        <strong className="text-slate-400">SynthID</strong>{' '}
        không nằm trong phạm vi hỗ trợ và{' '}
        <strong className="text-slate-400">không thể xóa bằng bất kỳ công cụ nào</strong>.
      </p>
    </div>
  );
}

export default function FlowVeoCleanup() {
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
        message: isPro
          ? `Video dài ${Math.round(duration)}s (giới hạn Pro: 2 phút). Hãy trim video trước.`
          : `Video dài ${Math.round(duration)}s (giới hạn free: 30 giây). Nâng lên Pro hoặc trim video ngắn hơn.`
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
      setError('Chỉ hỗ trợ MP4, MOV, WebM, MKV.');
      setStep('error');
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      setError('File vượt quá 500MB.');
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
        throw new Error(body.detail || `Upload lỗi (${res.status})`);
      }
      const data = await res.json();
      setTempId(data.temp_id);
      setVideoInfo(data.video_info);
      setPreviewUrl(`${data.preview_url}?t=${Date.now()}`);
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
      setFramePreviewUrl(`${data.preview_clean_url}?t=${Date.now()}`);
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
        throw new Error(body.detail || `Xử lý lỗi (${res.status})`);
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
  const methodLabel = method === 'telea' && delogoVariant === 'soft'
    ? 'TELEA mềm'
    : METHODS.find((m) => m.id === method)?.label || method;

  // CTA label follows selected method; style follows suitability level
  const ctaLabel = {
    natural: 'Xem trước kết quả →',
    crop:  'Dùng cách cắt khung hình',
    blur:  'Xem trước kết quả →',
  }[method] ?? 'Xem trước kết quả →';
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
          <div className="inline-flex items-center gap-2 px-4 py-1.5 mb-4 rounded-full bg-emerald-500/10 border border-emerald-500/25 text-xs font-bold text-emerald-400 tracking-wide">
            <Wand2 className="w-3 h-3" />
            <span>Flow/Veo · Làm sạch logo hiển thị</span>
          </div>
          <h2 className="text-2xl sm:text-3xl font-black text-white tracking-tight mb-2">
            Làm sạch logo hiển thị
            <span style={{fontSize:'9px', fontWeight:800, padding:'2px 6px', borderRadius:'4px', background:'rgba(139,92,246,0.2)', color:'#a78bfa', border:'1px solid rgba(139,92,246,0.3)', marginLeft:'6px', letterSpacing:'0.05em', verticalAlign:'middle'}}>EXPERIMENTAL</span>
          </h2>
          <p className="text-sm text-slate-400 max-w-md mx-auto mb-3">
            Dành cho video Flow/Veo có logo hiển thị{' '}
            <strong className="text-slate-300">trên khung hình</strong>.{' '}
            Không phải video nào cũng có.
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
              ? 'border-emerald-400/60 bg-emerald-500/5'
              : 'border-slate-700/60 bg-slate-800/30 hover:border-slate-600 hover:bg-slate-800/50'
          }`}
        >
          <div className="w-14 h-14 rounded-2xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
            <Upload className="w-6 h-6 text-emerald-400" />
          </div>
          <div className="text-center">
            <p className="text-base font-bold text-white mb-1">
              Kéo thả video vào đây hoặc bấm để chọn file
            </p>
            <p className="text-xs text-slate-500 mb-1">MP4 · MOV · WebM · MKV · tối đa 500MB</p>
            <p className="text-[11px] text-slate-600">
              Ưu tiên video có logo hiển thị rõ trên khung hình · Clip &lt; 2 phút tốt nhất
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
          <Loader2 className="w-8 h-8 text-emerald-400 animate-spin" />
          <div className="text-center">
            <p className="text-sm font-semibold text-slate-300">Đang tải lên video...</p>
            <p className="text-xs text-slate-500 mt-1">Trích xuất khung hình để xác định vùng logo hiển thị</p>
          </div>
        </div>
      )}

      {/* ── Step: preview ──────────────────────────────────────── */}
      {step === 'preview' && (
        <div className="space-y-5">

          {/* Frame preview — logo box in cleanup mode, crop boundary in crop mode */}
          <div className="relative w-full rounded-xl overflow-hidden bg-slate-900/50 border border-slate-700/50">
            <img src={previewUrl} alt="Khung hình đầu video" className="w-full h-auto block" />
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
                  className="absolute border-2 border-[#FB923C] pointer-events-none"
                  style={{
                    left:   `${cropBounds.cx * 100}%`,
                    top:    `${cropBounds.cy * 100}%`,
                    width:  `${cropBounds.cw * 100}%`,
                    height: `${cropBounds.ch * 100}%`,
                  }}
                />
                <div className="absolute bottom-2 left-2 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-[#FB923C] font-semibold">
                  Phần giữ lại sau khi cắt
                </div>
              </>
            )}
            {videoInfo && (
              <div className={`absolute px-2 py-0.5 rounded-md bg-black/60 text-[10px] text-slate-400 font-mono ${cropBounds ? 'bottom-2 right-2' : 'bottom-2 left-2'}`}>
                {videoInfo.width}×{videoInfo.height} · {videoInfo.duration}s
              </div>
            )}
          </div>

          {/* Suitability assessment — shown when we have a signal */}
          <SuitabilityBanner suitability={suitability} />

          {/* Eligibility prompt — method-aware guidance */}
          <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-slate-800/40 border border-slate-700/30">
            <Eye className="w-3.5 h-3.5 text-slate-500 flex-shrink-0 mt-0.5" />
            <p className="text-[11px] text-slate-400 leading-snug">
              {method === 'crop'
                ? 'Vùng sáng trong ảnh là phần giữ lại sau khi cắt — vùng tối chứa logo sẽ bị loại bỏ. Đổi góc bên dưới để thay đổi vùng cắt.'
                : 'Kéo vùng cam để di chuyển · kéo góc/cạnh để đổi kích thước. Căn chính xác vào vùng logo hiển thị.'}
            </p>
          </div>

          {/* Corner selector */}
          <div>
            <p className="text-xs font-bold text-slate-400 mb-2">Vùng logo hiển thị ở góc nào?</p>
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
                      ? 'bg-[#FB923C]/20 border-[#FB923C]/50 text-[#FB923C]'
                      : 'bg-slate-800/50 border-slate-700/50 text-slate-400 hover:border-slate-600 hover:text-white'
                  }`}
                >
                  <span className="text-base leading-none">{p.arrow}</span>
                  <span>{p.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* No-logo escape — real option, not a tiny link */}
          <button
            onClick={handleNoLogo}
            className="w-full flex items-center justify-center gap-2 py-2.5 text-xs text-slate-500 border border-slate-700/30 rounded-xl hover:text-slate-300 hover:border-slate-600 transition-colors cursor-pointer"
          >
            <Eye className="w-3.5 h-3.5" />
            Không thấy logo hiển thị trên video này
          </button>

          {/* Expand padding control — only for delogo/blur modes */}
          {method !== 'crop' && (
            <div className="flex items-center justify-between px-3 py-2 rounded-xl bg-slate-800/40 border border-slate-700/30">
              <span className="text-[11px] text-slate-500">Mở rộng vùng:</span>
              <div className="flex gap-1">
                {[0, 4, 8, 12, 16].map((px) => (
                  <button
                    key={px}
                    onClick={() => setExpandPx(px)}
                    className={`text-[10px] px-2 py-1 rounded-lg font-mono transition-colors cursor-pointer border ${
                      expandPx === px
                        ? 'bg-[#FB923C]/20 border-[#FB923C]/50 text-[#FB923C]'
                        : 'bg-slate-700/30 border-slate-700/30 text-slate-500 hover:text-white'
                    }`}
                  >
                    {px === 0 ? 'Khít' : `+${px}px`}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Method selector — recommendation badge per suitability level */}
          <div>
            <p className="text-xs font-bold text-slate-400 mb-2">Chọn phương thức</p>
            <p style={{fontSize:'10px', color:'#64748b', lineHeight:1.5, marginBottom:'8px'}}>
              ⚡ Tốt nhất với logo nhỏ, nền ít chuyển động. Video quá dài → hãy trim trước.
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
                        ? 'bg-slate-700/50 border-slate-600/60 text-white'
                        : 'bg-slate-800/30 border-slate-700/40 text-slate-400 hover:border-slate-600'
                    }`}
                  >
                    <div className={`w-4 h-4 rounded-full border-2 flex-shrink-0 flex items-center justify-center ${
                      isSelected ? 'border-[#FBBF24]' : 'border-slate-600'
                    }`}>
                      {isSelected && <div className="w-2 h-2 rounded-full bg-[#FBBF24]" />}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="font-bold text-sm leading-none">{m.label}</p>
                        {isRecommended && (
                          <span className={`text-[9px] px-1.5 py-0.5 rounded-full font-bold border ${
                            isSelected
                              ? 'bg-emerald-500/20 border-emerald-500/30 text-emerald-400'
                              : 'bg-amber-500/15 border-amber-500/25 text-amber-400'
                          }`}>
                            {isSelected ? '✓ Khuyến nghị' : 'Khuyến nghị'}
                          </span>
                        )}
                      </div>
                      <p className="text-[11px] text-slate-500 leading-snug mt-0.5">{m.desc}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* TELEA variant sub-toggle — only when TELEA is selected */}
          {method === 'telea' && (
            <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-slate-800/40 border border-slate-700/30">
              <span className="text-[11px] text-slate-500">Chế độ lấp pixel:</span>
              {[
                { id: 'standard', label: 'Chuẩn', hint: 'TELEA thuần' },
                { id: 'soft',     label: 'Mềm',   hint: 'TELEA + gblur' },
              ].map((v) => (
                <button
                  key={v.id}
                  onClick={() => setDelogoVariant(v.id)}
                  className={`text-[10px] px-2.5 py-1 rounded-md border transition-colors cursor-pointer ${
                    delogoVariant === v.id
                      ? 'bg-slate-700 border-slate-600 text-white'
                      : 'border-slate-700/30 text-slate-500 hover:text-slate-300'
                  }`}
                >
                  {v.label}
                </button>
              ))}
              {delogoVariant === 'soft' && (
                <span className="ml-1 text-[10px] text-slate-600">làm mịn viền sau TELEA</span>
              )}
            </div>
          )}

          {/* Rule-based guidance — deterministic, no fake confidence */}
          <div className="px-3 py-2 rounded-xl bg-slate-800/30 border border-slate-700/20 text-[11px] text-slate-500 space-y-1 leading-snug">
            {method !== 'crop' && <p>· TELEA phù hợp với logo nhỏ ở góc và nền tương đối sạch. Nên chọn dư ra một chút.</p>}
            {method === 'crop' && <p>· Cắt viền thường sạch nhất khi logo sát mép — không để vệt trên nền.</p>}
            {(suitability?.level === 'crop' || suitability?.level === 'low') && method !== 'crop' && (
              <p className="text-amber-600/80">· Clip này dài hoặc phức tạp — cắt viền có thể cho kết quả tốt hơn.</p>
            )}
            {regionAdjusted && <p>· Đã chỉnh vùng thủ công — xem preview để kiểm tra trước khi xử lý toàn bộ.</p>}
            {method !== 'crop' && expandPx === 0 && <p>· Padding 0px — nếu còn thấy viền mờ, thử tăng lên 8px.</p>}
          </div>

          {/* Duration guard warning */}
          {durationWarning && (
            <div style={{background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.25)', borderRadius:'10px', padding:'10px 12px', marginBottom:'10px'}}>
              <p style={{fontSize:'11px', fontWeight:700, color:'#fbbf24', marginBottom:'4px'}}>⏱ Video quá dài</p>
              <p style={{fontSize:'10px', color:'#94a3b8', lineHeight:1.5}}>{durationWarning.message}</p>
            </div>
          )}

          {/* Actions */}
          <div className="flex gap-3">
            <button
              onClick={reset}
              className="flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-bold text-slate-400 border border-slate-700/50 hover:text-white hover:border-slate-600 transition-all cursor-pointer"
            >
              <RotateCcw className="w-4 h-4" />
              Đổi video
            </button>
            <button
              onClick={method === 'crop' ? handleProcess : handlePreview}
              disabled={!!durationWarning}
              className={`flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold transition-all cursor-pointer ${
                !!durationWarning
                  ? 'opacity-40 cursor-not-allowed bg-slate-700 text-slate-400'
                  : ctaGradient
                  ? 'bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md shadow-[#FBBF24]/20 hover:shadow-[#FBBF24]/40'
                  : 'bg-orange-500/15 text-orange-300 border border-orange-500/30 hover:bg-orange-500/25'
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
              className="w-full flex items-center justify-center py-1.5 text-[11px] text-slate-600 hover:text-slate-400 transition-colors cursor-pointer"
            >
              Vẫn muốn thử xóa tự nhiên thay thế
            </button>
          )}
          {suitability?.level === 'low' && (
            <button
              onClick={() => setMethod('crop')}
              className="w-full flex items-center justify-center py-1.5 text-[11px] text-slate-600 hover:text-slate-400 transition-colors cursor-pointer"
            >
              Dùng cách cắt khung hình thay thế
            </button>
          )}
        </div>
      )}

      {/* ── Step: no-logo ──────────────────────────────────────── */}
      {step === 'no-logo' && (
        <div className="space-y-5">
          <div className="text-center">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 mb-4 rounded-full bg-slate-700/40 border border-slate-600/30 text-xs font-bold text-slate-400 tracking-wide">
              <Eye className="w-3 h-3" />
              <span>Không thấy logo hiển thị</span>
            </div>
            <h2 className="text-xl sm:text-2xl font-black text-white tracking-tight mb-2">
              Video này không cần làm sạch logo
            </h2>
            <p className="text-sm text-slate-400 max-w-sm mx-auto">
              Không phát hiện logo hiển thị trên khung hình. Nếu bạn vẫn thấy logo, hãy thử chọn vùng thủ công.
            </p>
          </div>

          {/* Explanations */}
          <div className="px-4 py-4 rounded-xl bg-slate-800/40 border border-slate-700/30 text-xs text-slate-500 space-y-2">
            <p className="font-semibold text-slate-400 mb-1">Một số trường hợp phổ biến:</p>
            <p>
              · <strong className="text-slate-300">Google One Ultra</strong> — Flow trên plan này thường không kèm logo hiển thị
            </p>
            <p>
              · <strong className="text-slate-300">Logo chỉ ở vài giây</strong> — kiểm tra đầu hoặc cuối clip, không chỉ khung hình đầu tiên
            </p>
            <p>
              · <strong className="text-slate-300">Xuất từ project riêng</strong> — một số luồng xuất không áp dụng logo nhìn thấy
            </p>
          </div>

          {/* SynthID explanation — important context */}
          <div className="flex items-start gap-2 px-4 py-3 rounded-xl bg-slate-800/40 border border-slate-700/30">
            <Info className="w-3.5 h-3.5 text-slate-500 flex-shrink-0 mt-0.5" />
            <p className="text-[11px] text-slate-500 leading-snug">
              Tất cả video Flow/Veo đều có{' '}
              <strong className="text-slate-400">SynthID</strong> — watermark vô hình, không thấy bằng mắt thường.
              SynthID khác hoàn toàn với logo hiển thị và{' '}
              <strong className="text-slate-400">không nằm trong phạm vi hỗ trợ</strong>.
            </p>
          </div>

          <div className="space-y-2">
            {/* Primary exit — this is the expected outcome, not an error */}
            <button
              onClick={reset}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md shadow-[#FBBF24]/20 cursor-pointer"
            >
              Tiếp tục với video gốc
            </button>
            {/* Secondary — only if user thinks they still see a logo */}
            <button
              onClick={() => setStep('preview')}
              className="w-full flex items-center justify-center gap-2 py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors cursor-pointer"
            >
              Tôi vẫn thấy logo — chọn vùng thủ công
            </button>
          </div>
        </div>
      )}

      {/* ── Step: frame-preview ────────────────────────────────── */}
      {step === 'frame-preview' && (
        <div className="space-y-4">
          <div className="text-center pt-2">
            <p className="text-sm font-bold text-white">Kết quả trên khung hình đầu</p>
            <p className="text-xs text-slate-500 mt-0.5">Kiểm tra trước khi xử lý toàn bộ video</p>
          </div>

          {/* Full cleaned frame with region overlay */}
          <div className="relative w-full rounded-xl overflow-hidden bg-slate-900/50 border border-slate-700/50">
            {framePreviewUrl && framePreviewUrl !== 'error' ? (
              <>
                <img src={framePreviewUrl} alt="Khung hình đã xử lý" className="w-full h-auto block" />
                {lastRegionRef.current && (
                  <div
                    className="absolute border-2 border-emerald-400/60 pointer-events-none"
                    style={{
                      left:   `${lastRegionRef.current.xPct * 100}%`,
                      top:    `${lastRegionRef.current.yPct * 100}%`,
                      width:  `${lastRegionRef.current.wPct * 100}%`,
                      height: `${lastRegionRef.current.hPct * 100}%`,
                    }}
                  />
                )}
                <div className="absolute top-2 right-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-emerald-400 font-semibold">
                  <CheckCircle className="w-3 h-3" />
                  Khung hình đầu · sau xử lý tự nhiên
                </div>
              </>
            ) : framePreviewUrl === 'error' ? (
              <div className="flex flex-col items-center justify-center py-10 gap-2">
                <AlertCircle className="w-5 h-5 text-amber-500" />
                <p className="text-xs text-slate-400">Không thể tải preview — vẫn có thể tiếp tục xử lý.</p>
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center py-14 gap-3">
                <Loader2 className="w-6 h-6 text-[#FB923C] animate-spin" />
                <p className="text-xs text-slate-400">Đang tạo preview từ khung hình đầu…</p>
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
                  ? 'text-sky-300 bg-sky-500/10 border-sky-500/20'
                  : 'text-emerald-300 bg-emerald-500/10 border-emerald-500/20'}`}>
                {previewInfo.motion === 'moving' ? 'Chế độ chuyển động · Ít nhòe hơn' : 'Nền tĩnh · Tự nhiên hơn'}
              </span>
              {previewInfo.quality === 'weak' && (
                <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg font-semibold text-amber-300 bg-amber-500/10 border border-amber-500/20">
                  Preview còn lem
                </span>
              )}
            </div>
          )}

          {/* Crop recommendation — do NOT auto-promote a weak result */}
          {previewInfo?.recommend_crop ? (
            <div className="px-3 py-3 rounded-xl bg-amber-500/10 border border-amber-500/30 space-y-2.5">
              <p className="text-xs text-amber-200 font-semibold leading-snug">
                {previewInfo.reason || 'Nếu preview còn lem, nên chuyển sang Cắt viền.'}
              </p>
              <div className="flex gap-2">
                <button
                  onClick={() => { setMethod('crop'); handleProcess(); }}
                  className="flex-1 flex items-center justify-center gap-2 px-3 py-2.5 rounded-lg text-sm font-bold bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md shadow-[#FBBF24]/20 hover:shadow-[#FBBF24]/40 transition-all cursor-pointer"
                >
                  ✂ Chuyển sang Cắt viền (sạch hơn)
                </button>
                <button
                  onClick={handleProcess}
                  className="px-3 py-2.5 rounded-lg text-xs font-semibold text-slate-400 border border-slate-700/50 hover:text-white hover:border-slate-600 transition-all cursor-pointer whitespace-nowrap"
                >
                  Vẫn xử lý tự nhiên
                </button>
              </div>
            </div>
          ) : (
            <div className="px-3 py-2.5 rounded-xl bg-slate-800/40 border border-slate-700/30 text-[11px] text-slate-500 space-y-1 leading-snug">
              <p>· {previewInfo?.reason || 'Phù hợp hơn khi có chuyển động — lấy nền thật từ khung hình lân cận.'}</p>
              <p>· Nếu thấy viền mờ — quay lại tăng padding hoặc mở rộng vùng chọn.</p>
              <p>· Đây là khung hình đầu — toàn clip dùng cùng cách xử lý này.</p>
            </div>
          )}

          {/* Actions */}
          {!previewInfo?.recommend_crop && (
            <div className="flex gap-3">
              <button
                onClick={() => setStep('preview')}
                className="flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-bold text-slate-400 border border-slate-700/50 hover:text-white hover:border-slate-600 transition-all cursor-pointer"
              >
                ← Chỉnh lại vùng
              </button>
              <button
                onClick={handleProcess}
                className="flex-1 flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md shadow-[#FBBF24]/20 hover:shadow-[#FBBF24]/40 transition-all cursor-pointer"
              >
                <Wand2 className="w-4 h-4" />
                Xử lý toàn bộ video →
              </button>
            </div>
          )}
          {previewInfo?.recommend_crop && (
            <button
              onClick={() => setStep('preview')}
              className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-xs font-semibold text-slate-400 border border-slate-700/50 hover:text-white hover:border-slate-600 transition-all cursor-pointer"
            >
              ← Chỉnh lại vùng
            </button>
          )}
        </div>
      )}

      {/* ── Step: processing ───────────────────────────────────── */}
      {step === 'processing' && (
        <div className="flex flex-col items-center gap-4 py-16">
          <Loader2 className="w-8 h-8 text-[#FBBF24] animate-spin" />
          <div className="text-center">
            <p className="text-sm font-semibold text-slate-200 mb-1">Đang làm sạch logo hiển thị...</p>
            <p className="text-xs text-slate-500">
              {method === 'natural'
                ? 'Đang tái tạo nền bằng patch texture + lấy nền thật từ các khung hình lân cận'
                : method === 'crop'
                ? 'Đang tính toán và cắt viền chứa vùng logo'
                : 'Đang che mờ vùng logo hiển thị'}
              {' '}· 30–180 giây tùy độ dài & chuyển động clip
            </p>
          </div>
        </div>
      )}

      {/* ── Step: done ─────────────────────────────────────────── */}
      {step === 'done' && result && (
        <div className="space-y-4">

          {/* 1. Title — compact, rewarding */}
          <div className="flex items-center gap-3 px-1 pt-4">
            <div className="w-9 h-9 flex-shrink-0 rounded-xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center">
              <CheckCircle className="w-5 h-5 text-emerald-400" />
            </div>
            <div>
              <p className="text-sm font-bold text-white leading-snug">
                {method === 'crop'
                  ? 'Đã cắt viền để loại bỏ logo'
                  : method === 'blur'
                  ? 'Đã che mờ vùng logo'
                  : 'Đã xử lý bằng xóa logo tự nhiên'}
              </p>
              <p className="text-[11px] text-slate-500">{result.file_size_mb} MB · Bản xuất hết hạn sau ~20 phút</p>
            </div>
          </div>

          {/* 2. Preview — method-aware reference visualization */}
          {previewUrl && (
            <div className="relative w-full rounded-xl overflow-hidden bg-slate-900/50 border border-slate-700/50">
              <img src={previewUrl} alt="Khung hình tham chiếu" className="w-full h-auto block opacity-75" />
              {method !== 'crop' ? (
                /* Cleanup / blur: emerald box over the exact region that was processed */
                <>
                  {lastRegionRef.current && (
                    <div
                      className="absolute border-2 border-emerald-400 bg-emerald-400/15 pointer-events-none"
                      style={{
                        left:   `${lastRegionRef.current.xPct * 100}%`,
                        top:    `${lastRegionRef.current.yPct * 100}%`,
                        width:  `${lastRegionRef.current.wPct * 100}%`,
                        height: `${lastRegionRef.current.hPct * 100}%`,
                      }}
                    />
                  )}
                  <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-emerald-400 font-semibold">
                    <CheckCircle className="w-3 h-3" />
                    Vùng đã xử lý
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
                      className="absolute border-2 border-emerald-400 pointer-events-none"
                      style={{
                        left:   `${cropBounds.cx * 100}%`,
                        top:    `${cropBounds.cy * 100}%`,
                        width:  `${cropBounds.cw * 100}%`,
                        height: `${cropBounds.ch * 100}%`,
                      }}
                    />
                    <div className="absolute bottom-2 left-2 flex items-center gap-1 px-2 py-0.5 rounded-md bg-black/70 text-[10px] text-emerald-400 font-semibold">
                      <CheckCircle className="w-3 h-3" />
                      Khung hình sau khi cắt
                    </div>
                  </>
                )
              )}
              <div className="absolute top-2 right-2 px-2 py-0.5 rounded-md bg-black/60 text-[10px] text-slate-400">
                Khung hình gốc · để tham chiếu
              </div>
            </div>
          )}

          {/* 3. Method summary — human-readable, mode-aware labels */}
          <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-slate-800/40 border border-slate-700/40 text-xs text-slate-500">
            <span>Phương thức:</span>
            <span className="text-slate-300 font-semibold">{methodLabel}</span>
            <span className="mx-1 text-slate-700">·</span>
            <span>{method === 'crop' ? 'Viền cắt:' : 'Vùng xử lý:'}</span>
            <span className="text-slate-300 font-semibold">
              {method === 'crop'
                ? (PRESETS.find(p => p.id === preset)?.label ?? preset)
                : regionAdjusted
                ? 'Vùng tùy chỉnh'
                : (PRESETS.find(p => p.id === preset)?.label ?? preset)
              }
            </span>
          </div>

          {/* 4. Primary CTA */}
          <a
            href={`/api/v1/download-local?filepath=${encodeURIComponent(result.cleaned_path)}&filename=${encodeURIComponent(result.filename)}`}
            download={result.filename}
            className="flex items-center justify-center gap-2.5 w-full px-5 py-4 rounded-xl text-base font-bold bg-gradient-to-r from-[#FB923C] to-[#FBBF24] text-[#012622] shadow-md shadow-[#FBBF24]/20 hover:shadow-[#FBBF24]/40 transition-all"
          >
            <Download className="w-5 h-5" />
            Tải video đã xử lý
          </a>

          {/* 5. Secondary actions */}
          <div className="flex gap-2">
            <button
              onClick={() => setStep('preview')}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 text-sm text-slate-500 border border-slate-700/40 rounded-xl hover:text-white hover:border-slate-600 transition-colors cursor-pointer"
            >
              Chỉnh lại vùng xử lý
            </button>
            <button
              onClick={reset}
              className="flex-1 flex items-center justify-center gap-2 py-2.5 text-sm text-slate-500 hover:text-white transition-colors cursor-pointer"
            >
              <RotateCcw className="w-4 h-4" />
              Xử lý video khác
            </button>
          </div>

          {/* 6. Quality + scope note — low emphasis; level-aware */}
          <SynthIDNote className="mt-1" />
          {suitability?.level === 'low' ? (
            <p className="text-[11px] text-orange-600/70 text-center px-4 leading-snug">
              Xử lý từ trường hợp độ tin cậy thấp — nếu thấy vệt, thử lại với phương thức{' '}
              <strong className="text-orange-500/80">Cắt viền</strong>.
            </p>
          ) : (
            <p className="text-[11px] text-slate-600 text-center px-4 leading-snug">
              Kết quả có thể khác nhau tùy kích thước logo và nền phía sau vùng xử lý.
            </p>
          )}
        </div>
      )}

      {/* ── Step: error ────────────────────────────────────────── */}
      {step === 'error' && (
        <div className="space-y-4 pt-4">
          <div className="flex items-start gap-3 px-5 py-4 rounded-2xl bg-red-500/10 border border-red-500/20">
            <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-red-400 mb-1">Chưa thể xử lý video này</p>
              <p className="text-xs text-red-300/70">{error || 'Vùng logo hiện chưa phù hợp để làm sạch tự động.'}</p>
            </div>
          </div>

          {/* Troubleshooting — honest about limitations */}
          <div className="px-4 py-3 rounded-xl bg-slate-800/40 border border-slate-700/30 text-xs text-slate-500 space-y-1">
            <p className="font-semibold text-slate-400 mb-1">Thử các cách sau:</p>
            <p>· Logo ở góc cạnh → thử phương thức <strong className="text-slate-300">Cắt viền</strong> — ổn định hơn trên mọi nền</p>
            <p>· Nền phức tạp → <strong className="text-slate-300">Lấp pixel</strong> có thể để vết không đều; Cắt viền hoặc Che mờ sẽ ổn hơn</p>
            <p>· Clip &gt; 2 phút → cắt ngắn đoạn cần thiết trước khi xử lý</p>
          </div>

          <div className="space-y-2">
            {/* Primary: try different region/method if we have a preview (processing error),
                or upload fresh if the error was during upload */}
            <button
              onClick={() => previewUrl ? setStep('preview') : reset()}
              className="w-full flex items-center justify-center gap-2 px-4 py-3 rounded-xl text-sm font-bold text-slate-200 border border-slate-600/60 hover:text-white hover:border-slate-500 transition-all cursor-pointer"
            >
              <RotateCcw className="w-4 h-4" />
              {previewUrl ? 'Thử vùng hoặc phương thức khác' : 'Thử lại'}
            </button>
            {previewUrl && (
              <button
                onClick={reset}
                className="w-full flex items-center justify-center py-2 text-xs text-slate-500 hover:text-slate-300 transition-colors cursor-pointer"
              >
                Đổi video khác
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
