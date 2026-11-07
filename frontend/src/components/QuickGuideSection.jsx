import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronDown,
  ChevronUp,
  Share2,
  Link2,
  Check,
  Puzzle,
  Zap,
  Download,
  Music,
  Video,
  FileText,
  Package,
  Layers,
  CheckCircle2,
  Sparkles,
} from 'lucide-react';

// Guide copy (label/title/description/visualSteps/steps/notes per tab) lives in
// locales/<lang>/guide.json under single / bulk / channel.
const TAB_KEYS = ['single', 'bulk', 'channel'];

// Maps parent tool tab → guide tab key
const TOOL_TAB_MAP = { single: 'single', bulk: 'bulk', channel: 'channel' };

function resolveInitialTab(effectiveToolTab, defaultGuideTab) {
  if (defaultGuideTab && TAB_KEYS.includes(defaultGuideTab)) return defaultGuideTab;
  return TOOL_TAB_MAP[effectiveToolTab] ?? 'single';
}

// ── Visual Illustration Mockups for Each Tab & Step ────────────────

function SingleStepVisual({ stepIndex, stepData }) {
  if (stepIndex === 0) {
    return (
      <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2.5 shadow-2xs select-none" aria-hidden="true">
        {/* Mini Platform Row */}
        <div className="flex items-center justify-between gap-1.5">
          <div className="flex items-center gap-1 flex-wrap">
            <span className="px-2 py-0.5 rounded-md bg-surface-2 border border-line text-[10px] font-semibold text-fg">TikTok</span>
            <span className="px-2 py-0.5 rounded-md bg-surface-2 border border-line text-[10px] font-semibold text-[#ff0050]">Douyin</span>
            <span className="px-2 py-0.5 rounded-md bg-surface-2 border border-line text-[10px] font-semibold text-[#FF0000]">YouTube</span>
            <span className="px-2 py-0.5 rounded-md bg-surface-2 border border-line text-[10px] font-semibold text-[#1877F2]">FB</span>
          </div>
          <span className="w-2 h-2 rounded-full bg-success/80 animate-pulse" />
        </div>

        {/* Share -> Copy Link visual flow */}
        <div className="flex items-center gap-1.5 bg-surface-2/80 border border-line rounded-lg p-2">
          <span className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-surface border border-line text-[11px] font-medium text-fg-2">
            <Share2 className="w-3 h-3 text-fg-muted" />
            {stepData?.mockShare || 'Share'}
          </span>
          <span className="text-xs text-fg-muted font-mono">→</span>
          <span className="flex-1 inline-flex items-center justify-center gap-1.5 px-2.5 py-1 rounded-md bg-accent text-accent-fg text-[11px] font-semibold shadow-2xs">
            <Link2 className="w-3 h-3 flex-shrink-0" />
            <span className="truncate">{stepData?.mockCopy || 'Copy link'}</span>
            <Check className="w-3 h-3 flex-shrink-0" />
          </span>
        </div>

        {/* Chrome Extension alternative badge */}
        <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-accent-soft/60 border border-accent/25 text-[10px] font-medium text-accent-text">
          <Puzzle className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="truncate">{stepData?.mockExt || 'Or auto-detect via Chrome Extension'}</span>
        </div>
      </div>
    );
  }

  if (stepIndex === 1) {
    return (
      <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2.5 shadow-2xs select-none" aria-hidden="true">
        {/* Mini Input Box + Extract Button */}
        <div className="flex items-center gap-1.5 p-1.5 rounded-lg bg-surface-2 border border-line-strong">
          <Link2 className="w-3.5 h-3.5 text-fg-muted ml-1 flex-shrink-0" />
          <span className="flex-1 text-[10px] font-mono text-fg-2 truncate">
            {stepData?.mockPlaceholder || 'https://www.tiktok.com/@vidgrab/video/...'}
          </span>
        </div>

        {/* Highlighted Extract CTA */}
        <div className="flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg bg-accent text-accent-fg text-[11px] font-bold tracking-wide shadow-2xs">
          <Zap className="w-3.5 h-3.5 fill-current" />
          <span>{stepData?.mockBtn || 'EXTRACT NOW'}</span>
        </div>

        {/* Mini Options Row */}
        <div className="flex items-center justify-between gap-1.5 text-[10px]">
          <span className="inline-flex items-center gap-1 text-fg-2 font-medium truncate">
            <span className="w-3.5 h-3.5 rounded bg-accent text-accent-fg inline-flex items-center justify-center flex-shrink-0">
              <Check className="w-2.5 h-2.5 stroke-[3]" />
            </span>
            <span className="truncate">{stepData?.mockNoWm || 'Remove logo'}</span>
          </span>
          <span className="px-1.5 py-0.5 rounded bg-surface-2 border border-line text-fg-muted whitespace-nowrap">
            {stepData?.mockSub || 'Subtitles: Auto'}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2 shadow-2xs select-none" aria-hidden="true">
      {/* Mini Result Header */}
      <div className="flex items-center justify-between gap-1.5 pb-1 border-b border-line">
        <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-success truncate">
          <CheckCircle2 className="w-3 h-3 flex-shrink-0" />
          <span className="truncate">{stepData?.mockReady || 'Extracted · No watermark'}</span>
        </span>
        <span className="text-[10px] font-mono text-fg-muted">00:45</span>
      </div>

      {/* Format Rows */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between gap-2 px-2 py-1 rounded-lg bg-accent-soft/70 border border-accent/30">
          <div className="flex items-center gap-1.5 min-w-0">
            <Video className="w-3 h-3 text-accent-text flex-shrink-0" />
            <span className="text-[10px] font-mono font-bold text-fg">MP4 · 1080p HD</span>
          </div>
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-accent text-accent-fg text-[10px] font-bold">
            <Download className="w-2.5 h-2.5" />
            {stepData?.mockSave || 'Download'}
          </span>
        </div>

        <div className="flex items-center justify-between gap-2 px-2 py-1 rounded-lg bg-surface-2 border border-line">
          <div className="flex items-center gap-1.5 min-w-0">
            <Sparkles className="w-3 h-3 text-accent-text flex-shrink-0" />
            <span className="text-[10px] font-mono font-semibold text-fg-2">MP4 · 4K Ultra</span>
            <span className=" text-[9px] text-fg-muted font-mono mx-0.5">·</span>
            <Music className="w-3 h-3 text-[#1DB954] flex-shrink-0" />
            <span className="text-[10px] font-mono font-semibold text-fg-2">MP3 320k</span>
          </div>
          <Download className="w-3 h-3 text-fg-muted flex-shrink-0" />
        </div>
      </div>
    </div>
  );
}

function BulkStepVisual({ stepIndex, stepData }) {
  if (stepIndex === 0) {
    return (
      <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2 shadow-2xs select-none" aria-hidden="true">
        <div className="rounded-lg bg-surface-2 border border-line p-2 font-mono text-[10px] text-fg-2 space-y-1">
          <div className="truncate"><span className="text-accent-text mr-1.5">01</span>https://tiktok.com/@u/video/1...</div>
          <div className="truncate"><span className="text-accent-text mr-1.5">02</span>https://youtube.com/watch?v=...</div>
          <div className="truncate"><span className="text-accent-text mr-1.5">03</span>https://douyin.com/video/73...</div>
        </div>
        <div className="inline-flex items-center justify-center gap-1.5 py-1 px-2.5 rounded-lg bg-surface-2 border border-line text-[10px] font-medium text-fg-2">
          <FileText className="w-3 h-3 text-accent-text" />
          <span>{stepData?.mockFile || 'Import .txt / .csv'}</span>
        </div>
      </div>
    );
  }

  if (stepIndex === 1) {
    return (
      <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2.5 shadow-2xs select-none" aria-hidden="true">
        <div className="flex items-center gap-1.5">
          <span className="px-2 py-1 rounded-md bg-accent-soft border border-accent/40 text-[10px] font-bold text-accent-text">MP4 · 1080p</span>
          <span className="px-2 py-1 rounded-md bg-surface-2 border border-line text-[10px] font-medium text-fg-2">MP3 · 320k</span>
          <span className="px-2 py-1 rounded-md bg-surface-2 border border-line text-[10px] font-medium text-fg-2">No WM</span>
        </div>
        <div className="flex items-center justify-center gap-1.5 py-2 px-3 rounded-lg bg-accent text-accent-fg text-[11px] font-bold tracking-wide shadow-2xs">
          <Layers className="w-3.5 h-3.5" />
          <span className="truncate">{stepData?.mockBtn || 'START BULK DOWNLOAD'}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2 shadow-2xs select-none" aria-hidden="true">
      <div className="flex items-center justify-between text-[10px] font-mono">
        <span className="text-success font-semibold inline-flex items-center gap-1">
          <CheckCircle2 className="w-3 h-3" /> 3 / 3 (100%)
        </span>
        <span className="text-fg-muted">MP4 · 1080p</span>
      </div>
      <div className="w-full h-1.5 rounded-full bg-surface-2 overflow-hidden">
        <div className="h-full w-full bg-accent rounded-full" />
      </div>
      <div className="mt-1 flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg bg-accent text-accent-fg text-[11px] font-bold shadow-2xs">
        <Package className="w-3.5 h-3.5" />
        <span>{stepData?.mockZip || 'Download all (.ZIP)'}</span>
      </div>
    </div>
  );
}

function ChannelStepVisual({ stepIndex, stepData }) {
  if (stepIndex === 0) {
    return (
      <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2.5 shadow-2xs select-none" aria-hidden="true">
        <div className="flex items-center gap-1.5 p-1.5 rounded-lg bg-surface-2 border border-line">
          <Link2 className="w-3.5 h-3.5 text-fg-muted ml-1 flex-shrink-0" />
          <span className="text-[10px] font-mono text-fg-2 truncate">https://www.tiktok.com/@creator_channel</span>
        </div>
        <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-accent-soft/70 border border-accent/30">
          <span className="text-[10px] font-semibold text-accent-text truncate">
            {stepData?.mockToggle || 'Channel Mode'}
          </span>
          <span className="w-7 h-4 rounded-full bg-accent flex items-center justify-end px-0.5 flex-shrink-0">
            <span className="w-3 h-3 rounded-full bg-white shadow-2xs" />
          </span>
        </div>
      </div>
    );
  }

  if (stepIndex === 1) {
    return (
      <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2.5 shadow-2xs select-none" aria-hidden="true">
        <div className="grid grid-cols-4 gap-1 text-center font-mono text-[10px]">
          <span className="py-1 rounded bg-surface-2 border border-line text-fg-2">10</span>
          <span className="py-1 rounded bg-surface-2 border border-line text-fg-2">20</span>
          <span className="py-1 rounded bg-accent text-accent-fg font-bold">50 ✓</span>
          <span className="py-1 rounded bg-surface-2 border border-line text-fg-2">100</span>
        </div>
        <div className="px-2.5 py-1.5 rounded-lg bg-surface-2 border border-line text-[10px] font-medium text-fg-2 flex items-center justify-between">
          <span className="truncate">{stepData?.mockCount || 'Fetch: Latest 50 videos'}</span>
          <Check className="w-3 h-3 text-accent-text flex-shrink-0" />
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-xl bg-surface border border-line p-3 flex flex-col gap-2 shadow-2xs select-none" aria-hidden="true">
      <div className="grid grid-cols-3 gap-1.5">
        {[1, 2, 3].map((n) => (
          <div key={n} className="h-7 rounded bg-surface-2 border border-line flex items-center justify-center text-[10px] font-mono text-success">
            #{n} ✓
          </div>
        ))}
      </div>
      <div className="flex items-center justify-center gap-1.5 py-1.5 px-3 rounded-lg bg-accent text-accent-fg text-[11px] font-bold shadow-2xs">
        <Package className="w-3.5 h-3.5" />
        <span>{stepData?.mockZip || 'Download channel (.ZIP)'}</span>
      </div>
    </div>
  );
}

function StepVisualCard({ tabKey, stepIndex, stepData }) {
  const badge = stepData?.badge || `0${stepIndex + 1}`;
  return (
    <div className="flex flex-col justify-between rounded-2xl bg-surface-2/60 border border-line p-3.5 sm:p-4 hover:border-line-strong transition-colors">
      <div>
        {/* Step Badge + Title */}
        <div className="flex items-center gap-2 mb-2.5">
          <span className="px-2 py-0.5 rounded-md bg-accent-soft border border-accent/30 font-mono text-[11px] font-bold text-accent-text">
            {badge}
          </span>
          <h3 className="text-xs sm:text-sm font-bold text-fg leading-snug">
            {stepData?.title}
          </h3>
        </div>

        {/* Visual UI Mockup */}
        <div className="mb-3">
          {tabKey === 'single' && <SingleStepVisual stepIndex={stepIndex} stepData={stepData} />}
          {tabKey === 'bulk' && <BulkStepVisual stepIndex={stepIndex} stepData={stepData} />}
          {tabKey === 'channel' && <ChannelStepVisual stepIndex={stepIndex} stepData={stepData} />}
        </div>
      </div>

      {/* Step Description */}
      <p className="text-xs text-fg-2 leading-relaxed">
        {stepData?.caption}
      </p>
    </div>
  );
}

// ── Subcomponents ──────────────────────────────────────────────────

function GuideTabPills({ activeTab, onSelect }) {
  const { t } = useTranslation('guide');
  return (
    <div className="flex flex-wrap gap-1.5" role="tablist" aria-label={t('tabsLabel')}>
      {TAB_KEYS.map((key) => {
        const isActive = activeTab === key;
        return (
          <button
            key={key}
            role="tab"
            aria-selected={isActive}
            aria-pressed={isActive}
            onClick={() => onSelect(key)}
            className={[
              'px-3 py-1.5 sm:py-1 rounded-full text-xs font-medium',
              'transition-all duration-150 cursor-pointer whitespace-nowrap',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40',
              'focus-visible:ring-offset-1 focus-visible:ring-offset-canvas',
              isActive
                ? 'bg-accent-soft border border-accent/40 text-accent-text'
                : 'bg-surface border border-line text-fg-2 hover:text-fg hover:border-line-strong',
            ].join(' ')}
          >
            {t(`${key}.label`)}
          </button>
        );
      })}
    </div>
  );
}

function GuideStepItem({ index, text }) {
  const num = String(index + 1).padStart(2, '0');
  return (
    <li className="flex items-start gap-3 py-2">
      <span className="flex-shrink-0 font-mono text-xs font-semibold text-accent-text leading-5" aria-hidden="true">
        {num}
      </span>
      <span className="text-xs sm:text-sm text-fg-2 leading-5 break-words">{text}</span>
    </li>
  );
}

function GuideNotes({ notes }) {
  if (!notes?.length) return null;
  return (
    <div className="mt-3 space-y-1 border-t border-line pt-3">
      {notes.map((note, idx) => (
        <p key={idx} className="text-xs text-fg-muted leading-snug flex gap-1.5 break-words">
          <span className="text-accent-text font-bold flex-shrink-0 mt-px">→</span>
          {note}
        </p>
      ))}
    </div>
  );
}

function GuidePanel({ tabKey, visible }) {
  const { t } = useTranslation('guide');
  const visualSteps = t(`${tabKey}.visualSteps`, { returnObjects: true });
  const hasVisualSteps = Array.isArray(visualSteps) && visualSteps.length > 0;
  const content = {
    label: t(`${tabKey}.label`),
    title: t(`${tabKey}.title`),
    description: t(`${tabKey}.description`),
    steps: t(`${tabKey}.steps`, { returnObjects: true }),
    notes: t(`${tabKey}.notes`, { returnObjects: true }),
  };

  return (
    <div
      role="tabpanel"
      aria-label={content.label}
      className="mt-4 transition-opacity duration-200"
      style={{ opacity: visible ? 1 : 0 }}
    >
      <div className="mb-3.5">
        <p className="text-sm font-semibold text-fg leading-snug">{content.title}</p>
        <p className="text-xs text-fg-muted mt-0.5 leading-snug">{content.description}</p>
      </div>

      {/* 3-Step Visual Illustration Cards */}
      {hasVisualSteps ? (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-3">
          {visualSteps.map((stepData, idx) => (
            <StepVisualCard
              key={idx}
              tabKey={tabKey}
              stepIndex={idx}
              stepData={stepData}
            />
          ))}
        </div>
      ) : (
        <ol className="divide-y divide-line border-y border-line">
          {Array.isArray(content.steps) &&
            content.steps.map((step, idx) => (
              <GuideStepItem key={idx} index={idx} text={step} />
            ))}
        </ol>
      )}

      <GuideNotes notes={content.notes} />
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────

const GUIDE_EXPANDED_KEY = 'vg_quickguide_v2_expanded';

export default function QuickGuideSection({
  className = '',
  currentToolTab,
  activeToolTab,
  defaultGuideTab,
  onGuideTabChange,
}) {
  const { t } = useTranslation('guide');
  const effectiveToolTab = currentToolTab ?? activeToolTab;

  const [activeGuide, setActiveGuide] = useState(() =>
    resolveInitialTab(effectiveToolTab, defaultGuideTab)
  );

  // Expanded by default on desktop (>= 640px) so visitors immediately see the
  // 3-step visual illustrations below the tool input; remembers user toggle.
  const [isExpanded, setIsExpanded] = useState(() => {
    if (typeof window === 'undefined') return true;
    try {
      const saved = localStorage.getItem(GUIDE_EXPANDED_KEY);
      if (saved !== null) return saved === '1';
    } catch {
      // Ignore storage access issues
    }
    return window.matchMedia('(min-width: 640px)').matches;
  });

  useEffect(() => {
    try {
      localStorage.setItem(GUIDE_EXPANDED_KEY, isExpanded ? '1' : '0');
    } catch {
      // Not being able to remember the preference must never break the guide.
    }
  }, [isExpanded]);

  const [visible, setVisible] = useState(true);

  const triggerSwitch = (key) => {
    setVisible(false);
    setTimeout(() => {
      setActiveGuide(key);
      setVisible(true);
      onGuideTabChange?.(key);
    }, 150);
  };

  useEffect(() => {
    const next = TOOL_TAB_MAP[effectiveToolTab];
    if (!next) return;
    if (next !== activeGuide) triggerSwitch(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveToolTab]);

  const handleTabSelect = (key) => {
    const isMobile = typeof window !== 'undefined' && !window.matchMedia('(min-width: 640px)').matches;
    if (key === activeGuide) {
      if (isMobile && !isExpanded) setIsExpanded(true);
      return;
    }
    triggerSwitch(key);
    if (!isExpanded) setIsExpanded(true);
  };

  return (
    <div className={`w-full max-w-3xl mx-auto mb-6 ${className}`}>
      <div className="bg-surface border border-line rounded-card shadow-card p-4 sm:p-5">

        {/* Header */}
        <div className="flex items-center justify-between mb-3 gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <p className="text-xs font-mono font-semibold text-fg whitespace-nowrap">{t('header')}</p>
            <p className="hidden sm:block text-xs text-fg-muted truncate">
              {t('subhead')}
            </p>
          </div>
          <button
            onClick={() => setIsExpanded((v) => !v)}
            className="flex-shrink-0 flex items-center gap-1 px-2 py-1.5 sm:py-1 rounded-lg text-fg-muted hover:text-fg hover:bg-surface-2 transition-colors cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-line-strong"
            aria-label={isExpanded ? t('collapseAria') : t('expandAria')}
            aria-expanded={isExpanded}
          >
            <span className="text-xs">{isExpanded ? t('collapse') : t('expand')}</span>
            {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
        </div>

        {/* Pills always visible */}
        <GuideTabPills activeTab={activeGuide} onSelect={handleTabSelect} />

        {/* Steps — always rendered; height animated via CSS grid trick */}
        <div
          style={{
            display: 'grid',
            gridTemplateRows: isExpanded ? '1fr' : '0fr',
            transition: 'grid-template-rows 220ms ease-out',
          }}
          aria-hidden={!isExpanded}
        >
          <div className="overflow-hidden">
            <GuidePanel tabKey={activeGuide} visible={visible} />
          </div>
        </div>
      </div>
    </div>
  );
}
