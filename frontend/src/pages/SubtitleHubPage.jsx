import { useState, useCallback } from 'react';
import { FileText, Mic, Languages, ArrowRight } from 'lucide-react';
import TranscriptAsrPage from './TranscriptAsrPage';
import TranscriptTranslatePage from './TranscriptTranslatePage';
import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';

export const HUB_TABS = [
  { key: 'extract',   labelKey: 'tabs.extract', icon: FileText },
  { key: 'asr',       labelKey: 'tabs.asr',     icon: Mic },
  { key: 'translate', labelKey: 'tabs.translate', icon: Languages },
];

function tabFromLocation() {
  const tabParam = new URLSearchParams(window.location.search).get('tab');
  return HUB_TABS.some((x) => x.key === tabParam) ? tabParam : null;
}

function ExtractTab({ onNavigate }) {
  const { t } = useTranslation('subtitleHub');
  const lp = useLocalePath();
  return (
    <div className="flex flex-col gap-4" data-testid="tab-extract">
      <p className="text-sm text-fg-muted leading-relaxed">
        {t('extractIntro')}
      </p>
      <ol className="bg-surface border border-line rounded-xl p-4 flex flex-col gap-3 text-sm text-fg-2">
        {['step1', 'step2', 'step3'].map((stepKey, i) => (
          <li key={stepKey} className="flex gap-3">
            <span className="font-mono text-xs w-6 h-6 rounded-full bg-accent-soft text-accent-text flex items-center justify-center shrink-0">{i + 1}</span>
            <span className="leading-relaxed">{t(stepKey)}</span>
          </li>
        ))}
      </ol>
      <p className="text-xs text-fg-muted leading-relaxed">
        {t('noSubsHint')}
      </p>
      <a
        href={lp('/')}
        onClick={(e) => { e.preventDefault(); onNavigate?.('landing', '/'); }}
        className="inline-flex items-center justify-center gap-2 min-h-11 px-4 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:opacity-90 transition self-start focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      >
        {t('home')} <ArrowRight className="w-4 h-4" />
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
  const { t } = useTranslation('subtitleHub');
  const [tab, setTab] = useState(() => tabFromLocation() || initialTab);

  const select = useCallback((key) => {
    setTab(key);
    if (window.location.pathname === '/phu-de') {
      window.history.replaceState({}, '', `/phu-de?tab=${key}`);
    }
  }, []);

  function onKeyDown(e) {
    const i = HUB_TABS.findIndex((x) => x.key === tab);
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
      <h1 className="text-2xl font-bold text-fg mb-1">{t('title')}</h1>
      <p className="text-sm text-fg-muted mb-5">{t('subtitle')}</p>

      <div role="tablist" aria-label={t('title')} onKeyDown={onKeyDown}
        className="flex gap-1 p-1 mb-6 bg-surface-2 border border-line rounded-xl">
        {HUB_TABS.map(({ key, labelKey, icon: Icon }) => {
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
              <span className="truncate">{t(labelKey)}</span>
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
