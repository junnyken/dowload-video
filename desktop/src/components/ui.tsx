import { useEffect, useRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import { Film, Loader2, X } from 'lucide-react';
import { platformLabel } from '../lib/format';

type BtnProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'sm' | 'md';
  icon?: ReactNode;
};

const VARIANT = {
  primary: 'bg-accent text-accent-fg hover:bg-accent-hover',
  secondary: 'bg-surface text-fg border border-line-strong hover:bg-surface-2',
  ghost: 'text-fg-2 hover:bg-surface-2 hover:text-fg',
  danger: 'bg-surface text-danger border border-line-strong hover:bg-danger-soft',
};

export function Button({ variant = 'secondary', size = 'md', icon, children, className = '', ...rest }: BtnProps) {
  return (
    <button
      type="button"
      {...rest}
      className={`inline-flex items-center justify-center gap-1.5 rounded-[10px] font-medium whitespace-nowrap transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
        size === 'sm' ? 'h-8 px-2.5 text-[13px]' : 'h-9 px-3.5 text-sm'
      } ${VARIANT[variant]} ${className}`}
    >
      {icon}
      {children}
    </button>
  );
}

export function IconButton({ label, children, className = '', ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      {...rest}
      className={`inline-flex h-8 w-8 items-center justify-center rounded-lg text-fg-2 transition-colors hover:bg-surface-2 hover:text-fg disabled:opacity-40 disabled:cursor-not-allowed ${className}`}
    >
      {children}
    </button>
  );
}

export function Badge({ tone = 'neutral', children }: { tone?: 'neutral' | 'success' | 'danger' | 'warning' | 'accent'; children: ReactNode }) {
  const t = {
    neutral: 'bg-surface-2 text-fg-2',
    success: 'bg-success-soft text-success',
    danger: 'bg-danger-soft text-danger',
    warning: 'bg-warning-soft text-warning',
    accent: 'bg-accent-soft text-accent-text',
  }[tone];
  return <span className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${t}`}>{children}</span>;
}

export function PlatformBadge({ platform }: { platform: string }) {
  return <Badge tone="neutral">{platformLabel(platform)}</Badge>;
}

export function Spinner({ className = '' }: { className?: string }) {
  return <Loader2 className={`animate-spin ${className}`} size={16} aria-hidden />;
}

export function Thumb({ src, className = '' }: { src: string | null; className?: string }) {
  return (
    <div className={`relative shrink-0 overflow-hidden rounded-lg bg-surface-2 ${className}`}>
      {src ? (
        <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full w-full items-center justify-center text-fg-muted"><Film size={22} aria-hidden /></div>
      )}
    </div>
  );
}

export function ProgressBar({ percent, tone = 'accent', indeterminate = false }: { percent: number | null; tone?: 'accent' | 'success' | 'warning' | 'danger'; indeterminate?: boolean }) {
  const color = { accent: 'bg-accent', success: 'bg-success', warning: 'bg-warning', danger: 'bg-danger' }[tone];
  const p = Math.max(0, Math.min(100, percent ?? 0));
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate || percent == null ? undefined : Math.round(p)}
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
    >
      <div className={`h-full rounded-full transition-[width] duration-300 ${color} ${indeterminate ? 'vg-skeleton w-full' : ''}`} style={indeterminate ? undefined : { width: `${p}%` }} />
    </div>
  );
}

export function Toggle({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition-colors ${checked ? 'bg-accent' : 'bg-line-strong'}`}
    >
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${checked ? 'left-[22px]' : 'left-0.5'}`} />
    </button>
  );
}

export function Tabs<T extends string>({ value, onChange, items }: { value: T; onChange: (v: T) => void; items: { value: T; label: string; count?: number }[] }) {
  return (
    <div role="tablist" className="inline-flex gap-1 rounded-[10px] bg-surface-2 p-1">
      {items.map((it) => (
        <button
          key={it.value}
          role="tab"
          aria-selected={value === it.value}
          type="button"
          onClick={() => onChange(it.value)}
          className={`inline-flex h-7 items-center gap-1.5 rounded-lg px-3 text-[13px] font-medium transition-colors ${
            value === it.value ? 'bg-surface text-fg shadow-sm' : 'text-fg-2 hover:text-fg'
          }`}
        >
          {it.label}
          {it.count ? <span className="rounded-full bg-accent-soft px-1.5 text-xs text-accent-text">{it.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Select({ value, onChange, children, label, className = '' }: { value: string; onChange: (v: string) => void; children: ReactNode; label: string; className?: string }) {
  return (
    <select
      aria-label={label}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={`h-9 rounded-[10px] border border-line-strong bg-surface px-2.5 text-sm text-fg ${className}`}
    >
      {children}
    </select>
  );
}

export function Modal({ title, onClose, children, width = 'max-w-md' }: { title: string; onClose: () => void; children: ReactNode; width?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('input, button, [tabindex]')?.focus();
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
      prev?.focus?.();
    };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/50 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} role="dialog" aria-modal="true" aria-label={title} className={`w-full ${width} rounded-2xl border border-line bg-surface p-5 shadow-2xl`}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-base font-semibold text-fg">{title}</h2>
          <IconButton label="Đóng" onClick={onClose}><X size={16} /></IconButton>
        </div>
        {children}
      </div>
    </div>
  );
}

export function EmptyState({ icon, title, text, children }: { icon: ReactNode; title: string; text?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="mb-1 flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-2 text-fg-muted">{icon}</div>
      <p className="text-[15px] font-semibold text-fg">{title}</p>
      {text && <p className="max-w-sm text-sm text-fg-muted">{text}</p>}
      {children}
    </div>
  );
}

export function ScreenHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <header className="flex shrink-0 items-center justify-between gap-3 px-6 pb-3 pt-5">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold text-fg">{title}</h1>
        {subtitle && <p className="mt-0.5 truncate text-[13px] text-fg-muted">{subtitle}</p>}
      </div>
      <div className="flex shrink-0 items-center gap-2">{actions}</div>
    </header>
  );
}
