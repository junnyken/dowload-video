# Hướng dẫn Triển khai & Rollback Production (`f97f282`)

Tài liệu này quy định trình tự triển khai (deploy), kiểm tra sau triển khai (smoke test) và quy trình hoàn tác (rollback) cho bản cập nhật gồm:
- Hệ thống xoá watermark/logo hiển thị (`flow-cleanup` P0 → P5.9.4, đang khoá giao diện mặc định qua `VITE_FEATURE_LOGO_INPAINT` = OFF).
- Bảng giá mới, phân quyền các gói trả phí (`starter`, `pro`, `team`, `enterprise`), sửa webhook thanh toán (Stripe, PayPal, SePay) và Admin.
- Sửa lỗi quét kênh YouTube `@handle`, sửa tính nhầm băng thông proxy YouTube và tối ưu Celery worker.

---

## 1. Thứ tự triển khai bắt buộc (Deployment Order)

Thực hiện **đúng thứ tự 3 bước** dưới đây. Script `deploy-vps.sh` đã có chốt chặn tự động kiểm tra đủ cả 3 điều kiện trước khi rsync/build.

### Bước 1 — Đặt `FLOW_URL_SIGNING_SECRET` trong `.env` trên VPS
Trên môi trường `ENV=production`, `backend` kiểm tra bắt buộc `FLOW_URL_SIGNING_SECRET` ngay lúc khởi động (`_validate_signing_secret_on_startup()` tại `backend/app/api/flow_cleanup.py:567`). Nếu biến này thiếu hoặc ngắn hơn 16 ký tự (chốt chặn `deploy-vps.sh` yêu cầu $\ge 32$ ký tự), backend sẽ dừng khởi động với `RuntimeError`.

1. Tạo khoá ngẫu nhiên 64 ký tự hex (32 byte):
   ```bash
   openssl rand -hex 32
   ```
2. Mở `/home/ubuntu/vidgrab/.env` trên VPS và cấu hình:
   ```dotenv
   # Bắt buộc >= 32 ký tự (KHÔNG dùng lại JWT_SECRET hay SUPABASE_SERVICE_KEY)
   FLOW_URL_SIGNING_SECRET=<chuoi_hex_64_ky_tu_vua_tao>

   # Đảm bảo KHÔNG bật VITE_FEATURE_LOGO_INPAINT (bỏ trống hoặc đặt =false)
   VITE_FEATURE_LOGO_INPAINT=false

   # Đảm bảo có REDIS_URL
   REDIS_URL=redis://redis:6379/0
   ```

### Bước 2 — Chạy Migration `044` trên Supabase Database
Bảng `flow_consent_audit_logs` (`database/migrations/044_flow_consent_audit_logs.sql`) lưu log xác nhận quyền chỉnh sửa video của người dùng kèm RLS. Script dùng `CREATE TABLE IF NOT EXISTS` / `DROP POLICY IF EXISTS` nên chạy lại nhiều lần an toàn.

- **Cách 1 (Supabase SQL Editor)**: Dán toàn bộ nội dung file `database/migrations/044_flow_consent_audit_logs.sql` vào SQL Editor trên Supabase Dashboard và bấm **Run**.
- **Cách 2 (`psql`)**:
  ```bash
  psql "$SUPABASE_DB_URL" -f database/migrations/044_flow_consent_audit_logs.sql
  ```

### Bước 3 — Chạy `deploy-vps.sh` kèm cờ `--confirm-migration-044`
1. Kiểm tra preflight trước (chỉ đọc `.env` trên VPS qua SSH, không sửa gì, không deploy):
   ```bash
   ./deploy-vps.sh --confirm-migration-044 --dry-run
   ```
   - Script sẽ kiểm tra trên VPS:
     - `FLOW_URL_SIGNING_SECRET` có mặt và dài $\ge 32$ ký tự (chỉ in `"có/không"` và độ dài, không in giá trị).
     - `VITE_FEATURE_LOGO_INPAINT` không có giá trị `true` hoặc `1`.
     - `REDIS_URL` (hoặc `CELERY_BROKER_URL` / `REDIS_HOST`) có mặt.
2. Lưu danh sách Docker image ID hiện tại trên VPS để phục vụ `scripts/rollback.sh`:
   ```bash
   ssh vidgrab "cd /home/ubuntu/vidgrab && bash ./scripts/rollback.sh save"
   ```
3. Chạy deploy chính thức:
   ```bash
   ./deploy-vps.sh --confirm-migration-044
   ```

---

## 2. Smoke Test sau khi Deploy (Post-Deploy Verification)

Thực hiện đầy đủ 4 bước kiểm tra dưới đây ngay sau khi `deploy-vps.sh` báo hoàn tất:

### 2.1. Kiểm tra log `backend` (không có `RuntimeError`) và trạng thái container
```bash
# Xem 100 dòng log mới nhất của backend
ssh vidgrab "cd /home/ubuntu/vidgrab && docker compose logs --tail=100 backend"

# Kiểm tra không có RuntimeError hoặc Traceback
ssh vidgrab "cd /home/ubuntu/vidgrab && docker compose logs --since=5m backend | grep -E 'RuntimeError|Traceback' || echo 'OK: No RuntimeError'"

# Kiểm tra endpoint /health
ssh vidgrab "docker exec vidgrab-backend-1 python3 -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health', timeout=10).read().decode())\""
```
- **Kỳ vọng**: Không có `RuntimeError: FLOW_URL_SIGNING_SECRET is required in production`; `/health` trả về `{"status":"ok",...}`.

### 2.2. Kiểm tra bảo mật Signed URL & Trạng thái khoá tính năng xoá logo
```bash
# 1. Truy cập frame không có chữ ký -> phải trả HTTP 404
curl -i https://dowloadvideo.io.vn/api/v1/flow-cleanup/frame/00000000111111112222222233333333

# 2. Mở trang chủ https://dowloadvideo.io.vn -> tab "Xoá Logo Video" phải ở trạng thái khoá (Soon / bị vô hiệu hoá)
```

### 2.3. Tải thử 1 video đơn & 1 kênh YouTube
1. Trên giao diện web `https://dowloadvideo.io.vn`, dán 1 link video YouTube hoặc TikTok bất kỳ và bấm tải về.
2. Chuyển sang tab **Tải hàng loạt (Bulk)**, bật **Channel mode**, chọn giới hạn `10` video và thử quét 1 kênh YouTube `@handle`.
3. Kiểm tra tốc độ trích xuất và tải về hoàn tất không báo lỗi `503 youtube_cost_limit`.

### 2.4. Thử giao dịch thật số tiền nhỏ trên từng cổng thanh toán đang bật & Kiểm tra Webhook nâng gói
Với mỗi cổng thanh toán đang bật trên production (SePay VietQR, Stripe, PayPal):
1. **Tạo đơn & Thanh toán thử**:
   - Dùng một tài khoản test đang ở gói `free`.
   - Với **SePay (Chuyển khoản VietQR)**: Tạo đơn gói `Starter Tháng` (hoặc chỉnh giá test nhỏ trong Admin `/vid-admin/plans`, thanh toán thật qua QR có mã `VGP...`, sau đó trả lại giá chuẩn).
   - Với **Stripe / PayPal** (nếu đang bật): Thực hiện 1 giao dịch thật với gói nhỏ nhất (`Starter Monthly` $\$1.99$) hoặc coupon giảm giá test.
2. **Kiểm tra log webhook & dữ liệu DB sau khi thanh toán**:
   ```bash
   ssh vidgrab "cd /home/ubuntu/vidgrab && docker compose logs --tail=100 backend | grep -E 'Payments|PaymentOrders|sepay|paypal'"
   ```
3. **Xác nhận tài khoản được nâng đúng gói**:
   - Kiểm tra trong Admin (`/vid-admin/users` và `/vid-admin/orders`):
     - Đơn hàng trong `payment_orders` chuyển sang trạng thái `paid`.
     - Bảng `profiles` của user có `tier` đúng bằng gói đã mua (`starter` / `pro` / `team`), `plan_name` khớp (`Starter` / `Pro` / `Team`), `subscription_expiry` được cộng đúng số ngày (`30` hoặc `365` ngày).
     - Bảng `user_usage` có cột `plan` khớp với `profiles.tier`.
   - Đăng nhập bằng tài khoản test, vào trang **Usage / Billing** hoặc gọi `/api/v1/quota` để xác nhận hạn mức ngày (`100` cho Starter, `300` cho Pro, `1000` cho Team) đã có hiệu lực ngay.

---

## 3. Quy trình Hoàn tác (Rollback Plan)

### 3.1. Rollback nhanh bằng `scripts/rollback.sh` (Khôi phục Docker image trước khi deploy)
Nếu đã chạy `bash ./scripts/rollback.sh save` trước khi deploy (lưu image ID vào `/tmp/vidgrab_last_image.txt`), chạy lệnh sau để đưa toàn bộ `backend`, `celery`, `celery-light`, `celery-beat` về image cũ ngay lập tức:
```bash
ssh vidgrab "cd /home/ubuntu/vidgrab && bash ./scripts/rollback.sh all"
```
Hoặc chỉ rollback riêng `backend`:
```bash
ssh vidgrab "cd /home/ubuntu/vidgrab && bash ./scripts/rollback.sh backend"
```

### 3.2. Rollback bằng snapshot `/tmp/vidgrab_last_images.json` (Do `deploy-vps.sh` tự động lưu)
Mỗi lần chạy `deploy-vps.sh`, script tự động lưu danh sách image đang chạy vào `/tmp/vidgrab_last_images.json` trên VPS (`deploy-vps.sh:199`). Có thể xem và gắn lại tag image cũ hoặc checkout về commit ổn định trước đó (`49edf1e` hoặc commit trước deploy):
```bash
# Xem image ID đã lưu trước khi deploy
ssh vidgrab "cat /tmp/vidgrab_last_images.json"

# Hoặc rollback code trên VPS về commit ổn định trước đó và build lại:
ssh vidgrab "cd /home/ubuntu/vidgrab && git fetch --all && git checkout 49edf1e && docker compose up -d --build"
```

### 3.3. Migration `044` (`flow_consent_audit_logs`) có cần rollback không?
- **KHÔNG cần rollback Migration `044`** khi hạ cấp (rollback) code ứng dụng.
- **Lý do**: Bảng `public.flow_consent_audit_logs` (`database/migrations/044_flow_consent_audit_logs.sql:11-37`) là bảng độc lập mới hoàn toàn, không sửa đổi bất kỳ cột, bảng, view, trigger hay khoá ngoại nào của hệ thống hiện có (`profiles`, `user_usage`, `download_jobs`, `payment_orders`). Code cũ hoàn toàn bỏ qua bảng này mà không phát sinh lỗi.
- Chỉ trong trường hợp muốn xoá hẳn bảng khỏi Supabase (không bắt buộc):
  ```sql
  DROP TABLE IF EXISTS public.flow_consent_audit_logs CASCADE;
  ```
