import { useState, useEffect } from 'react';
import { Settings2, Save, Loader2, CheckCircle } from 'lucide-react';
import { useAuth } from '../context/AuthContext';

const QUALITY_OPTIONS = [
  { value: 'video',   label: 'MP4 HD (tốt nhất ≤1080p)' },
  { value: 'mp4_4k',  label: 'MP4 4K (tốt nhất ≤4K)' },
  { value: 'mp3_320', label: 'MP3 320kbps' },
  { value: 'mp3_128', label: 'MP3 128kbps' },
];

const BULK_PRESETS = [10, 20, 50, 100, 200];

export default function PreferencesContent() {
  const { preferences, savePreferences } = useAuth();
  const [form, setForm]       = useState(null);
  const [saving, setSaving]   = useState(false);
  const [saved, setSaved]     = useState(false);

  useEffect(() => {
    if (preferences) setForm({ ...preferences });
  }, [preferences]);

  if (!form) return (
    <div className="flex justify-center py-16">
      <div className="w-6 h-6 border-2 border-[#FBBF24] border-t-transparent rounded-full animate-spin" />
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
        <div className="w-9 h-9 rounded-lg bg-slate-800 flex items-center justify-center">
          <Settings2 className="w-5 h-5 text-[#FBBF24]" />
        </div>
        <div>
          <h1 className="text-white font-bold text-lg">Preferences</h1>
          <p className="text-slate-400 text-xs">Cài đặt mặc định khi tải — sync theo tài khoản</p>
        </div>
      </div>

      {/* Card */}
      <div className="bg-slate-800/40 border border-slate-700/50 rounded-2xl p-5 space-y-5">

        {/* Default quality */}
        <Section title="Chất lượng mặc định">
          <div className="grid grid-cols-2 gap-2">
            {QUALITY_OPTIONS.map(opt => (
              <label key={opt.value}
                className={`flex items-center gap-2.5 px-3 py-2.5 rounded-lg border cursor-pointer transition-colors
                  ${form.default_quality === opt.value
                    ? 'border-[#FBBF24]/60 bg-[#FBBF24]/10 text-[#FBBF24]'
                    : 'border-slate-600/40 text-slate-400 hover:border-slate-500'}`}>
                <input type="radio" className="accent-[#FBBF24]"
                  checked={form.default_quality === opt.value}
                  onChange={() => update('default_quality', opt.value)} />
                <span className="text-sm">{opt.label}</span>
              </label>
            ))}
          </div>
        </Section>

        {/* Toggles */}
        <Section title="Tùy chọn mặc định">
          <div className="space-y-3">
            <Toggle
              label="Xoá watermark"
              desc="Tự động bật khi tải TikTok / Douyin"
              checked={form.remove_watermark}
              onChange={v => update('remove_watermark', v)}
            />
            <Toggle
              label="Tải phụ đề"
              desc="Kèm .srt nếu video có phụ đề"
              checked={form.download_subs}
              onChange={v => update('download_subs', v)}
            />
          </div>
        </Section>

        {/* Bulk preset */}
        <Section title="Số lượng mặc định (Bulk)">
          <div className="flex gap-2 flex-wrap">
            {BULK_PRESETS.map(n => (
              <button key={n}
                onClick={() => update('bulk_count_preset', n)}
                className={`px-4 py-2 rounded-lg text-sm font-semibold border transition-colors
                  ${form.bulk_count_preset === n
                    ? 'border-[#FBBF24]/60 bg-[#FBBF24]/10 text-[#FBBF24]'
                    : 'border-slate-600/40 text-slate-400 hover:border-slate-500'}`}>
                {n}
              </button>
            ))}
          </div>
        </Section>

        {/* Theme */}
        <Section title="Giao diện">
          <div className="flex gap-2">
            {['dark', 'light'].map(t => (
              <button key={t}
                onClick={() => update('theme_mode', t)}
                className={`px-4 py-2 rounded-lg text-sm font-semibold border transition-colors capitalize
                  ${form.theme_mode === t
                    ? 'border-[#FBBF24]/60 bg-[#FBBF24]/10 text-[#FBBF24]'
                    : 'border-slate-600/40 text-slate-400 hover:border-slate-500'}`}>
                {t === 'dark' ? '🌙 Tối' : '☀️ Sáng'}
              </button>
            ))}
          </div>
        </Section>
      </div>

      {/* Save button */}
      <button
        onClick={handleSave}
        disabled={saving}
        className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] font-bold hover:opacity-90 active:scale-[0.98] transition disabled:opacity-50"
      >
        {saving
          ? <><Loader2 className="w-4 h-4 animate-spin" /> Đang lưu...</>
          : saved
          ? <><CheckCircle className="w-4 h-4" /> Đã lưu!</>
          : <><Save className="w-4 h-4" /> Lưu preferences</>}
      </button>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="space-y-2.5">
      <p className="text-slate-300 text-sm font-semibold">{title}</p>
      {children}
    </div>
  );
}

function Toggle({ label, desc, checked, onChange }) {
  return (
    <label className="flex items-center justify-between gap-4 cursor-pointer">
      <div>
        <p className="text-slate-200 text-sm">{label}</p>
        {desc && <p className="text-slate-500 text-xs">{desc}</p>}
      </div>
      <button
        type="button"
        onClick={() => onChange(!checked)}
        className={`relative w-10 h-5.5 rounded-full transition-colors flex-shrink-0
          ${checked ? 'bg-[#FBBF24]' : 'bg-slate-600'}`}
        style={{ height: '22px', width: '40px' }}
      >
        <span className={`absolute top-0.5 left-0.5 w-4.5 h-4.5 bg-white rounded-full shadow transition-transform
          ${checked ? 'translate-x-[18px]' : 'translate-x-0'}`}
          style={{ width: '18px', height: '18px' }} />
      </button>
    </label>
  );
}
