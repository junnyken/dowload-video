import { useEffect, useState } from 'react';
import { Check, X, Crown, Copy, Loader2 } from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { API_BASE } from '../lib/apiBase';
import { usePlans, formatVnd } from '../lib/usePlans';
import { buildPlanRows } from '../lib/planRows';

// Pricing page — Khách / Free / Pro. Every number comes from GET /api/v1/plans
// (backend app/core/plans.py); nothing about limits is written in this file.
// Payment is a manual bank transfer: an admin turns Pro on once the money
// arrives. wording: BA review

const TIER_META = {
  guest: { name: 'Khách', sub: 'Chưa đăng nhập' },
  free:  { name: 'Free', sub: 'Có tài khoản, miễn phí' },
  pro:   { name: 'Pro', sub: 'Dành cho người dùng thường xuyên' },
};

function Cell({ value }) {
  if (value === true) return <Check className="w-4 h-4 text-success shrink-0" aria-label="Có" />;
  if (value === false) return <X className="w-4 h-4 text-fg-muted shrink-0" aria-label="Không có" />;
  return <span className="text-fg text-sm text-right">{value}</span>;
}

function CopyButton({ text, label }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch { /* clipboard blocked: user can still select the text */ }
  };
  return (
    <button type="button" onClick={copy} aria-label={label}
      className="shrink-0 inline-flex items-center gap-1 px-2.5 py-1.5 min-h-9 rounded-lg border border-line bg-surface text-fg-2 text-xs hover:bg-surface-2 transition-colors">
      {done ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
      {done ? 'Đã chép' : 'Chép'} {/* wording: BA review */}
    </button>
  );
}

function InfoRow({ label, value, copy }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2 border-b border-line last:border-0">
      <div className="min-w-0">
        <p className="text-xs text-fg-muted">{label}</p>
        <p className="text-sm font-semibold text-fg break-words">{value || '—'}</p>
      </div>
      {copy && value ? <CopyButton text={value} label={`Chép ${label}`} /> : null}
    </div>
  );
}

function PaymentSection({ pay, pro, period }) {
  const { isAuthenticated, session } = useAuth();
  const [me, setMe] = useState(null);
  const [err, setErr] = useState('');
  const token = session?.access_token;

  useEffect(() => {
    if (!token) return undefined;
    let alive = true;
    fetch(`${API_BASE}/api/v1/plans/me`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((d) => { if (alive) { setMe(d); setErr(''); } })
      .catch(() => { if (alive) setErr('Không tải được mã chuyển khoản. Vui lòng thử lại sau.'); }); // wording: BA review
    return () => { alive = false; };
  }, [token]);

  const amount = period === 'year' ? pro.price_year_vnd : pro.price_month_vnd;
  const periodLabel = period === 'year' ? '365 ngày' : '30 ngày';

  const box = 'mt-10 rounded-2xl border border-line bg-surface-2 p-5 sm:p-6';
  const openLogin = () => window.dispatchEvent(new Event('vidgrab:open-auth'));

  if (!pay?.ready) {
    return (
      <section className={box} aria-labelledby="pay-h">
        <h2 id="pay-h" className="text-lg font-bold text-fg mb-2">Thanh toán</h2>
        <p className="text-sm text-fg-2">
          {/* wording: BA review */}
          {pay?.contact ? `Để nâng cấp Pro, vui lòng liên hệ: ${pay.contact}` : 'Liên hệ qua nút Góp ý & Phản hồi để nâng cấp Pro.'}
        </p>
      </section>
    );
  }

  if (!isAuthenticated) {
    return (
      <section className={box} aria-labelledby="pay-h">
        <h2 id="pay-h" className="text-lg font-bold text-fg mb-2">Thanh toán</h2>
        {/* wording: BA review */}
        <p className="text-sm text-fg-2 mb-4">Đăng nhập để nhận mã chuyển khoản riêng của tài khoản bạn.</p>
        <button type="button" onClick={openLogin}
          className="px-5 py-2.5 min-h-11 rounded-xl bg-accent text-accent-fg text-sm font-semibold hover:opacity-90 transition">
          Đăng nhập
        </button>
      </section>
    );
  }

  const isPro = me?.tier === 'pro';
  return (
    <section className={box} aria-labelledby="pay-h">
      <h2 id="pay-h" className="text-lg font-bold text-fg mb-1">Thanh toán Pro bằng chuyển khoản</h2>
      {/* wording: BA review */}
      <p className="text-sm text-fg-muted mb-4">
        Chuyển khoản đúng số tiền và đúng nội dung bên dưới. Sau khi tiền về, Pro được bật thủ công (thường trong ngày).
      </p>
      {isPro && (
        <p className="mb-4 rounded-lg bg-accent-soft border border-accent/30 px-3 py-2 text-sm text-fg">
          {/* wording: BA review */}
          Bạn đang dùng Pro{me.expiry ? `, hết hạn ${new Date(me.expiry).toLocaleDateString('vi-VN')}` : ''}. Chuyển khoản thêm sẽ được cộng nối vào thời hạn hiện tại.
        </p>
      )}
      {me && !isPro && (
        <p className="mb-4 text-sm text-fg-2">Gói hiện tại của bạn: Free.{/* wording: BA review */}</p>
      )}
      {err && <p role="alert" className="mb-3 text-sm text-danger">{err}</p>}
      {!me && !err && (
        <p className="text-sm text-fg-muted flex items-center gap-2"><Loader2 className="w-4 h-4 animate-spin" /> Đang tải…</p>
      )}
      {me && (
        <div>
          <InfoRow label="Ngân hàng" value={pay.bank_name} />
          <InfoRow label="Số tài khoản" value={pay.bank_account} copy />
          <InfoRow label="Chủ tài khoản" value={pay.bank_holder} />
          <InfoRow label={`Số tiền (${periodLabel})`} value={formatVnd(amount)} />
          <InfoRow label="Nội dung chuyển khoản" value={me.transfer_code} copy />
        </div>
      )}
      {pay.contact ? <p className="mt-4 text-xs text-fg-muted">Cần hỗ trợ: {pay.contact}{/* wording: BA review */}</p> : null}
    </section>
  );
}

export default function PricingPage() {
  const { ready, plans } = usePlans();
  const [period, setPeriod] = useState('month');

  useEffect(() => { document.title = 'Bảng giá — VidGrab'; }, []); // wording: BA review

  const tiers = plans?.tiers || [];
  const pro = tiers.find((t) => t.id === 'pro');
  const rows = tiers.length ? buildPlanRows(tiers) : [];

  return (
    <div className="bg-canvas text-fg">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 py-10 sm:py-16">
        <div className="text-center mb-8">
          <h1 className="text-2xl sm:text-4xl font-extrabold tracking-tight mb-3">Bảng giá</h1>
          {/* wording: BA review */}
          <p className="text-fg-muted text-sm sm:text-base max-w-xl mx-auto">
            Dùng thử ngay không cần tài khoản. Đăng ký để có hạn mức cao hơn. Nâng cấp Pro khi cần dùng nhiều.
          </p>
        </div>

        {!ready && (
          <div className="flex justify-center py-16" role="status" aria-label="Đang tải">
            <Loader2 className="w-6 h-6 animate-spin text-fg-muted" />
          </div>
        )}
        {ready && !plans && (
          <p role="alert" className="text-center text-sm text-danger py-12">
            {/* wording: BA review */}
            Không tải được bảng giá. Vui lòng tải lại trang sau ít phút.
          </p>
        )}

        {plans && pro && (
          <>
            <div className="flex justify-center mb-8">
              <div role="group" aria-label="Chu kỳ thanh toán Pro" className="inline-flex rounded-xl border border-line bg-surface p-1">
                {[['month', 'Theo tháng'], ['year', 'Theo năm']].map(([k, l]) => (
                  <button key={k} type="button" aria-pressed={period === k} onClick={() => setPeriod(k)}
                    className={`px-4 py-2 min-h-10 rounded-lg text-sm font-medium transition-colors ${period === k ? 'bg-accent text-accent-fg' : 'text-fg-2 hover:text-fg'}`}>
                    {l}{/* wording: BA review */}
                  </button>
                ))}
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 items-start">
              {tiers.map((t) => {
                const meta = TIER_META[t.id] || { name: t.id, sub: '' };
                const isPro = t.id === 'pro';
                const price = isPro ? (period === 'year' ? t.price_year_vnd : t.price_month_vnd) : 0;
                return (
                  <article key={t.id}
                    className={`min-w-0 rounded-2xl border p-5 bg-surface-2 ${isPro ? 'border-accent shadow-lg' : 'border-line'}`}>
                    <h2 className="flex items-center gap-2 text-lg font-bold">
                      {isPro && <Crown className="w-4 h-4 text-accent-text" aria-hidden="true" />}
                      {meta.name}
                    </h2>
                    <p className="text-xs text-fg-muted mb-3">{meta.sub}</p>
                    <p className="mb-4">
                      <span className="text-3xl font-extrabold">{isPro ? formatVnd(price) : '0đ'}</span>
                      {isPro && <span className="text-sm text-fg-muted"> / {period === 'year' ? 'năm' : 'tháng'}</span>}
                    </p>
                    <ul className="divide-y divide-line">
                      {rows.map((r) => (
                        <li key={r.key} className="flex items-center justify-between gap-3 py-2">
                          <span className="text-xs text-fg-muted min-w-0">{r.label}</span>
                          <Cell value={r.get(t)} />
                        </li>
                      ))}
                    </ul>
                  </article>
                );
              })}
            </div>

            <p className="mt-4 text-xs text-fg-muted text-center">
              {/* wording: BA review */}
              Số lượt tải được tính lại lúc {plans.daily_reset_utc || '00:00'} UTC mỗi ngày.
            </p>

            <PaymentSection pay={plans.payment} pro={pro} period={period} />
          </>
        )}
      </div>
    </div>
  );
}
