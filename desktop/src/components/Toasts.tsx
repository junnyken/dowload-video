import { CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { toasts, dismissToast, confirmState } from '../lib/ui';
import { Button, Modal } from './ui';

export function Toasts() {
  const list = toasts.use();
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[340px] flex-col gap-2" role="status" aria-live="polite">
      {list.map((t) => {
        const Icon = t.kind === 'success' ? CheckCircle2 : t.kind === 'error' ? XCircle : Info;
        const tone = t.kind === 'success' ? 'text-success' : t.kind === 'error' ? 'text-danger' : 'text-accent-text';
        return (
          <div key={t.id} className="vg-toast pointer-events-auto flex items-start gap-2.5 rounded-xl border border-line bg-surface p-3 shadow-lg">
            <Icon size={18} className={`mt-0.5 shrink-0 ${tone}`} aria-hidden />
            <p className="min-w-0 flex-1 break-words text-[13px] text-fg">{t.text}</p>
            <button type="button" aria-label="Đóng thông báo" onClick={() => dismissToast(t.id)} className="text-fg-muted hover:text-fg"><X size={14} /></button>
          </div>
        );
      })}
    </div>
  );
}

export function ConfirmDialog() {
  const req = confirmState.use();
  if (!req) return null;
  const close = (v: boolean) => {
    confirmState.set(null);
    req.resolve(v);
  };
  return (
    <Modal title={req.title} onClose={() => close(false)}>
      <p className="mb-5 text-sm text-fg-2">{req.text}</p>
      <div className="flex justify-end gap-2">
        <Button onClick={() => close(false)}>Không</Button>
        <Button variant={req.danger ? 'danger' : 'primary'} onClick={() => close(true)}>{req.okLabel}</Button>
      </div>
    </Modal>
  );
}
