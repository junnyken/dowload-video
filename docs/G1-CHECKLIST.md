# Cổng G1 — Ra mắt mềm (cộng đồng Việt, không quảng cáo)

Điều kiện lấy từ `docs/PHASE-34-PLAN.md` §2. Đo ngày 13/10/2026 lúc 15:10 UTC (22:10 giờ VN) trên production.
✅ = có bằng chứng · ⚠️ = owner báo đã làm, tôi chưa kiểm được · ❌ = chưa đạt.

| # | Điều kiện | Trạng thái | Bằng chứng / việc còn lại |
|---|---|---|---|
| 1 | 34-0 kiểm thật xong | ⚠️ | `docs/PHASE-34-0-EVIDENCE.md`. Các luồng owner bấm thử (Theo dõi kênh, IG Watch Pro, kênh Xiaohongshu, Android, chữ ký lượt tải): owner báo xong, chưa có ảnh/ghi chép. |
| 2 | Giám sát ngoài + cảnh báo chạy | ⚠️ | UptimeRobot đã dựng (5 monitor). Telegram: owner quyết định không dùng → cảnh báo chỉ qua email UptimeRobot, chưa kiểm email có tới. |
| 2b | Link trang trạng thái | ✅ | `GET /api/v1/site-links` → `https://stats.uptimerobot.com/IrnzR8t2Rm` (HTTP 200, máy của UptimeRobot), hiện ở chân trang web. Máy chủ từ chối địa chỉ mẫu / địa chỉ trên vibe1. |
| 3 | 21/21 nền tảng có probe, ≤2 lỗi có lý do | ✅ | **Quyết định owner 13/10 (phương án b): Douyin, Xiaohongshu KHÔNG quảng bá ở G1**, không bật probe trả phí; web đã tự ẩn (trang chủ bỏ nền tảng trạng thái "unknown", trang SEO chỉ hiện "healthy": `LandingPage.jsx`, `PlatformLandingPage.jsx`). Còn 19 nền tảng có probe: 16 khoẻ · 2 lỗi (Instagram, Rumble) · Odysee "chưa xác nhận". Lý do (Admin, 13/10): **Instagram** — link mẫu đặt trong admin `reel/DeEll8vtSeu` đòi đăng nhập ("Instagram API is not granting access", kiểm lại từ workspace); reel mặc định `DeJjs2ipNPr` đọc được ẩn danh → lỗi ở link mẫu, không phải tải Instagram hỏng; owner đã đổi link mẫu sang `DeJjs2ipNPr`; **probe xanh** (kiểm 14/10: 18 khoẻ, Instagram + Odysee khoẻ, chỉ Rumble lỗi có lý do). **Rumble** — 403 từ IP máy chủ, owner chấp nhận là giới hạn. |
| 4 | Quét bí mật lịch sử git + đổi khoá k1 | ⚠️ | Quét xong: `docs/security/SECRET-SCAN-2026-10-13.md`. Đổi k1: app 0.14.x đã mang khoá k2; máy chủ chưa chuyển sang ký bằng k2 (chờ đa số người dùng ≥0.14, task #6330). |
| 5 | Trang Điều khoản + Gỡ nội dung | ✅ | `/terms`, `/takedown`, `/faq`, `/privacy` đều 200. |
| 6 | Diễn tập khôi phục 1 lần | ⏸ | **Owner hoãn (13/10)**: chưa làm lúc này. Rủi ro chấp nhận: thời gian khôi phục chưa đo, quy trình chỉ mới kiểm bằng test. |
| 7 | Tiện ích trên Chrome Web Store | ⏸ | **Owner quyết định (13/10): không đưa lên Chrome Web Store**; phát hành bằng tệp tải về + trang hướng dẫn `/extension`. Bù: tiện ích 5.6.0 tự báo khi có bản mới (banner cập nhật). |

## Để qua G1
1. ~~Trang trạng thái~~ — xong.
2. ~~Probe Douyin + Xiaohongshu~~ — đã chốt không quảng bá ở G1 (13/10).
3. ~~Instagram: đổi link mẫu~~ — xong, chờ probe xanh.
4. ~~Chrome Web Store~~ (không dùng); diễn tập khôi phục hoãn theo owner.
5. Chuyển máy chủ ký lượt tải bằng k2 khi số máy ≥0.14 đủ lớn (đo ở Admin → Đo lường / App Windows).
