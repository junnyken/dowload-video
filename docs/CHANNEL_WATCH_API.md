# Theo dõi kênh — API và webhook (Phase 33C)

Dành cho người dùng gói Pro trở lên muốn nối "Theo dõi kênh" vào hệ thống của mình
(n8n, Zapier, máy chủ riêng…).

## 1. Webhook: nhận video mới tự động

1. Đăng ký URL nhận (một URL cho mỗi tài khoản, phải là `https://` công khai):

   ```
   POST /api/v1/user/webhook/register
   Authorization: Bearer <phiên đăng nhập>   hoặc   X-API-Key: vidgrab_...
   {"webhook_url": "https://example.com/vidgrab"}
   ```

   Kết quả trả `secret` (dạng `whs_…`) **một lần duy nhất**. Khoá ký HMAC là
   `sha256(secret)` dạng hex, tức là giá trị máy chủ lưu.

2. Khi một kênh đang theo dõi có video mới, VidGrab gửi:

   ```
   POST <webhook_url>
   Content-Type: application/json
   X-VidGrab-Event: watch.new_items
   X-VidGrab-Signature: sha256=<HMAC-SHA256(body, key=sha256(secret) hex)>
   ```

   ```json
   {
     "event": "watch.new_items",
     "sent_at": "2026-10-12T04:00:00+00:00",
     "subscription_id": "…",
     "channel": {"platform": "youtube", "name": "NBA",
                 "url": "https://www.youtube.com/channel/UCWJ2lWNubArHWmf3FIHbfcQ"},
     "items": [
       {"id": "wj6iKc2UvN4", "url": "https://youtube.com/watch?v=wj6iKc2UvN4",
        "title": "…", "published_at": null, "discovered_at": "2026-10-12T03:59:10+00:00"}
     ]
   }
   ```

   - Gửi cùng lúc với thông báo đẩy, theo đúng nhịp nhận tin của kênh
     (Free 24 giờ, Pro 6 giờ). Các video mới trong cùng một nhịp gom vào một lần gửi.
   - `published_at` có thể `null` (YouTube không cho biết giờ đăng trong danh sách);
     `discovered_at` là lúc VidGrab thấy video.
   - Trả 2xx là thành công. Lỗi mạng hoặc 5xx được gửi lại 3 lần (30 s, 60 s, 120 s).
   - Tài khoản hạ xuống Free thì ngừng gửi webhook (thông báo đẩy vẫn còn).
   - 50 lần gửi gần nhất: `GET /api/v1/user/webhook/logs`.

Kiểm chữ ký (Python):

```python
import hashlib, hmac
key = hashlib.sha256(SECRET.encode()).hexdigest().encode()
ok = hmac.compare_digest("sha256=" + hmac.new(key, raw_body, hashlib.sha256).hexdigest(),
                         request.headers["X-VidGrab-Signature"])
```

## 2. API: tự đọc danh sách

Mọi endpoint dưới đây nhận `X-API-Key` (tạo ở trang tài khoản, gói Pro).

| Việc | Gọi |
|---|---|
| Trạng thái, giới hạn của tài khoản | `GET /api/v1/watch/status` |
| Theo dõi kênh mới | `POST /api/v1/watch/sources` `{"url": "https://www.youtube.com/@tenkenh", "mode": "notify_only"}` |
| Danh sách kênh đang theo dõi | `GET /api/v1/watch/subscriptions` |
| Tạm dừng / tiếp tục | `PATCH /api/v1/watch/subscriptions/{id}` `{"status": "paused"}` |
| Bỏ theo dõi | `DELETE /api/v1/watch/subscriptions/{id}` |
| Video mới (mặc định 7 ngày, tối đa 30) | `GET /api/v1/watch/items?since=2026-10-10T00:00:00Z` |

Lỗi trả `{"error_code": "...", "message": "..."}` (tiếng Việt).

Nền tảng: TikTok và YouTube (mọi tài khoản). Instagram đang làm (gói Pro).
