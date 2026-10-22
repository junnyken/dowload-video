import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { API_BASE } from '../lib/apiBase';
import { SUPPORT_EMAIL } from '../lib/contact';

// wording: BA review
const EMPTY = {
  name: '', email: '', capacity: '', content_url: '', related: '',
  description: '', declaration: false, website: '',
};
const MAX = 2000;
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s.]{2,}$/;
const HTTPS_RE = /^https:\/\/[^\s/$.?#]\S*$/i;

const INPUT =
  'w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-sm text-fg placeholder:text-fg-muted ' +
  'focus:outline-none focus:ring-2 focus:ring-accent';

function validate(f, t) {
  const e = {};
  if (!f.name.trim()) e.name = t('errors.name');
  if (!EMAIL_RE.test(f.email.trim())) e.email = t('errors.email');
  if (!f.capacity) e.capacity = t('errors.capacity');
  if (!HTTPS_RE.test(f.content_url.trim())) e.content_url = t('errors.url');
  if (f.related.trim().length > MAX) e.related = t('errors.max', { max: MAX });
  if (!f.description.trim()) e.description = t('errors.description');
  else if (f.description.length > MAX) e.description = t('errors.max', { max: MAX });
  if (!f.declaration) e.declaration = t('errors.declaration');
  return e;
}

function Field({ id, label, error, hint, children }) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-fg mb-1">{label}</label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-fg-muted">{hint}</p>}
      {error && <p id={`${id}-err`} role="alert" className="mt-1 text-xs text-danger">{error}</p>}
    </div>
  );
}

export default function TakedownPage() {
  const { t } = useTranslation('takedown');
  const notice = t('translationNotice');
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState(false);
  const [serverError, setServerError] = useState('');

  const set = (k) => (e) => {
    const v = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [k]: v }));
    if (errors[k]) setErrors((x) => ({ ...x, [k]: undefined }));
  };

  async function submit(e) {
    e.preventDefault();
    if (sending) return;
    setServerError('');
    const errs = validate(form, t);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setSending(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/takedown`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          name: form.name.trim(), email: form.email.trim(),
          content_url: form.content_url.trim(), related: form.related.trim(),
          description: form.description.trim(),
        }),
      });
      if (res.ok) { setDone(true); return; }
      if (res.status === 429) {
        setServerError(t('errors.rateLimit'));
      } else if (res.status === 422) {
        setServerError(t('errors.invalid'));
      } else {
        setServerError(t('errors.generic'));
      }
    } catch {
      setServerError(t('errors.network'));
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="text-fg">
      {notice && (
        <p role="note" className="mb-6 rounded-lg border border-line bg-surface px-3 py-2 text-sm text-fg-muted">{notice}</p>
      )}
      <div className="mb-8 border-b border-line pb-6">
        <h1 className="text-3xl font-extrabold text-fg mb-2">{t('title')}</h1>
        <p className="text-fg-muted text-sm">{t('subtitle')}</p>
      </div>

      <section className="mb-8 space-y-3 text-fg-2 leading-relaxed">
        <p>
          {t('intro1')}
        </p>
        <p>{t('intro2')}</p>
        <ul className="list-disc list-inside space-y-1 pl-2">
          <li>{t('li1')}</li>
          <li>{t('li2')}</li>
          <li>{t('li3')}</li>
        </ul>
        <p className="text-sm text-fg-muted">
          {t('otherHelp')}{' '}
          <a href={`mailto:${SUPPORT_EMAIL}`} className="text-accent-text underline break-all">{SUPPORT_EMAIL}</a>.
        </p>
      </section>

      {done ? (
        <div role="status" className="rounded-xl border border-success bg-success-soft p-5">
          <p className="font-semibold text-fg mb-1">{t('doneTitle')}</p>
          <p className="text-sm text-fg-2">
            {t('doneText')}
          </p>
        </div>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-5">
          {/* Honeypot: hidden from people and assistive tech; bots fill it. */}
          <div aria-hidden="true" className="absolute -left-[9999px] h-0 w-0 overflow-hidden">
            <label>{t('honeypot')}
              <input type="text" tabIndex={-1} autoComplete="off" value={form.website} onChange={set('website')} />
            </label>
          </div>

          <Field id="td-name" label={t('fields.name')} error={errors.name}>
            <input id="td-name" className={INPUT} value={form.name} onChange={set('name')}
              maxLength={MAX} autoComplete="name" aria-invalid={!!errors.name} />
          </Field>
          <Field id="td-email" label={t('fields.email')} error={errors.email}>
            <input id="td-email" type="email" className={INPUT} value={form.email} onChange={set('email')}
              maxLength={MAX} autoComplete="email" aria-invalid={!!errors.email} />
          </Field>
          <Field id="td-capacity" label={t('fields.capacity')} error={errors.capacity}>
            <select id="td-capacity" className={INPUT} value={form.capacity} onChange={set('capacity')}
              aria-invalid={!!errors.capacity}>
              <option value="">{t('fields.capacityPlaceholder')}</option>
              <option value="owner">{t('fields.capacityOwner')}</option>
              <option value="agent">{t('fields.capacityAgent')}</option>
            </select>
          </Field>
          <Field id="td-url" label={t('fields.url')} error={errors.content_url}
            hint={t('fields.urlHint')}>
            <input id="td-url" type="url" className={INPUT} value={form.content_url} onChange={set('content_url')}
              maxLength={MAX} placeholder={t('fields.urlPlaceholder')} aria-invalid={!!errors.content_url} />
          </Field>
          <Field id="td-related" label={t('fields.related')} error={errors.related}>
            <input id="td-related" className={INPUT} value={form.related} onChange={set('related')}
              maxLength={MAX} aria-invalid={!!errors.related} />
          </Field>
          <Field id="td-desc" label={t('fields.description')} error={errors.description}
            hint={t('fields.charCount', { current: form.description.length, max: MAX })}>
            <textarea id="td-desc" rows={5} className={INPUT} value={form.description} onChange={set('description')}
              maxLength={MAX} aria-invalid={!!errors.description} />
          </Field>

          <div>
            <label className="flex items-start gap-3 text-sm text-fg-2">
              <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-[var(--vg-accent)]"
                checked={form.declaration} onChange={set('declaration')} aria-invalid={!!errors.declaration} />
              <span>{t('fields.declaration')}</span>
            </label>
            {errors.declaration && <p role="alert" className="mt-1 text-xs text-danger">{errors.declaration}</p>}
          </div>

          {serverError && (
            <p role="alert" className="rounded-lg border border-danger bg-danger-soft px-3 py-2 text-sm text-danger">
              {serverError}
            </p>
          )}

          <button type="submit" disabled={sending}
            className="w-full sm:w-auto rounded-lg bg-accent px-6 py-3 text-sm font-semibold text-accent-fg hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed cursor-pointer">
            {sending ? t('sending') : t('submit')}
          </button>
        </form>
      )}
    </div>
  );
}
