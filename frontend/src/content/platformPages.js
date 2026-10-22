// ─────────────────────────────────────────────────────────────────────
// Nội dung các trang landing theo nền tảng (SEO).
// MỘT file duy nhất cho BA review/sửa chữ. Dùng chung bởi:
//   • src/pages/PlatformLandingPage.jsx  (SPA)
//   • scripts/prerender-seo.mjs          (sinh HTML tĩnh lúc build)
// Chỉ viết những điều code thật sự làm được (xem DashboardContent.jsx
// isWatermarkPlatform, PlatformsPage.jsx, backend/app/services/*).
// KHÔNG ghi: "không giới hạn", "4K cho mọi nền tảng", "không quảng cáo".
// ─────────────────────────────────────────────────────────────────────

export const SITE_NAME = 'VidGrab';

// Dòng lưu ý dùng chung cho mọi trang.
export const COMMON_NOTE =
  'Chỉ tải video công khai và tôn trọng bản quyền: hãy dùng video cho mục đích cá nhân hoặc khi bạn được tác giả cho phép.';

export const QUOTA_NOTE =
  'Người dùng chưa đăng nhập có hạn mức tải theo ngày. Khi chạm hạn mức, VidGrab sẽ báo trên màn hình.';

// English (22/10/2026, /en/<slugEn>). wording: BA review — drafted with AI.
export const COMMON_NOTE_EN =
  'Only download public videos and respect copyright: use videos for personal purposes or with the creator’s permission.';
export const QUOTA_NOTE_EN =
  'Visitors who are not signed in have a daily download limit. VidGrab tells you on screen when you reach it.';

/** A page's text in `lang` (falls back to Vietnamese). */
export function pageText(p, lang) {
  return lang === 'en' && p.en ? { ...p, ...p.en } : p;
}

export const platformPages = [
  {
    slug: 'tai-video-tiktok',
    slugEn: 'tiktok-video-downloader',
    en: {
      title: "TikTok Video Downloader without Watermark | VidGrab",
      description: "Paste a TikTok link to save the video to your device, with an option to remove the watermark/logo. Works with tiktok.com and vm.tiktok.com links, right in your browser.",
      h1: "Download TikTok videos without watermark",
      intro: "Paste a TikTok video link in the box below, turn on watermark removal and save a clean copy to your device. No app to install.",
      steps: [
        { title: "Copy the link", text: "In TikTok, tap Share and choose Copy link." },
        { title: "Paste it into VidGrab", text: "Paste the link in the box above, turn on “Remove watermark / logo” for a clean copy, then press download." },
        { title: "Save to your device", text: "Pick the format shown on screen (video or MP3) and download." },
      ],
      faq: [
        { q: "Can VidGrab download TikTok videos without the logo?", a: "Yes. For TikTok, VidGrab has a “Remove watermark / logo” option. Turn it on before downloading to get a copy without the logo." },
        { q: "Do short vm.tiktok.com links work?", a: "Yes. You can paste a full tiktok.com link or a short vm.tiktok.com link." },
        { q: "Can I save only the audio (MP3) of a TikTok video?", a: "VidGrab lets you download MP3 audio when the result screen offers that option." },
        { q: "Do I need to sign in to download TikTok videos?", a: "You can try it without an account, but visitors who are not signed in have a daily download limit." },
        { q: "Can private TikTok videos be downloaded?", a: "No. VidGrab only handles public videos. Please respect the creator’s copyright when you use a video." },
      ],
    },
    platform: 'TikTok',
    title: 'Tải video TikTok không logo, không watermark | VidGrab',
    description:
      'Dán link TikTok để lưu video về máy, có tùy chọn xoá watermark/logo. Hỗ trợ link tiktok.com và vm.tiktok.com, dùng ngay trên trình duyệt.',
    h1: 'Tải video TikTok không logo',
    intro:
      'Dán đường dẫn video TikTok vào ô bên dưới, bật tùy chọn xoá watermark và lưu bản sạch về máy. Không cần cài ứng dụng.',
    steps: [
      { title: 'Sao chép link', text: 'Mở video trong TikTok, bấm Chia sẻ rồi chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên, bật "Xoá watermark / logo" nếu muốn bản sạch, rồi bấm tải.' },
      { title: 'Lưu về máy', text: 'Chọn định dạng hiển thị trên màn hình (video hoặc MP3) và tải xuống.' },
    ],
    faq: [
      {
        q: 'VidGrab có tải được video TikTok không logo không?',
        a: 'Có. Với TikTok, VidGrab có tuỳ chọn "Xoá watermark / logo". Bật tuỳ chọn này trước khi tải để nhận bản không logo.',
      },
      {
        q: 'Link rút gọn vm.tiktok.com có dùng được không?',
        a: 'Được. Bạn có thể dán link đầy đủ tiktok.com hoặc link rút gọn vm.tiktok.com.',
      },
      {
        q: 'Có lưu riêng phần nhạc (MP3) của video TikTok được không?',
        a: 'VidGrab cho phép chọn tải âm thanh MP3 khi màn hình kết quả có tuỳ chọn này.',
      },
      {
        q: 'Tải video TikTok có cần đăng nhập không?',
        a: 'Bạn có thể dùng thử mà không cần đăng ký, nhưng khách chưa đăng nhập có hạn mức tải theo ngày.',
      },
      {
        q: 'Video TikTok riêng tư có tải được không?',
        a: 'Không. VidGrab chỉ xử lý video công khai. Hãy tôn trọng bản quyền của tác giả khi sử dụng video.',
      },
    ],
  },
  {
    slug: 'tai-video-facebook',
    slugEn: 'facebook-video-downloader',
    en: {
      title: "Facebook Video & Reels Downloader | VidGrab",
      description: "Save public Facebook videos, Reels and recorded live videos to your device from a link. Works with facebook.com and fb.watch links, right in your browser.",
      h1: "Download Facebook videos",
      intro: "Paste the link of a Facebook video, Reel or finished live video in the box below to save it to your device. Public content only.",
      steps: [
        { title: "Get the video link", text: "On Facebook, press Share or the three dots of the video and choose Copy link." },
        { title: "Paste it into VidGrab", text: "Paste the link in the box above and press download so VidGrab can analyse the video." },
        { title: "Choose and download", text: "Pick one of the available qualities in the results and save it." },
      ],
      faq: [
        { q: "What Facebook content can VidGrab download?", a: "Public videos, Reels and recordings of finished live videos. Both facebook.com and fb.watch links work." },
        { q: "Why does my Facebook video show an error?", a: "Usually the video is private, friends-only or in a closed group. VidGrab cannot download those. Facebook also blocks requests for a while sometimes — try again in a few minutes." },
        { q: "Can I choose the video quality?", a: "The available qualities depend on the original video and are listed in the results once VidGrab has analysed the link." },
        { q: "Can I download several Facebook videos at once?", a: "Yes, the home page has a mode for many links at once. Public content only." },
      ],
    },
    platform: 'Facebook',
    title: 'Tải video Facebook, Reels về máy nhanh | VidGrab',
    description:
      'Lưu video Facebook công khai, Reels và bản ghi video trực tiếp về máy chỉ với link. Hỗ trợ facebook.com và fb.watch, dùng ngay trên trình duyệt.',
    h1: 'Tải video Facebook',
    intro:
      'Dán link video, Reels hoặc video phát trực tiếp đã kết thúc của Facebook vào ô bên dưới để lưu về máy. Chỉ áp dụng cho nội dung công khai.',
    steps: [
      { title: 'Lấy link video', text: 'Trên Facebook, bấm Chia sẻ hoặc dấu ba chấm của video rồi chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên và bấm tải để VidGrab phân tích video.' },
      { title: 'Chọn và tải xuống', text: 'Chọn chất lượng có sẵn trong danh sách kết quả rồi lưu về máy.' },
    ],
    faq: [
      {
        q: 'VidGrab tải được những loại nội dung nào của Facebook?',
        a: 'Video công khai, Reels và bản ghi video phát trực tiếp đã kết thúc. Link dạng facebook.com và fb.watch đều dùng được.',
      },
      {
        q: 'Vì sao video Facebook của tôi báo lỗi?',
        a: 'Thường do video ở chế độ riêng tư, chỉ bạn bè xem được hoặc nằm trong nhóm kín. VidGrab không tải được các nội dung này. Facebook cũng đôi khi chặn tạm thời, bạn có thể thử lại sau ít phút.',
      },
      {
        q: 'Có chọn được chất lượng video không?',
        a: 'Các chất lượng có sẵn phụ thuộc vào video gốc và sẽ hiện trong danh sách kết quả sau khi VidGrab phân tích link.',
      },
      {
        q: 'Có tải nhiều video Facebook cùng lúc được không?',
        a: 'VidGrab có chế độ tải nhiều link cùng lúc ở trang chủ. Vẫn chỉ áp dụng cho nội dung công khai.',
      },
    ],
  },
  {
    slug: 'tai-video-threads',
    slugEn: 'threads-video-downloader',
    en: {
      title: "Threads Video & Photo Downloader | VidGrab",
      description: "Save videos and photos from public Threads posts to your device from a link. Works with threads.com and threads.net, right in your browser.",
      h1: "Download Threads videos",
      intro: "Paste the link of a public Threads post in the box below to save its video or photos. VidGrab cannot open posts that require signing in.",
      steps: [
        { title: "Copy the post link", text: "In Threads, press the share icon of the post and choose Copy link." },
        { title: "Paste it into VidGrab", text: "Paste the link in the box above — make sure it is the link of a single post — and press download." },
        { title: "Save the video or photos", text: "Pick the file shown in the results (video, photo, or each photo of a multi-photo post) and download it." },
      ],
      faq: [
        { q: "What can VidGrab download from Threads?", a: "Videos and photos in public Threads posts, including posts with several photos." },
        { q: "Which Threads links are supported?", a: "Links of public posts or profiles on threads.com or threads.net." },
        { q: "Why can’t VidGrab download a Threads post?", a: "Common causes: the post requires signing in or is not public, it has no video or photo, or Threads is temporarily blocking requests. Try again in a few minutes." },
        { q: "What happens with a profile link?", a: "A public profile link lists its recent posts for you to choose from. For an exact result, paste the link of a single post." },
      ],
    },
    platform: 'Threads',
    title: 'Tải video và ảnh Threads công khai | VidGrab',
    description:
      'Lưu video và ảnh từ bài đăng Threads công khai về máy bằng link. Hỗ trợ threads.com và threads.net, dùng ngay trên trình duyệt.',
    h1: 'Tải video Threads',
    intro:
      'Dán link bài đăng Threads công khai vào ô bên dưới để lưu video hoặc ảnh về máy. VidGrab không truy cập được bài đăng yêu cầu đăng nhập.',
    steps: [
      { title: 'Sao chép link bài đăng', text: 'Trong Threads, bấm biểu tượng chia sẻ của bài đăng rồi chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên, đảm bảo đây là link của đúng một bài đăng, rồi bấm tải.' },
      { title: 'Lưu video hoặc ảnh', text: 'Chọn tệp hiển thị trong kết quả (video, ảnh hoặc từng ảnh của bài nhiều ảnh) và tải xuống.' },
    ],
    faq: [
      {
        q: 'VidGrab tải được gì từ Threads?',
        a: 'Video và ảnh trong bài đăng Threads công khai, kể cả bài có nhiều ảnh.',
      },
      {
        q: 'Link Threads nào được hỗ trợ?',
        a: 'Link bài đăng hoặc trang cá nhân công khai, tên miền threads.com hoặc threads.net.',
      },
      {
        q: 'Vì sao VidGrab báo không tải được bài Threads?',
        a: 'Các nguyên nhân thường gặp: bài yêu cầu đăng nhập hoặc không công khai, bài không có video/ảnh, hoặc Threads tạm thời chặn yêu cầu. Bạn có thể thử lại sau ít phút.',
      },
      {
        q: 'Dán link trang cá nhân thì sao?',
        a: 'Link trang cá nhân công khai sẽ cho danh sách các bài đăng gần đây để bạn chọn. Nếu cần chính xác, hãy dán link đúng một bài đăng.',
      },
    ],
  },
  {
    slug: 'tai-video-douyin',
    slugEn: 'douyin-video-downloader',
    en: {
      title: "Douyin Video Downloader without Watermark | VidGrab",
      description: "Paste a Douyin link to save the video to your device, with an option to remove the watermark/logo. Works with douyin.com and short v.douyin.com links, right in your browser.",
      h1: "Download Douyin videos without watermark",
      intro: "Douyin is the Chinese version of TikTok. Paste a Douyin video link in the box below, turn on watermark removal and save a clean copy.",
      steps: [
        { title: "Copy the Douyin link", text: "In the Douyin app, tap Share and choose Copy link (usually a v.douyin.com link)." },
        { title: "Paste it into VidGrab", text: "Paste the link in the box above, turn on “Remove watermark / logo” and press download." },
        { title: "Save to your device", text: "Pick the format shown on screen and download." },
      ],
      faq: [
        { q: "Does VidGrab remove the watermark from Douyin videos?", a: "Yes. For Douyin, VidGrab has a “Remove watermark / logo” option. Turn it on before downloading." },
        { q: "Do short v.douyin.com links work?", a: "Yes. VidGrab expands short links before processing them." },
        { q: "Why does a Douyin video sometimes fail?", a: "Douyin tightly limits outside access, so extraction occasionally fails. Try again in a few minutes or try another link." },
        { q: "Can private Douyin videos be downloaded?", a: "No. VidGrab only handles public videos. Please respect the creator’s copyright when you use a video." },
      ],
    },
    platform: 'Douyin',
    title: 'Tải video Douyin không watermark | VidGrab',
    description:
      'Dán link Douyin để lưu video về máy, có tùy chọn xoá watermark/logo. Hỗ trợ douyin.com và link rút gọn v.douyin.com, dùng ngay trên trình duyệt.',
    h1: 'Tải video Douyin không logo',
    intro:
      'Douyin là phiên bản TikTok tại Trung Quốc. Dán link video Douyin vào ô bên dưới, bật tuỳ chọn xoá watermark và lưu bản sạch về máy.',
    steps: [
      { title: 'Sao chép link Douyin', text: 'Trong ứng dụng Douyin, bấm Chia sẻ rồi chọn Sao chép liên kết (link thường có dạng v.douyin.com).' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên, bật "Xoá watermark / logo" rồi bấm tải.' },
      { title: 'Lưu về máy', text: 'Chọn định dạng hiển thị trên màn hình và tải xuống.' },
    ],
    faq: [
      {
        q: 'VidGrab có xoá watermark video Douyin không?',
        a: 'Có. Với Douyin, VidGrab có tuỳ chọn "Xoá watermark / logo". Bật tuỳ chọn này trước khi tải.',
      },
      {
        q: 'Link rút gọn v.douyin.com có dùng được không?',
        a: 'Được. VidGrab tự mở rộng link rút gọn trước khi xử lý.',
      },
      {
        q: 'Vì sao video Douyin đôi khi tải không được?',
        a: 'Douyin siết chặt việc truy cập từ bên ngoài nên đôi lúc việc trích xuất thất bại. Hãy thử lại sau ít phút hoặc thử link khác.',
      },
      {
        q: 'Video Douyin riêng tư có tải được không?',
        a: 'Không. VidGrab chỉ xử lý video công khai. Hãy tôn trọng bản quyền của tác giả khi sử dụng video.',
      },
    ],
  },
];
