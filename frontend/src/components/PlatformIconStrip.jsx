import { useLang, useLocalePath } from '../i18n';
import { spaLink } from '../lib/spaLink';

const PLATFORM_ITEMS = [
  {
    id: 'gdrive',
    name: 'Google Drive',
    href: '/tai-video-google-drive',
    featured: true,
    badgeVi: 'Chỉ xem · Cả thư mục',
    badgeEn: 'View-Only · Full Folder',
    bg: 'bg-emerald-500/12 border-emerald-500/40 text-fg hover:border-emerald-500/70',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <path fill="#0066DA" d="M4.2 15.5L8.4 8.2l3.8 6.5L8 21H4.2z" />
        <path fill="#00AC47" d="M8.4 8.2L12.1 2h7.4l-3.7 6.2H8.4z" />
        <path fill="#EA4335" d="M15.8 8.2L19.5 2l4 6.8-3.7 6.2-4-6.8z" />
        <path fill="#00832D" d="M4.2 15.5L8 21h12l3.5-5.5H4.2z" opacity="0.92" />
        <path fill="#FFBA00" d="M12.2 14.7L15.8 8.2l4 6.8-3.8 6H8l4.2-6.3z" opacity="0.88" />
      </svg>
    ),
  },
  {
    id: 'onedrive',
    name: 'OneDrive',
    href: '/tai-video-onedrive',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <path fill="#0364B8" d="M14.5 7.2a5.3 5.3 0 0 0-9.9 1.7A4.2 4.2 0 0 0 5.2 17h13a3.8 3.8 0 0 0 .7-7.5 5.3 5.3 0 0 0-4.4-2.3z" />
        <path fill="#28A8EA" d="M18.2 17H9.5l4.4-4.2 4.3 4.2z" opacity="0.85" />
      </svg>
    ),
  },
  {
    id: 'mega',
    name: 'Mega.nz',
    href: '/tai-video-mega',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <circle cx="12" cy="12" r="10" fill="#D9272E" />
        <path fill="#FFF" d="M7 16V8h2.2l2.8 4.1L14.8 8H17v8h-2v-4.6l-2.3 3.3h-1.4L9 11.4V16H7z" />
      </svg>
    ),
  },
  {
    id: 'terabox',
    name: 'Terabox',
    href: '/tai-video-terabox',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <rect x="2" y="3" width="20" height="18" rx="4.5" fill="#1D6BFF" />
        <path fill="#FFF" d="M7.5 14.8a2.7 2.7 0 0 1 .4-5.4 3.8 3.8 0 0 1 7.2-.8 2.9 2.9 0 0 1 .9 5.7H7.5z" />
      </svg>
    ),
  },
  {
    id: 'pikpak',
    name: 'PikPak',
    href: '/tai-video-pikpak',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <rect x="2" y="2" width="20" height="20" rx="5" fill="#3B82F6" />
        <path fill="#FFF" d="M8 17V7h4.6c2.1 0 3.4 1.2 3.4 3.1 0 1.9-1.3 3.1-3.4 3.1H10.4V17H8zm2.4-5.7h1.9c.8 0 1.3-.4 1.3-1.2s-.5-1.2-1.3-1.2h-1.9v2.4z" />
      </svg>
    ),
  },
  {
    id: 'tiktok',
    name: 'TikTok',
    href: '/tai-video-tiktok',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <path fill="currentColor" d="M19.59 6.69a4.83 4.83 0 0 1-3.77-4.25V2h-3.45v13.67a2.89 2.89 0 0 1-5.2 1.74 2.89 2.89 0 0 1 2.31-4.64 2.93 2.93 0 0 1 .88.13V9.4a6.84 6.84 0 0 0-1-.05A6.33 6.33 0 0 0 5 20.1a6.34 6.34 0 0 0 10.86-4.43v-7a8.16 8.16 0 0 0 4.77 1.52v-3.4a4.85 4.85 0 0 1-1-.1z" />
      </svg>
    ),
  },
  {
    id: 'douyin',
    name: 'Douyin',
    href: '/tai-video-douyin',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <path fill="#FE2C55" d="M17.2 6.8a4.6 4.6 0 0 1-3.4-4.1V2h-3.1v13.2a2.7 2.7 0 1 1-2-2.6V9.3a6 6 0 1 0 5.1 5.9V8.5a7.7 7.7 0 0 0 4.4 1.4V6.8h-1z" />
        <path fill="#25F4EE" d="M16.4 6.2a4.6 4.6 0 0 1-3.4-4.1V1.4h-3.1v13.2a2.7 2.7 0 1 1-2-2.6V8.7a6 6 0 1 0 5.1 5.9V7.9a7.7 7.7 0 0 0 4.4 1.4V6.2h-1z" opacity="0.6" />
      </svg>
    ),
  },
  {
    id: 'youtube',
    name: 'YouTube',
    href: '/tai-video-youtube',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <path fill="#FF0000" d="M23.5 6.2a3 3 0 0 0-2.1-2.1C19.5 3.6 12 3.6 12 3.6s-7.5 0-9.4.5A3 3 0 0 0 .5 6.2 31.4 31.4 0 0 0 0 12a31.4 31.4 0 0 0 .5 5.8 3 3 0 0 0 2.1 2.1c1.9.5 9.4.5 9.4.5s7.5 0 9.4-.5a3 3 0 0 0 2.1-2.1A31.4 31.4 0 0 0 24 12a31.4 31.4 0 0 0-.5-5.8z" />
        <path fill="#FFF" d="M9.6 15.6V8.4l6.3 3.6-6.3 3.6z" />
      </svg>
    ),
  },
  {
    id: 'facebook',
    name: 'Facebook',
    href: '/tai-video-facebook',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <path fill="#1877F2" d="M24 12a12 12 0 1 0-13.88 11.85v-8.38H7.08V12h3.04V9.36c0-3 1.79-4.66 4.53-4.66 1.31 0 2.68.23 2.68.23v2.95h-1.51c-1.49 0-1.95.92-1.95 1.87V12h3.32l-.53 3.47h-2.79v8.38A12 12 0 0 0 24 12z" />
      </svg>
    ),
  },
  {
    id: 'instagram',
    name: 'Instagram',
    href: '/tai-video-instagram',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <rect x="2" y="2" width="20" height="20" rx="5" fill="#E1306C" />
        <circle cx="12" cy="12" r="4.5" fill="none" stroke="#FFF" strokeWidth="2" />
        <circle cx="17.5" cy="6.5" r="1.2" fill="#FFF" />
      </svg>
    ),
  },
  {
    id: 'x',
    name: 'X',
    href: '/tai-video-twitter-x',
    svg: (
      <svg viewBox="0 0 24 24" className="w-3.5 h-3.5 shrink-0" aria-hidden="true">
        <path fill="currentColor" d="M18.244 2.25h3.308l-7.227 8.26 8.502 11.24H16.17l-5.214-6.817L4.99 21.75H1.68l7.73-8.835L1.254 2.25H8.08l4.713 6.231zm-1.161 17.52h1.833L7.084 4.126H5.117z" />
      </svg>
    ),
  },
  {
    id: 'bilibili',
    name: 'Bilibili',
    href: '/tai-video-bilibili',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <rect x="2" y="6" width="20" height="14" rx="3.5" fill="#00A1D6" />
        <path stroke="#00A1D6" strokeWidth="2" strokeLinecap="round" d="M7.5 3l2.5 3M16.5 3L14 6" />
        <circle cx="8.5" cy="13" r="1.5" fill="#FFF" />
        <circle cx="15.5" cy="13" r="1.5" fill="#FFF" />
      </svg>
    ),
  },
  {
    id: 'xhs',
    name: 'Xiaohongshu',
    href: '/tai-video-xiaohongshu',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <rect x="2" y="2" width="20" height="20" rx="5" fill="#FF2442" />
        <text x="12" y="15.5" textAnchor="middle" fill="#FFF" fontSize="9.5" fontWeight="800" fontFamily="system-ui">小红</text>
      </svg>
    ),
  },
  {
    id: 'telegram',
    name: 'Telegram',
    href: '/tai-video-telegram',
    svg: (
      <svg viewBox="0 0 24 24" className="w-4 h-4 shrink-0" aria-hidden="true">
        <circle cx="12" cy="12" r="10" fill="#229ED9" />
        <path fill="#FFF" d="M16.9 7.6l-1.9 9c-.1.6-.5.8-1 .5l-2.9-2.1-1.4 1.3c-.2.2-.3.3-.6.3l.2-3 5.4-4.9c.2-.2 0-.3-.4-.1L7.6 12.8 4.7 11.9c-.6-.2-.6-.6.1-.9l11.2-4.3c.5-.2 1 .1.9.9z" />
      </svg>
    ),
  },
];

export default function PlatformIconStrip({ variant = 'compact', showNewHint = false, className = '' }) {
  const lang = useLang();
  const lp = useLocalePath();
  const isVi = lang === 'vi';

  return (
    <div className={`w-full ${className}`}>
      {variant === 'showcase' && (
        <p className="text-center text-xs font-bold uppercase tracking-wider text-fg-muted mb-3">
          {isVi
            ? 'Hỗ trợ 30+ nền tảng mạng xã hội & Cloud — nổi bật Google Drive Chỉ xem & Cả thư mục · Mega 1–4 GB · Terabox · OneDrive'
            : 'Supports 30+ social & cloud platforms — featuring Google Drive View-Only & Folders · Mega 1–4 GB · Terabox · OneDrive'}
        </p>
      )}
      <div className="flex flex-wrap items-center justify-center gap-1.5 sm:gap-2">
        {PLATFORM_ITEMS.map((item) => (
          <a
            key={item.id}
            href={lp(item.href)}
            onClick={spaLink(item.href)}
            className={`inline-flex items-center gap-1.5 rounded-full border transition-colors ${
              item.featured
                ? `${item.bg} px-3 py-1.5 shadow-sm`
                : 'border-line bg-surface-2/80 text-fg-2 px-2.5 py-1 hover:border-accent/50 hover:text-fg'
            }`}
          >
            {item.svg}
            <span className={`text-xs ${item.featured ? 'font-bold text-fg' : 'font-medium'}`}>
              {item.name}
            </span>
            {item.featured && (
              <span className="ml-0.5 px-1.5 py-0.5 rounded-full bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 text-[10px] font-bold">
                {isVi ? item.badgeVi : item.badgeEn}
              </span>
            )}
          </a>
        ))}
        <a
          href={lp('/platforms')}
          onClick={spaLink('/platforms')}
          className="inline-flex items-center px-2.5 py-1 rounded-full border border-dashed border-line text-[11px] font-medium text-fg-muted hover:text-fg hover:border-line-strong transition-colors"
        >
          {isVi ? '+20 kênh khác →' : '+20 more →'}
        </a>
      </div>
      {showNewHint && (
        <div className="mt-2.5 text-center">
          <a
            href={lp('/tinh-nang')}
            onClick={spaLink('/tinh-nang')}
            className="inline-flex items-center gap-1.5 text-[11px] sm:text-xs text-fg-muted hover:text-accent-text transition-colors"
          >
            <span className="px-1.5 py-0.5 rounded bg-accent-soft text-accent-text font-bold text-[10px]">
              {isVi ? 'MỚI' : 'NEW'}
            </span>
            <span>
              {isVi
                ? 'Tải Google Drive "Chỉ xem" & cả thư mục 8–16 luồng · Mega 1–4 GB · Terabox · Theo dõi kênh tự động'
                : 'Google Drive View-Only & full folders (8–16x) · Mega 1–4 GB · Terabox · Auto Channel Watch'}
            </span>
            <span aria-hidden="true">→</span>
          </a>
        </div>
      )}
    </div>
  );
}
