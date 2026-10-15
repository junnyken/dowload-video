import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { isEmptyFilter, judge, parseWords, unknownFields } from '../lib/channel-filter';
import type { ChannelFilter, ChannelVideo } from '../lib/types';

const FIELD_LABEL = { duration: 'thời lượng', views: 'lượt xem', date: 'ngày đăng' } as const; // wording: BA review

/** null when the filter is usable, else the first problem (Vietnamese). */
export function validateFilter(f: ChannelFilter): string | null {
  const nums = [f.minSeconds, f.maxSeconds, f.minViews];
  if (nums.some((x) => x != null && (!Number.isFinite(x) || x < 0))) return 'Giá trị không được âm.'; // wording: BA review
  if (f.minSeconds != null && f.maxSeconds != null && f.minSeconds > f.maxSeconds) return 'Thời lượng tối thiểu phải nhỏ hơn hoặc bằng tối đa.'; // wording: BA review
  if (f.maxAgeDays != null && (!Number.isInteger(f.maxAgeDays) || f.maxAgeDays < 1 || f.maxAgeDays > 3650)) return 'Số ngày phải từ 1 đến 3650.'; // wording: BA review
  return null;
}

const toNum = (s: string): number | null => (s.trim() === '' || !Number.isFinite(Number(s)) ? null : Number(s));
const minutesText = (sec: number | null) => (sec == null ? '' : String(Math.round((sec / 60) * 100) / 100));
const numText = (n: number | null) => (n == null ? '' : String(n));

const INPUT = 'h-9 w-full rounded-[10px] border border-line-strong bg-surface px-2.5 text-sm text-fg placeholder:text-fg-muted';

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-[13px] text-fg-2">
      {label}
      {children}
    </label>
  );
}

/**
 * Optional conditions for a watched channel's new videos. Keeps its own text
 * drafts (so "a, " is not rewritten while typing) and reports the parsed
 * ChannelFilter on every change; use validateFilter() to gate saving.
 */
export function ChannelFilterForm({ value, onChange, videos }: { value: ChannelFilter; onChange: (f: ChannelFilter) => void; videos?: ChannelVideo[] }) {
  const [open, setOpen] = useState(() => !isEmptyFilter(value));
  const [minMin, setMinMin] = useState(minutesText(value.minSeconds));
  const [maxMin, setMaxMin] = useState(minutesText(value.maxSeconds));
  const [views, setViews] = useState(numText(value.minViews));
  const [days, setDays] = useState(numText(value.maxAgeDays));
  const [inc, setInc] = useState(value.includeWords.join(', '));
  const [exc, setExc] = useState(value.excludeWords.join(', '));

  function emit(p: Partial<{ minMin: string; maxMin: string; views: string; days: string; inc: string; exc: string }>) {
    const d = { minMin, maxMin, views, days, inc, exc, ...p };
    const mn = toNum(d.minMin), mx = toNum(d.maxMin);
    onChange({
      minSeconds: mn == null ? null : Math.round(mn * 60),
      maxSeconds: mx == null ? null : Math.round(mx * 60),
      minViews: toNum(d.views),
      maxAgeDays: toNum(d.days),
      includeWords: parseWords(d.inc),
      excludeWords: parseWords(d.exc),
    });
  }

  const error = validateFilter(value);
  const unknown = useMemo(() => (videos ? unknownFields(videos, value) : []), [videos, value]);
  const match = useMemo(() => {
    if (!videos || !videos.length || isEmptyFilter(value) || error) return null;
    const now = Date.now();
    return videos.filter((v) => judge(v, value, now).pass).length;
  }, [videos, value, error]);

  return (
    <div className="w-full">
      <button
        type="button" aria-expanded={open} onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 rounded-lg py-0.5 text-[13px] font-medium text-fg-2 hover:text-fg"
      >
        {open ? <ChevronDown size={15} aria-hidden /> : <ChevronRight size={15} aria-hidden />}
        {/* wording: BA review */}
        Điều kiện tự tải video mới (tuỳ chọn)
        {!open && !isEmptyFilter(value) && <span className="rounded-full bg-accent-soft px-1.5 text-[11px] text-fg-2">đang bật</span>}
      </button>
      {open && (
        <div className="mt-2 rounded-xl border border-line bg-surface-2 p-3">
          {/* wording: BA review */}
          <p className="mb-3 text-xs text-fg-muted">Để trống = tự tải mọi video mới như bình thường. Video mới đăng thường chưa nhiều lượt xem.</p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {/* wording: BA review */}
            <Field label="Thời lượng tối thiểu (phút)">
              <input type="number" min={0} step="any" inputMode="decimal" className={INPUT} value={minMin} placeholder="Không giới hạn"
                onChange={(e) => { setMinMin(e.target.value); emit({ minMin: e.target.value }); }} />
            </Field>
            <Field label="Thời lượng tối đa (phút)">
              <input type="number" min={0} step="any" inputMode="decimal" className={INPUT} value={maxMin} placeholder="Không giới hạn"
                onChange={(e) => { setMaxMin(e.target.value); emit({ maxMin: e.target.value }); }} />
            </Field>
            <Field label="Lượt xem tối thiểu">
              <input type="number" min={0} step={1} inputMode="numeric" className={INPUT} value={views} placeholder="Không giới hạn"
                onChange={(e) => { setViews(e.target.value); emit({ views: e.target.value }); }} />
            </Field>
            <Field label="Chỉ video đăng trong … ngày">
              <input type="number" min={1} max={3650} step={1} inputMode="numeric" className={INPUT} value={days} placeholder="Không giới hạn"
                onChange={(e) => { setDays(e.target.value); emit({ days: e.target.value }); }} />
            </Field>
            <Field label="Tiêu đề có một trong các từ">
              <input type="text" className={INPUT} value={inc} placeholder="vd: review, hướng dẫn"
                onChange={(e) => { setInc(e.target.value); emit({ inc: e.target.value }); }} />
            </Field>
            <Field label="Bỏ qua nếu tiêu đề có">
              <input type="text" className={INPUT} value={exc} placeholder="vd: quảng cáo, trailer"
                onChange={(e) => { setExc(e.target.value); emit({ exc: e.target.value }); }} />
            </Field>
          </div>
          {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
          {unknown.map((u) => (
            // wording: BA review
            <p key={u} className="mt-2 text-xs text-fg-muted">Kênh này không cung cấp {FIELD_LABEL[u]} — điều kiện này sẽ bỏ qua.</p>
          ))}
          {match != null && videos && (
            // wording: BA review
            <p className="mt-2 text-xs text-fg-2">{match}/{videos.length} video trong danh sách khớp điều kiện</p>
          )}
        </div>
      )}
    </div>
  );
}
