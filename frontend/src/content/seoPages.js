// ─────────────────────────────────────────────────────────────────────
// SEO of the public pages, Vietnamese + English (22/10/2026).
// One table used by the SPA (lib/seo.js, on every navigation) and by
// scripts/prerender-seo.mjs (static HTML per page and language, hreflang,
// sitemap). The four platform pages live in platformPages.js.
// Only claims the product really makes (same rule as platformPages.js).
// wording: BA review — the English copy was drafted with AI.
// ─────────────────────────────────────────────────────────────────────

/** Production address (canonical, hreflang, sitemap). VITE_SITE_URL overrides. */
export const DEFAULT_SITE = 'https://dowloadvideo.io.vn';

export const seoPages = [
  {
    path: '/',
    priority: '1.0',
    vi: {
      title: 'VidGrab - Tải video TikTok, Douyin, Facebook, YouTube không logo',
      description: 'Dán link để tải video và nhạc từ TikTok, Douyin, Facebook, Instagram, YouTube và nhiều nền tảng khác. Xoá watermark, tải cả kênh, dùng ngay trên trình duyệt.',
      h1: 'Tải video và nhạc từ hơn 20 nền tảng',
    },
    en: {
      title: 'VidGrab - Download TikTok, Douyin, Facebook & YouTube videos without watermark',
      description: 'Paste a link to download videos and music from TikTok, Douyin, Facebook, Instagram, YouTube and more. Remove watermarks, grab whole channels, right in your browser.',
      h1: 'Download videos and music from 20+ platforms',
    },
  },
  {
    path: '/platforms',
    priority: '0.8',
    vi: {
      title: 'Nền tảng được hỗ trợ | VidGrab',
      description: 'Danh sách nền tảng VidGrab tải được video: TikTok, Douyin, Facebook, Instagram, YouTube, X, Bilibili, Xiaohongshu… kèm tình trạng hoạt động hiện tại.',
      h1: 'Nền tảng được hỗ trợ',
    },
    en: {
      title: 'Supported platforms | VidGrab',
      description: 'The platforms VidGrab can download videos from — TikTok, Douyin, Facebook, Instagram, YouTube, X, Bilibili, Xiaohongshu and more — with their current status.',
      h1: 'Supported platforms',
    },
  },
  {
    path: '/extension',
    priority: '0.8',
    vi: {
      title: 'Tiện ích VidGrab cho Chrome - bắt video trên mọi trang | VidGrab',
      description: 'Cài tiện ích VidGrab để tải video ngay trên trang đang xem: bắt MP4, HLS, DASH, tải cả kênh Douyin, TikTok, Facebook Reels và gửi sang app VidGrab.',
      h1: 'Tiện ích trình duyệt VidGrab',
    },
    en: {
      title: 'VidGrab Chrome extension - grab videos on any page | VidGrab',
      description: 'Install the VidGrab extension to download videos on the page you watch: grab MP4, HLS and DASH, collect whole Douyin, TikTok and Facebook Reels channels, and hand them to the VidGrab app.',
      h1: 'VidGrab browser extension',
    },
  },
  {
    path: '/download',
    priority: '0.8',
    vi: {
      title: 'Tải VidGrab cho Windows | VidGrab',
      description: 'Ứng dụng VidGrab cho Windows tải video ngay trên máy bạn: tải cả kênh, chọn chất lượng, hẹn giờ, theo dõi kênh và nhận video từ tiện ích trình duyệt.',
      h1: 'Tải VidGrab cho Windows',
    },
    en: {
      title: 'Download VidGrab for Windows | VidGrab',
      description: 'The VidGrab Windows app downloads videos on your own computer: whole channels, quality choice, scheduling, channel watch, and videos handed over by the browser extension.',
      h1: 'Download VidGrab for Windows',
    },
  },
  {
    path: '/pricing',
    priority: '0.7',
    vi: {
      title: 'Bảng giá VidGrab - Miễn phí, Free và Pro | VidGrab',
      description: 'So sánh gói Khách, Free và Pro của VidGrab: số lượt tải mỗi ngày, chất lượng tối đa, tải hàng loạt và giá gói Pro theo tháng hoặc năm.',
      h1: 'Bảng giá',
    },
    en: {
      title: 'VidGrab pricing - Guest, Free and Pro | VidGrab',
      description: 'Compare VidGrab’s Guest, Free and Pro plans: daily downloads, maximum quality, bulk downloads and the monthly or yearly Pro price.',
      h1: 'Pricing',
    },
  },
  {
    path: '/faq',
    priority: '0.6',
    vi: {
      title: 'Câu hỏi thường gặp khi tải video | VidGrab',
      description: 'Cách xử lý những lỗi hay gặp khi tải video bằng VidGrab: video riêng tư, link không hợp lệ, hết lượt tải, nền tảng tạm chặn và hơn thế nữa.',
      h1: 'Câu hỏi thường gặp',
    },
    en: {
      title: 'Video download FAQ | VidGrab',
      description: 'How to fix the most common problems when downloading with VidGrab: private videos, invalid links, daily limits, platforms blocking temporarily and more.',
      h1: 'Frequently asked questions',
    },
  },
  {
    path: '/privacy',
    priority: '0.3',
    vi: { title: 'Chính sách bảo mật | VidGrab', description: 'VidGrab thu thập những dữ liệu nào, dùng vào việc gì và bạn có quyền gì với dữ liệu của mình.', h1: 'Chính sách bảo mật' },
    en: { title: 'Privacy policy | VidGrab', description: 'What data VidGrab collects, what it is used for, and your rights over your data.', h1: 'Privacy policy' },
  },
  {
    path: '/terms',
    priority: '0.3',
    vi: { title: 'Điều khoản sử dụng | VidGrab', description: 'Điều khoản khi sử dụng VidGrab: nội dung được phép tải, trách nhiệm bản quyền, tài khoản và gói trả phí.', h1: 'Điều khoản sử dụng' },
    en: { title: 'Terms of use | VidGrab', description: 'The terms for using VidGrab: what may be downloaded, copyright responsibility, accounts and paid plans.', h1: 'Terms of use' },
  },
  {
    path: '/takedown',
    priority: '0.3',
    vi: { title: 'Yêu cầu gỡ nội dung | VidGrab', description: 'Chủ sở hữu nội dung hoặc người được uỷ quyền gửi yêu cầu gỡ nội dung liên quan tới VidGrab.', h1: 'Yêu cầu gỡ nội dung' },
    en: { title: 'Content takedown request | VidGrab', description: 'Content owners or their authorised representatives can send a takedown request concerning VidGrab.', h1: 'Content takedown request' },
  },
];

export const seoPageFor = (path) => seoPages.find((p) => p.path === path) || null;
