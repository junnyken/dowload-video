# ĐỀ XUẤT KỸ THUẬT: YouTube WebSub (PubSubHubbub) cho Theo Dõi Kênh

> **Trạng thái**: Đề xuất thiết kế (chưa triển khai code — theo yêu cầu Phần 4b).
> **Bối cảnh hiện tại**: Phần 4a đã triển khai quét YouTube qua Atom RSS (`https://www.youtube.com/feeds/videos.xml?channel_id=UC...`) với chu kỳ sàn `WATCH_PLATFORM_FLOOR_SEC_YOUTUBE=1800` (30 phút), 0 quota API, không dùng cookie. WebSub là bước nâng cấp tiếp theo để giảm độ trễ phát hiện video mới từ ~30 phút xuống **1–3 phút** sau khi kênh đăng video.

---

## 1. Kiến trúc tổng quan

YouTube phát thông báo đẩy (push) chuẩn W3C WebSub thông qua Google Hub công khai:
- **Hub URL**: `https://pubsubhubbub.appspot.com/subscribe`
- **Topic URL**: `https://www.youtube.com/xml/feeds/videos.xml?channel_id=<UC...>` (chú ý đường dẫn `/xml/feeds/videos.xml` do YouTube khai báo trong thẻ `<link rel="hub">` / `<link rel="self">`).
- **Luồng hoạt động**:
  1. Khi `watch_sources` (`platform='youtube'`, `status='active'`, `subscriber_count >= 1`) được tạo hoặc kích hoạt lại, Celery task gửi `POST` đăng ký tới Hub.
  2. Google Hub gọi `GET` về **Callback URL** của VidGrab để xác minh `hub.challenge`.
  3. Mỗi khi kênh đăng/cập nhật video, Google Hub gửi `POST` Atom XML kèm chữ ký HMAC (`X-Hub-Signature`) tới Callback URL.
  4. Backend xác thực HMAC → parse `<entry>` bằng chính hàm `parse_youtube_rss()` sẵn có → ghi vào bảng `watch_items_seen` (`on_conflict="source_id,external_item_id"`, `ignore_duplicates=True`) → tạo `watch_deliveries` và đánh thức Web Push / Webhook / App Windows poll.
  5. Quét RSS định kỳ (Phần 4a) vẫn giữ làm **lưới an toàn (fallback)** với chu kỳ thưa hơn (ví dụ 6 giờ/lần) phòng trường hợp Hub bỏ sót bản tin khi backend đang redeploy.

---

## 2. Thiết kế Callback URL

- **Endpoint công khai trên backend**:
  - `GET  https://dvid-api.vibe1.tinhgon.xyz/api/v1/watch/websub/callback/{source_id}` (xác minh `hub.challenge`)
  - `POST https://dvid-api.vibe1.tinhgon.xyz/api/v1/watch/websub/callback/{source_id}` (nhận Atom XML thông báo video mới)
- **Biến môi trường cấu hình**:
  - `WATCH_WEBSUB_ENABLED` (mặc định `false`)
  - `WATCH_WEBSUB_CALLBACK_BASE_URL` (ví dụ `https://dvid-api.vibe1.tinhgon.xyz`)
  - `WATCH_WEBSUB_SECRET_KEY` (khóa gốc 32+ bytes trên server dùng để dẫn xuất `hub.secret` riêng cho từng `source_id`, không lưu plaintext secret vào log)
- **Bảo vệ biên**:
  - Chỉ mở cho nguồn có `platform == "youtube"` và `"youtube"` nằm trong `WATCH_PLATFORMS_ENABLED`, đồng thời `WATCH_KILL_SWITCH == false`.
  - Giới hạn kích thước body tối đa `256 KB` và rate-limit theo IP/source để chống flood giả mạo.

---

## 3. Xác minh `hub.challenge` (Intent Verification)

Khi VidGrab gửi yêu cầu `subscribe` hoặc `unsubscribe`, Google Hub gọi:
```http
GET /api/v1/watch/websub/callback/{source_id}?hub.mode=subscribe&hub.topic=...&hub.challenge=<random>&hub.lease_seconds=432000
```

**Quy trình kiểm tra chống đăng ký/huỷ giả mạo (anti-spoofing)**:
1. Kiểm tra `source_id` tồn tại trong `watch_sources` và `platform == "youtube"`.
2. Kiểm tra `hub.topic` khớp chính xác với `external_channel_id` (`UC...`) của `source_id` đó.
3. Đối chiếu trạng thái mong muốn của nguồn:
   - Nếu `hub.mode == "subscribe"`: nguồn phải đang `status in ("active", "degraded")`, `subscriber_count >= 1`, và hệ thống thực sự vừa đặt cờ ожида (ví dụ khóa Redis `watch:websub:intent:{source_id} = subscribe` TTL 10 phút hoặc cột `websub_desired_mode`). Nếu không khớp → trả `404 Not Found`.
   - Nếu `hub.mode == "unsubscribe"`: chỉ chấp nhận khi `subscriber_count == 0` / `status == "idle"` hoặc có intent `unsubscribe` hợp lệ → tránh kẻ tấn công tự ý huỷ đăng ký kênh đang theo dõi.
4. Khi hợp lệ:
   - Cập nhật `websub_lease_expires_at = now + timedelta(seconds=int(hub.lease_seconds))` trên `watch_sources`.
   - Trả HTTP `200 OK` với `Content-Type: text/plain; charset=utf-8` và body **chính xác là chuỗi `hub.challenge`** (chỉ cho phép ký tự ASCII in được, độ dài `<= 512`).

---

## 4. Chữ ký HMAC (`X-Hub-Signature`)

Mỗi kênh có một `hub.secret` riêng được dẫn xuất tất định (hoặc lưu ngẫu nhiên theo nguồn):
```text
source_secret = HMAC-SHA256(WATCH_WEBSUB_SECRET_KEY, f"youtube-websub:{source_id}:{external_channel_id}").hexdigest()
```
Ưu điểm của dẫn xuất HMAC từ khóa gốc: không cần lưu secret nhạy cảm vào DB, xoay khóa dễ dàng khi gia hạn lease.

**Khi nhận `POST` từ Google Hub**:
1. Đọc nguyên bản `raw_body = await request.body()` (trước mọi bước parse).
2. Đọc header `X-Hub-Signature` (Google Hub gửi dạng `sha1=<hex>`, hỗ trợ thêm `sha256=<hex>` theo chuẩn WebSub):
   - Tách `algo, sig_hex = header.split("=", 1)`, chỉ chấp nhận `algo in ("sha1", "sha256")`.
   - Tính `expected = hmac.new(source_secret.encode(), raw_body, hashlib_algo).hexdigest()`.
   - So sánh hằng thời gian bằng `hmac.compare_digest(expected, sig_hex.lower())`.
3. Nếu thiếu header hoặc sai chữ ký:
   - Ghi metric cảnh báo (không log secret), trả `202 Accepted` hoặc `403 Forbidden` (theo khuyến nghị WebSub §8, trả `2xx` rỗng không xử lý hoặc `403` để chặn spam; trong nội bộ VidGrab không đụng vào DB).
4. Nếu chữ ký hợp lệ:
   - Parse XML (chặn `<!DOCTYPE` / `<!ENTITY` như `parse_youtube_rss` hiện tại).
   - Bỏ qua bản tin xoá (`<at:deleted-entry>`), chỉ lấy `<entry>` có `<yt:videoId>` hợp lệ.
   - Đẩy qua cùng hàm ghi ledger `watch_items_seen` (`on_conflict="source_id,external_item_id"`, `ignore_duplicates=True`) + kiểm tra `baseline_completed_at` để đảm bảo **không bao giờ tạo trùng thông báo** dù WebSub gửi lặp hoặc trùng với lần quét RSS định kỳ.

---

## 5. Gia hạn Lease (Lease Renewal & Vòng đời)

Google PubSubHubbub thường cấp `hub.lease_seconds = 432000` (5 ngày) đến `864000` (10 ngày). Nếu không gia hạn trước khi hết hạn, Hub sẽ ngừng gửi thông báo.

- **Cột DB bổ sung (migration mới khi triển khai)**:
  - `watch_sources.websub_status` (`none` | `pending` | `subscribed` | `failed`)
  - `watch_sources.websub_lease_expires_at` (`TIMESTAMPTZ NULL`)
  - `watch_sources.websub_last_push_at` (`TIMESTAMPTZ NULL`)
- **Celery Beat job gia hạn (`watch_renew_websub_leases`, chạy mỗi 1 giờ trên queue `celery`)**:
  - Tìm các nguồn `platform='youtube'`, `status='active'`, `subscriber_count > 0` có `websub_lease_expires_at IS NULL` hoặc `websub_lease_expires_at <= now() + interval '24 hours'` (gia hạn sớm trước 24 giờ).
  - Gửi `POST https://pubsubhubbub.appspot.com/subscribe` với:
    - `hub.callback`: URL callback của `source_id`
    - `hub.mode`: `subscribe`
    - `hub.topic`: `https://www.youtube.com/xml/feeds/videos.xml?channel_id=<UC...>`
    - `hub.verify`: `async`
    - `hub.secret`: `source_secret`
    - `hub.lease_seconds`: `432000`
  - Giới hạn tốc độ gửi đăng ký (ví dụ tối đa 30 nguồn/phút) để tránh bị Google Hub throttle.
- **Tự huỷ khi không còn người theo dõi**:
  - Khi người dùng cuối cùng bỏ theo dõi hoặc tạm dừng (`subscriber_count == 0` → `status = 'idle'`), gửi `hub.mode = unsubscribe` để không nhận lưu lượng thừa.

---

## 6. Chi phí & Vận hành

| Hạng mục | Chi phí / Tải hệ thống |
|---|---|
| **Google PubSubHubbub & YouTube Feed** | **\$0 / tháng** — dịch vụ chuẩn công khai của Google, không cần YouTube Data API key, không tốn quota 10.000 units/ngày, không cần cookie hay residential proxy. |
| **Băng thông & CPU trên Vibe Host** | Rất thấp: mỗi lần kênh đăng video mới chỉ có **1 HTTP POST ~2–4 KB XML** cho toàn bộ mọi người cùng theo dõi kênh đó (nhờ kiến trúc gộp 1 `watch_sources` cho nhiều `watch_subscriptions`). Với 1.000 kênh YouTube hoạt động (trung bình 1 video/ngày/kênh), tổng lưu lượng đẩy chỉ ~1.000 request/ngày (~4 MB/ngày) + ~200 request gia hạn lease/ngày. |
| **Redis & Supabase** | Không tăng số bảng lớn; chỉ thêm 3 cột metadata trên `watch_sources` và 1 khóa Redis ngắn hạn khi chờ `hub.challenge`. |
| **Độ tin cậy khi redeploy backend** | Google Hub tự retry với exponential backoff nếu callback tạm trả `5xx` lúc container đang khởi động lại; ngoài ra chu kỳ RSS fallback (Phần 4a) đảm bảo 100% không bỏ sót video nào. |
