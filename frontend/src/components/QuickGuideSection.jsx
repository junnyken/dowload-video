import { useState, useEffect } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

// ── Guide content data ─────────────────────────────────────────────
const GUIDE_CONTENT = {
  single: {
    label: 'Tải 1 video',
    title: 'Cách tải video hoặc nhạc nhanh',
    description: 'Dành cho nhu cầu tải từng link riêng lẻ.',
    steps: [
      'Dán link video hoặc bài nhạc vào ô nhập.',
      'Chọn định dạng: MP4 HD, 4K hoặc MP3.',
      'Bật Bỏ watermark (chỉ TikTok / Douyin) hoặc Tải phụ đề nếu cần.',
      'Nhấn "Bóc tách ngay", chờ xử lý và tải file về.',
    ],
    notes: [
      'TikTok / Douyin: tải không watermark. Hỗ trợ YouTube, Instagram, Facebook, Spotify, SoundCloud và nhiều nền tảng khác.',
    ],
  },
  bulk: {
    label: 'Tải hàng loạt',
    title: 'Cách tải nhiều link cùng lúc',
    description: 'Phù hợp khi cần xử lý nhiều URL trong một lần.',
    steps: [
      'Dán nhiều link (mỗi dòng một URL), hoặc từ file .txt/.csv.',
      'Chọn định dạng đầu ra cho toàn bộ danh sách.',
      'Nhấn bắt đầu, hệ thống xử lý lần lượt theo đợt.',
      'Sau khi xong, tải từng file, sao chép link hoặc tải ZIP.',
    ],
    notes: [
      'Phù hợp khi cần tải nhiều video từ nhiều link khác nhau.',
      'Link tải có thời hạn, nên tải ngay sau khi xử lý xong.',
    ],
  },
  channel: {
    label: 'Tải cả kênh',
    title: 'Cách quét và tải video từ kênh',
    description: 'Dùng khi muốn lấy nhiều video từ kênh, profile hoặc playlist.',
    steps: [
      'Chuyển sang tab "Tải Hàng Loạt".',
      'Dán link kênh, profile hoặc playlist vào ô nhập.',
      'Bật Channel Mode để quét video từ kênh thay vì tải 1 link.',
      'Chọn số video muốn lấy: 10, 20, 50, 100, 200, 500 hoặc tự nhập.',
      'Nhấn bắt đầu, theo dõi tiến trình và tải kết quả sau khi xong.',
    ],
    notes: [
      'Hỗ trợ YouTube, TikTok, Instagram profile, Facebook videos.',
      'Lần đầu nên chọn 10–50 video để kiểm tra nhanh hơn.',
      'Hệ thống xử lý theo đợt để tránh giới hạn nền tảng.',
    ],
  },
};

const TAB_KEYS = ['single', 'bulk', 'channel'];

// Maps parent tool tab → guide tab key
const TOOL_TAB_MAP = { single: 'single', bulk: 'bulk', channel: 'channel' };

function resolveInitialTab(effectiveToolTab, defaultGuideTab) {
  if (defaultGuideTab && TAB_KEYS.includes(defaultGuideTab)) return defaultGuideTab;
  return TOOL_TAB_MAP[effectiveToolTab] ?? 'single';
}

// ── Subcomponents ──────────────────────────────────────────────────

function GuideTabPills({ activeTab, onSelect }) {
  return (
    <div className="flex flex-wrap gap-1.5" role="tablist" aria-label="Loại hướng dẫn">
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
              'px-2.5 py-2 sm:px-3 sm:py-1 rounded-full text-[11px] font-medium',
              'transition-all duration-150 cursor-pointer whitespace-nowrap',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#FBBF24]/40',
              'focus-visible:ring-offset-1 focus-visible:ring-offset-[#011a17]',
              isActive
                ? 'bg-[#FBBF24]/15 border border-[#FBBF24]/40 text-[#FBBF24] font-semibold'
                : 'bg-transparent border border-slate-700/30 text-slate-500 hover:text-slate-400 hover:border-slate-600/50',
            ].join(' ')}
          >
            {GUIDE_CONTENT[key].label}
          </button>
        );
      })}
    </div>
  );
}

function GuideStepItem({ index, text }) {
  const num = String(index + 1).padStart(2, '0');
  return (
    <li className="flex items-start gap-2">
      <span
        className="flex-shrink-0 w-5 h-5 rounded-md bg-[#FBBF24]/10 border border-[#FBBF24]/20 flex items-center justify-center text-[9px] font-bold text-[#FBBF24] font-mono mt-px"
        aria-hidden="true"
      >
        {num}
      </span>
      <span className="text-[11px] sm:text-[11.5px] text-slate-300 leading-[1.45] break-words">{text}</span>
    </li>
  );
}

function GuideNotes({ notes }) {
  if (!notes?.length) return null;
  return (
    <div className="mt-1.5 pl-5 sm:pl-7 space-y-0.5 border-t border-slate-700/20 pt-1.5">
      {notes.map((note, idx) => (
        <p key={idx} className="text-[10px] text-slate-400 leading-[1.4] flex gap-1.5 break-words">
          <span className="text-slate-600 flex-shrink-0 mt-px">→</span>
          {note}
        </p>
      ))}
    </div>
  );
}

function GuidePanel({ tabKey, visible }) {
  const content = GUIDE_CONTENT[tabKey];
  return (
    <div
      role="tabpanel"
      aria-label={content.label}
      className="mt-2 sm:mt-2.5 transition-opacity duration-200"
      style={{ opacity: visible ? 1 : 0 }}
    >
      <div className="mb-1.5">
        <p className="text-[11px] font-semibold text-slate-300 leading-snug">{content.title}</p>
        <p className="text-[10px] text-slate-400 mt-0.5 leading-snug">{content.description}</p>
      </div>
      <ol className="space-y-1 sm:space-y-1.5">
        {content.steps.map((step, idx) => (
          <GuideStepItem key={idx} index={idx} text={step} />
        ))}
      </ol>
      <GuideNotes notes={content.notes} />
    </div>
  );
}

// ── Main component ─────────────────────────────────────────────────

/**
 * QuickGuideSection — onboarding guide for VidGrab users.
 *
 * Props:
 *   className        — extra wrapper classes
 *   currentToolTab   — 'single' | 'bulk' | 'channel' | 'history' — drives guide tab sync
 *   activeToolTab    — alias for currentToolTab (backward compat)
 *   defaultGuideTab  — override initial guide tab
 *   onGuideTabChange — callback when guide tab changes
 *
 * Sync rules:
 *   - parent prop 'single' → guide 'single'
 *   - parent prop 'bulk'   → guide 'bulk'
 *   - parent prop 'channel'→ guide 'channel'  (BulkContent passes this when channelMode=true)
 *   - parent prop 'history'→ no change (leave guide at last useful state)
 *   - user manually clicking any guide pill is always allowed
 *   - when parent prop changes, auto-sync overrides any manual selection
 *
 * Collapse rules:
 *   - first visit, mobile (< 640px): collapsed; pills always visible
 *   - first visit, desktop (≥ 640px): expanded
 *   - after that: whatever the reader last chose, remembered across visits
 *   - when parent prop changes on mobile, switch pill but keep collapse state
 */
const GUIDE_EXPANDED_KEY = 'vg_quickguide_expanded';

export default function QuickGuideSection({
  className = '',
  currentToolTab,
  activeToolTab,
  defaultGuideTab,
  onGuideTabChange,
}) {
  const effectiveToolTab = currentToolTab ?? activeToolTab;

  const [activeGuide, setActiveGuide] = useState(() =>
    resolveInitialTab(effectiveToolTab, defaultGuideTab)
  );

  // Desktop starts expanded; mobile starts collapsed — but only until the
  // reader says otherwise. Collapsing used to last exactly one visit, so a
  // returning user scrolled past the same beginner's guide every time to
  // reach the input it sits on top of. Their choice is remembered now; the
  // width rule is just the first-visit default.
  const [isExpanded, setIsExpanded] = useState(() => {
    if (typeof window === 'undefined') return false;
    try {
      const saved = localStorage.getItem(GUIDE_EXPANDED_KEY);
      if (saved !== null) return saved === '1';
    } catch {
      // Private mode / storage blocked — fall through to the width default.
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

  // Sync guide tab when parent tool tab prop changes.
  // 'history' → leave guide state unchanged.
  // Any other recognized tab → always sync (overrides manual selection).
  useEffect(() => {
    const next = TOOL_TAB_MAP[effectiveToolTab];
    if (!next) return; // history or unknown → preserve current guide tab
    if (next !== activeGuide) triggerSwitch(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveToolTab]);

  const handleTabSelect = (key) => {
    const isMobile = typeof window !== 'undefined' && !window.matchMedia('(min-width: 640px)').matches;
    if (key === activeGuide) {
      // Tapping same pill on mobile → expand steps
      if (isMobile && !isExpanded) setIsExpanded(true);
      return;
    }
    triggerSwitch(key);
    // Tapping any pill always expands steps
    if (!isExpanded) setIsExpanded(true);
  };

  return (
    <div className={`w-full max-w-3xl mx-auto mb-3 sm:mb-4 ${className}`}>
      <div className="bg-[#011a17]/80 border border-slate-700/30 rounded-xl p-2.5 sm:p-3 md:p-3.5">

        {/* Header */}
        <div className="flex items-center justify-between mb-2 gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <p className="text-[11px] font-semibold text-slate-400 whitespace-nowrap">Hướng dẫn nhanh</p>
            <span className="hidden sm:block text-[10px] text-slate-600">·</span>
            <p className="hidden sm:block text-[10px] text-slate-500 truncate">
              Người mới làm theo từng bước dưới đây.
            </p>
          </div>
          <button
            onClick={() => setIsExpanded((v) => !v)}
            className="flex-shrink-0 flex items-center gap-1 px-2 py-1.5 sm:py-1 rounded-lg text-slate-500 hover:text-slate-400 hover:bg-slate-700/30 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-600"
            aria-label={isExpanded ? 'Thu gọn hướng dẫn' : 'Mở rộng hướng dẫn'}
            aria-expanded={isExpanded}
          >
            <span className="text-[10px]">{isExpanded ? 'Thu gọn' : 'Xem bước'}</span>
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
