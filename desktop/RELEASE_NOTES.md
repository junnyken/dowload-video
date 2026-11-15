## 0.22.6
- Tăng tốc tải HLS/DASH không cần cookie lên `--concurrent-fragments 8` kèm cơ chế tự khôi phục khi rớt mảnh (`--fragment-retries 10`, backoff lũy thừa, timeout 20s) và tải song song phụ đề HLS theo 6 luồng.
- Nâng cấp dịch phụ đề AI 1 chạm (thêm nút ngay tại tab Hàng đợi -> Hoàn tất): tự tách phụ đề nhúng trong MP4/MKV để dịch sang tiếng Việt mà không phải tải lại video, và tự động gắn mượt bản `.vi.srt` thành track mặc định vào MP4.
- Tách link thông minh hơn từ văn bản chia sẻ dính dấu câu CJK/ngoặc đơn (Douyin, Bilibili, Xiaohongshu, Zalo), mở rộng nhận diện Dailymotion, Twitch, VK Video, Rumble, Odysee, Lemon8, Snapchat, `fb.gg`, và ứng dụng AI làm sạch tên phim / tập (`SS2 EP14`, `OVA`, `[08/24]`).

- Tự động nhúng phụ đề mềm (Soft-Subtitle mov_text) trực tiếp vào tệp .mp4 (song song giữ tệp .srt rời) không cần mã hóa lại hình ảnh/âm thanh, giúp bật/tắt phụ đề ngay trên VLC, MPC-HC, QuickTime và Windows Media Player.
- Hỗ trợ chọn luồng âm thanh đa ngôn ngữ (Thuyết minh / Lồng tiếng / Âm thanh gốc) từ tiện ích trình duyệt chuyển thẳng sang yt-dlp cục bộ.
- Tự động gom các tập phim dài kỳ / anime vào thư mục riêng theo tên bộ phim, hỗ trợ tự động tải tiếp khi chuyển tập và chuyển tiếp phiên phát tạm thời trong RAM (gói Team).
- Cải thiện nhận diện luồng HEVC, AV1, đa luồng âm thanh và bảo vệ an toàn các nội dung khóa bản quyền DRM.
