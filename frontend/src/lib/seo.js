// Head tags of a public page, on every in-app navigation (22/10/2026).
// The prerendered HTML (scripts/prerender-seo.mjs) has the same tags for
// crawlers; this keeps them right after client-side navigation too:
// <title>, description, canonical, hreflang (vi / en / x-default), og:*.
import { seoPageFor, DEFAULT_SITE } from '../content/seoPages';
import { platformPages, pageText } from '../content/platformPages';
import { localizePath } from '../i18n/paths';

export const SITE = (import.meta.env.VITE_SITE_URL || DEFAULT_SITE).replace(/\/+$/, '');

function setMeta(attr, key, content) {
  let el = document.head.querySelector(`meta[${attr}="${key}"]`);
  if (!el) { el = document.createElement('meta'); el.setAttribute(attr, key); document.head.appendChild(el); }
  el.setAttribute('content', content);
}

function setLink(rel, href, hreflang) {
  const sel = hreflang ? `link[rel="${rel}"][hreflang="${hreflang}"]` : `link[rel="${rel}"]:not([hreflang])`;
  let el = document.head.querySelector(sel);
  if (!el) {
    el = document.createElement('link');
    el.setAttribute('rel', rel);
    if (hreflang) el.setAttribute('hreflang', hreflang);
    document.head.appendChild(el);
  }
  el.setAttribute('href', href);
}

/** Title + description of a Vietnamese path in `lang`, or null when it is not a public page. */
export function seoText(viPath, lang) {
  const slug = viPath.replace(/^\//, '');
  const p = platformPages.find((x) => x.slug === slug);
  if (p) {
    const t = pageText(p, lang);
    return { title: t.title, description: t.description };
  }
  const s = seoPageFor(viPath);
  return s ? s[lang] || s.vi : null;
}

export function applySeo(viPath, lang) {
  const text = seoText(viPath, lang);
  if (!text) return;   // signed-in pages keep the home tags (they are not indexed)
  document.title = text.title;
  setMeta('name', 'description', text.description);
  setMeta('property', 'og:title', text.title);
  setMeta('property', 'og:description', text.description);
  setMeta('property', 'og:url', SITE + localizePath(viPath, lang));
  setMeta('property', 'og:locale', lang === 'en' ? 'en_US' : 'vi_VN');
  setLink('canonical', SITE + localizePath(viPath, lang));
  setLink('alternate', SITE + localizePath(viPath, 'vi'), 'vi');
  setLink('alternate', SITE + localizePath(viPath, 'en'), 'en');
  setLink('alternate', SITE + localizePath(viPath, 'vi'), 'x-default');
}
