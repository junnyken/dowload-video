import { useEffect, useState } from 'react';
import { useAuth } from '../context/AuthContext';
import { authLinkError } from '../lib/authRecovery';
import { useTranslation } from 'react-i18next';
import { useLocalePath } from '../i18n';

/**
 * Landing page for the Supabase password-recovery link.
 *
 * resetPassword() has always sent users to <origin>/reset-password, but that
 * path was not in PATH_MAP, so the link fell through to the landing page and
 * nothing ever asked for a new password. The recovery token in the URL fragment
 * signs the user in silently, which looks like the reset "worked" while the old
 * password is still the only one that exists.
 */

const MIN_LENGTH = 8;

export default function ResetPasswordPage() {
  const { t } = useTranslation('authPages');
  const lp = useLocalePath();
  const { updatePassword, session, loading } = useAuth();
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState('');
  // Supabase reports a bad or expired link in the URL fragment rather than
  // throwing, so read it instead of leaving the user on a form that cannot work.
  const [linkError, setLinkError] = useState('');

  useEffect(() => {
    // Read the snapshot, not window.location.hash: the Supabase client clears
    // the fragment on load, well before this effect runs.
    const err = authLinkError();
    if (err) setLinkError(err);
  }, []);

  const tooShort = pw.length > 0 && pw.length < MIN_LENGTH;
  const mismatch = pw2.length > 0 && pw !== pw2;
  const canSubmit = pw.length >= MIN_LENGTH && pw === pw2 && !busy;

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit) return;
    setBusy(true); setError('');
    const { error } = await updatePassword(pw);
    setBusy(false);
    if (error) { setError(error.message || t('reset.errFallback')); return; }
    setDone(true);
    // Clear the recovery token out of the address bar once it is spent.
    window.history.replaceState({}, '', '/reset-password');
  }

  const field = 'w-full rounded-lg bg-canvas border border-line px-3 py-2.5 ' +
                'text-sm text-fg placeholder:text-fg-muted focus:border-accent focus:outline-none';

  if (done) {
    return (
      <div className="max-w-md mx-auto text-center space-y-4">
        <div className="text-4xl">✓</div>
        <h1 className="text-xl font-bold text-fg">{t('reset.doneTitle')}</h1>
        <p className="text-sm text-fg-muted">
          {t('reset.doneBody')}
        </p>
        <a href={lp('/')} className="inline-block rounded-lg bg-accent px-4 py-2 text-sm font-bold text-accent-fg">
          {t('reset.home')}
        </a>
      </div>
    );
  }

  return (
    <div className="max-w-md mx-auto space-y-5">
      <div>
        <h1 className="text-xl font-bold text-fg">{t('reset.title')}</h1>
        <p className="mt-1 text-sm text-fg-muted">
          {t('reset.subtitle')}
        </p>
      </div>

      {linkError && (
        <div className="rounded-lg border border-danger/60 bg-danger-soft p-3 text-sm text-danger">
          {t('reset.linkUnusable', { error: linkError })}
          <div className="mt-2 text-xs text-danger">
            {t('reset.linkHint')}
          </div>
        </div>
      )}

      {/* Without a recovery session there is nothing to update — say so instead
          of letting the user type a password that cannot be saved. */}
      {!loading && !session && !linkError && (
        <div className="rounded-lg border border-accent/60 bg-accent-soft p-3 text-sm text-accent-text">
          {t('reset.noSession')}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-semibold text-fg-muted">{t('reset.newPassword')}</label>
          <input type={show ? 'text' : 'password'} value={pw} onChange={e => setPw(e.target.value)}
                 autoComplete="new-password" className={field} placeholder={t('reset.minPlaceholder', { count: MIN_LENGTH })} />
          {tooShort && <p className="mt-1 text-xs text-accent-text">{t('reset.tooShort', { count: MIN_LENGTH })}</p>}
        </div>

        <div>
          <label className="mb-1 block text-xs font-semibold text-fg-muted">{t('reset.confirmPassword')}</label>
          <input type={show ? 'text' : 'password'} value={pw2} onChange={e => setPw2(e.target.value)}
                 autoComplete="new-password" className={field} placeholder={t('reset.confirmPlaceholder')} />
          {mismatch && <p className="mt-1 text-xs text-accent-text">{t('reset.mismatch')}</p>}
        </div>

        <label className="flex items-center gap-2 text-xs text-fg-muted">
          <input type="checkbox" checked={show} onChange={e => setShow(e.target.checked)} />
          {t('reset.show')}
        </label>

        {error && <p className="text-sm text-danger">{error}</p>}

        <button type="submit" disabled={!canSubmit}
          className="w-full rounded-lg bg-accent px-4 py-2.5 text-sm font-bold text-accent-fg
                     disabled:opacity-40 disabled:cursor-not-allowed">
          {busy ? t('reset.saving') : t('reset.submit')}
        </button>
      </form>
    </div>
  );
}
