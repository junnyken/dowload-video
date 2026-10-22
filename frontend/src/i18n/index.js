// Vietnamese / English with i18next + react-i18next (22/10/2026).
//
// Strings: src/locales/<lang>/<namespace>.json, one namespace per screen
// (app, platformLanding, …). In a component: const { t } = useTranslation('ns');
// t('key', { var }) with "{{var}}" in the JSON; plurals: key_one / key_other.
// Missing English falls back to Vietnamese.
// wording: BA review — the English JSON was drafted with AI; BA approves it.
//
// The language comes from the ADDRESS, never from the browser, so every URL
// has one language for search engines: Vietnamese at the root ("/pricing"),
// English under "/en" ("/en/pricing"; platform pages have English slugs, see
// i18n/paths.js). history.pushState / replaceState are wrapped so every
// existing in-app navigation keeps the current language.
import i18n from 'i18next';
import { initReactI18next, useTranslation } from 'react-i18next';
import { useMemo } from 'react';
import { DEFAULT_LANG, LANGS, langFromPath, localizePath, viewPath } from './paths';

export { DEFAULT_LANG, LANGS, langFromPath, localizePath, viewPath };

const PREF_KEY = 'vg_lang';
// Never localized: the API, the admin, files the server answers.
const RAW_PREFIX = /^\/(en|api|vid-admin|icons|assets)(\/|$)|^\/[^/?#]+\.[a-z0-9]{2,5}([?#]|$)/i;

// Bundled (two languages, small): no loading flash, works offline.
const files = import.meta.glob('../locales/*/*.json', { eager: true, import: 'default' });
const resources = {};
for (const [path, data] of Object.entries(files)) {
  const [, lang, ns] = path.match(/locales\/([^/]+)\/([^/]+)\.json$/);
  (resources[lang] ||= {})[ns] = data;
}

i18n.use(initReactI18next).init({
  resources,
  lng: typeof window !== 'undefined' ? langFromPath(window.location.pathname) : DEFAULT_LANG,
  fallbackLng: DEFAULT_LANG,
  supportedLngs: LANGS,
  ns: Object.keys(resources[DEFAULT_LANG] || {}),
  defaultNS: 'app',
  interpolation: { escapeValue: false },   // React escapes
  returnNull: false,
  react: { useSuspense: false },
});

export default i18n;
export const getLang = () => (i18n.language === 'en' ? 'en' : 'vi');

const rawPush = typeof window !== 'undefined' ? window.history.pushState.bind(window.history) : null;
const rawReplace = typeof window !== 'undefined' ? window.history.replaceState.bind(window.history) : null;

/** Same-site path that should carry the current language. */
function shouldLocalize(url) {
  return typeof url === 'string' && url.startsWith('/') && !url.startsWith('//') && !RAW_PREFIX.test(url);
}

export function installLocaleHistory() {
  if (typeof window === 'undefined' || window.__vgLocaleHistory) return;
  window.__vgLocaleHistory = true;
  const wrap = (raw) => (state, title, url) => raw(state, title, getLang() === 'en' && shouldLocalize(url) ? localizePath(url, 'en') : url);
  window.history.pushState = wrap(rawPush);
  window.history.replaceState = wrap(rawReplace);
}

/** Switch language: the same page in the other language, remembered for later visits. */
export function switchLang(lang) {
  if (!LANGS.includes(lang) || typeof window === 'undefined') return;
  try { localStorage.setItem(PREF_KEY, lang); } catch { /* storage off */ }
  const { pathname, search, hash } = window.location;
  rawPush({}, '', localizePath(pathname, lang) + search + hash);
  window.dispatchEvent(new PopStateEvent('popstate'));
}

/** The current language ('vi' | 'en'), re-rendering on change. */
export function useLang() {
  const { i18n: inst } = useTranslation();
  return inst.language === 'en' ? 'en' : 'vi';
}

/** Address of a Vietnamese path in the current language, for <a href>. */
export function useLocalePath() {
  const lang = useLang();
  return useMemo(() => (path) => localizePath(path, lang), [lang]);
}
