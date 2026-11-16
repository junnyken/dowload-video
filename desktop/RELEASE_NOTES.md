## 0.22.7
- Tối ưu ổn định kết nối & giảm nghẽn mạng: đặt timeout rõ ràng cho mọi lời gọi API (`4.5s`–`12s`, `45s` cho phân tích máy chủ) và cơ chế tự ngắt mạch tạm thời (`30s`–`45s`) khi máy chủ phản hồi chậm, không còn kẹt UI hay chậm hàng đợi tải cục bộ.
- Tăng tốc kiểm tra toàn vẹn sidecar (`yt-dlp`, `ffmpeg`, `ffprobe`, `deno`) bằng SHA-256 đa luồng song song giúp khởi động lượt tải nhanh hơn.
- Giữ toàn bộ cải tiến của `0.22.6`: `--concurrent-fragments 8`, tải song song phụ đề HLS 6 luồng, tách phụ đề nhúng trong MP4/MKV để dịch AI 1 chạm ngay ở Hàng đợi và tự gắn mềm `.vi.srt` thành track mặc định.

- Tự động nhúng phụ đề mềm (Soft-Subtitle mov_text) trực tiếp vào tệp .mp4 (song song giữ tệp .srt rời) không cần mã hóa lại hình ảnh/âm thanh, giúp bật/tắt phụ đề ngay trên VLC, MPC-HC, QuickTime và Windows Media Player.
- Hỗ trợ chọn luồng âm thanh đa ngôn ngữ (Thuyết minh / Lồng tiếng / Âm thanh gốc) từ tiện ích trình duyệt chuyển thẳng sang yt-dlp cục bộ.
- Tự động gom các tập phim dài kỳ / anime vào thư mục riêng theo tên bộ phim, hỗ trợ tự động tải tiếp khi chuyển tập và chuyển tiếp phiên phát tạm thời trong RAM (gói Team).
- Cải thiện nhận diện luồng HEVC, AV1, đa luồng âm thanh và bảo vệ an toàn các nội dung khóa bản quyền DRM.
