import { useState } from 'react'
import { cn } from '../../utils/cn'

export interface PlatformCookieSpec {
  id: string
  name: string
  badge: string
  robotsUrl: string
  sameMethodAsYoutube: string
  recommendedPool: string
  requiredKeys: string[]
  optionalKeys?: string[]
  lifespanTip: string
  gmailTrick: string
  steps: string[]
  warning?: string
}

export const PLATFORM_COOKIE_GUIDES: Record<string, PlatformCookieSpec> = {
  youtube: {
    id: 'youtube',
    name: 'YouTube',
    badge: 'Quan trọng nhất',
    robotsUrl: 'https://www.youtube.com/robots.txt',
    sameMethodAsYoutube: 'Chuẩn gốc: Incognito + /robots.txt + Tắt ngang cửa sổ (X)',
    recommendedPool: '5 – 10 cookie xoay vòng',
    requiredKeys: ['SID', 'HSID', 'SSID', 'APISID', 'SAPISID', '__Secure-1PSID'],
    optionalKeys: ['LOGIN_INFO', '__Secure-3PSID', 'PREF'],
    lifespanTip:
      'YouTube xoay vòng __Secure-1PSIDPS mỗi 10–15 phút nếu bạn vẫn mở tab YouTube trên trình duyệt. Bắt buộc lấy ở Cửa sổ Ẩn danh -> vào /robots.txt -> Export -> Đóng thẳng cửa sổ Ẩn danh (X) để đóng băng token sống 3–6 tháng.',
    gmailTrick:
      '1 Gmail tạo được 10–20 Kênh Thương Hiệu (Brand Accounts) tại youtube.com/channel_switcher. Mỗi lần mở 1 cửa sổ Ẩn danh mới -> Chọn 1 kênh phụ khác nhau -> Vào /robots.txt -> Export.',
    steps: [
      'Mở Cửa sổ Ẩn danh (Ctrl + Shift + N trên Chrome/Edge).',
      'Vào youtube.com, đăng nhập tài khoản phụ (hoặc chọn 1 Brand Channel).',
      'Trên cùng tab đó, gõ thẳng URL: https://www.youtube.com/robots.txt',
      'Bấm extension "Get cookies.txt LOCALLY" -> Export định dạng Netscape.',
      'Bấm dấu (X) đóng ngay cửa sổ Ẩn danh. Tuyệt đối KHÔNG bấm Đăng xuất!',
    ],
    warning: 'Không dùng lại tài khoản đó trên trình duyệt thường sau khi đã xuất cookie.',
  },
  instagram: {
    id: 'instagram',
    name: 'Instagram',
    badge: 'Rate-limit gắt (20s/lần)',
    robotsUrl: 'https://www.instagram.com/robots.txt',
    sameMethodAsYoutube: 'GIỐNG HỆT YOUTUBE (Incognito + /robots.txt + Tắt cửa sổ X)',
    recommendedPool: '3 – 5 cookie xoay vòng',
    requiredKeys: ['sessionid', 'ds_user_id', 'csrftoken'],
    optionalKeys: ['mid', 'ig_did', 'rur'],
    lifespanTip:
      'Instagram kiểm soát xung đột phiên rất gắt (chỉ ~40 lượt tải/nick/ngày, cooldown 20s). Nếu vừa cắm cookie trên server vừa lướt IG trên điện thoại/máy tính, IG sẽ báo "Yêu cầu đăng nhập lại" ngay. Phải dùng nick phụ ẩn danh + đóng cửa sổ X.',
    gmailTrick:
      'Dùng 1 Gmail tạo 5 nick IG bằng mẹo dấu cộng (+): ten+ig1@gmail.com, ten+ig2@gmail.com, ten+ig3@gmail.com (hoặc dấu chấm t.en@gmail.com). Mọi mã OTP đều đổ về 1 hộp thư chính.',
    steps: [
      'Mở Cửa sổ Ẩn danh (Ctrl + Shift + N) -> vào instagram.com đăng nhập nick phụ.',
      'Nếu hỏi "Lưu thông tin đăng nhập?", bấm "Lúc khác / Not Now" rồi chuyển sang https://www.instagram.com/robots.txt',
      'Export cookie định dạng Netscape (kiểm tra phải có dòng sessionid).',
      'Đóng ngay cửa sổ Ẩn danh bằng nút (X). Không bấm Log Out!',
    ],
    warning: 'Nếu bấm Log Out trên trình duyệt, khóa sessionid trên server sẽ chết ngay lập tức.',
  },
  facebook: {
    id: 'facebook',
    name: 'Facebook',
    badge: 'Cần cho Reel/Video riêng tư',
    robotsUrl: 'https://www.facebook.com/robots.txt',
    sameMethodAsYoutube: 'GIỐNG HỆT YOUTUBE (Incognito + /robots.txt + Tắt cửa sổ X)',
    recommendedPool: '3 – 4 cookie xoay vòng',
    requiredKeys: ['c_user', 'xs'],
    optionalKeys: ['datr', 'fr', 'sb'],
    lifespanTip:
      'Cookie quan trọng nhất của Facebook là cặp c_user (ID nick) và xs (Session secret). Nếu bấm Đăng xuất hoặc đổi mật khẩu, mã xs bị hủy ngay. Lấy qua /robots.txt ở tab Ẩn danh rồi tắt X sẽ giữ xs sống 2–4 tháng.',
    gmailTrick:
      'Facebook đôi khi chặn dấu (+), hãy dùng mẹo DẤU CHẤM (.) hoặc @googlemail.com từ 1 Gmail gốc: ví dụ a.bc@gmail.com, ab.c@gmail.com, abc@googlemail.com để tạo 3–4 nick clone.',
    steps: [
      'Mở Cửa sổ Ẩn danh (Ctrl + Shift + N) -> Đăng nhập nick Facebook phụ (nên bật 2FA để ít bị checkpoint).',
      'Truy cập thẳng: https://www.facebook.com/robots.txt',
      'Export cookie Netscape bằng "Get cookies.txt LOCALLY".',
      'Đóng cửa sổ Ẩn danh bằng nút (X) góc trên. Tuyệt đối không bấm Đăng xuất.',
    ],
  },
  tiktok: {
    id: 'tiktok',
    name: 'TikTok',
    badge: 'Video thường tự tải không cần cookie',
    robotsUrl: 'https://www.tiktok.com/robots.txt',
    sameMethodAsYoutube: 'GIỐNG YOUTUBE (Incognito + /robots.txt + Tắt cửa sổ X)',
    recommendedPool: '2 – 4 cookie (dự phòng video 18+ / riêng tư)',
    requiredKeys: ['sessionid', 'sessionid_ss'],
    optionalKeys: ['ttwid', 'sid_tt', 'uid_tt', 'msToken'],
    lifespanTip:
      '95% video TikTok công khai đã được hệ thống tự động tải qua TikWM/Cobalt mà không tốn cookie. Cookie TikTok chỉ dùng khi tải video giới hạn độ tuổi hoặc kênh khóa. Làm giống hệt YouTube để giữ sessionid sống lâu.',
    gmailTrick:
      'TikTok cho phép dùng dấu cộng (+) và dấu chấm (.) thoải mái: ten+tt1@gmail.com, ten+tt2@gmail.com, ten+tt3@gmail.com đều nhận mã xác nhận về cùng 1 Gmail.',
    steps: [
      'Mở Cửa sổ Ẩn danh (Ctrl + Shift + N) -> Đăng nhập tiktok.com bằng email.',
      'Vượt qua captcha kéo hình (nếu có), sau đó vào https://www.tiktok.com/robots.txt',
      'Export cookie Netscape (đảm bảo có sessionid hoặc sessionid_ss).',
      'Đóng cửa sổ Ẩn danh bằng nút (X).',
    ],
  },
  twitter: {
    id: 'twitter',
    name: 'Twitter / X',
    badge: 'Rate-limit gắt (30s/lần)',
    robotsUrl: 'https://x.com/robots.txt',
    sameMethodAsYoutube: 'GIỐNG HỆT YOUTUBE (Incognito + x.com/robots.txt + Tắt X)',
    recommendedPool: '3 – 5 cookie xoay vòng',
    requiredKeys: ['auth_token', 'ct0'],
    optionalKeys: ['twid', 'guest_id'],
    lifespanTip:
      'X/Twitter kiểm tra khóa auth_token và ct0. Cooldown mỗi nick là 30 giây (tối đa ~50 lượt/ngày). Bắt buộc lấy trong Cửa sổ Ẩn danh rồi tắt ngang (X) để auth_token sống tới 6–12 tháng.',
    gmailTrick:
      'Twitter/X hỗ trợ cực tốt dấu cộng (+): ten+x1@gmail.com, ten+x2@gmail.com, ten+x3@gmail.com. Chỉ mất 3 phút tạo được 4–5 nick X từ 1 Gmail duy nhất.',
    steps: [
      'Mở Cửa sổ Ẩn danh (Ctrl + Shift + N) -> Đăng nhập https://x.com',
      'Chuyển sang URL tĩnh: https://x.com/robots.txt',
      'Export cookie Netscape (kiểm tra có auth_token và ct0).',
      'Tắt cửa sổ Ẩn danh bằng dấu (X), không bấm Log out.',
    ],
  },
  douyin: {
    id: 'douyin',
    name: 'Douyin (抖音)',
    badge: 'Cần vượt Captcha 1 lần',
    robotsUrl: 'https://www.douyin.com/robots.txt',
    sameMethodAsYoutube: 'Tương tự YouTube (Không bắt buộc đăng nhập SĐT TQ)',
    recommendedPool: '2 – 3 cookie',
    requiredKeys: ['ttwid', 'odin_tt'],
    optionalKeys: ['sessionid', 'sessionid_ss', 'passport_csrf_token', 's_v_web_id'],
    lifespanTip:
      'Với Douyin, ngay cả khi không có SĐT Trung Quốc để đăng nhập, bạn chỉ cần mở Ẩn danh vào douyin.com, xem 1 video và giải Captcha trượt hình (để sinh ra s_v_web_id + ttwid hợp lệ), sau đó vào /robots.txt xuất cookie và đóng cửa sổ.',
    gmailTrick:
      'Không cần Gmail — mỗi cửa sổ Ẩn danh mới sau khi giải xong Captcha trên douyin.com sẽ sinh 1 bộ cookie khách (Visitor Cookie: ttwid + s_v_web_id) độc lập để nạp vào hồ xoay vòng.',
    steps: [
      'Mở Cửa sổ Ẩn danh -> Vào https://www.douyin.com và bấm xem 1 video bất kỳ.',
      'Nếu hiện Captcha mảnh ghép, kéo giải xong để video phát bình thường (hoặc quét QR đăng nhập nếu có app Douyin).',
      'Chuyển sang https://www.douyin.com/robots.txt -> Export Netscape cookie.',
      'Đóng cửa sổ Ẩn danh bằng nút (X).',
    ],
  },
  xiaohongshu: {
    id: 'xiaohongshu',
    name: 'Xiaohongshu (Tiểu Hồng Thư)',
    badge: 'Hỗ trợ link khám phá & rút gọn',
    robotsUrl: 'https://www.xiaohongshu.com/robots.txt',
    sameMethodAsYoutube: 'GIỐNG YOUTUBE (Incognito + /robots.txt + Tắt X)',
    recommendedPool: '1 – 2 cookie',
    requiredKeys: ['web_session', 'a1'],
    optionalKeys: ['webId', 'gid'],
    lifespanTip:
      'Link Tiểu Hồng Thư có `xsec_token` thường tự tải được. Cookie `web_session` + `a1` giúp tải link rút gọn xhslink.com hoặc bài viết yêu cầu đăng nhập.',
    gmailTrick:
      'Có thể đăng nhập bằng Apple ID / WeChat hoặc lấy cookie phiên khách đã xác thực captcha trên xiaohongshu.com.',
    steps: [
      'Mở Cửa sổ Ẩn danh -> Vào https://www.xiaohongshu.com',
      'Đăng nhập hoặc mở 1 bài viết bất kỳ cho tới khi hiển thị nội dung.',
      'Vào https://www.xiaohongshu.com/robots.txt -> Export Netscape cookie -> Đóng cửa sổ (X).',
    ],
  },
  reddit: {
    id: 'reddit',
    name: 'Reddit',
    badge: 'Video NSFW / Private Subreddit',
    robotsUrl: 'https://www.reddit.com/robots.txt',
    sameMethodAsYoutube: 'GIỐNG YOUTUBE (Incognito + /robots.txt + Tắt X)',
    recommendedPool: '2 – 3 cookie',
    requiredKeys: ['reddit_session', 'token_v2'],
    optionalKeys: ['csv', 'edgebucket'],
    lifespanTip:
      'Reddit cho phép tạo nhiều tài khoản phụ từ cùng 1 email cực nhanh. Lấy ở tab Ẩn danh + đóng cửa sổ (X) giữ reddit_session sống nhiều tháng.',
    gmailTrick:
      'Dùng ten+rd1@gmail.com, ten+rd2@gmail.com hoặc tạo trực tiếp nhiều tài khoản Reddit trên cùng 1 email gốc.',
    steps: [
      'Mở Cửa sổ Ẩn danh -> Đăng nhập https://www.reddit.com',
      'Truy cập https://www.reddit.com/robots.txt -> Export Netscape cookie -> Đóng cửa sổ (X).',
    ],
  },
  bilibili: {
    id: 'bilibili',
    name: 'Bilibili',
    badge: 'Cần cho 1080p+ trên Bilibili',
    robotsUrl: 'https://www.bilibili.com/robots.txt',
    sameMethodAsYoutube: 'GIỐNG YOUTUBE (Incognito + /robots.txt + Tắt X)',
    recommendedPool: '1 – 2 cookie',
    requiredKeys: ['SESSDATA', 'bili_jct'],
    optionalKeys: ['DedeUserID', 'buvid3'],
    lifespanTip:
      'Bilibili giới hạn khách không đăng nhập ở 480p. Có cookie SESSDATA sẽ mở khóa tải 1080p/4K mượt mà.',
    gmailTrick:
      'Dùng biến thể dấu cộng (+) hoặc dấu chấm (.) khi liên kết email tài khoản Bilibili.',
    steps: [
      'Mở Cửa sổ Ẩn danh -> Đăng nhập https://www.bilibili.com',
      'Vào https://www.bilibili.com/robots.txt -> Export Netscape cookie -> Đóng cửa sổ (X).',
    ],
  },
}

interface CookieGuideBannerProps {
  activePlatform: string
  onSelectPlatform?: (platform: string) => void
}

export function CookieGuideBanner({ activePlatform, onSelectPlatform }: CookieGuideBannerProps) {
  const [open, setOpen] = useState(false)
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)
  const [selectedGuideTab, setSelectedGuideTab] = useState<string>(
    PLATFORM_COOKIE_GUIDES[activePlatform] ? activePlatform : 'youtube',
  )

  const currentKey = PLATFORM_COOKIE_GUIDES[selectedGuideTab]
    ? selectedGuideTab
    : PLATFORM_COOKIE_GUIDES[activePlatform]
    ? activePlatform
    : 'youtube'

  const spec = PLATFORM_COOKIE_GUIDES[currentKey]

  function copyText(text: string) {
    navigator.clipboard?.writeText(text).catch(() => {})
    setCopiedUrl(text)
    setTimeout(() => setCopiedUrl(null), 1800)
  }

  return (
    <div className="rounded-card border border-line bg-surface shadow-card overflow-hidden">
      {/* Header bar — always visible */}
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-left hover:bg-surface-2/60 transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-control bg-accent/15 text-accent font-semibold text-xs">
            ?
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold text-fg">
                Hướng dẫn lấy Cookie Bất Tử (Sống 3–6 tháng) & Mẹo 1 Gmail tạo 20 Tài khoản
              </span>
              <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[10px] font-semibold text-accent">
                YouTube · TikTok · Facebook · Instagram · Twitter/X · Douyin
              </span>
            </div>
            <p className="mt-0.5 text-[11px] text-fg-muted truncate">
              Tất cả nền tảng đều áp dụng quy tắc: <strong>Cửa sổ Ẩn danh (Ctrl+Shift+N)</strong> → Vào <strong>/robots.txt</strong> → <strong>Export Netscape</strong> → <strong>Đóng thẳng cửa sổ (X), KHÔNG bấm Đăng xuất</strong>.
            </p>
          </div>
        </div>

        <span className="inline-flex items-center gap-1 rounded-control border border-line bg-surface-2 px-2.5 py-1 text-[11px] font-medium text-fg-2 flex-shrink-0">
          {open ? 'Thu gọn hướng dẫn ▲' : 'Xem chi tiết từng nền tảng ▼'}
        </span>
      </button>

      {/* Expandable body */}
      {open && (
        <div className="border-t border-line px-4 py-4 space-y-4 bg-surface">
          {/* 1. Golden Rule + 1 Gmail Multi-Account Tricks */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
            {/* Golden rule */}
            <div className="rounded-control border border-line bg-surface-2/50 p-3.5 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold text-fg">
                  🛡️ Quy tắc Vàng 4 Bước (Áp dụng cho MỌI nền tảng: YT, TikTok, FB, IG, X)
                </h3>
                <span className="rounded bg-success-soft px-1.5 py-0.5 text-[10px] font-semibold text-success">
                  Chống chết Cookie sớm
                </span>
              </div>
              <p className="text-[11px] text-fg-muted leading-relaxed">
                Không chỉ riêng YouTube — <strong>TikTok, Facebook, Instagram và Twitter/X đều hủy hoặc xoay mã phiên (Session ID)</strong> nếu bạn tiếp tục lướt web trên trình duyệt hoặc bấm nút <em>Đăng xuất (Log out)</em>.
              </p>
              <ol className="list-decimal list-inside space-y-1 text-[11px] text-fg-2">
                <li>
                  Bấm <code className="rounded bg-surface px-1 py-0.5 font-mono text-[10px]">Ctrl + Shift + N</code> mở <strong>Cửa sổ Ẩn danh</strong> (đảm bảo đã bật <em>Cho phép ở chế độ ẩn danh</em> cho extension <strong>Get cookies.txt LOCALLY</strong>).
                </li>
                <li>
                  Đăng nhập tài khoản phụ trên nền tảng cần lấy.
                </li>
                <li>
                  Trên cùng tab đó, gõ đường dẫn tĩnh <code className="rounded bg-surface px-1 py-0.5 font-mono text-[10px]">/robots.txt</code> của nền tảng (ví dụ <code className="font-mono text-[10px]">facebook.com/robots.txt</code>) để khóa tiến trình xoay token ngầm.
                </li>
                <li>
                  Bấm <strong>Export (Netscape)</strong> rồi <strong>bấm dấu (X) tắt thẳng cửa sổ Ẩn danh</strong>. Tuyệt đối <strong>KHÔNG bấm Đăng xuất</strong>!
                </li>
              </ol>
            </div>

            {/* 1 Gmail -> 20 accounts */}
            <div className="rounded-control border border-line bg-surface-2/50 p-3.5 space-y-2">
              <div className="flex items-center justify-between gap-2">
                <h3 className="text-xs font-semibold text-fg">
                  ✉️ 3 Cách dùng 1 Gmail tạo 15–20 Tài khoản phụ xoay vòng
                </h3>
                <span className="rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold text-accent">
                  Không cần mua SIM
                </span>
              </div>
              <div className="space-y-1.5 text-[11px] text-fg-2">
                <div className="rounded border border-line bg-surface p-2">
                  <span className="font-semibold text-fg">Cách 1 — Mẹo dấu cộng (+tag) cho Instagram, TikTok, Twitter/X, Reddit:</span>
                  <p className="mt-0.5 text-fg-muted">
                    Từ 1 Gmail <code className="font-mono text-[10px] text-fg">tenban@gmail.com</code>, khi đăng ký nick mới hãy nhập:{' '}
                    <code className="font-mono text-[10px] text-fg">tenban+ig1@gmail.com</code>,{' '}
                    <code className="font-mono text-[10px] text-fg">tenban+ig2@gmail.com</code>,{' '}
                    <code className="font-mono text-[10px] text-fg">tenban+tt1@gmail.com</code>,{' '}
                    <code className="font-mono text-[10px] text-fg">tenban+x1@gmail.com</code>. Nền tảng tính là email mới 100%, nhưng mọi mã OTP đều gửi về hộp thư <code className="font-mono text-[10px]">tenban@gmail.com</code>!
                  </p>
                </div>
                <div className="rounded border border-line bg-surface p-2">
                  <span className="font-semibold text-fg">Cách 2 — Mẹo dấu chấm (.) & @googlemail.com (Dành cho Facebook):</span>
                  <p className="mt-0.5 text-fg-muted">
                    Nếu trang chặn dấu <code className="font-mono text-[10px]">+</code>, hãy dịch dấu chấm:{' '}
                    <code className="font-mono text-[10px] text-fg">t.enban@gmail.com</code>,{' '}
                    <code className="font-mono text-[10px] text-fg">te.nban@gmail.com</code>,{' '}
                    <code className="font-mono text-[10px] text-fg">ten.ban@gmail.com</code> hoặc đổi đuôi thành{' '}
                    <code className="font-mono text-[10px] text-fg">tenban@googlemail.com</code>. Tất cả đều về chung 1 Gmail!
                  </p>
                </div>
                <div className="rounded border border-line bg-surface p-2">
                  <span className="font-semibold text-fg">Cách 3 — Kênh Thương Hiệu (Dành riêng cho YouTube):</span>
                  <p className="mt-0.5 text-fg-muted">
                    Truy cập <code className="font-mono text-[10px] text-fg">https://www.youtube.com/channel_switcher</code> → Bấm <strong>Tạo kênh mới (Create a channel)</strong> 5–10 lần trên cùng 1 Gmail. Mở Ẩn danh chọn từng kênh phụ và xuất 5–10 cookie riêng biệt!
                  </p>
                </div>
              </div>
            </div>
          </div>

          {/* 2. Platform selector tabs inside guide */}
          <div className="space-y-2.5">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wider text-fg-muted">
                Chi tiết cách làm theo từng nền tảng:
              </span>
              <div className="flex flex-wrap gap-1.5">
                {Object.values(PLATFORM_COOKIE_GUIDES).map(p => {
                  const active = p.id === currentKey
                  return (
                    <button
                      key={p.id}
                      type="button"
                      onClick={() => {
                        setSelectedGuideTab(p.id)
                        onSelectPlatform?.(p.id)
                      }}
                      className={cn(
                        'rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors cursor-pointer',
                        active
                          ? 'border-accent bg-accent text-accent-fg font-semibold'
                          : 'border-line bg-surface-2 text-fg-2 hover:border-line-strong',
                      )}
                    >
                      {p.name}
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Selected platform card */}
            <div className="rounded-control border border-line bg-surface-2/40 p-3.5 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line pb-2.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-sm font-semibold text-fg">{spec.name}</span>
                  <span className="rounded-full bg-surface-2 border border-line px-2 py-0.5 text-[10px] font-medium text-fg-2">
                    {spec.badge}
                  </span>
                  <span className="rounded-full bg-success-soft px-2 py-0.5 text-[10px] font-semibold text-success">
                    Khuyên dùng: {spec.recommendedPool}
                  </span>
                </div>

                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] text-fg-muted">Link khóa phiên:</span>
                  <code className="rounded border border-line bg-surface px-2 py-0.5 font-mono text-[11px] text-fg">
                    {spec.robotsUrl}
                  </code>
                  <button
                    type="button"
                    onClick={() => copyText(spec.robotsUrl)}
                    className="rounded border border-line bg-surface px-2 py-0.5 text-[10px] font-semibold text-fg-2 hover:bg-surface-2 cursor-pointer"
                  >
                    {copiedUrl === spec.robotsUrl ? '✓ Đã chép' : 'Copy URL'}
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-[11px]">
                {/* Steps */}
                <div className="md:col-span-2 space-y-1.5">
                  <div className="font-semibold text-fg">
                    Các bước chuẩn ({spec.sameMethodAsYoutube}):
                  </div>
                  <ol className="list-decimal list-inside space-y-1 text-fg-2">
                    {spec.steps.map((s, idx) => (
                      <li key={idx} className="leading-relaxed">{s}</li>
                    ))}
                  </ol>
                  <p className="mt-1.5 text-fg-muted leading-relaxed">
                    💡 <strong>Giải thích kỹ thuật:</strong> {spec.lifespanTip}
                  </p>
                  {spec.warning && (
                    <p className="text-warning font-medium">
                      ⚠️ {spec.warning}
                    </p>
                  )}
                </div>

                {/* Required keys & 1-Gmail trick */}
                <div className="space-y-2 rounded border border-line bg-surface p-2.5">
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-fg-muted">
                      Khóa Cookie bắt buộc phải có
                    </div>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {spec.requiredKeys.map(k => (
                        <code
                          key={k}
                          className="rounded bg-surface-2 border border-line px-1.5 py-0.5 font-mono text-[10px] font-semibold text-fg"
                        >
                          {k}
                        </code>
                      ))}
                    </div>
                  </div>

                  {spec.optionalKeys && spec.optionalKeys.length > 0 && (
                    <div>
                      <div className="text-[10px] font-semibold uppercase tracking-wider text-fg-muted">
                        Khóa bổ trợ
                      </div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {spec.optionalKeys.map(k => (
                          <code
                            key={k}
                            className="rounded bg-surface-2/60 px-1.5 py-0.5 font-mono text-[10px] text-fg-muted"
                          >
                            {k}
                          </code>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="pt-1 border-t border-line">
                    <div className="text-[10px] font-semibold uppercase tracking-wider text-fg-muted">
                      Mẹo nhân bản bằng 1 Gmail
                    </div>
                    <p className="mt-1 text-[11px] text-fg-2 leading-relaxed">
                      {spec.gmailTrick}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
