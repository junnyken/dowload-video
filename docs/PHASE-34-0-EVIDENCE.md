# Phase 34-0 — Bảng bằng chứng (task #6329)

Đo trên production (dvid / dvid-api .vibe1.tinhgon.xyz), không đăng nhập trừ khi ghi rõ. "Chưa kiểm" = chưa có bằng chứng, không suy đoán.

## 13/10/2026

| Luồng / mục | Kết quả | Giờ (VN) | Bằng chứng |
|---|---|---|---|
| Host vibe1 | **Suy giảm** 09:40–11:30: TCP lúc không mở được, bắt tay TLS 5–14 s, /health timeout; Vibe Host deploy "fetch failed 6/6"; log "wings_error" | 09:40–11:30 | curl có đo thời gian từng pha; job deploy thất bại |
| Host vibe1 | Phục hồi: /health 0,4–0,8 s | 11:25 → | curl |
| Tải TikTok (@nba/7390205678729579807) | ✅ 200, 1,4 s (lúc host chậm: 118 s) | 11:41 | POST /api/v1/fetch-link video_fast |
| Tải Instagram (reel DeJjs2ipNPr) | ✅ 200, 4,9 s. Sáng: 504 extraction_timeout do host chậm (Cobalt-first vượt 30 s), không phải Instagram chặn — yt-dlp từ workspace đọc được cùng lúc | 11:41 | fetch-link + yt-dlp -s |
| Tải X (CaptainAmerica/1866159498557812761) | ❌ 400 "X yêu cầu tài khoản đăng nhập…" cho bài này; probe X (link chuẩn) vẫn khoẻ | 11:41 | fetch-link |
| Tải Facebook (cnn/videos/1036093534830440) | ❌ 500 processing_failed cho video này; probe Facebook (link chuẩn) vẫn khoẻ | 11:41 | fetch-link |
| Sức khoẻ nền tảng | 12 khoẻ, 2 lỗi (Instagram probe, Odysee — link Odysee đã thay, chờ lượt chạy), 5 probe mới chờ lượt đầu, Douyin/Xiaohongshu chưa đặt (trả phí, admin bật) | 11:40 | /api/v1/platform-status |
| Cập nhật app | ✅ /client/version latest 0.13.1 + sha256; 0.13.0 → mời cập nhật (200) | 10:15 | curl |
| yt-dlp cho app | ✅ /client/ytdlp 204 (chưa bật — đúng mặc định) | 11:40 | curl |
| Trang /terms, /takedown, /faq | ✅ 200 | 11:40 | curl |
| Gửi gỡ nội dung sai dữ liệu | ✅ 422 (kiểm hợp lệ chạy) | 11:40 | curl |
| Admin metrics / takedown khi chưa đăng nhập | ✅ 401 | 11:40 | curl |
| Theo dõi kênh TikTok @cz3z qua proxy dự phòng | Chưa kiểm (log Vibe Host không đọc được cả ngày; chờ ảnh màn app của owner) | — | — |
| Instagram Watch (Pro) | Chưa kiểm (chưa có kênh IG nào được thêm) | — | — |
| Tải kênh Xiaohongshu, chia sẻ Android, 2 bước bấm thử chữ ký lượt tải | Chưa kiểm | — | — |

## Danh sách đang hỏng / cần xem
1. Host vibe1 suy giảm từng đợt — owner báo người quản trị Vibe Host; bật giám sát ngoài (docs/runbooks/external-monitoring.md).
2. X: một số bài báo cần đăng nhập — xem kho cookie X trong admin.
3. Facebook: một số video báo lỗi xử lý — xem lại với link khác, kho cookie.
4. Probe Instagram còn đỏ dù tải thật đã chạy — chờ lượt probe kế tiếp; nếu vẫn đỏ thì soi đường probe.
