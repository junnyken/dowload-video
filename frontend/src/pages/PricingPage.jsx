import { useState } from 'react';
import {
  CheckCircle, X, Zap, Users, Code2, Crown,
  ChevronDown, Loader2,
} from 'lucide-react';
import { supabase } from '../lib/supabaseClient';

const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

// ── Plan definitions ─────────────────────────────────────────────────────────

const PLANS = [
  {
    id: 'free',
    name: 'Free',
    icon: <Zap className="w-5 h-5" />,
    description: 'Khởi đầu miễn phí, không cần thẻ tín dụng.',
    monthlyPrice: 0,
    yearlyPrice: 0,
    cta: 'Bắt đầu miễn phí',
    ctaVariant: 'secondary',
    featured: false,
    features: [
      { label: '10 downloads/ngày', included: true },
      { label: 'Batch tối đa 3 URL', included: true },
      { label: 'Chất lượng tối đa 1080p', included: true },
      { label: 'YouTube: chỉ MP3', included: true },
      { label: 'Lịch sử 7 ngày', included: true },
      { label: 'AI Smart Tools', included: false },
      { label: 'Bulk ZIP', included: false },
      { label: 'API key', included: false },
      { label: 'Hàng đợi ưu tiên', included: false },
    ],
  },
  {
    id: 'pro',
    name: 'Pro',
    icon: <Crown className="w-5 h-5" />,
    description: 'Dành cho người dùng cá nhân cần sức mạnh thực sự.',
    monthlyPrice: 9.99,
    yearlyPrice: 79,
    cta: 'Nâng cấp lên Pro',
    ctaVariant: 'primary',
    featured: true,
    features: [
      { label: '100 downloads/ngày', included: true },
      { label: 'Batch tối đa 100 URL', included: true },
      { label: 'Chất lượng 4K', included: true },
      { label: 'YouTube MP3 + Video', included: true },
      { label: 'Lịch sử 90 ngày', included: true },
      { label: 'AI Smart Tools (50 phân tích/ngày)', included: true },
      { label: 'Bulk ZIP', included: true },
      { label: 'API key (100 calls/ngày)', included: true },
      { label: 'Hàng đợi ưu tiên', included: true },
    ],
  },
  {
    id: 'team',
    name: 'Team',
    icon: <Users className="w-5 h-5" />,
    description: 'Cộng tác nhóm, quản lý thành viên tập trung.',
    monthlyPrice: 29.99,
    yearlyPrice: 239,
    priceSuffix: '/ 5 ghế',
    cta: 'Nâng cấp lên Team',
    ctaVariant: 'secondary',
    featured: false,
    features: [
      { label: '500 downloads/ngày (dùng chung)', included: true },
      { label: 'Tất cả tính năng Pro', included: true },
      { label: 'Workspace team', included: true },
      { label: 'Lịch sử chung nhóm', included: true },
      { label: 'Quản lý thành viên', included: true },
      { label: 'API key nhóm (500 calls/ngày)', included: true },
      { label: 'Hàng đợi ưu tiên', included: true },
      { label: 'Bulk ZIP', included: true },
      { label: 'AI Smart Tools', included: true },
    ],
  },
  {
    id: 'api_partner',
    name: 'API Partner',
    icon: <Code2 className="w-5 h-5" />,
    description: 'Tích hợp API mạnh mẽ cho nhà phát triển và doanh nghiệp.',
    monthlyPrice: 19.99,
    yearlyPrice: 159,
    cta: 'Nâng cấp lên API Partner',
    ctaVariant: 'secondary',
    featured: false,
    features: [
      { label: '1000 downloads/ngày', included: true },
      { label: 'API Partner key (1000 calls/tháng)', included: true },
      { label: 'Webhook HMAC', included: true },
      { label: 'Batch tối đa 500 URL', included: true },
      { label: 'AI phân tích (100/ngày)', included: true },
      { label: 'Hỗ trợ email ưu tiên <24h', included: true },
      { label: 'Hàng đợi ưu tiên', included: true },
      { label: 'Bulk ZIP', included: true },
      { label: 'Workspace team', included: false },
    ],
  },
];

const FAQS = [
  {
    q: 'Tôi có thể huỷ đăng ký bất cứ lúc nào không?',
    a: 'Có. Bạn có thể huỷ gói bất kỳ lúc nào từ trang Billing. Quyền lợi vẫn giữ đến hết chu kỳ thanh toán hiện tại, sau đó tài khoản tự động về Free.',
  },
  {
    q: 'Gói Yearly tiết kiệm như thế nào?',
    a: 'Gói năm tương đương khoảng 34% so với thanh toán tháng. Ví dụ Pro năm chỉ $79 thay vì $119.88 (12 × $9.99). Thanh toán một lần, không phiền phức.',
  },
  {
    q: 'VidGrab hỗ trợ những nền tảng nào?',
    a: 'YouTube, TikTok, Instagram, Facebook, Twitter/X, SoundCloud và hơn 50 nền tảng khác. Xem danh sách đầy đủ tại trang Platforms.',
  },
  {
    q: 'API Partner có thể tích hợp vào hệ thống của tôi như thế nào?',
    a: 'Gói API Partner cấp Partner key riêng, hỗ trợ HMAC Webhook để nhận thông báo download hoàn thành. Xem tài liệu chi tiết tại trang API Docs.',
  },
  {
    q: 'Nếu tôi vượt giới hạn download trong ngày thì sao?',
    a: 'Yêu cầu vượt hạn ngạch sẽ bị từ chối với thông báo rõ ràng. Giới hạn tự reset vào 00:00 UTC mỗi ngày. Bạn có thể nâng cấp gói bất cứ lúc nào để tăng giới hạn ngay lập tức.',
  },
];

// ── Sub-components ────────────────────────────────────────────────────────────

function PeriodToggle({ period, onChange }) {
  return (
    <div className="flex items-center justify-center gap-3">
      <span className={`text-sm font-medium transition-colors ${period === 'monthly' ? 'text-white' : 'text-white/40'}`}>
        Tháng
      </span>

      <button
        onClick={() => onChange(period === 'monthly' ? 'yearly' : 'monthly')}
        aria-label="Chuyển đổi chu kỳ thanh toán"
        className={`relative w-12 h-6 rounded-full transition-colors duration-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 ${
          period === 'yearly' ? 'bg-emerald-600' : 'bg-white/20'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform duration-300 ${
            period === 'yearly' ? 'translate-x-6' : 'translate-x-0'
          }`}
        />
      </button>

      <span className={`text-sm font-medium transition-colors ${period === 'yearly' ? 'text-white' : 'text-white/40'}`}>
        Năm
      </span>

      {period === 'yearly' && (
        <span className="text-xs font-semibold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 px-2 py-0.5 rounded-full">
          Tiết kiệm 34%
        </span>
      )}
    </div>
  );
}

function FeatureRow({ label, included }) {
  return (
    <li className="flex items-start gap-2.5 text-sm">
      {included ? (
        <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
      ) : (
        <X className="w-4 h-4 text-white/25 shrink-0 mt-0.5" />
      )}
      <span className={included ? 'text-white/80' : 'text-white/30 line-through decoration-white/20'}>
        {label}
      </span>
    </li>
  );
}

function PriceDisplay({ plan, period }) {
  const price = period === 'yearly' ? plan.yearlyPrice : plan.monthlyPrice;
  const suffix = period === 'yearly' ? '/năm' : '/tháng';

  if (price === 0) {
    return (
      <div className="mt-4 mb-1">
        <span className="text-4xl font-extrabold text-white">$0</span>
        <span className="text-white/40 text-sm ml-1">mãi mãi</span>
      </div>
    );
  }

  return (
    <div className="mt-4 mb-1">
      <span className="text-4xl font-extrabold text-white">${price}</span>
      <span className="text-white/40 text-sm ml-1">{suffix}</span>
      {plan.priceSuffix && (
        <span className="block text-xs text-white/30 mt-0.5">{plan.priceSuffix}</span>
      )}
    </div>
  );
}

function PlanCard({ plan, period, onCheckout }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleCta = async () => {
    setError('');
    if (plan.id === 'free') {
      onCheckout(null);
      return;
    }
    setLoading(true);
    try {
      await onCheckout(plan.id, period);
    } catch (err) {
      setError(err.message || 'Có lỗi xảy ra. Vui lòng thử lại.');
    } finally {
      setLoading(false);
    }
  };

  const isFeatured = plan.featured;

  return (
    <div
      className={`relative flex flex-col rounded-2xl p-6 transition-all duration-200 ${
        isFeatured
          ? 'bg-[#0d2e29] border border-emerald-500/50 shadow-[0_0_32px_rgba(16,185,129,0.12)] lg:scale-105 lg:-translate-y-1 z-10'
          : 'bg-[#0d2e29] border border-white/10 hover:border-white/20'
      }`}
    >
      {isFeatured && (
        <div className="absolute -top-3 left-1/2 -translate-x-1/2">
          <span className="bg-emerald-500 text-white text-xs font-bold px-3 py-1 rounded-full shadow-lg whitespace-nowrap">
            Phổ biến nhất
          </span>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-2.5">
        <span className={`p-2 rounded-lg ${isFeatured ? 'bg-emerald-500/20 text-emerald-400' : 'bg-white/10 text-white/60'}`}>
          {plan.icon}
        </span>
        <h3 className="text-lg font-bold text-white">{plan.name}</h3>
      </div>

      {/* Price */}
      <PriceDisplay plan={plan} period={period} />

      {/* Description */}
      <p className="text-white/50 text-sm mb-5 leading-relaxed">{plan.description}</p>

      {/* CTA */}
      <button
        onClick={handleCta}
        disabled={loading}
        className={`w-full py-2.5 px-4 rounded-xl text-sm font-semibold flex items-center justify-center gap-2 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 disabled:opacity-60 disabled:cursor-not-allowed ${
          isFeatured
            ? 'bg-emerald-500 hover:bg-emerald-400 text-white shadow-lg shadow-emerald-500/20'
            : 'bg-white/10 hover:bg-white/20 text-white border border-white/10'
        }`}
        aria-label={`${plan.cta} — gói ${plan.name}`}
      >
        {loading ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            Đang xử lý...
          </>
        ) : (
          plan.cta
        )}
      </button>

      {error && (
        <p className="mt-2 text-xs text-red-400 text-center leading-snug">{error}</p>
      )}

      {/* Divider */}
      <hr className="border-white/10 my-5" />

      {/* Features */}
      <ul className="space-y-2.5 flex-1">
        {plan.features.map((f) => (
          <FeatureRow key={f.label} label={f.label} included={f.included} />
        ))}
      </ul>
    </div>
  );
}

function FaqItem({ q, a }) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border-b border-white/10 last:border-b-0">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between gap-4 py-4 text-left text-white/80 hover:text-white transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 rounded"
        aria-expanded={open}
      >
        <span className="font-medium text-sm sm:text-base">{q}</span>
        <ChevronDown
          className={`w-5 h-5 text-white/40 shrink-0 transition-transform duration-300 ${open ? 'rotate-180' : ''}`}
        />
      </button>

      <div
        className={`overflow-hidden transition-all duration-300 ease-in-out ${
          open ? 'max-h-64 opacity-100 pb-4' : 'max-h-0 opacity-0'
        }`}
      >
        <p className="text-sm text-white/50 leading-relaxed">{a}</p>
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function PricingPage({ onNavigate }) {
  const [period, setPeriod] = useState('monthly');

  const _nav = (view, path) => {
    if (onNavigate) onNavigate(view, path);
    else window.history.pushState({}, '', path);
  };

  const handleCheckout = async (planId, billingPeriod) => {
    // Free tier → go home
    if (!planId) {
      _nav('landing', '/');
      return;
    }

    // Get Supabase token
    const { data: { session } } = await supabase.auth.getSession();
    const token = session?.access_token;

    const res = await fetch(`${API}/payments/create-checkout-session-v2`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({
        plan: planId,
        period: billingPeriod,
        success_url: `${window.location.origin}/billing?success=1`,
        cancel_url: `${window.location.origin}/pricing`,
      }),
    });

    if (res.status === 503) {
      throw new Error('Hệ thống thanh toán chưa được cấu hình. Vui lòng liên hệ hỗ trợ.');
    }

    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || body.message || 'Không thể tạo phiên thanh toán.');
    }

    const { checkout_url } = await res.json();
    if (!checkout_url) throw new Error('Không nhận được URL thanh toán.');
    window.location.href = checkout_url;
  };

  return (
    <div className="min-h-screen bg-[#071a16] text-white">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-16 sm:py-24">

        {/* Hero */}
        <div className="text-center mb-12">
          <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight mb-4">
            Chọn gói phù hợp với bạn
          </h1>
          <p className="text-white/50 text-base sm:text-lg max-w-xl mx-auto">
            Bắt đầu miễn phí. Nâng cấp khi cần. Huỷ bất cứ lúc nào.
          </p>
        </div>

        {/* Period toggle */}
        <div className="flex justify-center mb-12">
          <PeriodToggle period={period} onChange={setPeriod} />
        </div>

        {/* Plan cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 items-start">
          {PLANS.map((plan) => (
            <PlanCard
              key={plan.id}
              plan={plan}
              period={period}
              onCheckout={handleCheckout}
            />
          ))}
        </div>

        {/* Enterprise strip */}
        <div className="mt-10 rounded-2xl border border-white/10 bg-[#0d2e29] px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-6">
          <div>
            <h3 className="text-lg font-bold text-white mb-1">Enterprise</h3>
            <p className="text-white/50 text-sm max-w-md">
              Dung lượng không giới hạn, SLA tuỳ chỉnh, hỗ trợ kỹ thuật chuyên biệt, triển khai
              riêng (on-premise hoặc cloud). Hãy nói cho chúng tôi biết nhu cầu của bạn.
            </p>
          </div>
          <a
            href="mailto:support@vidgrab.app?subject=Enterprise%20Inquiry"
            className="shrink-0 px-6 py-3 rounded-xl bg-white/10 hover:bg-white/20 border border-white/10 text-white text-sm font-semibold transition-colors whitespace-nowrap focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500"
          >
            Liên hệ đội ngũ
          </a>
        </div>

        {/* FAQ */}
        <section className="mt-20" aria-labelledby="faq-heading">
          <h2 id="faq-heading" className="text-2xl font-bold text-center mb-8">
            Câu hỏi thường gặp
          </h2>
          <div className="max-w-2xl mx-auto bg-[#0d2e29] border border-white/10 rounded-2xl px-6 divide-y-0">
            {FAQS.map((faq) => (
              <FaqItem key={faq.q} q={faq.q} a={faq.a} />
            ))}
          </div>
        </section>

        {/* Footer note */}
        <p className="text-center text-white/25 text-xs mt-12">
          Giá hiển thị bằng USD. Thuế (nếu có) sẽ được tính thêm lúc thanh toán.
          Bảo mật thẻ bởi Stripe — VidGrab không lưu thông tin thẻ.
        </p>
      </div>
    </div>
  );
}
