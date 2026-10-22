// VI / EN switch in the top bar: the same page in the other language.
import { Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { switchLang, useLang } from '../i18n';

export default function LangSwitch() {
  const lang = useLang();
  const { t } = useTranslation('app');
  const other = lang === 'en' ? 'vi' : 'en';
  return (
    <button
      type="button"
      onClick={() => switchLang(other)}
      title={t('langSwitchTo')}
      aria-label={`${t('langLabel')}: ${t('langSwitchTo')}`}
      className="inline-flex items-center gap-1 h-8 px-2 rounded-lg border border-line text-fg-2 hover:bg-surface-2 hover:text-fg text-xs font-semibold transition-colors cursor-pointer"
    >
      <Globe className="w-3.5 h-3.5" aria-hidden="true" />
      {other.toUpperCase()}
    </button>
  );
}
