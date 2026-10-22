// wording: BA review
// Task #6398 (13/10/2026): rewritten to match what the code actually stores —
// see docs/security/SECURITY-REVIEW-2026-10-13.md §D for the evidence per line.
// Do not add a practice here unless the code does it.

import { useTranslation } from 'react-i18next';

function Section({ title, children }) {
  return (
    <section>
      <h2 className="text-xl font-bold text-fg mb-3">{title}</h2>
      {children}
    </section>
  );
}

function Item({ label, children }) {
  return (
    <li>
      <span className="text-fg font-medium">{label}</span> — {children}
    </li>
  );
}

export default function PrivacyPolicy() {
  const { t } = useTranslation('privacy');
  const notice = t('translationNotice');
  return (
    <div className="text-fg">
      {notice && (
        <p role="note" className="mb-6 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg-muted">{notice}</p>
      )}
      {/* Header */}
      <div className="mb-10 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">{t('title')}</h1>
        <p className="text-fg-muted text-sm">
          {t('subtitle') && `${t('subtitle')} — `}<span className="text-fg-2">{t('updated', { date: '13/10/2026' })}</span>
        </p>
      </div>

      <div className="space-y-10 text-fg-2 leading-relaxed">

        <Section title={t('s1.title')}>
          <p className="mb-3">
            {t('s1.intro')}
          </p>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label={t('s1.account.label')}>
              {t('s1.account.text')}
            </Item>
            <Item label={t('s1.history.label')}>
              {t('s1.history.text')}
            </Item>
            <Item label={t('s1.prefs.label')}>
              {t('s1.prefs.text')}
            </Item>
            <Item label={t('s1.ip.label')}>
              {t('s1.ip.text')}
            </Item>
            <Item label={t('s1.browser.label')}>
              {t('s1.browser.text')}
            </Item>
            <Item label={t('s1.stats.label')}>
              {t('s1.stats.text')}
            </Item>
          </ul>
        </Section>

        <Section title={t('s2.title')}>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <li>{t('s2.card')}</li>
            <li>{t('s2.video')}</li>
            <li>{t('s2.sensitive')}</li>
          </ul>
        </Section>

        <Section title={t('s3.title')}>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label={t('s3.links.label')}>{t('s3.links.text')}</Item>
            <Item label={t('s3.temp.label')}>{t('s3.temp.text')}</Item>
            <Item label={t('s3.history.label')}>
              {t('s3.history.text')}
            </Item>
            <Item label={t('s3.counters.label')}>
              {t('s3.counters.text')}
            </Item>
            <Item label={t('s3.securityLog.label')}>
              {t('s3.securityLog.text')}
            </Item>
            <Item label={t('s3.takedown.label')}>
              {t('s3.takedown.text')}
            </Item>
            <Item label={t('s3.backup.label')}>
              {t('s3.backup.text')}
            </Item>
            <Item label={t('s3.account.label')}>
              {t('s3.account.text')}
            </Item>
          </ul>
        </Section>

        <Section title={t('s4.title')}>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label={t('s4.tabs.label')}>
              {t('s4.tabs.text')}
            </Item>
            <Item label={t('s4.cookies.label')}>
              {t('s4.cookies.text')}
            </Item>
            <Item label={t('s4.other.label')}>
              {t('s4.other.text')}
            </Item>
          </ul>
        </Section>

        <Section title={t('s5.title')}>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label={t('s5.cookies.label')}>
              {t('s5.cookies.text')}
            </Item>
            <Item label={t('s5.device.label')}>
              {t('s5.device.text')}
            </Item>
            <Item label={t('s5.sync.label')}>
              {t('s5.sync.text')}
            </Item>
          </ul>
        </Section>

        <Section title={t('s6.title')}>
          <ul className="list-disc list-inside space-y-2 pl-2">
            <Item label={t('s6.supabase.label')}>{t('s6.supabase.text')}</Item>
            <Item label={t('s6.stripe.label')}>{t('s6.stripe.text')}</Item>
            <Item label={t('s6.proxy.label')}>
              {t('s6.proxy.text')}
            </Item>
            <Item label={t('s6.ai.label')}>
              {t('s6.ai.text')}
            </Item>
            <Item label={t('s6.telegram.label')}>
              {t('s6.telegram.text')}
            </Item>
            <Item label={t('s6.sentry.label')}>
              {t('s6.sentry.text')}
            </Item>
            <Item label={t('s6.fonts.label')}>
              {t('s6.fonts.text')}
            </Item>
            <Item label={t('s6.push.label')}>
              {t('s6.push.text')}
            </Item>
          </ul>
          <p className="mt-4 font-semibold text-danger">
            {t('s6.noSale')}
          </p>
        </Section>

        <Section title={t('s7.title')}>
          <p>
            {t('s7.text')}
          </p>
        </Section>

        <Section title={t('s8.title')}>
          <p className="mb-4">
            {t('s8.intro')}
          </p>
          <a
            href="mailto:privacy@vidgrab.io"
            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent-soft border border-accent/30 text-accent-text font-semibold hover:bg-accent/20 transition-colors"
          >
            privacy@vidgrab.io
          </a>
          <p className="mt-4 text-fg-muted text-sm">
            {t('s8.sla')}
          </p>
        </Section>

      </div>
    </div>
  );
}
