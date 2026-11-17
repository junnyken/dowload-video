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
