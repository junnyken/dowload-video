import { useState } from 'react';
import {
  Puzzle,
  Download,
  Copy,
  Check,
  Send,
  ArrowRight,
  Info,
} from 'lucide-react';
import { trackEvent, EVENT } from '../utils/trackEvent';

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
  { icon: TikTokIcon, label: 'TikTok',    color: 'text-fg-2 border-line bg-surface-2', ic: 'text-[#69C9D0]' },
  { icon: YouTubeIcon, label: 'YouTube',  color: 'text-fg-2 border-line bg-surface-2', ic: 'text-[#FF0000]' },
  { icon: FacebookIcon, label: 'Facebook', color: 'text-fg-2 border-line bg-surface-2', ic: 'text-[#1877F2]' },
  { icon: InstagramIcon, label: 'Instagram', color: 'text-fg-2 border-line bg-surface-2', ic: 'text-[#E1306C]' },
  { icon: DouyinIcon, label: 'Douyin',    color: 'text-fg-2 border-line bg-surface-2', ic: 'text-[#ff0050]' },
  { icon: SpotifyIcon, label: 'Spotify',  color: 'text-fg-2 border-line bg-surface-2', ic: 'text-[#1DB954]' },
];

// ── Install guide ────────────────────────────────────────────
const EXT_VERSION = '5.2.4';

// Copyable mono chip — chrome:// links can't be opened from a web page.
function CopyChip({ text }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand('copy'); } catch { /* ignore */ }
      document.body.removeChild(ta);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };
  return (
    <span className="inline-flex items-center gap-1 bg-surface border border-line rounded-lg pl-3 pr-1 py-1">
      <code className="font-mono text-sm text-fg">{text}</code>
      <button
        type="button"
        onClick={copy}
        aria-label={`Sao chép ${text}`}
        className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-semibold text-accent-text hover:bg-accent-soft transition-colors"
      >
        {copied ? <Check className="w-3.5 h-3.5" /> : <Copy className="w-3.5 h-3.5" />}
        {copied ? 'Đã chép' : 'Sao chép'}
      </button>
    </span>
  );
}

const Code = ({ children }) => (
  <code className="font-mono text-[0.85em] bg-surface border border-line rounded px-1.5 py-0.5 text-fg">{children}</code>
);

const steps = [
  {
    title: 'Tải file ZIP',
    body: (
      <>
        <p>Nhấn nút bên dưới để tải file ZIP (phiên bản {EXT_VERSION}) về máy.</p>
        <a
          href={`${API_BASE}/api/v1/extension/download`}
          onClick={() => trackEvent(EVENT.EXTENSION_INSTALL_CLICK, { from: 'extension_guide' })}
          download
          className="mt-3 inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-accent text-accent-fg font-bold text-sm hover:bg-accent-hover transition-colors"
        >
          <Download className="w-4 h-4" />
          Tải Extension (.ZIP)
        </a>
      </>
    ),
  },
  {
    title: 'Giải nén',
    body: <p>Giải nén file ZIP ra một thư mục cố định. Đừng xoá thư mục này, trình duyệt cần nó để chạy extension.</p>,
  },
  {
    title: 'Mở trang tiện ích',
    body: (
      <>
        <p className="mb-2">Dán địa chỉ này vào thanh địa chỉ của trình duyệt rồi nhấn Enter:</p>
        <CopyChip text="chrome://extensions" />
      </>
    ),
  },
  {
    title: 'Bật chế độ nhà phát triển',
    body: <p>Bật <strong className="text-fg">Chế độ dành cho nhà phát triển</strong> (Developer mode) ở góc trên bên phải.</p>,
  },
  {
    title: 'Tải tiện ích đã giải nén',
    body: <p>Nhấn <strong className="text-fg">Tải tiện ích đã giải nén</strong> (Load unpacked), rồi chọn thư mục vừa giải nén.</p>,
  },
  {
    title: 'Ghim extension',
    body: <p>Nhấn biểu tượng mảnh ghép trên thanh công cụ, rồi nhấn biểu tượng ghim cạnh VidGrab.</p>,
  },
  {
    title: 'Cập nhật phiên bản mới',
    body: <p>Tải file ZIP mới, giải nén đè lên thư mục cũ, rồi nhấn nút tải lại (mũi tên vòng) ở thẻ VidGrab trong trang <Code>chrome://extensions</Code>.</p>,
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
          <div className="w-20 h-20 md:w-24 md:h-24 rounded-3xl bg-accent flex items-center justify-center shadow-2xl">
            <Puzzle className="w-10 h-10 md:w-12 md:h-12 text-accent-fg" strokeWidth={2.5} />
          </div>

          {/* Heading */}
          <div className="flex flex-col items-center gap-3">
            <h1 className="text-3xl sm:text-4xl md:text-5xl font-black tracking-tight text-fg leading-tight">
              Cài{' '}
              <span className=" text-accent-text">
                VidGrab
              </span>{' '}
              Extension
            </h1>
            <p className="max-w-lg text-sm sm:text-base text-fg-2 font-medium leading-relaxed">
              Tải video 1 click ngay trên trang TikTok, YouTube, Facebook và nhiều nền tảng khác — không cần copy link.
            </p>
          </div>

          {/* CTA buttons */}
          <div className="flex flex-col sm:flex-row gap-3 w-full max-w-sm sm:max-w-lg">
            <a
              href={`${API_BASE}/api/v1/extension/download`}
              onClick={() => trackEvent(EVENT.EXTENSION_INSTALL_CLICK, { from: 'extension_page' })}
              download
              className="flex-1 inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-2xl bg-accent text-accent-fg font-extrabold text-sm shadow-lg hover:opacity-90 hover:scale-[1.02] active:scale-[0.98] transition-all duration-200 cursor-pointer"
            >
              <Download className="w-4 h-4 flex-shrink-0" />
              Tải Extension (.ZIP)
            </a>
            <a
              href={TELEGRAM_BOT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="flex-1 inline-flex items-center justify-center gap-2 px-6 py-3.5 rounded-2xl bg-surface/60 border border-line-strong text-fg font-bold text-sm hover:border-accent/50 hover:bg-surface/80 hover:scale-[1.02] active:scale-[0.98] transition-all duration-200 cursor-pointer backdrop-blur-sm"
            >
              <Send className="w-4 h-4 flex-shrink-0" />
              Nhận qua Telegram
            </a>
          </div>
        </section>

        {/* ── Platform chips ────────────────────────────────── */}
        <section className="w-full" aria-label="Nền tảng hỗ trợ">
          <p className="text-center text-xs font-semibold text-fg-muted uppercase tracking-widest mb-4">
            Hỗ trợ tải từ
          </p>
          {/* Negative mx to let chips bleed on mobile for scroll feel */}
          <div className="relative sm:overflow-visible overflow-hidden -mx-4 sm:mx-0">
            <div
              className="flex gap-2.5 overflow-x-auto pb-2 px-4 sm:px-0 sm:flex-wrap sm:justify-center"
              style={{ scrollbarWidth: 'none', msOverflowStyle: 'none' }}
              aria-label="Danh sách nền tảng"
            >
              {platforms.map(({ icon: Icon, label, color, ic }) => (
                <span
                  key={label}
                  className={`inline-flex items-center gap-2 px-4 py-2 rounded-full border text-sm font-semibold whitespace-nowrap flex-shrink-0 ${color}`}
                >
                  <span className={ic}><Icon /></span>
                  {label}
                </span>
              ))}
            </div>
            {/* Fade hint on right edge — mobile only */}
            <div className="sm:hidden absolute right-0 top-0 bottom-2 w-10 bg-gradient-to-l from-canvas to-transparent pointer-events-none" />
          </div>
        </section>

        {/* ── Install guide ─────────────────────────────────── */}
        <section className="w-full" aria-labelledby="install-heading">
          <h2
            id="install-heading"
            className="text-center text-xl sm:text-2xl font-black text-fg mb-8"
          >
            Hướng dẫn cài đặt
          </h2>

          <ol className="flex flex-col gap-3">
            {steps.map(({ title, body }, i) => (
              <li
                key={title}
                className="flex gap-4 p-5 rounded-2xl bg-surface-2 border border-line"
              >
                <span className="w-8 h-8 shrink-0 rounded-xl bg-accent text-accent-fg font-black text-sm flex items-center justify-center select-none">
                  {i + 1}
                </span>
                <div className="min-w-0 text-sm text-fg-muted leading-relaxed">
                  <h3 className="font-bold text-fg text-base mb-1">{title}</h3>
                  {body}
                </div>
              </li>
            ))}
          </ol>
        </section>

        {/* ── Other browsers ────────────────────────────────── */}
        <div
          className="w-full flex items-start gap-3 p-4 rounded-2xl bg-accent-soft border border-accent/25"
          role="note"
          aria-label="Trình duyệt khác"
        >
          <Info className="w-5 h-5 text-accent-text flex-shrink-0 mt-0.5" />
          <p className="text-sm text-accent-text leading-relaxed">
            Edge, Cốc Cốc và Brave cài tương tự. Trên Edge dùng{' '}
            <span className="font-mono font-semibold">edge://extensions</span>, trên Brave dùng{' '}
            <span className="font-mono font-semibold">brave://extensions</span>.
          </p>
        </div>

        {/* ── Telegram CTA ──────────────────────────────────── */}
        <section
          className="w-full"
          aria-labelledby="telegram-heading"
        >
          <div className="flex flex-col items-center gap-5 p-8 md:p-10 rounded-3xl bg-surface-2 border border-line text-center backdrop-blur-sm">
            {/* Bot icon */}
            <div className="w-14 h-14 rounded-2xl bg-surface-2 border border-line flex items-center justify-center">
              <Send className="w-7 h-7 text-[#229ED9]" />
            </div>

            <div className="flex flex-col gap-2">
              <h2
                id="telegram-heading"
                className="text-xl sm:text-2xl font-black text-fg"
              >
                Nhận file qua Telegram Bot
              </h2>
              <p className="text-sm sm:text-base text-fg-muted max-w-sm mx-auto leading-relaxed">
                Nhận bất kỳ nội dung → bot tự gửi file ZIP + hướng dẫn cài đặt chi tiết.
              </p>
            </div>

            <a
              href={TELEGRAM_BOT_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-7 py-3.5 rounded-2xl bg-accent text-accent-fg font-extrabold text-sm hover:bg-accent-hover hover:scale-[1.03] active:scale-[0.98] transition-all duration-200 shadow-lg cursor-pointer"
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
