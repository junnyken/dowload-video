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
  {
    slug: 'tai-video-instagram',
    slugEn: 'instagram-video-downloader',
    en: {
      title: 'Instagram Reels, Video & Photo Downloader | VidGrab',
      description: 'Paste an Instagram link to save public Reels, videos and multi-photo posts to your device. Works with instagram.com links right in your browser.',
      h1: 'Download Instagram Reels and videos',
      intro: 'Paste the link of a public Instagram Reel, video or photo post in the box below to save it to your device. Public posts only.',
      steps: [
        { title: 'Copy the Instagram link', text: 'In Instagram, tap the share icon or three dots on a post or Reel and choose Copy link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the link in the box above and press download so VidGrab can fetch the media.' },
        { title: 'Save to your device', text: 'Choose the video or photo from the results and save it to your device.' },
      ],
      faq: [
        { q: 'What Instagram content can VidGrab download?', a: 'Public Reels, videos and photo carousels from instagram.com links.' },
        { q: 'Why does an Instagram post sometimes require signing in or fail?', a: 'Private accounts, age-restricted posts, Stories and some posts behind Instagram login walls cannot be accessed without available session cookies. Try again later if Instagram is rate-limiting requests.' },
        { q: 'Can I download multiple photos from an Instagram carousel?', a: 'Yes. When a public post contains multiple photos or videos, VidGrab lists the items so you can save them.' },
        { q: 'Do I need to install an app?', a: 'No. VidGrab works directly in your web browser on phone or computer.' },
      ],
    },
    platform: 'Instagram',
    title: 'Tải video Instagram, Reels và ảnh về máy | VidGrab',
    description:
      'Dán link Instagram để lưu Reels, video và bài đăng nhiều ảnh công khai về máy. Hỗ trợ link instagram.com ngay trên trình duyệt.',
    h1: 'Tải video và Reels Instagram',
    intro:
      'Dán đường dẫn bài đăng, video hoặc Reels Instagram công khai vào ô bên dưới để lưu về máy. Chỉ hỗ trợ nội dung công khai.',
    steps: [
      { title: 'Sao chép link Instagram', text: 'Trong Instagram, bấm biểu tượng chia sẻ hoặc dấu ba chấm ở bài đăng/Reels rồi chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên và bấm tải để VidGrab trích xuất nội dung.' },
      { title: 'Lưu về máy', text: 'Chọn video hoặc ảnh hiển thị trong kết quả và tải xuống thiết bị.' },
    ],
    faq: [
      {
        q: 'VidGrab tải được nội dung nào từ Instagram?',
        a: 'Reels, video và bài đăng nhiều ảnh (carousel) ở chế độ công khai từ link instagram.com.',
      },
      {
        q: 'Vì sao có bài Instagram báo cần đăng nhập hoặc lỗi?',
        a: 'Tài khoản riêng tư, bài giới hạn độ tuổi, Story hoặc bài bị Instagram chặn đăng nhập sẽ không thể truy cập nếu hệ thống tạm hết phiên cookie. Bạn có thể thử lại sau ít phút.',
      },
      {
        q: 'Bài đăng nhiều ảnh có tải được hết không?',
        a: 'Có. Với bài đăng công khai có nhiều ảnh hoặc video, VidGrab sẽ liệt kê các mục để bạn chọn tải.',
      },
      {
        q: 'Có cần cài đặt phần mềm không?',
        a: 'Không. Bạn dùng trực tiếp trên trình duyệt điện thoại hoặc máy tính.',
      },
    ],
  },
  {
    slug: 'tai-video-youtube',
    slugEn: 'youtube-video-downloader',
    en: {
      title: 'YouTube Video, Shorts & MP3 Downloader | VidGrab',
      description: 'Paste a YouTube or youtu.be link to save videos, Shorts, MP3 audio or subtitles to your device. Choose from the available resolutions in your browser.',
      h1: 'Download YouTube videos and MP3',
      intro: 'Paste a youtube.com or youtu.be link below to fetch available video qualities, MP3 audio and subtitles. Availability depends on server status.',
      steps: [
        { title: 'Copy the YouTube link', text: 'Open the YouTube video or Short, press Share and copy the youtube.com or youtu.be link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the link in the box above and press download to inspect available formats.' },
        { title: 'Pick quality or MP3', text: 'Select the video resolution, MP3 audio or subtitle option shown in the results and save it.' },
      ],
      faq: [
        { q: 'Can I choose the video resolution or MP3 audio?', a: 'Yes. Once the link is analysed, VidGrab shows the available video resolutions and MP3 audio options for that video.' },
        { q: 'Why is YouTube sometimes unavailable on the web version?', a: 'YouTube frequently blocks cloud server IP addresses. When web extraction is restricted, you can check the Supported Platforms page for current status or use the VidGrab Windows app.' },
        { q: 'Do YouTube Shorts links work?', a: 'Yes, public youtube.com/shorts links and short youtu.be links are supported when YouTube extraction is active.' },
        { q: 'Can VidGrab extract YouTube subtitles?', a: 'Yes, when the video has captions available, you can download subtitles from the result screen or the Subtitles tool.' },
      ],
    },
    platform: 'YouTube',
    title: 'Tải video YouTube, Shorts và nhạc MP3 | VidGrab',
    description:
      'Dán link YouTube hoặc youtu.be để lưu video, Shorts, tách nhạc MP3 hoặc tải phụ đề về máy. Chọn độ phân giải có sẵn ngay trên trình duyệt.',
    h1: 'Tải video YouTube và MP3',
    intro:
      'Dán đường dẫn youtube.com hoặc youtu.be vào ô bên dưới để lấy danh sách độ phân giải video, âm thanh MP3 và phụ đề có sẵn.',
    steps: [
      { title: 'Sao chép link YouTube', text: 'Mở video hoặc Shorts trên YouTube, bấm Chia sẻ và sao chép liên kết youtube.com hoặc youtu.be.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên và bấm tải để kiểm tra các định dạng có sẵn.' },
      { title: 'Chọn chất lượng hoặc MP3', text: 'Chọn độ phân giải video, âm thanh MP3 hoặc phụ đề trong kết quả và lưu về máy.' },
    ],
    faq: [
      {
        q: 'Tôi có thể chọn độ phân giải hoặc tải riêng nhạc MP3 không?',
        a: 'Có. Sau khi phân tích link, VidGrab hiển thị các mức chất lượng video và tuỳ chọn âm thanh MP3 hiện có của video đó.',
      },
      {
        q: 'Vì sao đôi khi YouTube tạm ngưng trên bản web?',
        a: 'YouTube thường xuyên chặn dải IP máy chủ đám mây. Khi bản web bị giới hạn, bạn có thể xem trang Nền tảng hỗ trợ để biết trạng thái hoặc dùng ứng dụng VidGrab cho Windows.',
      },
      {
        q: 'Link YouTube Shorts có dùng được không?',
        a: 'Được. Cả link youtube.com/shorts và link rút gọn youtu.be đều được hỗ trợ khi kênh tải YouTube đang mở.',
      },
      {
        q: 'VidGrab có tải được phụ đề YouTube không?',
        a: 'Có. Nếu video có phụ đề, bạn có thể tải phụ đề ngay ở màn hình kết quả hoặc trong mục Phụ đề.',
      },
    ],
  },
  {
    slug: 'tai-video-twitter-x',
    slugEn: 'twitter-video-downloader',
    en: {
      title: 'Twitter / X Video & GIF Downloader | VidGrab',
      description: 'Save videos and GIFs from public X (Twitter) posts to your device. Works with x.com and twitter.com links right in your browser.',
      h1: 'Download Twitter / X videos',
      intro: 'Paste the link of a public X (formerly Twitter) post containing a video or GIF in the box below to save it as MP4.',
      steps: [
        { title: 'Copy the post link', text: 'On X (Twitter), tap the share icon on the post and select Copy link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the x.com or twitter.com link in the box above and press download.' },
        { title: 'Save the MP4 file', text: 'Choose from the available video qualities in the results and save it to your device.' },
      ],
      faq: [
        { q: 'Do both x.com and twitter.com links work?', a: 'Yes. You can paste links from x.com, twitter.com or short t.co links.' },
        { q: 'Can I save GIFs from X / Twitter?', a: 'Yes. X stores animated GIFs as looped MP4 videos, which VidGrab lets you save directly or convert to GIF.' },
        { q: 'Why do some X posts fail to download?', a: 'Protected accounts, age-restricted (NSFW) posts or posts requiring an active X login session may fail if login cookies are unavailable.' },
      ],
    },
    platform: 'Twitter / X',
    title: 'Tải video Twitter / X và GIF về máy | VidGrab',
    description:
      'Lưu video và GIF từ bài đăng X (Twitter) công khai về thiết bị. Hỗ trợ link x.com và twitter.com ngay trên trình duyệt.',
    h1: 'Tải video Twitter / X',
    intro:
      'Dán link bài đăng X (trước đây là Twitter) công khai có chứa video hoặc GIF vào ô bên dưới để lưu về máy dưới dạng MP4.',
    steps: [
      { title: 'Sao chép link bài đăng', text: 'Trên X (Twitter), bấm biểu tượng chia sẻ ở bài đăng và chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link x.com hoặc twitter.com vào ô phía trên rồi bấm tải.' },
      { title: 'Lưu tệp về máy', text: 'Chọn chất lượng video có sẵn trong kết quả và tải xuống.' },
    ],
    faq: [
      {
        q: 'Link x.com và twitter.com đều dùng được chứ?',
        a: 'Đúng. Bạn có thể dán link từ x.com, twitter.com hoặc link rút gọn t.co.',
      },
      {
        q: 'Có tải được ảnh động GIF trên X không?',
        a: 'Có. X lưu GIF dưới dạng video MP4 lặp lại; bạn có thể tải trực tiếp bản MP4 hoặc dùng công cụ đổi sang GIF trong kết quả.',
      },
      {
        q: 'Vì sao một số bài đăng X không tải được?',
        a: 'Tài khoản khoá riêng tư hoặc bài giới hạn độ tuổi yêu cầu đăng nhập X sẽ không tải được khi hệ thống thiếu phiên cookie.',
      },
    ],
  },
  {
    slug: 'tai-video-pinterest',
    slugEn: 'pinterest-video-downloader',
    en: {
      title: 'Pinterest Video & Pin Downloader | VidGrab',
      description: 'Paste a Pinterest link to save video pins or board videos to your device. Works with pinterest.com and pin.it links in your browser.',
      h1: 'Download Pinterest videos',
      intro: 'Paste a public Pinterest video pin or board link in the box below to save the video to your phone or computer.',
      steps: [
        { title: 'Copy the Pin link', text: 'Open the video pin on Pinterest, tap Share and choose Copy link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the pinterest.com or pin.it link into the box above and press download.' },
        { title: 'Save the video', text: 'Download the MP4 video file shown in the results.' },
      ],
      faq: [
        { q: 'Do short pin.it links work?', a: 'Yes. Both full pinterest.com links and short pin.it links are supported.' },
        { q: 'Can I preview a Pinterest board before downloading?', a: 'Yes. Paste a public board link or use the Bulk tab to preview and select video pins from a board.' },
        { q: 'Why was a pin skipped during board download?', a: 'Static image pins without video streams are automatically skipped when extracting videos from a board.' },
      ],
    },
    platform: 'Pinterest',
    title: 'Tải video Pinterest về máy nhanh | VidGrab',
    description:
      'Dán link Pinterest để lưu video pin hoặc bảng (board) công khai về máy. Hỗ trợ link pinterest.com và pin.it trên trình duyệt.',
    h1: 'Tải video Pinterest',
    intro:
      'Dán đường dẫn video pin hoặc bảng (board) Pinterest công khai vào ô bên dưới để lưu video về điện thoại hoặc máy tính.',
    steps: [
      { title: 'Sao chép link Pin', text: 'Mở ghim video trên Pinterest, bấm Chia sẻ rồi chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link pinterest.com hoặc pin.it vào ô phía trên và bấm tải.' },
      { title: 'Lưu video về máy', text: 'Tải tệp video MP4 hiển thị trong phần kết quả.' },
    ],
    faq: [
      {
        q: 'Link rút gọn pin.it có dùng được không?',
        a: 'Được. VidGrab hỗ trợ cả link đầy đủ pinterest.com và link rút gọn pin.it.',
      },
      {
        q: 'Có thể tải nhiều video từ một bảng (board) Pinterest không?',
        a: 'Có. Bạn có thể dán link board công khai hoặc dùng tab Tải hàng loạt để xem trước và chọn các ghim video.',
      },
      {
        q: 'Vì sao một số ghim bị bỏ qua khi tải cả bảng?',
        a: 'Các ghim chỉ có ảnh tĩnh (không phải video) sẽ được tự động bỏ qua khi lọc video.',
      },
    ],
  },
  {
    slug: 'tai-nhac-spotify',
    slugEn: 'spotify-mp3-downloader',
    en: {
      title: 'Spotify to MP3 Downloader — Tracks & Playlists | VidGrab',
      description: 'Paste a Spotify track, album or playlist link to find and download matching MP3 audio in your browser.',
      h1: 'Download Spotify tracks and playlists as MP3',
      intro: 'Paste an open.spotify.com link for a song, album or playlist below. VidGrab reads the track metadata and fetches matching MP3 audio.',
      steps: [
        { title: 'Copy the Spotify link', text: 'In Spotify, press the three dots next to a song, album or playlist, then Share and Copy link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the open.spotify.com link in the box above and press download.' },
        { title: 'Download MP3 or ZIP', text: 'Save a single MP3 track, or select tracks from a playlist/album to download.' },
      ],
      faq: [
        { q: 'How does VidGrab download music from Spotify links?', a: 'VidGrab reads the public track information from your open.spotify.com link and matches it against available external audio sources (such as SoundCloud) to provide an MP3 file.' },
        { q: 'Why does a track occasionally match a remix or different version?', a: 'Because audio is matched by title and artist from external sources, rare tracks or live versions may occasionally differ from the studio recording.' },
        { q: 'Can I preview a Spotify playlist before downloading?', a: 'Yes. Pasting a playlist or album link lets you inspect the tracklist and pick the songs you want.' },
      ],
    },
    platform: 'Spotify',
    title: 'Tải nhạc Spotify sang MP3 — Bài lẻ & Playlist | VidGrab',
    description:
      'Dán link bài hát, album hoặc playlist Spotify để tìm và tải tệp âm thanh MP3 tương ứng ngay trên trình duyệt.',
    h1: 'Tải nhạc Spotify sang MP3',
    intro:
      'Dán đường dẫn open.spotify.com của bài hát, album hoặc playlist vào ô bên dưới. VidGrab đọc thông tin bài nhạc và tìm nguồn âm thanh MP3 tương ứng.',
    steps: [
      { title: 'Sao chép link Spotify', text: 'Trong Spotify, bấm dấu ba chấm cạnh bài hát, album hoặc playlist, chọn Chia sẻ rồi Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link open.spotify.com vào ô phía trên và bấm tải.' },
      { title: 'Lưu MP3 về máy', text: 'Tải từng bài MP3 hoặc chọn các bài trong danh sách phát để tải xuống.' },
    ],
    faq: [
      {
        q: 'VidGrab tải nhạc từ link Spotify bằng cách nào?',
        a: 'VidGrab đọc thông tin bài hát công khai từ link open.spotify.com rồi đối chiếu với nguồn âm thanh bên ngoài (như SoundCloud) để tạo tệp MP3.',
      },
      {
        q: 'Vì sao đôi khi bản tải về lại là bản cover hoặc remix?',
        a: 'Do hệ thống tìm kiếm theo tên bài và nghệ sĩ trên nguồn ngoài nên một số bài hiếm có thể khớp sang bản thu khác.',
      },
      {
        q: 'Có xem trước danh sách bài trong playlist Spotify trước khi tải được không?',
        a: 'Được. Khi dán link playlist hoặc album, bạn có thể xem danh sách bài và chọn những bài muốn tải.',
      },
    ],
  },
  {
    slug: 'tai-nhac-soundcloud',
    slugEn: 'soundcloud-mp3-downloader',
    en: {
      title: 'SoundCloud to MP3 Downloader — Tracks & Sets | VidGrab',
      description: 'Paste a SoundCloud link to save public tracks, sets and playlists as MP3 audio right in your browser.',
      h1: 'Download SoundCloud tracks to MP3',
      intro: 'Paste a public soundcloud.com track, playlist or artist link in the box below to save MP3 audio to your device.',
      steps: [
        { title: 'Copy the SoundCloud link', text: 'On SoundCloud, click Share under the track or playlist and copy the link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the soundcloud.com link in the box above and press download.' },
        { title: 'Save the MP3', text: 'Download the MP3 file directly, or select tracks from a playlist.' },
      ],
      faq: [
        { q: 'Can VidGrab download whole SoundCloud playlists or artist pages?', a: 'Yes. Paste a playlist or artist link to preview the tracks and queue the ones you want.' },
        { q: 'What audio format is downloaded?', a: 'Tracks are saved as MP3 audio files compatible with any music player.' },
        { q: 'Why do some SoundCloud tracks fail?', a: 'Private tracks, Go+ subscription-only previews or geo-restricted songs cannot be downloaded.' },
      ],
    },
    platform: 'SoundCloud',
    title: 'Tải nhạc SoundCloud MP3 — Bài lẻ & Playlist | VidGrab',
    description:
      'Dán link SoundCloud để lưu bài nhạc, set nhạc hoặc playlist công khai dưới dạng MP3 ngay trên trình duyệt.',
    h1: 'Tải nhạc SoundCloud MP3',
    intro:
      'Dán đường dẫn bài nhạc, playlist hoặc trang nghệ sĩ soundcloud.com công khai vào ô bên dưới để lưu tệp MP3 về máy.',
    steps: [
      { title: 'Sao chép link SoundCloud', text: 'Trên SoundCloud, bấm nút Chia sẻ dưới bài nhạc hoặc playlist và sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link soundcloud.com vào ô phía trên rồi bấm tải.' },
      { title: 'Lưu tệp MP3', text: 'Tải trực tiếp tệp MP3 hoặc chọn các bài từ danh sách phát.' },
    ],
    faq: [
      {
        q: 'Có tải được cả playlist hoặc trang nghệ sĩ SoundCloud không?',
        a: 'Có. Khi dán link playlist hoặc nghệ sĩ, VidGrab sẽ hiển thị danh sách bài để bạn chọn tải.',
      },
      {
        q: 'Nhạc tải về có định dạng gì?',
        a: 'Âm thanh được lưu dưới dạng tệp MP3 tương thích với mọi thiết bị nghe nhạc.',
      },
      {
        q: 'Vì sao một số bài SoundCloud không tải được?',
        a: 'Bài nhạc riêng tư, bài giới hạn vùng quốc gia hoặc chỉ dành cho gói SoundCloud Go+ sẽ không tải được bản đầy đủ.',
      },
    ],
  },
  {
    slug: 'tai-video-reddit',
    slugEn: 'reddit-video-downloader',
    en: {
      title: 'Reddit Video Downloader with Audio (MP4) | VidGrab',
      description: 'Paste a Reddit post link to download videos with sound merged into a single MP4 file. Works with reddit.com and redd.it links.',
      h1: 'Download Reddit videos with sound',
      intro: 'Reddit hosts video and audio in separate streams. Paste a public reddit.com or redd.it link below and VidGrab merges them into a single MP4.',
      steps: [
        { title: 'Copy the Reddit link', text: 'On the Reddit post, tap Share and choose Copy link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the reddit.com or redd.it link in the box above and press download.' },
        { title: 'Save MP4 with audio', text: 'Pick the quality from the results and save the merged MP4 file.' },
      ],
      faq: [
        { q: 'Does the downloaded Reddit video include sound?', a: 'Yes, when the original Reddit video has an audio track, VidGrab merges the video and audio streams automatically.' },
        { q: 'Why did my downloaded Reddit video have no sound?', a: 'Many videos uploaded to Reddit are GIF-style clips that never had an audio track in the original post.' },
        { q: 'Can VidGrab download from private or gated subreddits?', a: 'Only public subreddit posts work without authentication. Quarantined or private communities cannot be accessed.' },
      ],
    },
    platform: 'Reddit',
    title: 'Tải video Reddit có tiếng (MP4) | VidGrab',
    description:
      'Dán link bài đăng Reddit để tải video kèm âm thanh đã ghép sẵn thành tệp MP4. Hỗ trợ link reddit.com và redd.it.',
    h1: 'Tải video Reddit có âm thanh',
    intro:
      'Reddit lưu hình ảnh và âm thanh ở hai luồng riêng. Dán link reddit.com hoặc redd.it công khai vào ô bên dưới để VidGrab tự ghép thành tệp MP4 hoàn chỉnh.',
    steps: [
      { title: 'Sao chép link Reddit', text: 'Ở bài đăng Reddit, bấm Chia sẻ rồi chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link reddit.com hoặc redd.it vào ô phía trên và bấm tải.' },
      { title: 'Lưu tệp MP4', text: 'Chọn chất lượng trong kết quả và tải tệp MP4 đã ghép tiếng về máy.' },
    ],
    faq: [
      {
        q: 'Video Reddit tải về có tiếng không?',
        a: 'Có. Nếu video gốc trên Reddit có luồng âm thanh, VidGrab sẽ tự động ghép hình và tiếng vào chung một tệp MP4.',
      },
      {
        q: 'Vì sao có video tải về vẫn không có tiếng?',
        a: 'Nhiều video đăng lên Reddit vốn là đoạn clip ngắn dạng GIF không có âm thanh ngay từ bản gốc.',
      },
      {
        q: 'Bài trong subreddit riêng tư có tải được không?',
        a: 'VidGrab chỉ hỗ trợ các bài đăng công khai. Các cộng đồng riêng tư hoặc bị giới hạn truy cập sẽ không tải được.',
      },
    ],
  },
  {
    slug: 'tai-video-bilibili',
    slugEn: 'bilibili-video-downloader',
    en: {
      title: 'Bilibili Video & Subtitle Downloader | VidGrab',
      description: 'Paste a Bilibili or b23.tv link to download public videos, audio and subtitles to your device right in your browser.',
      h1: 'Download Bilibili videos',
      intro: 'Paste a public bilibili.com or short b23.tv link below to save videos, MP3 audio or subtitles to your device.',
      steps: [
        { title: 'Copy the Bilibili link', text: 'Open the video on Bilibili, click Share and copy the bilibili.com or b23.tv link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the link in the box above and press download.' },
        { title: 'Choose quality and save', text: 'Pick the resolution or audio option shown in the results and download.' },
      ],
      faq: [
        { q: 'Do short b23.tv links work?', a: 'Yes. Both standard bilibili.com links and short b23.tv share links are supported.' },
        { q: 'Why are higher resolutions sometimes unavailable?', a: 'Bilibili restricts 1080p60 and 4K streams to logged-in or premium accounts when session cookies are not configured.' },
        { q: 'Can I download multi-part Bilibili videos or series?', a: 'Yes, public multi-part videos and channel batches can be queued through the Bulk tab.' },
      ],
    },
    platform: 'Bilibili',
    title: 'Tải video Bilibili và phụ đề về máy | VidGrab',
    description:
      'Dán link Bilibili hoặc b23.tv để lưu video công khai, âm thanh MP3 và phụ đề về thiết bị ngay trên trình duyệt.',
    h1: 'Tải video Bilibili',
    intro:
      'Dán đường dẫn bilibili.com hoặc link rút gọn b23.tv công khai vào ô bên dưới để lưu video, âm thanh hoặc phụ đề về máy.',
    steps: [
      { title: 'Sao chép link Bilibili', text: 'Mở video trên Bilibili, bấm Chia sẻ và sao chép link bilibili.com hoặc b23.tv.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên rồi bấm tải.' },
      { title: 'Chọn chất lượng và lưu', text: 'Chọn độ phân giải hoặc tệp âm thanh trong kết quả và tải xuống.' },
    ],
    faq: [
      {
        q: 'Link rút gọn b23.tv có dùng được không?',
        a: 'Được. Cả link bilibili.com đầy đủ và link rút gọn b23.tv đều dùng được.',
      },
      {
        q: 'Vì sao một số mức chất lượng cao không hiện ra?',
        a: 'Bilibili giới hạn các luồng 1080p tốc độ cao hoặc 4K cho tài khoản đăng nhập khi hệ thống không có phiên cookie.',
      },
      {
        q: 'Có tải được video nhiều tập trên Bilibili không?',
        a: 'Bạn có thể dùng chế độ Tải hàng loạt ở trang chủ để quét danh sách tập công khai.',
      },
    ],
  },
  {
    slug: 'tai-video-threads',
    slugEn: 'threads-video-downloader',
    en: {
      title: 'Threads Video & Photo Downloader | VidGrab',
      description: 'Paste a Threads link to download public videos, photos and multi-item posts in MP4 or original image format in your browser.',
      h1: 'Download Threads videos & photos',
      intro: 'Paste a public threads.net or threads.com post link below to save videos, photos or carousel items to your device.',
      steps: [
        { title: 'Copy the Threads link', text: 'Tap the share icon on the Threads post and choose Copy link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the threads.net or threads.com link into the box above and press download.' },
        { title: 'Save video or photos', text: 'Download the MP4 video or individual images from the post.' },
      ],
      faq: [
        { q: 'Does VidGrab support multi-photo or video carousels on Threads?', a: 'Yes. When a public Threads post contains multiple images or clips, VidGrab lists each item so you can save them individually.' },
        { q: 'Do both threads.net and threads.com links work?', a: 'Yes, both domain formats are supported.' },
        { q: 'Can I download from private Threads profiles?', a: 'Public posts work directly. Posts from private profiles are not accessible without an authorized session.' },
      ],
    },
    platform: 'Threads',
    title: 'Tải video và ảnh Threads về máy | VidGrab',
    description:
      'Dán link bài đăng Threads để lưu video MP4, hình ảnh và bài đăng nhiều mục công khai về điện thoại hoặc máy tính ngay trên trình duyệt.',
    h1: 'Tải video & ảnh Threads',
    intro:
      'Dán đường dẫn bài đăng công khai từ threads.net hoặc threads.com vào ô bên dưới để lưu video, ảnh đơn hoặc toàn bộ carousel về thiết bị.',
    steps: [
      { title: 'Sao chép link Threads', text: 'Bấm biểu tượng chia sẻ ở bài đăng Threads và chọn Sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link threads.net hoặc threads.com vào ô phía trên và bấm tải.' },
      { title: 'Lưu video hoặc ảnh', text: 'Tải tệp MP4 hoặc từng ảnh trong bài đăng về máy.' },
    ],
    faq: [
      { q: 'Bài đăng Threads có nhiều ảnh hoặc video có tải được hết không?', a: 'Được. Khi bài đăng công khai có nhiều mục, VidGrab sẽ hiển thị từng ảnh và video để bạn tải riêng.' },
      { q: 'Link threads.com và threads.net đều dùng được chứ?', a: 'Đúng, cả hai tên miền của Threads đều được hỗ trợ.' },
      { q: 'Tài khoản Threads riêng tư có tải được không?', a: 'VidGrab chỉ tải trực tiếp các bài đăng công khai.' },
    ],
  },
  {
    slug: 'tai-video-lemon8',
    slugEn: 'lemon8-video-downloader',
    en: {
      title: 'Lemon8 Video & Photo Downloader | VidGrab',
      description: 'Paste a Lemon8 link to download public videos and photo carousels to your phone or computer in your browser.',
      h1: 'Download Lemon8 videos & photos',
      intro: 'Paste a public lemon8-app.com post link below to save videos or photo slides to your device.',
      steps: [
        { title: 'Copy the Lemon8 link', text: 'Open the post on Lemon8, tap Share and copy the link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the lemon8-app.com URL into the box above and press download.' },
        { title: 'Download the file', text: 'Pick the video or image item from the result and save it.' },
      ],
      faq: [
        { q: 'Can I save Lemon8 photo posts?', a: 'Yes, public photo posts and video clips on Lemon8 are supported.' },
        { q: 'Do I need the Lemon8 app installed?', a: 'No, you only need the public share link and a web browser.' },
        { q: 'Why did a Lemon8 link fail?', a: 'Make sure the post is public and has not been removed by the creator.' },
      ],
    },
    platform: 'Lemon8',
    title: 'Tải video và ảnh Lemon8 về máy | VidGrab',
    description:
      'Dán link Lemon8 để tải video và bộ ảnh công khai về điện thoại hoặc máy tính ngay trên trình duyệt.',
    h1: 'Tải video & ảnh Lemon8',
    intro:
      'Dán đường dẫn bài đăng công khai từ lemon8-app.com vào ô bên dưới để lưu video hoặc ảnh về thiết bị.',
    steps: [
      { title: 'Sao chép link Lemon8', text: 'Mở bài đăng trên Lemon8, bấm Chia sẻ và sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link lemon8-app.com vào ô phía trên rồi bấm tải.' },
      { title: 'Lưu về thiết bị', text: 'Chọn video hoặc hình ảnh hiển thị trong kết quả để tải xuống.' },
    ],
    faq: [
      { q: 'Có tải được bài đăng dạng bộ ảnh trên Lemon8 không?', a: 'Có, các bài đăng ảnh và video công khai trên Lemon8 đều được hỗ trợ.' },
      { q: 'Có cần cài ứng dụng Lemon8 trên máy không?', a: 'Không cần. Bạn chỉ cần có link chia sẻ công khai và trình duyệt web.' },
      { q: 'Vì sao một số link Lemon8 báo lỗi?', a: 'Hãy kiểm tra xem bài đăng còn tồn tại và đang ở chế độ công khai hay không.' },
    ],
  },
  {
    slug: 'tai-video-snapchat',
    slugEn: 'snapchat-video-downloader',
    en: {
      title: 'Snapchat Spotlight & Public Story Downloader | VidGrab',
      description: 'Paste a public Snapchat Spotlight or Story link to download MP4 videos to your device right in your browser.',
      h1: 'Download Snapchat Spotlight videos',
      intro: 'Paste a public snapchat.com or story.snapchat.com link below to save Spotlight clips and public Stories in MP4.',
      steps: [
        { title: 'Copy the Snapchat link', text: 'On a public Spotlight or Story, tap Share and copy the link.' },
        { title: 'Paste it into VidGrab', text: 'Paste the snapchat.com link into the box above and press download.' },
        { title: 'Save the MP4 clip', text: 'Pick the video from the results and save it to your device.' },
      ],
      faq: [
        { q: 'What Snapchat content can be downloaded?', a: 'Public Spotlight videos and public Stories with a shareable snapchat.com link can be downloaded.' },
        { q: 'Can VidGrab download private Snaps sent in chat?', a: 'No. Private messages and friends-only Stories are not accessible.' },
        { q: 'What format are Snapchat videos saved in?', a: 'Videos are saved as standard MP4 files with audio.' },
      ],
    },
    platform: 'Snapchat',
    title: 'Tải video Snapchat Spotlight & Story công khai | VidGrab',
    description:
      'Dán link Snapchat Spotlight hoặc Story công khai để lưu video MP4 về điện thoại hoặc máy tính ngay trên trình duyệt.',
    h1: 'Tải video Snapchat Spotlight',
    intro:
      'Dán đường dẫn công khai từ snapchat.com hoặc story.snapchat.com vào ô bên dưới để lưu video Spotlight và Story công khai dạng MP4.',
    steps: [
      { title: 'Sao chép link Snapchat', text: 'Ở video Spotlight hoặc Story công khai, bấm Chia sẻ và sao chép liên kết.' },
      { title: 'Dán vào VidGrab', text: 'Dán link snapchat.com vào ô phía trên rồi bấm tải.' },
      { title: 'Lưu tệp MP4', text: 'Chọn video trong kết quả và tải tệp MP4 về máy.' },
    ],
    faq: [
      { q: 'Những nội dung nào trên Snapchat tải được?', a: 'Các video Spotlight công khai và Story công khai có đường dẫn chia sẻ đều tải được.' },
      { q: 'Tin nhắn Snap riêng tư có tải được không?', a: 'Không. Các Snap gửi riêng hoặc Story giới hạn bạn bè không thể truy cập được.' },
      { q: 'Video tải về có định dạng gì?', a: 'Video được lưu dưới dạng tệp MP4 tiêu chuẩn có kèm âm thanh.' },
    ],
  },
  {
    slug: 'tai-video-vk',
    slugEn: 'vk-video-downloader',
    en: {
      title: 'VK Video Downloader — Save Public VK Videos in MP4 | VidGrab',
      description: 'Paste a vk.com or vkvideo.ru link to download public VK videos and playlists in MP4 or MP3 right in your browser.',
      h1: 'Download VK videos',
      intro: 'Paste a public vk.com or vkvideo.ru video link below to choose a resolution and save it to your device.',
      steps: [
        { title: 'Copy the VK video link', text: 'Open the video on VK, click Share / Export and copy the direct video URL.' },
        { title: 'Paste it into VidGrab', text: 'Paste the vk.com or vkvideo.ru link into the box above and press download.' },
        { title: 'Choose quality and save', text: 'Pick the resolution offered for the video and download the MP4 file.' },
      ],
      faq: [
        { q: 'Do both vk.com and vkvideo.ru links work?', a: 'Yes, public video URLs from both vk.com and vkvideo.ru are supported.' },
        { q: 'What resolutions are available for VK videos?', a: 'You can pick from the resolutions uploaded by the creator (such as 360p, 720p or 1080p), within your plan quality cap.' },
        { q: 'Why did a VK video fail to load?', a: 'Videos restricted to logged-in VK members or closed community groups cannot be fetched without access.' },
      ],
    },
    platform: 'VK',
    title: 'Tải video VK (VKontakte) về máy định dạng MP4 | VidGrab',
    description:
      'Dán link vk.com hoặc vkvideo.ru để tải video công khai trên VK về điện thoại hoặc máy tính với các mức chất lượng hiện có.',
    h1: 'Tải video VK',
    intro:
      'Dán đường dẫn video công khai từ vk.com hoặc vkvideo.ru vào ô bên dưới để chọn độ phân giải và lưu tệp MP4 về máy.',
    steps: [
      { title: 'Sao chép link video VK', text: 'Mở video trên VK, bấm Chia sẻ và sao chép đường dẫn trực tiếp của video.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vk.com hoặc vkvideo.ru vào ô phía trên và bấm tải.' },
      { title: 'Chọn chất lượng và lưu', text: 'Chọn độ phân giải hiển thị trong kết quả và tải tệp MP4 xuống.' },
    ],
    faq: [
      { q: 'Cả link vk.com và vkvideo.ru đều dùng được chứ?', a: 'Được, VidGrab hỗ trợ cả hai tên miền video của VK.' },
      { q: 'Có những mức độ phân giải nào?', a: 'Các mức độ phân giải do người đăng tải lên (như 360p, 720p, 1080p) sẽ hiển thị theo giới hạn gói của bạn.' },
      { q: 'Vì sao có video VK không tải được?', a: 'Video nằm trong nhóm kín hoặc yêu cầu đăng nhập tài khoản VK sẽ không thể tải nếu không có quyền truy cập.' },
    ],
  },
  {
    slug: 'tai-video-twitch',
    slugEn: 'twitch-clip-vod-downloader',
    en: {
      title: 'Twitch Clip & VOD Downloader | VidGrab',
      description: 'Paste a Twitch clip or public VOD link to download MP4 video or MP3 audio to your device right in your browser.',
      h1: 'Download Twitch clips & VODs',
      intro: 'Paste a public twitch.tv video or clips.twitch.tv link below to save clips and past broadcasts in MP4.',
      steps: [
        { title: 'Copy the Twitch link', text: 'Open the Twitch clip or past broadcast (VOD) and copy the URL from your browser or Share menu.' },
        { title: 'Paste it into VidGrab', text: 'Paste the twitch.tv or clips.twitch.tv link into the box above and press download.' },
        { title: 'Pick quality and save', text: 'Select the resolution you want from the available streams and download.' },
      ],
      faq: [
        { q: 'Can I download both Twitch Clips and full VODs?', a: 'Public Twitch Clips work quickly; longer VODs can also be downloaded when they are publicly available and within processing timeouts.' },
        { q: 'Can subscriber-only VODs be downloaded?', a: 'No. Subscriber-only broadcasts are restricted by Twitch.' },
        { q: 'Why is part of the audio muted in a Twitch VOD?', a: 'When Twitch mutes copyrighted music in the original VOD stream, the downloaded file reflects that muted audio.' },
      ],
    },
    platform: 'Twitch',
    title: 'Tải Twitch Clip và VOD về máy định dạng MP4 | VidGrab',
    description:
      'Dán link Twitch Clip hoặc VOD công khai từ twitch.tv để lưu video MP4 hoặc âm thanh về máy tính và điện thoại.',
    h1: 'Tải Twitch Clip & VOD',
    intro:
      'Dán đường dẫn công khai từ twitch.tv hoặc clips.twitch.tv vào ô bên dưới để lưu các đoạn clip highlight hoặc video phát lại.',
    steps: [
      { title: 'Sao chép link Twitch', text: 'Mở clip hoặc video VOD trên Twitch, bấm Chia sẻ và sao chép đường dẫn.' },
      { title: 'Dán vào VidGrab', text: 'Dán link twitch.tv hoặc clips.twitch.tv vào ô phía trên và bấm tải.' },
      { title: 'Chọn chất lượng và tải', text: 'Chọn độ phân giải trong danh sách kết quả và lưu tệp MP4 về máy.' },
    ],
    faq: [
      { q: 'Có tải được cả Twitch Clip ngắn và VOD dài không?', a: 'Các đoạn Clip ngắn tải rất nhanh; VOD công khai cũng tải được nếu thời lượng nằm trong giới hạn xử lý.' },
      { q: 'VOD giới hạn cho người đăng ký (Sub-only) có tải được không?', a: 'Không. Các VOD chỉ dành cho hội viên trả phí trên Twitch không thể truy cập.' },
      { q: 'Vì sao một đoạn trong VOD tải về bị mất tiếng?', a: 'Nếu bản VOD gốc trên Twitch đã bị tắt tiếng do nhạc bản quyền thì tệp tải về cũng sẽ bị tắt tiếng ở đoạn đó.' },
    ],
  },
  {
    slug: 'tai-video-rumble',
    slugEn: 'rumble-video-downloader',
    en: {
      title: 'Rumble Video Downloader — Save Public Rumble Videos | VidGrab',
      description: 'Paste a rumble.com link to download public Rumble videos in MP4 or extract MP3 audio right in your browser.',
      h1: 'Download Rumble videos',
      intro: 'Paste a public rumble.com video link below to view available resolutions and download the MP4 file.',
      steps: [
        { title: 'Copy the Rumble link', text: 'Open the video on Rumble and copy the URL from the address bar or Share button.' },
        { title: 'Paste it into VidGrab', text: 'Paste the rumble.com link into the box above and press download.' },
        { title: 'Select quality and save', text: 'Choose your preferred resolution or MP3 audio option and download.' },
      ],
      faq: [
        { q: 'What quality options are available for Rumble?', a: 'VidGrab shows the MP4 resolutions provided by Rumble for that video, up to your plan limit.' },
        { q: 'Can I extract MP3 audio from a Rumble talk or podcast?', a: 'Yes, choose the MP3 option in the results to save audio only.' },
        { q: 'Do live streams work?', a: 'Completed public broadcasts work once Rumble finishes processing the replay.' },
      ],
    },
    platform: 'Rumble',
    title: 'Tải video Rumble về máy định dạng MP4, MP3 | VidGrab',
    description:
      'Dán link rumble.com để tải video công khai trên Rumble dưới dạng MP4 hoặc tách nhạc MP3 ngay trên trình duyệt.',
    h1: 'Tải video Rumble',
    intro:
      'Dán đường dẫn video công khai từ rumble.com vào ô bên dưới để xem các độ phân giải hiện có và tải về thiết bị.',
    steps: [
      { title: 'Sao chép link Rumble', text: 'Mở video trên Rumble và sao chép URL trên thanh địa chỉ hoặc nút Chia sẻ.' },
      { title: 'Dán vào VidGrab', text: 'Dán link rumble.com vào ô phía trên rồi bấm tải.' },
      { title: 'Chọn chất lượng và lưu', text: 'Chọn độ phân giải MP4 hoặc mục tách âm thanh MP3 để tải xuống.' },
    ],
    faq: [
      { q: 'Video Rumble tải được ở những độ phân giải nào?', a: 'VidGrab hiển thị các mức độ phân giải MP4 mà Rumble cung cấp cho video đó, theo giới hạn gói của bạn.' },
      { q: 'Có thể chỉ tải âm thanh MP3 từ video Rumble không?', a: 'Có, bạn có thể chọn định dạng MP3 trong kết quả để chỉ lưu phần âm thanh.' },
      { q: 'Video phát trực tiếp có tải được không?', a: 'Các buổi phát trực tiếp công khai sẽ tải được sau khi kết thúc và được Rumble xử lý thành bản xem lại.' },
    ],
  },
  {
    slug: 'tai-video-odysee',
    slugEn: 'odysee-video-downloader',
    en: {
      title: 'Odysee Video & Audio Downloader | VidGrab',
      description: 'Paste an Odysee link to download public videos in MP4 or extract MP3 audio to your device right in your browser.',
      h1: 'Download Odysee videos',
      intro: 'Paste a public odysee.com video link below to save MP4 video or MP3 audio to your computer or phone.',
      steps: [
        { title: 'Copy the Odysee link', text: 'Open the video on Odysee, click Share and copy the odysee.com URL.' },
        { title: 'Paste it into VidGrab', text: 'Paste the link into the box above and press download.' },
        { title: 'Download MP4 or MP3', text: 'Select the video resolution or audio option from the results and save.' },
      ],
      faq: [
        { q: 'Are both video and audio downloads supported for Odysee?', a: 'Yes, you can save the full MP4 video or extract MP3 audio.' },
        { q: 'Can paid or rental Odysee videos be downloaded?', a: 'No, only free public uploads on Odysee are accessible.' },
        { q: 'Does it work on mobile browsers?', a: 'Yes, you can paste Odysee links in any modern mobile or desktop browser.' },
      ],
    },
    platform: 'Odysee',
    title: 'Tải video và nhạc Odysee về máy | VidGrab',
    description:
      'Dán link odysee.com để tải video công khai dạng MP4 hoặc tách âm thanh MP3 về điện thoại và máy tính.',
    h1: 'Tải video Odysee',
    intro:
      'Dán đường dẫn video công khai từ odysee.com vào ô bên dưới để lưu tệp MP4 hoặc âm thanh MP3 về thiết bị.',
    steps: [
      { title: 'Sao chép link Odysee', text: 'Mở video trên Odysee, bấm Chia sẻ và sao chép liên kết odysee.com.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên rồi bấm tải.' },
      { title: 'Lưu MP4 hoặc MP3', text: 'Chọn độ phân giải video hoặc tệp âm thanh trong kết quả để tải về.' },
    ],
    faq: [
      { q: 'VidGrab có hỗ trợ tải cả hình và tiếng trên Odysee không?', a: 'Có, bạn có thể lưu video MP4 đầy đủ hoặc chỉ tách riêng tệp nhạc MP3.' },
      { q: 'Nội dung trả phí trên Odysee có tải được không?', a: 'Không, công cụ chỉ hỗ trợ các video được đăng công khai và miễn phí.' },
      { q: 'Có dùng được trên điện thoại không?', a: 'Được, bạn có thể dán link trực tiếp trên trình duyệt điện thoại hoặc máy tính.' },
    ],
  },
  {
    slug: 'tai-video-dailymotion',
    slugEn: 'dailymotion-video-downloader',
    en: {
      title: 'Dailymotion Video Downloader — Save MP4 & MP3 | VidGrab',
      description: 'Paste a Dailymotion link to download public videos in MP4 or extract MP3 audio right in your browser.',
      h1: 'Download Dailymotion videos',
      intro: 'Paste a public dailymotion.com or dai.ly link below to pick a resolution and save the video to your device.',
      steps: [
        { title: 'Copy the Dailymotion link', text: 'Open the video on Dailymotion, click Share and copy the dailymotion.com or dai.ly URL.' },
        { title: 'Paste it into VidGrab', text: 'Paste the link into the box above and press download.' },
        { title: 'Pick quality and save', text: 'Choose the resolution or MP3 audio option from the results and download.' },
      ],
      faq: [
        { q: 'Do short dai.ly links work?', a: 'Yes, both standard dailymotion.com URLs and short dai.ly share links are supported.' },
        { q: 'What resolutions can I download from Dailymotion?', a: 'Available resolutions (such as 360p, 480p, 720p or 1080p) are listed based on the original upload and your plan cap.' },
        { q: 'Can geo-restricted or private Dailymotion videos be downloaded?', a: 'Private or password-protected videos cannot be downloaded without access.' },
      ],
    },
    platform: 'Dailymotion',
    title: 'Tải video Dailymotion về máy định dạng MP4, MP3 | VidGrab',
    description:
      'Dán link Dailymotion hoặc dai.ly để tải video công khai dạng MP4 hoặc tách nhạc MP3 về máy tính và điện thoại.',
    h1: 'Tải video Dailymotion',
    intro:
      'Dán đường dẫn dailymotion.com hoặc link rút gọn dai.ly công khai vào ô bên dưới để chọn độ phân giải và lưu video về máy.',
    steps: [
      { title: 'Sao chép link Dailymotion', text: 'Mở video trên Dailymotion, bấm Chia sẻ và sao chép link dailymotion.com hoặc dai.ly.' },
      { title: 'Dán vào VidGrab', text: 'Dán link vào ô phía trên và bấm tải.' },
      { title: 'Chọn chất lượng và lưu', text: 'Chọn độ phân giải MP4 hoặc tách âm thanh MP3 trong kết quả để tải về.' },
    ],
    faq: [
      { q: 'Link rút gọn dai.ly có dùng được không?', a: 'Được, cả link dailymotion.com đầy đủ lẫn link rút gọn dai.ly đều được hỗ trợ.' },
      { q: 'Có những độ phân giải nào khi tải Dailymotion?', a: 'Các mức chất lượng có sẵn ở video gốc (360p, 480p, 720p, 1080p) sẽ hiển thị theo giới hạn gói của bạn.' },
      { q: 'Video riêng tư hoặc đặt mật khẩu có tải được không?', a: 'Không, VidGrab chỉ hỗ trợ các video Dailymotion công khai.' },
    ],
  },
];
