// Language in the address (22/10/2026) — plain JS, shared by the app
// (i18n/index.jsx) and the build (scripts/prerender-seo.mjs).
// Vietnamese at the root, English under /en; the platform pages have English
// slugs ("/tai-video-tiktok" <-> "/en/tiktok-video-downloader").
import { platformPages } from '../content/platformPages.js';

export const LANGS = ['vi', 'en'];
export const DEFAULT_LANG = 'vi';

const VI_TO_EN = Object.fromEntries(platformPages.filter((p) => p.slugEn).map((p) => [`/${p.slug}`, `/${p.slugEn}`]));
const EN_TO_VI = Object.fromEntries(Object.entries(VI_TO_EN).map(([vi, en]) => [en, vi]));

export const langFromPath = (pathname) => (/^\/en(\/|$)/.test(pathname || '') ? 'en' : 'vi');

const splitUrl = (url) => {
  const m = String(url).match(/^([^?#]*)(.*)$/);
  return [m[1] || '/', m[2] || ''];
};
const trimSlash = (p) => (p.length > 1 ? p.replace(/\/+$/, '') : p);

/** The Vietnamese path the app routes on, for any address ("/en/tiktok-video-downloader" -> "/tai-video-tiktok"). */
export function viewPath(pathname) {
  const p = trimSlash(pathname || '/');
  if (langFromPath(p) !== 'en') return p;
  const rest = trimSlash(p.replace(/^\/en/, '') || '/');
  return EN_TO_VI[rest] || rest;
}

/** The address of a Vietnamese path in `lang` ("/tai-video-tiktok", "en" -> "/en/tiktok-video-downloader"). */
export function localizePath(url, lang) {
  const [path, tail] = splitUrl(url);
  const vp = viewPath(path);
  if (lang !== 'en') return vp + tail;
  return (vp === '/' ? '/en' : `/en${VI_TO_EN[vp] || vp}`) + tail;
}
