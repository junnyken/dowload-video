## 0.23.3
- Tải siêu tốc **Google Drive "Chỉ xem" (View-Only)** bị tắt nút tải bằng 16 kết nối song song (`aria2c -x16 -s16`) cho luồng `/videoplayback`, đẩy tốc độ từ ~150 KB/s lên 10–30 MB/s.
- Hỗ trợ tải trọn bộ **Thư mục Google Drive**, nâng cấp trích xuất **OneDrive / SharePoint**, **Mega.nz** (giải mã trực tiếp file lớn 1–4 GB) và **Terabox** đa luồng.
- Sửa lỗi biên dịch TypeScript (`allowImportingTsExtensions`) và đồng bộ tự động phát hành cả bản Windows x64 (`.exe`) lẫn macOS Apple Silicon (`.dmg`).

## 0.23.2
- Khắc phục lỗi khi bấm nút tải nổi kiểu IDM trên Douyin (`douyin.com/jingxuan?modal_id=...`) hoặc TikTok feed chuyển sang App Windows bị nhận nhầm là luồng `Other · Tải trên máy này`: tự động chuẩn hóa URL sang `douyin.com/video/<id>` và gửi thẳng luồng MP4 gốc không logo kèm Referer sang App Windows.
- Khắc phục triệt để lỗi Tiện ích Chrome (`v5.30.3`) tải lặp lại video đầu tiên khi lướt sang các video tiếp theo trên Douyin/Reels: bắt đầy đủ các API feed (`/tab/feed/`, `/channel/feed/`, `/module/feed/`, `cards`) và ưu tiên đúng thẻ `<video>` đang phát trên màn hình.

## 0.23.1
- Đồng bộ phiên đăng nhập Cloud Drive (Google Drive, OneDrive, Terabox) trực tiếp trong Desktop App (Cài đặt -> Tài khoản nền tảng, mã hóa DPAPI) và tự động nhận cookie phiên trình duyệt từ Tiện ích Chrome `v5.30.1` giúp tải tệp chia sẻ riêng tư hoặc vượt hạn mức tải công khai.
- Mở khóa hoàn toàn luồng nhận video & stream từ Tiện ích Chrome sang Desktop App cho mọi tài khoản đã đăng nhập (kể cả gói Free/Starter/Pro) và tự động bắt liên kết Cloud Drive từ Clipboard để tải siêu tốc 16 kênh `aria2c`.
- Hỗ trợ duyệt thư mục đệ quy hàng loạt (`CloudExpander`) cho Google Drive, PikPak, Terabox, Mega, OneDrive, Dropbox và mở khóa liên kết có mật khẩu (`pass_code`) ngay trên giao diện Web.

## 0.23.0
- Mở khóa tính năng "Xem là Tải" (Send-to-App qua cầu nối Native với Tiện ích Chrome `v5.30.0`) cho toàn bộ tài khoản đã đăng nhập: tự động đẩy luồng video (VOD, HLS, DASH, Live) kèm phiên cookie trình duyệt và phụ đề sang App để tải siêu tốc 16 kênh `aria2c`.
- Mở rộng hỗ trợ tải trực tiếp từ các kho lưu trữ đám mây: Google Drive (tự xử lý cảnh báo quét virus file lớn & hướng dẫn vượt hạn mức chia sẻ), OneDrive/SharePoint, Mega.nz (giải mã AES-CTR cục bộ), Terabox, Dropbox và PikPak v2 (duyệt thư mục đệ quy, mọi định dạng tệp, link có mật khẩu).
- Bổ sung nguồn mạng xã hội & Livestream mới: Telegram (kênh công khai & đăng nhập MTProto tài khoản cá nhân an toàn), Weibo, Xigua/Toutiao và ghi hình Livestream trực tiếp (TikTok, YouTube, Twitch, Kick).
- Hỗ trợ chính thức bản cài đặt macOS Apple Silicon (`.dmg` M1/M2/M3/M4) song song với Windows x64.

## 0.22.11
- Tích hợp sẵn bộ tăng tốc đa luồng `aria2c` (`1.37.0`, kiểm chứng toàn vẹn SHA-256) đẩy tốc độ tải file lẻ lớn (`.mp4`, `.mkv` từ PikPak / Cloud) lên 16 kết nối song song chuẩn IDM.
- Hiển thị trạng thái sẵn sàng của bộ tăng tốc đa luồng 16 kênh và phiên bản `aria2c` ngay trong màn hình Cài đặt.
- Đồng bộ kiểm soát chế độ Tải siêu tốc (IDM Turbo) cho tài khoản đăng nhập, tự động hạ về luồng tiêu chuẩn cho Khách chưa đăng nhập.

## 0.22.10
- Hỗ trợ tải trực tiếp video từ liên kết chia sẻ PikPak Cloud (`mypikpak.com/s/...`) qua máy chủ VidGrab kết hợp chia khối 10MB.
- Mở khóa chế độ Tải siêu tốc kiểu IDM (chia khối 10MB, tự nối `.part` khi đứt mạng 90–99%, tự làm mới link 403) cho toàn bộ tài khoản đã đăng ký (từ gói Free trở lên).
- Hiển thị thông báo rõ ràng khi dán liên kết từ trang web chưa hỗ trợ thay vì báo lỗi không xác định, và đồng bộ giới hạn 720p cho Khách chưa đăng nhập.

## 0.22.9
- Tải file đơn trực tiếp (`.mp4`, `.mkv` từ PikPak / Cloud) theo khối `--http-chunk-size 10M` chuẩn IDM, chống bóp băng thông kết nối đơn và chống ngắt socket cuối file.
- Tự động nối lại ngầm (Auto-Resume `.part` tối đa 3 lần) ngay trong tiến trình tải khi gặp lỗi mạng tạm thời hoặc đứt kết nối SSL/EOF ở 90–99%.
- Tự động làm mới địa chỉ luồng phát đã hết hạn (`refresh_stream` qua cầu nối Native với Tiện ích Chrome `v5.29.3`) khi gặp lỗi `403 Forbidden` và tiếp tục tải nối vào tệp `.part` đang dở.
- Giữ nguyên thanh tiến độ `%` và dung lượng đã lưu trên thẻ Hàng đợi khi Tạm dừng, Chờ tải tiếp hoặc Lỗi.

## 0.22.8
- Khắc phục lỗi biên dịch Rust unit test (`ext_bridge.rs`) giúp CI phát hành bản cài đặt Windows ký số tự động (`vidgrab-releases`).
- Đồng bộ phiên bản với Web Studio & Tiện ích `v5.29.2`, nâng độ ổn định kết nối và hoàn tất luồng dịch phụ đề AI + nhúng mềm MP4.

## 0.22.7
- Tối ưu ổn định kết nối & giảm nghẽn mạng: đặt timeout rõ ràng cho mọi lời gọi API (`4.5s`–`12s`, `45s` cho phân tích máy chủ) và cơ chế tự ngắt mạch tạm thời (`30s`–`45s`) khi máy chủ phản hồi chậm, không còn kẹt UI hay chậm hàng đợi tải cục bộ.
- Tăng tốc kiểm tra toàn vẹn sidecar (`yt-dlp`, `ffmpeg`, `ffprobe`, `deno`) bằng SHA-256 đa luồng song song giúp khởi động lượt tải nhanh hơn.
- Giữ toàn bộ cải tiến của `0.22.6`: `--concurrent-fragments 8`, tải song song phụ đề HLS 6 luồng, tách phụ đề nhúng trong MP4/MKV để dịch AI 1 chạm ngay ở Hàng đợi và tự gắn mềm `.vi.srt` thành track mặc định.

- Tự động nhúng phụ đề mềm (Soft-Subtitle mov_text) trực tiếp vào tệp .mp4 (song song giữ tệp .srt rời) không cần mã hóa lại hình ảnh/âm thanh, giúp bật/tắt phụ đề ngay trên VLC, MPC-HC, QuickTime và Windows Media Player.
- Hỗ trợ chọn luồng âm thanh đa ngôn ngữ (Thuyết minh / Lồng tiếng / Âm thanh gốc) từ tiện ích trình duyệt chuyển thẳng sang yt-dlp cục bộ.
- Tự động gom các tập phim dài kỳ / anime vào thư mục riêng theo tên bộ phim, hỗ trợ tự động tải tiếp khi chuyển tập và chuyển tiếp phiên phát tạm thời trong RAM (gói Team).
- Cải thiện nhận diện luồng HEVC, AV1, đa luồng âm thanh và bảo vệ an toàn các nội dung khóa bản quyền DRM.
