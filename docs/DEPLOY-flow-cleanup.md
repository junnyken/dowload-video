> [!WARNING]
> **TÀI LIỆU LỖI THỜI (OBSOLETE — KHÔNG SỬ DỤNG)**
> Tài liệu này viết cho mô hình VPS `.env` + `docker compose` cũ (`deploy-vps.sh`), không khớp với hạ tầng production thật trên **Vibe Host (Mắt Bão — cụm `vibe1.tinhgon.xyz`)**.
> Vui lòng sử dụng quy trình deploy hiện hành tại: **`docs/DEPLOY-VIBEHOST-2026-11.md`**.

# Hướng dẫn Triển khai & Vận hành Production — Flow/Veo Visible-Logo Cleanup (`flow-cleanup`)

Tài liệu này quy định trình tự triển khai (deploy), lệnh kiểm tra sau triển khai (post-deploy verification) và quy trình hoàn tác (rollback) cho tính năng xoá watermark/logo hiển thị (`flow-cleanup` — Phase P5).

> **Lưu ý phạm vi**: Tính năng này chỉ xử lý watermark/logo hiển thị trên khung hình (`visible_logo_cleanup`). Không xử lý SynthID hoặc watermark AI ẩn.

---

## 1. Thứ tự triển khai bắt buộc (Deployment Order)

Triển khai theo đúng thứ tự **4 bước tuần tự** dưới đây để không làm gián đoạn dịch vụ tải video hiện có và không gây lỗi khởi động trên môi trường `ENV=production`:

### Bước 1 — Chạy Migration `044` và `045` trên Supabase Database (BẮT BUỘC TRƯỚC KHI DEPLOY)
- **Migration `044`** (`database/migrations/044_flow_consent_audit_logs.sql`): Bảng `flow_consent_audit_logs` lưu vết xác nhận quyền sử dụng/chỉnh sửa video của người dùng (`user_id`, `temp_id`, `action`, `created_at`) kèm Row Level Security (RLS).
- **Migration `045`** (`database/migrations/045_payment_webhook_events.sql`): Bảng `payment_webhook_events` lưu các sự kiện webhook thanh toán cần kiểm tra thủ công (`needs_manual_review`), kèm index và RLS chỉ cho phép `service_role`. **Bắt buộc phải chạy Migration `045` trên Supabase TRƯỚC khi deploy nhánh `fix/payments-safety`.**

Cả hai script đều thiết kế idempotent (`IF NOT EXISTS`), chạy lại nhiều lần không gây lỗi:

```bash
# Cách 1: Chạy qua psql với chuỗi kết nối Supabase Postgres (Transaction/Session pooler hoặc Direct)
psql "$SUPABASE_DB_URL" -f database/migrations/044_flow_consent_audit_logs.sql
psql "$SUPABASE_DB_URL" -f database/migrations/045_payment_webhook_events.sql

# Cách 2: Chạy qua Supabase CLI
supabase db execute --file database/migrations/044_flow_consent_audit_logs.sql
supabase db execute --file database/migrations/045_payment_webhook_events.sql
```

### Bước 2 — Cấu hình biến môi trường trên VPS (`.env`)
Trên môi trường `ENV=production` (mặc định trong `docker-compose.yml`), `backend` sẽ **kiểm tra bắt buộc `FLOW_URL_SIGNING_SECRET` ngay lúc khởi động** (`_validate_signing_secret_on_startup()`). Nếu biến này bị thiếu hoặc ngắn hơn 16 ký tự, backend sẽ dừng khởi động ngay với `RuntimeError`.

1. Tạo một khoá ký URL riêng biệt (tối thiểu 32 byte ngẫu nhiên, **tuyệt đối không dùng lại `JWT_SECRET` hay `SUPABASE_SERVICE_KEY`**):
   ```bash
   openssl rand -hex 32
   ```
2. Thêm vào file `.env` trên VPS:
   ```dotenv
   # Bắt buộc trên production — khoá HMAC-SHA256 ký URL ngắn hạn (TTL <= 10 phút) cho frame & preview clip
   FLOW_URL_SIGNING_SECRET=<chuoi_hex_64_ky_tu_vua_tao>

   # Các tham số giới hạn & vận hành (giữ mặc định an toàn nếu không cần chỉnh)
   FLOW_INPAINT_WORKER_CONCURRENCY=2
   FLOW_DAILY_USER_QUOTA=20
   FLOW_MAX_USER_CONCURRENT_JOBS=1
   FLOW_JOB_TIMEOUT_SEC=300
   FLOW_SEC_PER_MPX_FRAME=0.45
   FLOW_CLEANUP_TTL_SEC=1200
   ```
3. Đảm bảo `docker-compose.yml` truyền `FLOW_URL_SIGNING_SECRET=${FLOW_URL_SIGNING_SECRET}` vào cả service `backend` và `celery-media` (vì worker `celery-media` tạo signed URL cho kết quả `preview_clip`).

### Bước 3 — Build & Khởi động lại `backend` API
```bash
docker compose build backend
docker compose up -d backend
```

### Bước 4 — Build & Khởi động `celery-media` Worker (Hàng đợi `media`)
Tác vụ `/track`, `/preview-clip` và `/process` đều được đẩy vào hàng đợi riêng `-Q media` (`celery-media`), tách biệt hoàn toàn với worker tải video thường (`celery` chạy `-Q downloads,partner`) và worker tác vụ nhẹ (`celery-light` chạy `-Q celery`).

```bash
docker compose build celery-media
docker compose up -d celery-media
```

*(Tuỳ chọn)* Nếu muốn bật nút lối tắt hậu kỳ trên thẻ kết quả tải video ở trang chủ, đặt `VITE_FEATURE_LOGO_INPAINT=true` khi build `frontend`. Mặc định cờ này là `OFF` để không ảnh hưởng luồng tải video chính.

---

## 2. Lệnh kiểm tra sau khi Deploy (Post-Deploy Verification)

Thực hiện lần lượt 6 bước kiểm tra dưới đây ngay sau khi deploy xong:

### 2.1. Kiểm tra bảng Database & RLS trên Supabase
```bash
psql "$SUPABASE_DB_URL" -c "\d flow_consent_audit_logs"
psql "$SUPABASE_DB_URL" -c "\d payment_webhook_events"
psql "$SUPABASE_DB_URL" -c "SELECT policyname, tablename, roles, cmd FROM pg_policies WHERE tablename IN ('flow_consent_audit_logs', 'payment_webhook_events');"
```
- **Kỳ vọng**: Bảng `flow_consent_audit_logs` có policy `flow_consent_audit_logs_own`; bảng `payment_webhook_events` có các cột `id, provider, provider_event_id, event_type, user_id, order_id, current_tier, target_tier, amount, status, reason, raw_payload, created_at` và policy `payment_webhook_events_service_role_only` chỉ mở cho `{service_role}`.

### 2.2. Kiểm tra `backend` khởi động thành công với `FLOW_URL_SIGNING_SECRET`
```bash
docker compose logs --tail=50 backend
curl -fsS http://localhost:8000/health
```
- **Kỳ vọng**: Container `backend` ở trạng thái `Up (healthy)`, không có `RuntimeError: FLOW_URL_SIGNING_SECRET is required in production`, endpoint `/health` trả `HTTP 200`.

### 2.3. Kiểm tra Worker `celery-media` nhận hàng đợi `media`
```bash
docker compose exec celery-media celery -A app.core.celery_app.celery_app inspect ping --timeout 10
docker compose exec celery-media celery -A app.core.celery_app.celery_app inspect active_queues
```
- **Kỳ vọng**: `ping` trả về `{"celery@...": {"ok": "pong"}}` và `active_queues` hiển thị queue `media` với concurrency = `2`.

### 2.4. Kiểm tra kết nối Redis cho Quota ngày & Khoá 1-job/user
```bash
docker compose exec redis redis-cli PING
docker compose exec redis redis-cli KEYS "flow:*"
```
- **Kỳ vọng**: `PING` trả về `PONG`. Khi có người dùng sử dụng tính năng, các key `flow:quota:<ns>:<YYYY-MM-DD>:<user_id>` và `flow:active_job:<ns>:<user_id>` đều có TTL dương (`TTL > 0`).

### 2.5. Kiểm tra bảo mật Signed URL & Phân quyền (Smoke Test)
```bash
# 1. Truy cập frame không có chữ ký -> phải trả 404 (không lộ thông tin job)
curl -i http://localhost:8000/api/v1/flow-cleanup/frame/00000000111111112222222233333333

# 2. Gọi /no-logo hoặc /upload khi chưa đăng nhập -> phải trả 401
curl -i -X POST http://localhost:8000/api/v1/flow-cleanup/no-logo \
  -H "Content-Type: application/json" \
  -d '{"temp_id":"00000000111111112222222233333333"}'
```
- **Kỳ vọng**: Lệnh (1) trả `HTTP 404`, lệnh (2) trả `HTTP 401`.

### 2.6. Kiểm tra không có file audit log nằm trong `downloads/`
```bash
docker compose exec backend sh -c "ls -la /app/downloads/flow_consent_audit* 2>/dev/null || echo 'OK: No audit file in downloads'"
```
- **Kỳ vọng**: In ra `OK: No audit file in downloads`.

---

## 3. Quy trình Hoàn tác (Rollback Plan)

Nếu phát hiện sự cố về CPU, hàng đợi hoặc lỗi ứng dụng sau khi deploy, thực hiện hoàn tác theo 3 cấp độ từ nhẹ đến triệt để:

### Cấp độ 1 — Tắt lối tắt giao diện & Chặn nhận job mới (Tức thì, không downtime)
1. Nếu đã bật cờ `VITE_FEATURE_LOGO_INPAINT=true`, đổi lại `VITE_FEATURE_LOGO_INPAINT=false` và build lại `frontend`.
2. Hoặc đặt `FLOW_DAILY_USER_QUOTA=0` (hoặc dừng `celery-media`):
   ```bash
   docker compose stop celery-media
   ```
   - Khi `celery-media` dừng và `FLOW_CLEANUP_ALLOW_INPROCESS=0` (mặc định production), các endpoint `/track`, `/preview-clip`, `/process` sẽ trả ngay `HTTP 503 queue_unavailable` (`retryable: true`) mà **không chạy in-process trên API server** và **không ảnh hưởng đến việc tải video thường trên worker `celery` (`downloads`)**.

### Cấp độ 2 — Hoàn tác Code Backend & Worker về Commit trước đó
```bash
# 1. Đưa nhánh/code về commit ổn định trước khi deploy
git checkout <commit_on_dinh_truoc_do>

# 2. Build và khởi động lại backend + celery-media
docker compose up -d --build backend celery-media

# 3. Xoá các khoá active_job tạm thời trên Redis (nếu có job bị ngắt giữa chừng)
docker compose exec redis redis-cli --scan --pattern "flow:active_job:*" | xargs -r docker compose exec -T redis redis-cli DEL
```

### Cấp độ 3 — Hoàn tác Database Migration `044` (Chỉ khi thật sự cần thiết)
Bảng `flow_consent_audit_logs` hoàn toàn độc lập (không có khoá ngoại trỏ từ bảng khác), nên việc giữ nguyên bảng trên Supabase **không gây xung đột với code cũ**. Nếu muốn xoá hoàn toàn:
```bash
psql "$SUPABASE_DB_URL" -c "DROP TABLE IF EXISTS flow_consent_audit_logs CASCADE;"
```
