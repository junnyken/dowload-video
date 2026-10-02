import { useEffect } from 'react';
import { Sparkles } from 'lucide-react';
import DashboardContent from '../components/DashboardContent';
import { spaLink } from '../lib/spaLink';
import { platformPages, COMMON_NOTE, QUOTA_NOTE } from '../content/platformPages';

const chip =
  'px-3 py-1.5 rounded-full bg-surface border border-line text-sm text-fg-2 hover:text-fg hover:border-line-strong transition-colors';

// Landing page per platform. Copy lives in content/platformPages.js; the
// download tool is the same DashboardContent used on the home page.
export default function PlatformLandingPage({ slug }) {
  const page = platformPages.find((p) => p.slug === slug);

  useEffect(() => {
    if (!page) return undefined;
    const prev = document.title;
    document.title = page.title;
    return () => { document.title = prev; };
  }, [page]);

  if (!page) return null;
  const others = platformPages.filter((p) => p.slug !== slug);

  return (
    <div className="min-h-screen relative overflow-hidden pb-44 md:pb-24">
      <div aria-hidden="true" className="vg-hero-grid absolute inset-x-0 top-0 h-[520px] pointer-events-none" />
      <div aria-hidden="true" className="vg-hero-glow absolute inset-x-0 top-0 h-[520px] pointer-events-none" />

      <div className="relative z-10 w-full max-w-4xl mx-auto px-4 sm:px-6 pt-16 md:pt-24 flex flex-col items-center">
        <section className="w-full flex flex-col items-center text-center mb-8 md:mb-10">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-surface border border-line text-[11px] font-mono text-fg-2 max-w-full mb-5">
            <Sparkles className="w-3 h-3 flex-shrink-0 text-accent-text" />
            <span className="truncate">{`// ${page.platform}`}</span>
          </div>
          <h1 className="font-semibold tracking-tight leading-[1.08] text-fg text-balance text-4xl sm:text-5xl md:text-6xl">
            {page.h1}
          </h1>
          <p className="mt-5 max-w-2xl text-base text-fg-2">{page.intro}</p>
        </section>

        <div className="w-full">
          <DashboardContent />
        </div>

        <p className="mt-6 max-w-2xl text-center text-xs text-fg-muted">
          {COMMON_NOTE} {QUOTA_NOTE}
        </p>

        <section className="w-full max-w-3xl mt-14">
          <h2 className="text-2xl font-semibold text-fg">Cách tải video {page.platform} bằng VidGrab</h2>
          <ol className="mt-5 grid gap-3 sm:grid-cols-3">
            {page.steps.map((s, i) => (
              <li key={s.title} className="rounded-2xl bg-surface border border-line p-4">
                <span className="inline-flex w-7 h-7 items-center justify-center rounded-full bg-accent-soft text-accent-text text-sm font-semibold font-mono">
                  {i + 1}
                </span>
                <h3 className="mt-3 font-semibold text-fg">{s.title}</h3>
                <p className="mt-1 text-sm text-fg-2">{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="w-full max-w-3xl mt-14">
          <h2 className="text-2xl font-semibold text-fg">Câu hỏi thường gặp</h2>
          <div className="mt-5 space-y-3">
            {page.faq.map((f) => (
              <div key={f.q} className="rounded-2xl bg-surface border border-line p-4">
                <h3 className="font-semibold text-fg">{f.q}</h3>
                <p className="mt-1 text-sm text-fg-2">{f.a}</p>
              </div>
            ))}
          </div>
        </section>

        <nav aria-label="Tải video từ nền tảng khác" className="w-full max-w-3xl mt-14">
          <h2 className="text-sm font-semibold text-fg-muted">Nền tảng khác</h2>
          <div className="mt-3 flex flex-wrap gap-2">
            {others.map((o) => (
              <a key={o.slug} href={`/${o.slug}`} onClick={spaLink(`/${o.slug}`)} className={chip}>
                Tải video {o.platform}
              </a>
            ))}
            <a href="/" onClick={spaLink('/')} className={chip}>
              Trang chủ VidGrab
            </a>
          </div>
        </nav>
      </div>
    </div>
  );
}
