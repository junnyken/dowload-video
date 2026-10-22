// Post-build: static HTML for every public page in Vietnamese and English,
// plus sitemap.xml (with hreflang alternates) and robots.txt.
// Runs after `vite build` (package.json "build"). No extra packages.
//   VITE_SITE_URL — production address (default content/seoPages.js DEFAULT_SITE)
//
// Each page × language gets dist/<path>/index.html with: <html lang>, its own
// <title> / description / og:* / twitter:*, canonical, hreflang vi / en /
// x-default, JSON-LD (inLanguage), and a static <main id="seo-static"> in
// #root that React replaces on load. nginx serves dist/<path>/index.html
// for the path (try_files $uri/index.html), the SPA index.html for the rest.
// 22/10/2026: bilingual (was: Vietnamese home + 4 platform pages only).
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { platformPages, pageText, SITE_NAME, COMMON_NOTE, QUOTA_NOTE, COMMON_NOTE_EN, QUOTA_NOTE_EN } from '../src/content/platformPages.js';
import { seoPages, DEFAULT_SITE } from '../src/content/seoPages.js';
import { localizePath } from '../src/i18n/paths.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const SITE = (process.env.VITE_SITE_URL || DEFAULT_SITE).replace(/\/+$/, '');
const LANGS = ['vi', 'en'];
const TODAY = new Date().toISOString().slice(0, 10);

// Words of the static body. wording: BA review (English drafted with AI).
const L = {
  vi: { howTo: (p) => `Cách tải video ${p} bằng ${SITE_NAME}`, faq: 'Câu hỏi thường gặp', more: 'Xem thêm', dl: (p) => `Tải video ${p}`, home: `Trang chủ ${SITE_NAME}`, note: `${COMMON_NOTE} ${QUOTA_NOTE}`, locale: 'vi_VN' },
  en: { howTo: (p) => `How to download ${p} videos with ${SITE_NAME}`, faq: 'Frequently asked questions', more: 'See also', dl: (p) => `${p} video downloader`, home: `${SITE_NAME} home`, note: `${COMMON_NOTE_EN} ${QUOTA_NOTE_EN}`, locale: 'en_US' },
};

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// JSON inside <script>: no "</script>", no line separators.
const jsonLd = (o) =>
  JSON.stringify(o)
    .replace(/</g, '\\u003c')
    .replace(new RegExp(String.fromCharCode(0x2028), 'g'), '\\u2028')
    .replace(new RegExp(String.fromCharCode(0x2029), 'g'), '\\u2029');

const indexPath = join(dist, 'index.html');
if (!existsSync(indexPath)) throw new Error('dist/index.html not found — run vite build first');
const template = readFileSync(indexPath, 'utf8');

const setTag = (html, re, replacement, label) => {
  if (!re.test(html)) throw new Error(`template missing ${label}`);
  return html.replace(re, () => replacement);
};

// Every public page: path (Vietnamese) + its text per language.
const pages = [
  ...seoPages.map((s) => ({ path: s.path, priority: s.priority, kind: s.path === '/' ? 'home' : 'page', text: (lang) => s[lang] || s.vi })),
  ...platformPages.map((p) => ({ path: `/${p.slug}`, priority: '0.9', kind: 'platform', platform: p, text: (lang) => pageText(p, lang) })),
];

const url = (path, lang) => SITE + localizePath(path, lang);

function links(lang, except) {
  const items = [
    ...platformPages.filter((p) => `/${p.slug}` !== except).map((p) => `<a href="${localizePath(`/${p.slug}`, lang)}">${esc(L[lang].dl(p.platform))}</a>`),
    ...seoPages.filter((s) => s.path !== except && s.path !== '/').map((s) => `<a href="${localizePath(s.path, lang)}">${esc((s[lang] || s.vi).h1)}</a>`),
  ];
  if (except !== '/') items.push(`<a href="${localizePath('/', lang)}">${esc(L[lang].home)}</a>`);
  return items.join(' · ');
}

function bodyHtml(page, lang) {
  const t = page.text(lang);
  const w = L[lang];
  if (page.kind !== 'platform') {
    return `<main id="seo-static" class="max-w-3xl mx-auto px-4 pt-20 pb-16 text-fg">
<h1 class="text-4xl font-semibold tracking-tight text-center">${esc(t.h1)}</h1>
<p class="mt-4 text-fg-2 text-center">${esc(t.description)}</p>
<p class="mt-10 text-sm text-fg-muted">${esc(w.more)}: ${links(lang, page.path)}</p>
</main>`;
  }
  return `<main id="seo-static" class="max-w-3xl mx-auto px-4 pt-20 pb-16 text-fg">
<h1 class="text-4xl font-semibold tracking-tight text-center">${esc(t.h1)}</h1>
<p class="mt-4 text-fg-2 text-center">${esc(t.intro)}</p>
<p class="mt-2 text-sm text-fg-muted text-center">${esc(w.note)}</p>
<h2 class="mt-10 text-2xl font-semibold">${esc(w.howTo(t.platform))}</h2>
<ol class="mt-4 space-y-3 list-decimal pl-5">
${t.steps.map((s) => `<li><strong>${esc(s.title)}.</strong> ${esc(s.text)}</li>`).join('\n')}
</ol>
<h2 class="mt-10 text-2xl font-semibold">${esc(w.faq)}</h2>
<div class="mt-4 space-y-4">
${t.faq.map((f) => `<section><h3 class="font-semibold">${esc(f.q)}</h3><p class="text-fg-2">${esc(f.a)}</p></section>`).join('\n')}
</div>
<p class="mt-10 text-sm text-fg-muted">${esc(w.more)}: ${links(lang, page.path)}</p>
</main>`;
}

function structuredData(page, lang) {
  const t = page.text(lang);
  const u = url(page.path, lang);
  if (page.kind === 'home') {
    return [{ '@context': 'https://schema.org', '@type': 'WebSite', name: SITE_NAME, url: u, description: t.description, inLanguage: lang }];
  }
  if (page.kind === 'page') {
    return [{ '@context': 'https://schema.org', '@type': 'WebPage', name: t.title, url: u, description: t.description, inLanguage: lang,
              isPartOf: { '@type': 'WebSite', name: SITE_NAME, url: url('/', lang) } }];
  }
  return [
    { '@context': 'https://schema.org', '@type': 'WebApplication', name: `${SITE_NAME} — ${t.h1}`, url: u, description: t.description,
      applicationCategory: 'MultimediaApplication', operatingSystem: 'Any (web browser)', inLanguage: lang },
    { '@context': 'https://schema.org', '@type': 'FAQPage', inLanguage: lang,
      mainEntity: t.faq.map((f) => ({ '@type': 'Question', name: f.q, acceptedAnswer: { '@type': 'Answer', text: f.a } })) },
  ];
}

function render(page, lang) {
  const t = page.text(lang);
  const other = lang === 'en' ? 'vi' : 'en';
  const u = url(page.path, lang);
  let html = template;
  html = setTag(html, /<html lang="[^"]*"/, `<html lang="${lang}"`, 'html lang');
  html = setTag(html, /<title>[\s\S]*?<\/title>/, `<title>${esc(t.title)}</title>`, 'title');
  html = setTag(html, /<meta name="description"[^>]*>/, `<meta name="description" content="${esc(t.description)}" />`, 'description');
  html = setTag(html, /<meta property="og:title"[^>]*>/, `<meta property="og:title" content="${esc(t.title)}" />`, 'og:title');
  html = setTag(html, /<meta property="og:description"[^>]*>/,
    `<meta property="og:description" content="${esc(t.description)}" />\n    <meta property="og:url" content="${u}" />\n    <meta property="og:site_name" content="${SITE_NAME}" />`, 'og:description');
  html = setTag(html, /<meta property="og:image"[^>]*>/, `<meta property="og:image" content="${SITE}/icons/icon-512.svg" />`, 'og:image');
  html = setTag(html, /<meta property="og:locale"[^>]*>/,
    `<meta property="og:locale" content="${L[lang].locale}" />\n    <meta property="og:locale:alternate" content="${L[other].locale}" />`, 'og:locale');
  html = setTag(html, /<meta name="twitter:title"[^>]*>/, `<meta name="twitter:title" content="${esc(t.title)}" />`, 'twitter:title');
  html = setTag(html, /<meta name="twitter:description"[^>]*>/, `<meta name="twitter:description" content="${esc(t.description)}" />`, 'twitter:description');
  html = setTag(html, /<link rel="canonical"[^>]*>/, [
    `<link rel="canonical" href="${u}" />`,
    `<link rel="alternate" hreflang="vi" href="${url(page.path, 'vi')}" />`,
    `<link rel="alternate" hreflang="en" href="${url(page.path, 'en')}" />`,
    `<link rel="alternate" hreflang="x-default" href="${url(page.path, 'vi')}" />`,
    ...structuredData(page, lang).map((o) => `<script type="application/ld+json">${jsonLd(o)}</script>`),
  ].join('\n    '), 'canonical');
  html = setTag(html, /<div id="root"><\/div>/, `<div id="root">${bodyHtml(page, lang)}</div>`, '#root');
  return html;
}

let written = 0;
for (const page of pages) {
  for (const lang of LANGS) {
    const html = render(page, lang);
    const rel = localizePath(page.path, lang).replace(/^\//, '');
    const out = rel ? join(dist, rel, 'index.html') : indexPath;
    mkdirSync(dirname(out), { recursive: true });
    writeFileSync(out, html);
    written += 1;
  }
}

const sitemap = pages.flatMap((page) => LANGS.map((lang) => `  <url>
    <loc>${url(page.path, lang)}</loc>
    <lastmod>${TODAY}</lastmod>
    <priority>${page.priority}</priority>
    <xhtml:link rel="alternate" hreflang="vi" href="${url(page.path, 'vi')}" />
    <xhtml:link rel="alternate" hreflang="en" href="${url(page.path, 'en')}" />
    <xhtml:link rel="alternate" hreflang="x-default" href="${url(page.path, 'vi')}" />
  </url>`));
writeFileSync(join(dist, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">\n${sitemap.join('\n')}\n</urlset>\n`);
writeFileSync(join(dist, 'robots.txt'), `User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /vid-admin\n\nSitemap: ${SITE}/sitemap.xml\n`);
console.log(`[prerender-seo] ${written} pages (${pages.length} × ${LANGS.length} languages), site=${SITE}`);
