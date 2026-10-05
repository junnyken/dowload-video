import { useState, useCallback } from 'react';
import { FileText, Mic, Languages, ArrowRight } from 'lucide-react';
import TranscriptAsrPage from './TranscriptAsrPage';
import TranscriptTranslatePage from './TranscriptTranslatePage';

export const HUB_TABS = [
  { key: 'extract',   label: 'Trích phụ đề', icon: FileText },
  { key: 'asr',       label: 'Phiên âm AI',  icon: Mic },
  { key: 'translate', label: 'Dịch',         icon: Languages },
];

function tabFromLocation() {
  const t = new URLSearchParams(window.location.search).get('tab');
  return HUB_TABS.some((x) => x.key === t) ? t : null;
}

function ExtractTab({ onNavigate }) {
  return (
    <div className="flex flex-col gap-4" data-testid="tab-extract">
      <p className="text-sm text-fg-muted leading-relaxed">
        Nhiều video đã có sẵn phụ đề do người đăng tải. VidGrab lấy phụ đề đó về cho bạn,
        không cần nhận diện giọng nói nên nhanh và miễn phí hạn mức phiên âm.
      </p>
      <ol className="bg-surface border border-line rounded-xl p-4 flex flex-col gap-3 text-sm text-fg-2">
        {[
          'Dán link video vào ô tải ở trang chủ rồi bấm lấy thông tin video.',
          'Ở thẻ kết quả, mở thẻ "Phụ đề" (chỉ hiện khi video có phụ đề sẵn).',
          'Chọn ngôn ngữ và định dạng (SRT/VTT), rồi bấm "Tải phụ đề".',
        ].map((t, i) => (
          <li key={t} className="flex gap-3">
            <span className="font-mono text-xs w-6 h-6 rounded-full bg-accent-soft text-accent-text flex items-center justify-center shrink-0">{i + 1}</span>
            <span className="leading-relaxed">{t}</span>
          </li>
        ))}
      </ol>
      <p className="text-xs text-fg-muted leading-relaxed">
        Video không có thẻ "Phụ đề"? Chuyển sang tab "Phiên âm AI" để tạo phụ đề từ giọng nói.
      </p>
      <a
        href="/"
        onClick={(e) => { e.preventDefault(); onNavigate?.('landing', '/'); }}
        className="inline-flex items-center justify-center gap-2 min-h-11 px-4 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 transition self-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        Về trang chủ để dán link video <ArrowRight className="w-4 h-4" />
      </a>
    </div>
  );
}

/**
 * "Phụ đề và Phiên âm" hub. Old routes keep working: /transcript-asr opens the
 * "asr" tab and /transcript-translate the "translate" tab (URL left as is),
 * /phu-de reads ?tab= and defaults to "extract".
 */
export default function SubtitleHubPage({ initialTab = 'extract', onNavigate }) {
  const [tab, setTab] = useState(() => tabFromLocation() || initialTab);

  const select = useCallback((key) => {
    setTab(key);
    if (window.location.pathname === '/phu-de') {
      window.history.replaceState({}, '', `/phu-de?tab=${key}`);
    }
  }, []);

  function onKeyDown(e) {
    const i = HUB_TABS.findIndex((t) => t.key === tab);
    let next = null;
    if (e.key === 'ArrowRight') next = (i + 1) % HUB_TABS.length;
    if (e.key === 'ArrowLeft') next = (i - 1 + HUB_TABS.length) % HUB_TABS.length;
    if (next !== null) {
      e.preventDefault();
      select(HUB_TABS[next].key);
      document.getElementById(`hub-tab-${HUB_TABS[next].key}`)?.focus();
    }
  }

  return (
    <div className="max-w-3xl mx-auto px-4 md:px-8 py-8 md:py-12">
      <h1 className="text-2xl font-bold text-fg mb-1">Phụ đề và Phiên âm</h1>
      <p className="text-sm text-fg-muted mb-5">Lấy phụ đề có sẵn, tạo phụ đề từ giọng nói, hoặc dịch sang ngôn ngữ khác.</p>

      <div role="tablist" aria-label="Phụ đề và Phiên âm" onKeyDown={onKeyDown}
        className="flex gap-1 p-1 mb-6 bg-surface-2 border border-line rounded-xl">
        {HUB_TABS.map(({ key, label, icon: Icon }) => {
          const active = tab === key;
          return (
            <button
              key={key}
              id={`hub-tab-${key}`}
              role="tab"
              aria-selected={active}
              aria-controls={`hub-panel-${key}`}
              tabIndex={active ? 0 : -1}
              onClick={() => select(key)}
              className={`flex-1 min-h-11 px-2 rounded-lg text-sm font-semibold flex items-center justify-center gap-1.5 cursor-pointer transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                active ? 'bg-surface text-accent-text border border-line shadow-card' : 'text-fg-muted hover:text-fg border border-transparent'
              }`}
            >
              <Icon className="w-4 h-4 shrink-0" />
              <span className="truncate">{label}</span>
            </button>
          );
        })}
      </div>

      <div role="tabpanel" id={`hub-panel-${tab}`} aria-labelledby={`hub-tab-${tab}`}>
        {tab === 'extract' && <ExtractTab onNavigate={onNavigate} />}
        {tab === 'asr' && <TranscriptAsrPage onOpenTranslate={() => select('translate')} />}
        {tab === 'translate' && <TranscriptTranslatePage embedded onOpenAsr={() => select('asr')} />}
      </div>
    </div>
  );
}
