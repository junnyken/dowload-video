import { useState, useEffect } from 'react';
import { Settings2, Save, Loader2, CheckCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../context/AuthContext';

// Labels: locales/*/settings.json (prefs.q.<value>).
const QUALITY_OPTIONS = ['video', 'mp4_4k', 'mp3_320', 'mp3_128'];

const BULK_PRESETS = [10, 20, 50, 100, 200];

export default function PreferencesContent() {
  const { t } = useTranslation('settings');
  const { preferences, savePreferences } = useAuth();
  const [form, setForm]       = useState(null);
  const [saving, setSaving]   = useState(false);
  const [saved, setSaved]     = useState(false);

  useEffect(() => {
    if (preferences) setForm({ ...preferences });
  }, [preferences]);

  if (!form) return (
    <div className="flex justify-center py-16">
      <div className="w-6 h-6 border-2 border-accent border-t-transparent rounded-full animate-spin" />
    </div>
  );

  const update = (key, val) => setForm(f => ({ ...f, [key]: val }));

  const handleSave = async () => {
    setSaving(true); setSaved(false);
    await savePreferences(form);
    setSaving(false); setSaved(true);
    setTimeout(() => setSaved(false), 2500);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-9 h-9 rounded-lg bg-surface flex items-center justify-center">
          <Settings2 className="w-5 h-5 text-accent-text" />
        </div>
        <div>
          <h1 className="text-fg font-bold text-lg">{t('prefs.title')}</h1>
          <p className="text-fg-muted text-xs">{t('prefs.subtitle')}</p>
        </div>
      </div>

      {/* Card */}
      <div className="bg-surface-2 border border-line rounded-2xl p-5 space-y-5">

        {/* Default quality */}
        <Section title={t('prefs.quality')}>
          <div className="grid grid-cols-2 gap-2">
            {QUALITY_OPTIONS.map(opt => (
              <label key={opt}
                className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors
                  ${form.default_quality === opt
                    ? 'border-accent/60 bg-accent-soft text-accent-text'
                    : 'border-line-strong text-fg-muted hover:border-line-strong'}`}>
                <input type="radio" className="accent-accent"
                  checked={form.default_quality === opt}
                  onChange={() => update('default_quality', opt)} />
                <span className="text-sm">{t(`prefs.q.${opt}`)}</span>
              </label>
            ))}
          </div>
        </Section>

        {/* Toggles */}
        <Section title={t('prefs.defaults')}>
          <div className="space-y-3">
            <Toggle
              label={t('prefs.watermark')}
              desc={t('prefs.watermarkDesc')}
              checked={form.remove_watermark}
              onChange={v => update('remove_watermark', v)}
            />
            <Toggle
              label={t('prefs.subs')}
              desc={t('prefs.subsDesc')}
              checked={form.download_subs}
              onChange={v => update('download_subs', v)}
            />
          </div>
        </Section>

        {/* Bulk preset */}
        <Section title={t('prefs.bulk')}>
          <div className="flex gap-2 flex-wrap">
            {BULK_PRESETS.map(n => (
              <button key={n}
                onClick={() => update('bulk_count_preset', n)}
                className={`px-4 py-2 rounded-lg text-sm font-semibold border transition-colors
                  ${form.bulk_count_preset === n
                    ? 'border-accent/60 bg-accent-soft text-accent-text'
                    : 'border-line-strong text-fg-muted hover:border-line-strong'}`}>
                {n}
              </button>
            ))}
          </div>
        </Section>

        {/* Theme */}
        <Section title={t('prefs.theme')}>
          <div className="flex gap-2">
            {['dark', 'light'].map(mode => (
              <button key={mode}
                onClick={() => update('theme_mode', mode)}
                className={`px-4 py-2 rounded-lg text-sm font-semibold border transition-colors capitalize
                  ${form.theme_mode === mode
                    ? 'border-accent/60 bg-accent-soft text-accent-text'
                    : 'border-line-strong text-fg-muted hover:border-line-strong'}`}>
                {mode === 'dark' ? t('prefs.dark') : t('prefs.light')}
              </button>
            ))}
          </div>
        </Section>
      </div>

      {/* Save button */}
      <button
        onClick={handleSave}
        disabled={saving}
        className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-accent text-accent-fg font-bold hover:opacity-90 active:scale-[0.98] transition disabled:opacity-50"
      >
        {saving
          ? <><Loader2 className="w-4 h-4 animate-spin" /> {t('prefs.saving')}</>
          : saved
          ? <><CheckCircle className="w-4 h-4" /> {t('prefs.saved')}</>
          : <><Save className="w-4 h-4" /> {t('prefs.save')}</>}
      </button>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="space-y-2.5">
      <p className="text-fg-2 text-sm font-semibold">{title}</p>
      {children}
    </div>
  );
}

function Toggle({ label, desc, checked, onChange }) {
  return (
    <label className="flex items-center justify-between gap-4 cursor-pointer">
      <div>
        <p className="text-fg-2 text-sm">{label}</p>
        {desc && <p className="text-fg-muted text-xs">{desc}</p>}
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`relative w-10 h-5.5 rounded-full transition-colors flex-shrink-0
          ${checked ? 'bg-accent' : 'bg-line-strong'}`}
        style={{ height: '22px', width: '40px' }}
      >
        <span className={`absolute top-0.5 left-0.5 w-4.5 h-4.5 bg-surface rounded-full shadow transition-transform
          ${checked ? 'translate-x-[18px]' : 'translate-x-0'}`}
          style={{ width: '18px', height: '18px' }} />
      </button>
    </label>
  );
}
