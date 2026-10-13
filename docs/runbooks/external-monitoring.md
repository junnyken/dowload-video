# Giám sát VidGrab từ bên ngoài + trang trạng thái (Phase 34A-1, task #6331)

Mục tiêu: máy chủ chết hoặc chậm thì owner biết trong ≤ 10 phút qua Telegram, và người dùng có một trang trạng thái **không nằm trên host vibe1** (vẫn mở được khi vibe1 chết). Chi phí: 0 đồng (UptimeRobot bản Free: 50 monitor, kiểm 5 phút/lần, trang trạng thái công khai, báo Telegram + email).

Vì sao cần: sáng 13/10/2026 host vibe1 chập chờn hàng giờ (web, API, app, tiện ích cùng "mất kết nối") mà không có cảnh báo nào — owner chỉ biết khi tự mở app.

## 1. Tạo tài khoản (owner, 5 phút)
1. Vào https://uptimerobot.com → Sign up (email công việc).
2. **Integrations & API → Telegram** → làm theo hướng dẫn (mở bot của UptimeRobot trong Telegram, bấm Start) để nhận cảnh báo. Thêm cả email.

## 2. Tạo các monitor (Add New Monitor)

| Tên | Loại | URL | Chu kỳ | Điều kiện báo động |
|---|---|---|---|---|
| VidGrab API | Keyword | `https://dvid-api.vibe1.tinhgon.xyz/health` | 5 phút | Keyword `"status":"ok"` **không có** · Timeout 30 s |
| VidGrab Web | HTTP(s) | `https://dvid.vibe1.tinhgon.xyz/` | 5 phút | không trả 2xx · Timeout 30 s |
| Cập nhật app Windows | Keyword | `https://dvid-api.vibe1.tinhgon.xyz/api/v1/client/version` | 5 phút | Keyword `"latest"` không có |
| Sức khoẻ nền tảng | Keyword | `https://dvid-api.vibe1.tinhgon.xyz/api/v1/platform-status` | 5 phút | Keyword `"success":true` không có |
| Theo dõi kênh (API sống) | HTTP(s) | `https://dvid-api.vibe1.tinhgon.xyz/api/v1/watch/status` | 5 phút | Mã trả về **phải là 401** (Advanced → "Up HTTP status codes": 401) — 401 = API sống và chặn khách đúng |

Ở mỗi monitor: **Alert contacts** chọn Telegram + email; **Notify after** 2 lần lỗi liên tiếp (tránh báo nhầm khi mạng nháy 1 lần).

Ghi chú: `/health` luôn trả `"status":"ok"` khi tiến trình còn sống, kể cả lúc máy rất chậm (13/10: 20–30 s/lần). Vì vậy **Timeout 30 s là tín hiệu chính** — máy chậm hơn mức đó sẽ bị báo. Có thể đặt thêm cảnh báo thời gian phản hồi (Response time > 5 000 ms) nếu gói cho phép.

Không giám sát "một lượt tải mẫu" bằng UptimeRobot: mỗi lượt tốn tài nguyên máy chủ (và với nền tảng trả phí là tốn tiền). Tình trạng tải thật đã có probe nội bộ → hiện qua monitor "Sức khoẻ nền tảng".

## 3. Trang trạng thái công khai
UptimeRobot → **Status pages → Create**: tên "VidGrab — Trạng thái hệ thống", chọn 5 monitor trên, ngôn ngữ hiển thị tuỳ chọn. Đặt địa chỉ dạng `https://stats.uptimerobot.com/<mã>` (miễn phí) — trang này nằm trên máy của UptimeRobot, **vẫn mở được khi vibe1 chết**.

Sau khi có địa chỉ: gửi cho Claude để thêm liên kết "Trạng thái hệ thống" vào chân trang web, trang Câu hỏi thường gặp, và thông báo lỗi kết nối trong app/tiện ích.

## 4. Khi có cảnh báo
1. Mở trang trạng thái: một monitor đỏ hay tất cả?
2. **Tất cả đỏ** (web + API) → sự cố host vibe1 / mạng: báo người quản trị Vibe Host, kèm giờ bắt đầu từ UptimeRobot. Không deploy trong lúc này (deploy cũng sẽ lỗi "fetch failed").
3. **Chỉ API đỏ, web xanh** → tiến trình backend: Vibe Host → dowload-video-backend → xem log; nếu treo thì Redeploy.
4. **Chỉ "Sức khoẻ nền tảng" đỏ** → admin → Nền tảng: xem nền tảng nào lỗi, theo runbook `cookie-blocked.md` / `provider-down.md`.
5. Ghi lại giờ bắt đầu/kết thúc vào nhật ký sự cố (dùng cho mục tiêu uptime ≥ 99% của cổng G2).
