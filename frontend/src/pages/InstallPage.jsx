import { useState } from 'react';
import { Download, AppWindow as Chrome, CheckCircle, ChevronRight, Zap, Shield, RefreshCw, ChevronDown, ChevronUp } from 'lucide-react';

const EXT_VERSION = 'v5.1.0';

const STEPS = [
  { n: 1, title: 'Tải file ZIP', desc: 'Tải extension về máy tính', hasBtn: true },
  { n: 2, title: 'Giải nén file ZIP', desc: 'Chuột phải → Extract All (Windows) hoặc double-click (Mac)' },
  { n: 3, title: 'Mở trang Extensions', desc: 'Gõ chrome://extensions vào thanh địa chỉ, nhấn Enter' },
  { n: 4, title: 'Bật Developer Mode', desc: 'Bật toggle "Developer mode" ở góc trên bên phải' },
  { n: 5, title: 'Load extension', desc: 'Nhấn "Load unpacked" và chọn folder vừa giải nén' },
  { n: 6, title: 'Xong!', desc: 'VidGrab icon xuất hiện trên thanh công cụ trình duyệt ✅' },
];

const FAQ = [
  {
    q: 'Tại sao cần bật Developer Mode?',
    a: 'Extensions chưa trên Chrome Web Store cần Developer Mode để cài thủ công. Đây là tính năng chính thức của Chrome — không có rủi ro bảo mật nào.',
  },
  {
    q: 'Extension có an toàn không?',
    a: 'VidGrab extension không thu thập dữ liệu cá nhân, không theo dõi lịch sử duyệt web, và chỉ kích hoạt khi bạn nhấn nút download.',
  },
  {
    q: 'Extension cần những quyền gì?',
    a: 'activeTab (đọc URL trang hiện tại khi nhấn nút), storage (lưu cài đặt cục bộ), scripting (phát hiện link video). Không có quyền nào hoạt động nền.',
  },
  {
    q: 'Cách cập nhật extension?',
    a: 'Tải lại file ZIP mới từ trang này, giải nén đè lên folder cũ, vào chrome://extensions nhấn nút refresh 🔄 ở card VidGrab.',
  },
  {
    q: 'Dùng được trên Edge và Brave không?',
    a: 'Có! Các bước tương tự. Trên Edge: edge://extensions. Trên Brave: brave://extensions.',
  },
];

const CHANGELOG = [
  { v: 'v5.1.0', notes: '11 nền tảng mới (Bilibili, VK, Twitch, Rumble, Odysee). Cập nhật Manifest V3 đầy đủ.' },
  { v: 'v4.9.5', notes: 'Cải thiện tốc độ tải YouTube 40%. Sửa lỗi cookies. Hỗ trợ 4K.' },
];

export default function InstallPage() {
  const [openFaq, setOpenFaq] = useState(null);
  const [downloading, setDownloading] = useState(false);

  const handleDownload = () => {
    setDownloading(true);
    // The extension zip is served by the backend (app/api/routes.py
    // GET /extension/download), which can be a different domain than the
    // frontend in split deployments — a bare relative path 404s there.
    const apiBase = import.meta.env.VITE_API_URL ?? '';
    window.open(`${apiBase}/extension/download`, '_blank');
    setTimeout(() => setDownloading(false), 3000);
  };

  const navigate = (path) => {
    window.history.pushState({}, '', path);
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  return (
    <div className="min-h-screen bg-[#012622] text-white">
      <div className="max-w-2xl mx-auto px-4 py-8 sm:py-12">

        {/* Hero */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center gap-2 bg-indigo-500/10 border border-indigo-500/30 text-indigo-300 text-xs font-medium px-3 py-1 rounded-full mb-4">
            <Zap className="w-3 h-3" />
            Chrome Extension {EXT_VERSION}
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold mb-3">Tải video 1 chạm</h1>
          <p className="text-white/50 text-base max-w-md mx-auto">
            Right-click bất kỳ link video → Download ngay. Không cần copy, không cần chuyển tab.
          </p>
        </div>

        {/* Two paths */}
        <div className="grid grid-cols-2 gap-3 mb-8">
          <div className="bg-white/5 border border-white/10 rounded-xl p-4 opacity-50">
            <div className="flex items-center gap-2 mb-2">
              <Chrome className="w-5 h-5 text-white/40" />
              <span className="text-sm font-semibold text-white/60">Chrome Web Store</span>
            </div>
            <span className="text-[11px] bg-yellow-500/20 text-yellow-400 border border-yellow-500/30 px-2 py-0.5 rounded-full">Đang xét duyệt</span>
            <p className="text-xs text-white/30 mt-2 leading-relaxed">Sẽ ra mắt sớm — cài 1 click, tự cập nhật.</p>
          </div>
          <div className="bg-indigo-600/10 border border-indigo-500/40 rounded-xl p-4">
            <div className="flex items-center gap-2 mb-2">
              <Download className="w-5 h-5 text-indigo-400" />
              <span className="text-sm font-semibold text-white">Cài thủ công</span>
            </div>
            <span className="text-[11px] bg-green-500/20 text-green-400 border border-green-500/30 px-2 py-0.5 rounded-full">Có ngay</span>
            <p className="text-xs text-white/50 mt-2 leading-relaxed">6 bước đơn giản, không cần tài khoản.</p>
          </div>
        </div>

        {/* Steps */}
        <div className="mb-8">
          <h2 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-4">Hướng dẫn cài đặt</h2>
          <div className="space-y-4">
            {STEPS.map((step) => (
              <div key={step.n} className="flex gap-4 items-start">
                <div className="w-7 h-7 rounded-full bg-indigo-600 flex items-center justify-center text-xs font-bold text-white flex-shrink-0 mt-0.5">
                  {step.n}
                </div>
                <div className="flex-1">
                  <p className="text-sm font-semibold text-white">{step.title}</p>
                  <p className="text-xs text-white/40 mt-0.5 leading-relaxed">{step.desc}</p>
                  {step.hasBtn && (
                    <button
                      onClick={handleDownload}
                      className="mt-2.5 flex items-center gap-2 bg-indigo-600 hover:bg-indigo-700 text-white text-xs font-semibold px-4 py-2 rounded-lg transition-colors"
                    >
                      <Download className="w-3.5 h-3.5" />
                      {downloading ? 'Đang tải...' : `Tải VidGrab-extension.zip (${EXT_VERSION})`}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Permissions */}
        <div className="bg-white/5 border border-white/10 rounded-xl p-4 mb-8">
          <div className="flex items-center gap-2 mb-3">
            <Shield className="w-4 h-4 text-green-400" />
            <span className="text-sm font-semibold text-white">Quyền truy cập an toàn</span>
          </div>
          <div className="space-y-2">
            {[
              { p: 'activeTab', d: 'Đọc URL trang hiện tại khi bạn nhấn nút download' },
              { p: 'storage', d: 'Lưu cài đặt của bạn trên máy (chất lượng, định dạng)' },
              { p: 'scripting', d: 'Phát hiện link video trên trang bạn đang xem' },
            ].map(({ p, d }) => (
              <div key={p} className="flex items-start gap-2">
                <CheckCircle className="w-3.5 h-3.5 text-green-400 flex-shrink-0 mt-0.5" />
                <span className="text-xs text-white/60">
                  <strong className="text-white/80">{p}</strong> — {d}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Changelog */}
        <div className="mb-8">
          <div className="flex items-center gap-2 mb-3">
            <RefreshCw className="w-3.5 h-3.5 text-white/30" />
            <span className="text-xs font-semibold text-white/40 uppercase tracking-wider">Lịch sử cập nhật</span>
          </div>
          <div className="space-y-2">
            {CHANGELOG.map(({ v, notes }) => (
              <div key={v} className="flex gap-3 items-start">
                <span className="text-xs font-mono bg-white/10 text-white/60 px-2 py-0.5 rounded flex-shrink-0">{v}</span>
                <span className="text-xs text-white/40 leading-relaxed">{notes}</span>
              </div>
            ))}
          </div>
        </div>

        {/* FAQ */}
        <div className="mb-10">
          <h2 className="text-sm font-semibold text-white/60 uppercase tracking-wider mb-3">Câu hỏi thường gặp</h2>
          <div className="space-y-2">
            {FAQ.map((item, i) => (
              <div key={i} className="bg-white/5 border border-white/10 rounded-xl overflow-hidden">
                <button
                  className="w-full flex items-center justify-between px-4 py-3 text-left"
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                >
                  <span className="text-sm font-medium text-white/80 pr-4">{item.q}</span>
                  {openFaq === i
                    ? <ChevronUp className="w-4 h-4 text-white/30 flex-shrink-0" />
                    : <ChevronDown className="w-4 h-4 text-white/30 flex-shrink-0" />}
                </button>
                {openFaq === i && (
                  <div className="px-4 pb-3 text-xs text-white/50 leading-relaxed border-t border-white/5 pt-2">
                    {item.a}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Bottom CTA */}
        <div className="text-center border-t border-white/10 pt-6">
          <p className="text-sm text-white/40 mb-3">Muốn dùng ngay mà không cần cài đặt?</p>
          <button
            onClick={() => navigate('/')}
            className="inline-flex items-center gap-2 text-sm text-indigo-400 hover:text-indigo-300 transition-colors font-medium"
          >
            Thử web app ngay <ChevronRight className="w-4 h-4" />
          </button>
        </div>

      </div>
    </div>
  );
}
