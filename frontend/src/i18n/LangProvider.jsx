// Keeps i18next on the language of the address (back / forward, the switch)
// and <html lang> in step.
import { useEffect } from 'react';
import i18n, { langFromPath } from './index';

export default function LangProvider({ children }) {
  useEffect(() => {
    const sync = () => {
      const lang = langFromPath(window.location.pathname);
      if (i18n.language !== lang) i18n.changeLanguage(lang);
      document.documentElement.lang = lang;
    };
    sync();
    window.addEventListener('popstate', sync);
    return () => window.removeEventListener('popstate', sync);
  }, []);
  return children;
}
