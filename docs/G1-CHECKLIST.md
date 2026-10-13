# Cổng G1 — Ra mắt mềm (cộng đồng Việt, không quảng cáo)

Điều kiện lấy từ `docs/PHASE-34-PLAN.md` §2. Đo ngày 13/10/2026 lúc 15:10 UTC (22:10 giờ VN) trên production.
✅ = có bằng chứng · ⚠️ = owner báo đã làm, tôi chưa kiểm được · ❌ = chưa đạt.

| # | Điều kiện | Trạng thái | Bằng chứng / việc còn lại |
|---|---|---|---|
| 1 | 34-0 kiểm thật xong | ⚠️ | `docs/PHASE-34-0-EVIDENCE.md`. Các luồng owner bấm thử (Theo dõi kênh, IG Watch Pro, kênh Xiaohongshu, Android, chữ ký lượt tải): owner báo xong, chưa có ảnh/ghi chép. |
| 2 | Giám sát ngoài + cảnh báo chạy | ⚠️ | UptimeRobot đã dựng (5 monitor). Telegram: owner quyết định không dùng → cảnh báo chỉ qua email UptimeRobot, chưa kiểm email có tới. |
| 2b | Link trang trạng thái | ❌ | `GET /api/v1/site-links` trả `https://stats.uptimerobot.com/...`: chữ mẫu, **link chết**. Dán địa chỉ thật (Admin → Công tắc khẩn cấp). |
| 3 | 21/21 nền tảng có probe, ≤2 lỗi có lý do | ⚠️ | **Quyết định owner 13/10 (phương án b): Douyin, Xiaohongshu KHÔNG quảng bá ở G1**, không bật probe trả phí; web đã tự ẩn (trang chủ bỏ nền tảng trạng thái "unknown", trang SEO chỉ hiện "healthy": `LandingPage.jsx`, `PlatformLandingPage.jsx`). Còn 19 nền tảng có probe: 16 khoẻ · 2 lỗi (Instagram, Rumble) · Odysee "chưa xác nhận". Còn thiếu: lý do Instagram; Rumble ghi "giới hạn đã biết". |
| 4 | Quét bí mật lịch sử git + đổi khoá k1 | ⚠️ | Quét xong: `docs/security/SECRET-SCAN-2026-10-13.md`. Đổi k1: app 0.14.x đã mang khoá k2; máy chủ chưa chuyển sang ký bằng k2 (chờ đa số người dùng ≥0.14, task #6330). |
| 5 | Trang Điều khoản + Gỡ nội dung | ✅ | `/terms`, `/takedown`, `/faq`, `/privacy` đều 200. |
| 6 | Diễn tập khôi phục 1 lần | ⚠️ | Owner báo xong; nhật ký diễn tập trong `docs/runbooks/backup-restore.md` §5 vẫn trống: cần ghi ngày, thời gian Redis/CSDL, sai lệch. |
| 7 | Tiện ích trên Chrome Web Store | ⚠️ | Owner báo xong; chưa có link cửa hàng. Bản đã nộp: 5.6.0. |

## Để qua G1
1. Dán địa chỉ trang trạng thái thật (hết link chết).
2. ~~Probe Douyin + Xiaohongshu~~ — đã chốt không quảng bá ở G1 (13/10).
3. Instagram: đọc lý do ở Admin → Sức khoẻ nền tảng; Rumble: ghi "giới hạn đã biết".
4. Gửi link Chrome Web Store, ghi nhật ký diễn tập khôi phục.
5. Chuyển máy chủ ký lượt tải bằng k2 khi số máy ≥0.14 đủ lớn (đo ở Admin → Đo lường / App Windows).
