import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';
import { SUPPORT_EMAIL } from '../lib/contact';

// wording: BA review
export default function TermsPage({ onNavigate }) {
  const { t } = useTranslation('terms');
  const lp = useLocalePath();
  const notice = t('translationNotice');
  const go = (e, view, path) => { e.preventDefault(); onNavigate?.(view, path); };
  return (
    <div className="text-fg">
      {notice && (
        <p role="note" className="mb-6 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg-muted">{notice}</p>
      )}
      <div className="mb-10 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">{t('title')}</h1>
        <p className="text-fg-muted text-sm">
          {t('updated')} <span className="text-fg-2">14/10/2026</span>
        </p>
      </div>

      <div className="space-y-10 text-fg-2 leading-relaxed">
        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s1.title')}</h2>
          <p>
            {t('s1.text')}
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s2.title')}</h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>{t('s2.own')}</li>
            <li>{t('s2.platformTerms')}</li>
            <li>{t('s2.noRights')}</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s3.title')}</h2>
          <p>
            {t('s3.text')}
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s4.title')}</h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>{t('s4.repost')}</li>
            <li>{t('s4.automation')}</li>
            <li>{t('s4.evade')}</li>
            <li>{t('s4.illegal')}</li>
          </ul>
          <p className="mt-3">{t('s4.consequence')}</p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s5.title')}</h2>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li><span className="text-fg font-medium">{t('s5.guestLabel')}</span> {t('s5.guest')}</li>
            <li><span className="text-fg font-medium">{t('s5.memberLabel')}</span> {t('s5.member')}</li>
            <li>{t('s5.adjust')}</li>
            <li>{t('s5.credentials')}</li>
          </ul>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s6.title')}</h2>
          <p>
            {/* wording: BA review */}
            {t('s6.before')}<a href={lp('/pricing')} className="text-accent-text underline">{t('s6.link')}</a>{t('s6.after')}
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s7.title')}</h2>
          <p>
            {t('s7.before')}{' '}
            <a href={lp('/takedown')} onClick={(e) => go(e, 'takedown', '/takedown')} className="text-accent-text underline">/takedown</a>{t('s7.after')}
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s8.title')}</h2>
          <p>
            {t('s8.text')}
          </p>
        </section>

        <section>
          <h2 className="text-xl font-bold text-fg mb-3">{t('s9.title')}</h2>
          <p>
            {t('s9.before')}{' '}
            <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent-text underline break-all">{SUPPORT_EMAIL}</a>{t('s9.after')}
          </p>
        </section>
      </div>
    </div>
  );
}
