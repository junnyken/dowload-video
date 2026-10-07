import { useEffect, useState } from 'react';
import { AlertCircle, ArrowUpCircle, LogIn, RotateCw } from 'lucide-react';
import { Badge, Button } from './ui';
import { auth } from '../lib/auth';
import { quota, quotaGate, clearGate } from '../lib/quota';
import { badgeText, gatePaused, isIpLimit, isUnlimited } from '../lib/quota-core';
import { deviceInfo } from '../lib/device';
import { api } from '../lib/tauri';
import { openSignIn, toast } from '../lib/ui';
import { WEBSITE_URL } from '../lib/config';

/** "Hôm nay x/y lượt" — hidden until the server answered, and when the feature is off (503). */
export function QuotaBadge() {
  const q = quota.use();
  if (q.status !== 'enabled' || !q.snap) return null;
  const s = q.snap;
  const out = !isUnlimited(s) && s.remaining <= 0;
  return (
    <span title={s.resetTimeVn && !isUnlimited(s) ? `Lượt mới được cộng lại lúc ${s.resetTimeVn}` : undefined}> {/* wording: BA review */}
      <Badge tone={out ? 'warning' : 'neutral'}>{badgeText(s)}</Badge>
    </span>
  );
}

/** Shown while the server's refusal holds the queue: the server's own text, then sign-in or upgrade. */
export function QuotaNotice() {
  const g = quotaGate.use();
  const a = auth.use();
  const q = quota.use();
  // Machine code for support (PLAN-32E §6): the server's copy, else the one Rust computes.
  const [localCode, setLocalCode] = useState<string | null>(null);
  useEffect(() => { void deviceInfo().then((d) => setLocalCode(d?.code ?? null)); }, []);
  if (!gatePaused(g, Date.now())) return null;
  const r = g!.refusal;
  // The server sends "guest -> signin, signed in -> upgrade"; fall back on our own sign-in state.
  const upsell = r.upsell ?? (a.status === 'in' ? 'upgrade' : 'signin');
  const text = r.detail || (isIpLimit(r)
    ? 'Mạng này đã dùng hết lượt tải của khách hôm nay.' // wording: BA review
    : 'Bạn đã hết lượt tải trong hôm nay.'); // wording: BA review
  const showReset = r.resetTimeVn && !text.includes(r.resetTimeVn);
  const code = q.snap?.deviceCode || localCode;
  return (
    <div role="alert" className="mx-6 mb-3 flex flex-wrap items-center gap-3 rounded-lg bg-warning-soft px-3 py-2 text-[13px] text-fg">
      <AlertCircle size={18} className="shrink-0 text-warning" aria-hidden />
      <p className="min-w-0 flex-1 select-text">
        {text}
        {showReset && <> Lượt mới được cộng lại lúc {r.resetTimeVn}.</> /* wording: BA review */}
        {code && <> Nếu cho rằng bị nhầm, gửi Mã máy {code} cho hỗ trợ.</> /* wording: BA review */}
      </p>
      {upsell === 'signin' ? (
        <Button size="sm" variant="primary" icon={<LogIn size={14} />} onClick={() => openSignIn()}>Đăng nhập để có 20 lượt/ngày{/* wording: BA review */}</Button>
      ) : (
        <Button
          size="sm" variant="primary" icon={<ArrowUpCircle size={14} />}
          onClick={() => { api.openUrl(`${WEBSITE_URL}/upgrade`).catch(() => toast('error', 'Không mở được trình duyệt.')); }}
        >Nâng cấp{/* wording: BA review */}</Button>
      )}
      <Button size="sm" icon={<RotateCw size={14} />} onClick={clearGate}>Thử lại{/* wording: BA review */}</Button>
    </div>
  );
}
