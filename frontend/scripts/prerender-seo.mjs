// Post-build: sinh HTML tĩnh cho từng trang landing nền tảng + sitemap.xml + robots.txt.
// Chạy sau `vite build` (xem script "build" trong package.json). Không cần gói mới.
//   VITE_SITE_URL — domain gốc (mặc định https://dvid.vibe1.tinhgon.xyz)
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { platformPages, SITE_NAME, COMMON_NOTE, QUOTA_NOTE } from '../src/content/platformPages.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dist = join(root, 'dist');
const SITE = (process.env.VITE_SITE_URL || 'https://dvid.vibe1.tinhgon.xyz').replace(/\/+$/, '');

const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
// JSON nhúng trong <script>: chặn "</script>" và ký tự phân tách dòng.
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

// Trang chủ: canonical lấy từ cùng cấu hình (index.html còn giữ domain cũ).
const home = setTag(template, /<link rel="canonical"[^>]*>/, `<link rel="canonical" href="${SITE}/" />`, 'canonical');
writeFileSync(indexPath, home);

function bodyHtml(p) {
  const links = platformPages
    .filter((o) => o.slug !== p.slug)
    .map((o) => `<a href="/${o.slug}">Tải video ${esc(o.platform)}</a>`)
    .join(' · ');
  return `<main id="seo-static" class="max-w-3xl mx-auto px-4 pt-20 pb-16 text-fg">
<h1 class="text-4xl font-semibold tracking-tight text-center">${esc(p.h1)}</h1>
<p class="mt-4 text-fg-2 text-center">${esc(p.intro)}</p>
<p class="mt-2 text-sm text-fg-muted text-center">${esc(COMMON_NOTE)} ${esc(QUOTA_NOTE)}</p>
<h2 class="mt-10 text-2xl font-semibold">Cách tải video ${esc(p.platform)} bằng ${SITE_NAME}</h2>
<ol class="mt-4 space-y-3 list-decimal pl-5">
${p.steps.map((s) => `<li><strong>${esc(s.title)}.</strong> ${esc(s.text)}</li>`).join('\n')}
</ol>
<h2 class="mt-10 text-2xl font-semibold">Câu hỏi thường gặp</h2>
<div class="mt-4 space-y-4">
${p.faq.map((f) => `<section><h3 class="font-semibold">${esc(f.q)}</h3><p class="text-fg-2">${esc(f.a)}</p></section>`).join('\n')}
</div>
<p class="mt-10 text-sm text-fg-muted">Xem thêm: ${links} · <a href="/">Trang chủ ${SITE_NAME}</a></p>
</main>`;
}

for (const p of platformPages) {
  const url = `${SITE}/${p.slug}`;
  const ld = [
    {
      '@context': 'https://schema.org',
      '@type': 'WebApplication',
      name: `${SITE_NAME} — ${p.h1}`,
      url,
      description: p.description,
      applicationCategory: 'MultimediaApplication',
      operatingSystem: 'Any (web browser)',
      inLanguage: 'vi',
    },
    {
      '@context': 'https://schema.org',
      '@type': 'FAQPage',
      mainEntity: p.faq.map((f) => ({
        '@type': 'Question',
        name: f.q,
        acceptedAnswer: { '@type': 'Answer', text: f.a },
      })),
    },
  ];
  let html = template;
  html = setTag(html, /<title>[\s\S]*?<\/title>/, `<title>${esc(p.title)}</title>`, 'title');
  html = setTag(html, /<meta name="description"[^>]*>/, `<meta name="description" content="${esc(p.description)}" />`, 'description');
  html = setTag(html, /<meta property="og:title"[^>]*>/, `<meta property="og:title" content="${esc(p.title)}" />`, 'og:title');
  html = setTag(
    html,
    /<meta property="og:description"[^>]*>/,
    `<meta property="og:description" content="${esc(p.description)}" />\n    <meta property="og:url" content="${url}" />\n    <meta property="og:site_name" content="${SITE_NAME}" />`,
    'og:description',
  );
  html = setTag(html, /<meta property="og:image"[^>]*>/, `<meta property="og:image" content="${SITE}/icons/icon-512.svg" />`, 'og:image');
  html = setTag(html, /<meta name="twitter:title"[^>]*>/, `<meta name="twitter:title" content="${esc(p.title)}" />`, 'twitter:title');
  html = setTag(html, /<meta name="twitter:description"[^>]*>/, `<meta name="twitter:description" content="${esc(p.description)}" />`, 'twitter:description');
  html = setTag(
    html,
    /<link rel="canonical"[^>]*>/,
    `<link rel="canonical" href="${url}" />\n    ${ld.map((o) => `<script type="application/ld+json">${jsonLd(o)}</script>`).join('\n    ')}`,
    'canonical',
  );
  html = setTag(html, /<div id="root"><\/div>/, `<div id="root">${bodyHtml(p)}</div>`, '#root');
  mkdirSync(join(dist, p.slug), { recursive: true });
  writeFileSync(join(dist, p.slug, 'index.html'), html);
}

const urls = [`${SITE}/`, ...platformPages.map((p) => `${SITE}/${p.slug}`)];
writeFileSync(
  join(dist, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls
    .map((u) => `  <url><loc>${u}</loc></url>`)
    .join('\n')}\n</urlset>\n`,
);
writeFileSync(join(dist, 'robots.txt'), `User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /vid-admin\n\nSitemap: ${SITE}/sitemap.xml\n`);
console.log(`[prerender-seo] ${platformPages.length} pages, site=${SITE}`);
