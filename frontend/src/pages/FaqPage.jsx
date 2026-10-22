import { Trans, useTranslation } from 'react-i18next';
import { SUPPORT_EMAIL } from '../lib/contact';
import { useLocalePath } from '../i18n';

// wording: BA review (the 10 Q&As live in locales/*/faq.json, items[])
const CODE = 'rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-fg break-all';

export default function FaqPage({ onNavigate }) {
  const { t } = useTranslation('faq');
  const lp = useLocalePath();
  const faqs = t('items', { returnObjects: true });
  const go = (e, view, path) => { e.preventDefault(); onNavigate?.(view, path); };
  return (
    <div className="text-fg">
      <div className="mb-8 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">{t('title')}</h1>
        <p className="text-fg-muted text-sm">{t('subtitle')}</p>
      </div>

      <div className="space-y-3 mb-12">
        {(Array.isArray(faqs) ? faqs : []).map(({ q, a }, i) => (
          <details key={i} className="group rounded-xl border border-line bg-surface p-4">
            <summary className="cursor-pointer list-none flex gap-3 font-semibold text-fg">
              <span className="text-fg-muted">{i + 1}.</span><span>{q}</span>
            </summary>
            <p className="mt-3 pl-7 text-sm text-fg-2 leading-relaxed">{a}</p>
          </details>
        ))}
      </div>

      <section id="cai-ung-dung-windows" className="space-y-4 text-fg-2 leading-relaxed">
        <h2 className="text-2xl font-bold text-fg">{t('winTitle')}</h2>

        <h3 className="text-lg font-semibold text-fg">{t('whyWarnTitle')}</h3>
        <p>{t('whyWarnBody')}</p>

        <h3 className="text-lg font-semibold text-fg">{t('runTitle')}</h3>
        <ol className="list-decimal list-inside space-y-1 pl-2">
          <li><Trans t={t} i18nKey="runStep1" components={{ b: <span className="text-fg font-medium" /> }} /></li>
          <li><Trans t={t} i18nKey="runStep2" components={{ b: <span className="text-fg font-medium" /> }} /></li>
        </ol>

        <h3 className="text-lg font-semibold text-fg">{t('shaTitle')}</h3>
        <p>{t('shaIntro')}</p>
        <pre className="overflow-x-auto rounded-lg bg-surface-2 p-3 text-xs text-fg"><code>Get-FileHash .\VidGrab_x.y.z_x64-setup.exe -Algorithm SHA256</code></pre>
        <p>
          <Trans t={t} i18nKey="shaCompare" components={{
            code: <span className={CODE} />,
            downloadLink: <a href={lp('/download')} onClick={(e) => go(e, 'download', '/download')} className="text-accent-text underline" />,
          }} />
        </p>

        <h3 className="text-lg font-semibold text-fg">{t('dataTitle')}</h3>
        <ul className="list-disc list-inside space-y-2 pl-2">
          <li><Trans t={t} i18nKey="dataKeep" components={{ b: <span className="text-fg font-medium" /> }} /></li>
          <li><Trans t={t} i18nKey="dataSend" components={{ b: <span className="text-fg font-medium" /> }} /></li>
        </ul>
        <p className="text-sm text-fg-muted">
          <Trans t={t} i18nKey="moreInfo" values={{ email: SUPPORT_EMAIL }} components={{
            privacyLink: <a href={lp('/privacy')} onClick={(e) => go(e, 'privacy', '/privacy')} className="text-accent-text underline" />,
            mailLink: <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent-text underline break-all" />,
          }} />
        </p>
      </section>
    </div>
  );
}
