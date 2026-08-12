import {
  Puzzle,
  Download,
  FolderOpen,
  MousePointerClick,
  AlertTriangle,
  Send,
  ArrowRight,
} from 'lucide-react';

// ── Constants ────────────────────────────────────────────────
const API_BASE = import.meta.env.VITE_API_URL || '';
const TELEGRAM_BOT_URL = 'https://t.me/dowloadextension_bot';

// ── Platform SVG Icons (inline — no extra deps) ──────────────
const TikTokIcon = () => (
  <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
    <path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1v-3.5a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.15 15.2a6.34 6.34 0 0010.86 4.46V13.2a8.16 8.16 0 005.58 2.2v-3.45a4.85 4.85 0 01-3.77-1.49V6.69h3.77z" />
  </svg>
);
const YouTubeIcon = () => (
  <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
    <path d="M23.5 6.19a3.02 3.02 0 00-2.12-2.14C19.54 3.5 12 3.5 12 3.5s-7.54 0-9.38.55A3.02 3.02 0 00.5 6.19 31.6 31.6 0 000 12a31.6 31.6 0 00.5 5.81 3.02 3.02 0 002.12 2.14c1.84.55 9.38.55 9.38.55s7.54 0 9.38-.55a3.02 3.02 0 002.12-2.14A31.6 31.6 0 0024 12a31.6 31.6 0 00-.5-5.81zM9.55 15.57V8.43L15.82 12l-6.27 3.57z" />
  </svg>
);
const FacebookIcon = () => (
  <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
    <path d="M24 12.07C24 5.41 18.63 0 12 0S0 5.41 0 12.07c0 6.02 4.39 11.01 10.13 11.93v-8.44H7.08v-3.49h3.04V9.41c0-3.02 1.79-4.69 4.53-4.69 1.31 0 2.69.24 2.69.24v2.97h-1.51c-1.49 0-1.96.93-1.96 1.89v2.26h3.33l-.53 3.49h-2.8v8.44C19.61 23.08 24 18.09 24 12.07z" />
  </svg>
);
const InstagramIcon = () => (
  <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
    <path d="M12 2.16c3.2 0 3.58.01 4.85.07 3.25.15 4.77 1.69 4.92 4.92.06 1.27.07 1.65.07 4.85 0 3.2-.01 3.58-.07 4.85-.15 3.23-1.66 4.77-4.92 4.92-1.27.06-1.65.07-4.85.07-3.2 0-3.58-.01-4.85-.07-3.26-.15-4.77-1.7-4.92-4.92-.06-1.27-.07-1.65-.07-4.85 0-3.2.01-3.58.07-4.85C2.38 3.86 3.9 2.31 7.15 2.23 8.42 2.17 8.8 2.16 12 2.16zM12 0C8.74 0 8.33.01 7.05.07 2.7.27.27 2.7.07 7.05.01 8.33 0 8.74 0 12s.01 3.67.07 4.95c.2 4.36 2.62 6.78 6.98 6.98C8.33 23.99 8.74 24 12 24s3.67-.01 4.95-.07c4.35-.2 6.78-2.62 6.98-6.98.06-1.28.07-1.69.07-4.95s-.01-3.67-.07-4.95c-.2-4.35-2.63-6.78-6.98-6.98C15.67.01 15.26 0 12 0zm0 5.84A6.16 6.16 0 1018.16 12 6.16 6.16 0 0012 5.84zM12 16a4 4 0 110-8 4 4 0 010 8zm6.41-11.85a1.44 1.44 0 100 2.88 1.44 1.44 0 000-2.88z" />
  </svg>
);
const DouyinIcon = () => (
  <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
    <path d="M19.59 6.69a4.83 4.83 0 01-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 01-2.88 2.5 2.89 2.89 0 01-2.89-2.89 2.89 2.89 0 012.89-2.89c.28 0 .54.04.79.1v-3.5a6.37 6.37 0 00-.79-.05A6.34 6.34 0 003.15 15.2a6.34 6.34 0 0010.86 4.46V13.2a8.16 8.16 0 005.58 2.2v-3.45a4.85 4.85 0 01-3.77-1.49V6.69h3.77z" />
  </svg>
);
const SpotifyIcon = () => (
  <svg viewBox="0 0 24 24" className="w-4 h-4" fill="currentColor">
    <path d="M12 0C5.4 0 0 5.4 0 12s5.4 12 12 12 12-5.4 12-12S18.66 0 12 0zm5.521 17.34c-.24.359-.66.48-1.021.24-2.82-1.74-6.36-2.101-10.561-1.141-.418.122-.779-.179-.899-.539-.12-.421.18-.78.54-.9 4.56-1.021 8.52-.6 11.64 1.32.42.18.479.659.301 1.02zm1.44-3.3c-.301.42-.84.6-1.262.3-3.239-1.98-8.159-2.58-11.939-1.38-.479.12-1.02-.12-1.14-.6-.12-.48.12-1.02.6-1.141C9.6 9.9 15 10.561 18.72 12.84c.361.181.54.78.241 1.2zm.12-3.36C15.24 8.4 8.82 8.16 5.16 9.301c-.6.179-1.2-.181-1.38-.721-.18-.6.18-1.2.72-1.38 4.26-1.26 11.28-1.02 15.721 1.621.539.3.719 1.02.419 1.56-.239.54-.959.72-1.56.3z" />
  </svg>
);

// ── Feature chip data ────────────────────────────────────────
const platforms = [
  { icon: TikTokIcon, label: 'TikTok',    color: 'text-[#69C9D0] border-[#69C9D0]/30 bg-[#69C9D0]/10' },
  { icon: YouTubeIcon, label: 'YouTube',  color: 'text-[#FF0000] border-[#FF0000]/30 bg-[#FF0000]/10' },
  { icon: FacebookIcon, label: 'Facebook', color: 'text-[#1877F2] border-[#1877F2]/30 bg-[#1877F2]/10' },
  { icon: InstagramIcon, label: 'Instagram', color: 'text-[#E1306C] border-[#E1306C]/30 bg-[#E1306C]/10' },
  { icon: DouyinIcon, label: 'Douyin',    color: 'text-[#ff0050] border-[#ff0050]/30 bg-[#ff0050]/10' },
  { icon: SpotifyIcon, label: 'Spotify',  color: 'text-[#1DB954] border-[#1DB954]/30 bg-[#1DB954]/10' },
];

// ── Install step data ────────────────────────────────────────
const steps = [
  {
    num: 1,
    icon: Download,
    title: 'Tải file ZIP',
    desc: 'Nhấn nút "Tải Extension" ở trên để tải file ZIP về máy.',
  },
  {
    num: 2,
    icon: FolderOpen,
    title: 'Mở Extensions',
    desc: 'Vào chrome://extensions trên thanh địa chỉ, sau đó bật Developer mode.',
  },
  {
    num: 3,
    icon: MousePointerClick,
    title: 'Load Extension',
    desc: 'Kéo thả file ZIP vào trang extensions hoặc nhấn "Load unpacked".',
  },
];

// ── Component ────────────────────────────────────────────────
export default function ExtensionPage() {
  return (
    <div className="min-h-screen pb-28 md:pb-24">
      <div className="relative z-10 w-full max-w-3xl mx-auto px-4 sm:px-6 pt-20 md:pt-28 flex flex-col items-center gap-12 md:gap-16">

        {/* ── Hero ─────────────────────────────────────────── */}
        <section className="w-full flex flex-col items-center text-center gap-6">
          {/* Icon badge */}
          <div className="w-20 h-20 md:w-24 md:h-24 rounded-3xl bg-gradient-to-br from-[#FBBF24] to-[#FB923C] flex items-center justify-center shadow-2xl shadow-[#FBBF24]/30">
            <Puzzle className="w-10 h-10 md:w-12 md:h-12 text-[#012622]" strokeWidth={2.5} />
          </div>

          {/* Heading */}
          <div className="flex flex-col items-center gap-3">
            <h1 className="text-3xl sm:text-4xl md:text-5xl font-black tracking-tight text-white leading-tight">
              Cài{' '}
              <span className="bg-gradient-to-r from-[#FBBF24] to-[#FB923C] bg-clip-text text-transparent">
                VidGrab
              </span>{' '}
              Extension
            </h1>
            <p className="max-w-lg text-sm sm:text-base text-slate-300 font-medium leading-relaxed">
              Tải video 1 click ngay trên trang TikTok, YouTube, Facebook và nhiều nền tảng khác — không cần copy link.
            </p>
          </div>

          {/* CTA buttons */}
          <div className="flex flex-col sm:flex-row gap-3 w-full max-w-sm sm:max-w-lg">
            <a
              href={`${API_BASE}/api/v1/extension/download`}
              download
              className="flex-1 inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-2xl bg-gradient-to-r from-[#FBBF24] to-[#FB923C] text-[#012622] font-extrabold text-sm shadow-lg shadow-[#FBBF24]/25 hover:opacity-90 hover:scale-[1.02] active:scale-[0.98] transition-all duration-200 cursor-pointer"
            >
              <Download className="w-4 h-4 flex-shrink-0" />
              Tải Extension (.ZIP)
            </a>
            <a
              href={TELEGRAM_BOT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-1 inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-2xl bg-[#012622]/60 border border-slate-600/60 text-white font-bold text-sm hover:border-[#FBBF24]/50 hover:bg-[#012622]/80 hover:scale-[1.02] active:scale-[0.98] transition-all duration-200 cursor-pointer backdrop-blur-sm"
            >
              <Send className="w-4 h-4 flex-shrink-0" />
              Nhận qua Telegram
            </a>
          </div>
        </section>

        {/* ── Platform chips ────────────────────────────────── */}
        <section className="w-full" aria-label="Nền tảng hỗ trợ">
          <p className="text-center text-xs font-semibold text-slate-500 uppercase tracking-widest mb-4">
            Hỗ trợ tải từ
          </p>
          {/* Negative mx to let chips bleed on mobile for scroll feel */}
          <div className="relative sm:overflow-visible overflow-hidden -mx-4 sm:mx-0">
            <div
              className="flex gap-2.5 overflow-x-auto pb-2 px-4 sm:px-0 sm:flex-wrap sm:justify-center"
              style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
              aria-label="Danh sách nền tảng"
            >
              {platforms.map(({ icon: Icon, label, color }) => (
                <span
                  key={label}
                  className={`inline-flex items-center gap-2 px-4 py-2 rounded-full border text-sm font-semibold whitespace-nowrap flex-shrink-0 ${color}`}
                >
                  <Icon />
                  {label}
                </span>
              ))}
            </div>
            {/* Fade hint on right edge — mobile only */}
            <div className="sm:hidden absolute right-0 top-0 bottom-2 w-10 bg-gradient-to-l from-[#012622] to-transparent pointer-events-none" />
          </div>
        </section>

        {/* ── Install guide ─────────────────────────────────── */}
        <section className="w-full" aria-labelledby="install-heading">
          <h2
            id="install-heading"
            className="text-center text-xl sm:text-2xl font-black text-white mb-8"
          >
            Hướng dẫn cài đặt
          </h2>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 md:gap-6">
            {steps.map(({ num, icon: Icon, title, desc }) => (
              <div
                key={num}
                className="relative flex flex-col gap-4 p-6 rounded-2xl bg-white/5 border border-slate-700/50 backdrop-blur-sm hover:border-[#FBBF24]/30 transition-colors duration-300"
              >
                {/* Number badge */}
                <div className="absolute -top-3.5 -left-3.5 w-8 h-8 rounded-xl bg-gradient-to-br from-[#FBBF24] to-[#FB923C] text-[#012622] font-black text-sm flex items-center justify-center shadow-md shadow-[#FBBF24]/30 select-none">
                  {num}
                </div>

                {/* Step icon */}
                <div className="w-10 h-10 rounded-xl bg-[#FBBF24]/10 border border-[#FBBF24]/20 flex items-center justify-center">
                  <Icon className="w-5 h-5 text-[#FBBF24]" />
                </div>

                {/* Text */}
                <div>
                  <h3 className="font-bold text-white text-base mb-1">{title}</h3>
                  <p className="text-sm text-slate-400 leading-relaxed">{desc}</p>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* ── Warning note ──────────────────────────────────── */}
        <div
          className="w-full flex items-start gap-3 p-4 rounded-2xl bg-amber-500/10 border border-amber-500/25"
          role="note"
          aria-label="Lưu ý quan trọng"
        >
          <AlertTriangle className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-amber-200 leading-relaxed">
            <span className="font-bold">Lưu ý: </span>
            Cần bật <span className="font-mono font-semibold text-amber-300">Developer mode</span> trong{' '}
            <span className="font-mono font-semibold text-amber-300">chrome://extensions</span> trước khi cài extension thủ công.
          </p>
        </div>

        {/* ── Telegram CTA ──────────────────────────────────── */}
        <section
          className="w-full"
          aria-labelledby="telegram-heading"
        >
          <div className="flex flex-col items-center gap-5 p-8 md:p-10 rounded-3xl bg-white/5 border border-slate-700/50 text-center backdrop-blur-sm">
            {/* Bot icon */}
            <div className="w-14 h-14 rounded-2xl bg-[#229ED9]/10 border border-[#229ED9]/25 flex items-center justify-center">
              <Send className="w-7 h-7 text-[#229ED9]" />
            </div>

            <div className="flex flex-col gap-2">
              <h2
                id="telegram-heading"
                className="text-xl sm:text-2xl font-black text-white"
              >
                Nhận file qua Telegram Bot
              </h2>
              <p className="text-sm sm:text-base text-slate-400 max-w-sm mx-auto leading-relaxed">
                Nhận bất kỳ nội dung → bot tự gửi file ZIP + hướng dẫn cài đặt chi tiết.
              </p>
            </div>

            <a
              href={TELEGRAM_BOT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-7 py-3.5 rounded-2xl bg-[#229ED9] text-white font-extrabold text-sm hover:bg-[#1a8fc4] hover:scale-[1.03] active:scale-[0.98] transition-all duration-200 shadow-lg shadow-[#229ED9]/25 cursor-pointer"
            >
              Mở Telegram Bot
              <ArrowRight className="w-4 h-4" />
            </a>
          </div>
        </section>

      </div>
    </div>
  );
}
