# Quy trình Deploy Production trên Vibe Host — Tháng 11/2026 (`DEPLOY-VIBEHOST-2026-11`)

Tài liệu chính thức quy định **6 bước tuần tự bắt buộc** để đưa nhánh `fix/payments-safety` (kèm phần backend `f97f282` đang chờ) lên production an toàn trên hạ tầng **Vibe Host (Mắt Bão — cụm `vibe1.tinhgon.xyz`)**.

> [!IMPORTANT]
> - Production tự động build khi nhánh **`github/main`** (`https://github.com/junnyken/dowload-video.git`) nhận commit mới. **Tuyệt đối không push `github/main`** trước khi hoàn tất Bước 1 và Bước 2.
> - Hạ tầng gồm 2 project độc lập sau IP `203.171.31.200`:
>   - **Backend (`dvid-api`)**: `https://dvid-api.vibe1.tinhgon.xyz` (build từ `backend/Dockerfile`, chạy `./docker-entrypoint.sh`).
>   - **Frontend (`dvid`)**: `https://dowloadvideo.io.vn` & `https://dvid.vibe1.tinhgon.xyz` (build từ `frontend/Dockerfile`, `VITE_API_URL=https://dvid-api.vibe1.tinhgon.xyz`).
> - Các tài liệu cũ (`docs/RUNBOOK.md`, `deploy-vps.sh`, `docs/DEPLOY-2026-11.md`, `docs/DEPLOY-flow-cleanup.md`) đã **LỖI THỜI** — chỉ áp dụng tài liệu này.

---

## Bước 1 — Khai báo biến môi trường trên Vibe Host (`dvid-api`) & chỉnh lại giá Redis `plans:settings`

### 1.1 Khai báo biến môi trường trên Vibe Host → Project `dvid-api`

1. Sinh khoá ký URL riêng biệt (tối thiểu 32 ký tự hex, **không dùng trùng** với `JWT_SECRET` hay `SUPABASE_SERVICE_KEY`):
   ```bash
   openssl rand -hex 32
   ```
2. Vào giao diện **Vibe Host → Project `dvid-api` → Environment Variables**, thêm / kiểm tra có đủ các biến sau (chỉ lưu cấu hình, chưa cần redeploy thủ công):
   - **Biến mới cần thêm trong đợt này**:
     - `FLOW_URL_SIGNING_SECRET`: dán chuỗi 64 ký tự hex vừa tạo (**bắt buộc**, thiếu hoặc `< 16` ký tự thì `backend` dừng khởi động ngay tại `_validate_signing_secret_on_startup()`).
     - `TELEGRAM_BOT_TOKEN`: token bot nhận cảnh báo vận hành / duyệt tay giao dịch.
     - `TELEGRAM_CHAT_ID`: chat ID admin nhận cảnh báo khi phát sinh bản ghi `needs_manual_review`.
   - **Danh sách biến `${VAR}` không có mặc định trong `docker-compose.yml`** *(bắt buộc có mặt nếu trình quét pre-deploy của Vibe Host kiểm tra toàn bộ `docker-compose.yml` để chặn `ENV_REQUIRED`)*:
     - `FLOW_URL_SIGNING_SECRET`
     - `SUPABASE_URL`
     - `SUPABASE_KEY`
     - `TELEGRAM_DIST_BOT_TOKEN` *(nếu Vibe Host báo `ENV_REQUIRED` đòi biến của service `telegram-bot`, điền token bot phân phối hoặc chuỗi placeholder hợp lệ nếu `dvid-api` không chạy `telegram-bot`)*
     - `VITE_SUPABASE_URL` *(trên project `dvid`; nếu Vibe Host quét chung trên `dvid-api` thì điền cùng URL Supabase)*
     - `VITE_SUPABASE_ANON_KEY` *(trên project `dvid`; nếu Vibe Host quét chung trên `dvid-api` thì điền cùng anon key)*
   - **Các biến runtime cốt lõi đang có của `dvid-api` (xác nhận giữ nguyên, không bị xoá)**:
     - `ENV=production`, `REDIS_URL`, `SUPABASE_SERVICE_KEY` (`service_role` key — bắt buộc để đọc/ghi bảng `payment_webhook_events` qua RLS), `ADMIN_PASSWORD`.
     - Biến cổng thanh toán đang mở: `SEPAY_WEBHOOK_SECRET`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID` (hoặc `PAYPAL_ME_URL`), `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_*_PRICE_ID`.
3. Kiểm tra nhanh trên **Project `dvid` (Frontend)**:
   - Đảm bảo `VITE_API_URL=https://dvid-api.vibe1.tinhgon.xyz`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` giữ nguyên.
   - Đảm bảo **KHÔNG** đặt `VITE_FEATURE_LOGO_INPAINT=true` hoặc `1` (bỏ trống hoặc `false` để giữ ẩn giao diện Xoá Logo cho tới khi mở chính thức).

### 1.2 Cập nhật giá `pro` bị lệch trong Redis `plans:settings` (có thể làm ngay trước khi deploy)

Hiện tại Redis `plans:settings` trên production đang giữ giá cũ trước đợt tách gói Starter (`price_month_vnd = 49000`, `price_year_vnd = 399000`), làm Pro tháng đầu bị bán `49.000₫` và trùng giá với `starter_month` (`49.000₫`).

- **Cách 1 (Giao diện Admin)**: Đăng nhập `https://dowloadvideo.io.vn/vid-admin` → mục **Gói & Thanh toán (`Plans & Billing`)** → sửa:
  - `price_month_vnd`: **`99000`**
  - `price_year_vnd`: **`890000`**
  → bấm **Lưu**.
- **Cách 2 (API Admin)**:
  ```bash
  curl -sS -X POST "https://dvid-api.vibe1.tinhgon.xyz/api/v1/admin/plans/settings" \
    -H "Content-Type: application/json" \
    -H "X-Admin-Token: <ADMIN_SESSION_TOKEN>" \
    -d '{"price_month_vnd": 99000, "price_year_vnd": 890000}'
  ```

---

## Bước 2 — Chạy Migration `044` và `045` trên Supabase (kèm SQL kiểm tra)

Mở **Supabase Dashboard → SQL Editor**, chạy lần lượt nội dung nguyên bản của 2 file trong repo (cả hai đều idempotent `IF NOT EXISTS`, chạy lại không lỗi):

1. `database/migrations/044_flow_consent_audit_logs.sql` — tạo bảng `flow_consent_audit_logs`, 2 index (`idx_flow_consent_audit_logs_user`, `idx_flow_consent_audit_logs_temp_id`) và RLS `flow_consent_audit_logs_own`.
2. `database/migrations/045_payment_webhook_events.sql` — tạo bảng `payment_webhook_events`, 3 index (`idx_payment_webhook_events_status`, `idx_payment_webhook_events_user`, `idx_payment_webhook_events_provider_event`) và RLS `payment_webhook_events_service_role_only`.

### SQL kiểm tra ngay sau khi chạy migration

Chạy truy vấn kiểm tra sau trong **SQL Editor**:

```sql
-- 1. Kiểm tra 2 bảng đã tồn tại và đã bật RLS (rowsecurity = true)
SELECT tablename, rowsecurity
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
ORDER BY tablename;

-- 2. Kiểm tra đủ 5 index của migration 044 và 045
SELECT tablename, indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
ORDER BY tablename, indexname;

-- 3. Kiểm tra policy RLS trên 2 bảng
SELECT tablename, policyname, roles, cmd
FROM pg_policies
WHERE schemaname = 'public'
  AND tablename IN ('flow_consent_audit_logs', 'payment_webhook_events')
ORDER BY tablename, policyname;
```

**Kết quả kỳ vọng**:
- `pg_tables`: trả đủ 2 dòng `flow_consent_audit_logs` và `payment_webhook_events`, cả hai có `rowsecurity = true`.
- `pg_indexes`: có đủ `idx_flow_consent_audit_logs_temp_id`, `idx_flow_consent_audit_logs_user`, `idx_payment_webhook_events_provider_event`, `idx_payment_webhook_events_status`, `idx_payment_webhook_events_user` (cùng 2 primary key index).
- `pg_policies`: có `flow_consent_audit_logs_own` (`SELECT`) và `payment_webhook_events_service_role_only` (`ALL`, roles `{service_role}`).

---

## Bước 3 — Merge `fix/payments-safety` vào `main` và push `github/main` (= DEPLOY)

> [!CAUTION]
> Bước này kích hoạt auto-build trực tiếp lên production trên Vibe Host. Agent **CHỈ thực hiện** sau khi bạn xác nhận Bước 1 + Bước 2 hoàn tất và trả lời nguyên văn câu:
> **`đồng ý deploy fix/payments-safety`**

Lệnh thực thi ở Bước 3 (khi đã có xác nhận):

```bash
git checkout main
git merge --no-ff fix/payments-safety -m "Merge branch 'fix/payments-safety' into main"
git push github main
```

---

## Bước 4 — Theo dõi tiến trình build `dvid-api` và `dvid` trên Vibe Host

Ngay sau khi `git push github main` hoàn tất:

1. Mở giao diện **Vibe Host (`vibe1.tinhgon.xyz`)** → kiểm tra tab **Deployments / Build Logs** của cả 2 project:
   - **`dvid-api` (Backend)**: xác nhận lượt build mới từ commit merge trên `github/main` vượt qua bước kiểm tra biến môi trường (không báo `ENV_REQUIRED`), build image thành công và chuyển container sang trạng thái **Running / Healthy**.
   - **`dvid` (Frontend)**: xác nhận lượt build mới hoàn tất xanh (nếu Vibe Host build lại cả frontend khi `github/main` có commit mới).
2. Mở **Runtime Logs** của `dvid-api`:
   - Xác nhận `uvicorn` và các tiến trình trong `docker-entrypoint.sh` lên sạch, **không** có `RuntimeError: FLOW_URL_SIGNING_SECRET is required in production` và **không** có `ValueError` khi đọc biến môi trường.

---

## Bước 5 — Smoke Test sau Deploy

Chạy lần lượt 5 bài kiểm tra sau:

1. **Healthcheck Backend**:
   ```bash
   curl -sS -i https://dvid-api.vibe1.tinhgon.xyz/health
   ```
   → Kỳ vọng: HTTP `200 OK`.

2. **Kiểm tra bảng giá `/api/v1/plans` đúng giá chuẩn**:
   ```bash
   curl -sS https://dvid-api.vibe1.tinhgon.xyz/api/v1/plans
   ```
   → Kỳ vọng:
   - `starter`: `price_month_vnd = 49000`, `price_year_vnd = 590000`, `price_month_usd = 1.99`
   - `pro`: **`price_month_vnd = 99000`**, **`price_year_vnd = 890000`**, `regular_month_vnd = 139000`, `price_month_usd = 3.99`
   - `team`: `price_month_vnd = 279000`, `price_year_vnd = 2790000`, `price_month_usd = 11.99`

3. **Chứng minh container `dvid-api` mới đã thực sự lên thay cho `29db500`**:
   ```bash
   curl -sS -o /dev/null -w "%{http_code}\n" https://dvid-api.vibe1.tinhgon.xyz/api/v1/flow-cleanup/status/0000000000000000
   ```
   → Kỳ vọng: trả **`401`** (thiếu token đăng nhập). Nếu vẫn trả `404` tức là lưu lượng vẫn đang vào container cũ `29db500`.

4. **Tải thử 1 video thật trên giao diện người dùng**:
   - Mở `https://dowloadvideo.io.vn`, đăng nhập, dán 1 URL video, kiểm tra lấy thông tin và tải 1080p thành công; đồng thời xác nhận nút/tab Xoá Logo vẫn đang khoá/ẩn.

5. **Kiểm tra thanh toán thật (1 giao dịch nhỏ mỗi cổng đang bật + chặn hạ gói)**:
   - Với từng cổng đang mở (SePay/VietQR, PayPal, Stripe nếu bật): tạo 1 giao dịch thật mệnh giá nhỏ nhất / gói Starter hoặc kiểm thử luồng tạo đơn, xác nhận webhook khớp đúng gói, cộng đúng hạn sử dụng và không trùng lặp khi gọi lại.
   - Dùng tài khoản đang còn hạn gói cao (vd `pro` hoặc `team`), thử gọi tạo đơn gói thấp hơn (`starter_month`) → xác nhận API trả **`409 Conflict`** (chặn hạ gói ngay từ bước checkout).
   - Mở `/vid-admin` → xác nhận danh sách giao dịch cần duyệt tay (`needs_manual_review`) tải bình thường không lỗi `500`.

---

## Bước 6 — Quy trình Rollback (khi phát sinh sự cố)

Hai bảng mới `flow_consent_audit_logs` (`044`) và `payment_webhook_events` (`045`) là bảng độc lập (chỉ thêm mới, không sửa bảng cũ `payment_orders` hay `profiles`), nên khi rollback ứng dụng **KHÔNG cần xoá bảng trên Supabase**.

### Cách A — Rollback tức thì trên giao diện Vibe Host (nhanh nhất, < 1 phút)
1. Mở **Vibe Host → Project `dvid-api` (hoặc `dvid`) → Deployments / Lịch sử triển khai**.
2. Chọn bản build ổn định liền trước và bấm **Rollback** để chuyển lưu lượng ngay về image cũ mà không cần chờ build lại Docker image.

### Cách B — Revert commit trên `github/main` để kích hoạt auto-build bản sạch
*(Tuyệt đối dùng `git revert`, **không** dùng `git reset --hard` + `git push --force` lên `github/main`).*

```bash
git checkout main
git pull github main
# Revert merge commit vừa đưa lên (giữ parent 1 là main trước khi merge)
git revert -m 1 <MERGE_COMMIT_SHA> --no-edit
git push github main
```

Sau khi push commit revert, Vibe Host sẽ tự động build lại `dvid-api` và `dvid` về đúng trạng thái code trước đợt deploy. Kiểm tra lại `GET https://dvid-api.vibe1.tinhgon.xyz/health` trả `200 OK`.
