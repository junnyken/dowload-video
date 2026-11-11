# Quy trình Deploy Production trên Vibe Host — Tháng 11/2026 (`DEPLOY-VIBEHOST-2026-11`)

Tài liệu chính thức quy định quy trình triển khai và kiểm chứng production trên hạ tầng **Vibe Host (Mắt Bão — cụm `vibe1.tinhgon.xyz`)**.

> [!IMPORTANT]
> - **Vibe Host KHÔNG tự động build khi push `github/main`.** Quy tắc chuẩn: **Deploy = `git push github main` + bấm Redeploy trên giao diện Vibe Host (hoặc gọi MCP `redeploy_project`)**, luôn **redeploy `dowload-video-backend` TRƯỚC**, chờ hoàn tất `online` rồi mới redeploy `dowload-video-frontend`.
> - Hạ tầng gồm 2 project ứng dụng + 2 dịch vụ dữ liệu nội bộ sau IP `203.171.31.200`:
>   - **Backend (`dowload-video-backend`, ID `cmsq51nq002be0i5fjsgw8nqh`)**: `https://dvid-api.vibe1.tinhgon.xyz` (`subdir: backend`, build từ `backend/Dockerfile`, chạy `./docker-entrypoint.sh`).
>   - **Frontend (`dowload-video-frontend`, ID `cmsq522wp02c00i5fxgb7mtas`)**: `https://dowloadvideo.io.vn` & `https://dvid.vibe1.tinhgon.xyz` (`subdir: frontend`, build từ `frontend/Dockerfile`, `VITE_API_URL=https://dvid-api.vibe1.tinhgon.xyz`).
>   - **Database thật đang chạy (`DB_BACKEND=postgres`)**: **PostgreSQL `vidgrab-backup`** (`cmt72sxcd00h80i5futghc3dj`) của Vibe Host qua adapter `PgClient` (`backend/app/core/pg_client.py`) — **KHÔNG phải Supabase** (Supabase chỉ còn giữ vai trò xác thực Auth & bản sao dự phòng).
>   - **Redis đang chạy**: **`dowload-video-redis`** (`cmspydxqw003v0i5fl6bn1k69`) qua `REDIS_URL`.
> - Các tài liệu cũ (`docs/RUNBOOK.md`, `deploy-vps.sh`, `docs/DEPLOY-2026-11.md`, `docs/DEPLOY-flow-cleanup.md`) đã **LỖI THỜI** — chỉ áp dụng tài liệu này.

---

## Bước 1 — Biến môi trường (`dowload-video-backend`) & Bảng giá Redis `plans:settings`

### 1.1 Biến môi trường trên Vibe Host → Project `dowload-video-backend`

1. Các biến bắt buộc đã khai báo trên `dowload-video-backend`:
   - `FLOW_URL_SIGNING_SECRET`: chuỗi bí mật riêng **tối thiểu 16 ký tự** (khuyến nghị 64 ký tự hex từ `openssl rand -hex 32`, không dùng trùng `JWT_SECRET` hay `SUPABASE_SERVICE_KEY`).
   - `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID`: bot và nhóm/chat admin nhận tin `[Startup]` cùng cảnh báo giao dịch `needs_manual_review` / lỗi khởi tạo bảng.
   - `DB_BACKEND=postgres` & `DATABASE_URL`: kết nối tới PostgreSQL `vidgrab-backup`.
   - `REDIS_URL`: kết nối tới `dowload-video-redis`.
2. **Kết quả rà soát `ENV=production` (đã xác nhận an toàn)**:
   - Toàn bộ `backend/` chỉ có 2 file đọc `ENV` / `ENVIRONMENT` / `APP_ENV`:
     - `backend/app/main.py` (dòng 273, 354): mặc định vốn đã là `"production"` (ẩn `/docs`, `/redoc`, `/openapi.json` và chạy ping IndexNow sau 45s) → **không đổi hành vi**.
     - `backend/app/api/flow_cleanup.py` (`_is_production_env()`): khi `ENV=production`, bắt buộc `len(FLOW_URL_SIGNING_SECRET) >= 16` ngay lúc khởi động và tắt fallback Redis in-memory (trả `503` fail-closed nếu mất Redis).
   - Không phát sinh biến mới chưa khai báo, không ảnh hưởng CORS, cookie, auth hay cổng thanh toán → **an toàn tuyệt đối để đặt `ENV=production`** (với điều kiện `FLOW_URL_SIGNING_SECRET` dài `>= 16` ký tự).
3. Kiểm tra nhanh trên **Project `dowload-video-frontend`**:
   - Giữ nguyên `VITE_API_URL=https://dvid-api.vibe1.tinhgon.xyz`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`.
   - Đảm bảo **KHÔNG** bật `VITE_FEATURE_LOGO_INPAINT=true` cho tới khi tính năng Xoá Logo sẵn sàng mở chính thức.

### 1.2 Cấu hình giá gói tại `/vid-admin/plans` (Redis hash `plans:settings`)

- Đăng nhập `https://dowloadvideo.io.vn/vid-admin/plans`:
  - Kiểm tra trình duyệt không bị ghi đè `localStorage.getItem('vg_api_base')`.
  - Cập nhật giá **Starter** (`49000` / `590000`), **Pro** (`99000` / `890000`), **Team** (`279000` / `2790000`) và USD tương ứng.
  - Từ bản `fix/post-deploy-2026-11-11`:
    - `POST /api/v1/admin/plans/settings` nhận đủ mọi trường Starter / Pro / Team (VNĐ + USD), từ chối trường lạ bằng `HTTP 422`, và đọc lại giá trị thực trực tiếp từ Redis (`strict=True`) để trả về.
    - Giao diện chỉ hiện **"Đã lưu"** khi toàn bộ trường trả về khớp giá trị vừa nhập, đồng thời hiện **cảnh báo đỏ** nếu 2 gói có cùng giá VNĐ (hoặc chênh `≤ 1.000₫`) trong cùng kỳ tháng/năm (tránh SePay Case A phải chuyển duyệt tay).

---

## Bước 2 — Khởi tạo tự động bảng `044` & `045` trên PostgreSQL `vidgrab-backup`

> [!NOTE]
> - Database thật đang phục vụ người dùng là **Postgres `vidgrab-backup` của Vibe Host** (`DB_BACKEND=postgres`), không phải Supabase.
> - Trên Postgres thuần của Vibe Host, role `service_role` của Supabase thường không tồn tại (user kết nối `DATABASE_URL` không có quyền `CREATEROLE`, nhưng là `tableowner` nên mặc định bypass RLS).
> - Từ bản `fix/post-deploy-2026-11-11`, **không cần chạy SQL thủ công khi deploy**: hàm `ensure_044_045_once_on_startup()` (`backend/app/core/migrations_044_045.py`, theo mẫu `ensure_schema` của `042` trong `payment_orders.py`) sẽ **tự động tạo idempotent** cả 2 bảng `flow_consent_audit_logs` (`044`) và `payment_webhook_events` (`045`) kèm đủ 5 index + `ENABLE ROW LEVEL SECURITY` ngay lúc backend khởi động:
>   - Có khoá Redis `SET NX` (`db:migrations:044_045:startup_lock`) để 2 worker `uvicorn` không chạy chồng nhau.
>   - Policy `TO service_role` chỉ được tạo khi `pg_roles` có role `service_role`.
>   - Nếu có lỗi kết nối/SQL: tự động che mật khẩu DSN, ghi log `[Startup] Migrations 044/045 ensure FAILED` và gửi cảnh báo Telegram mà **không làm sập ứng dụng**.
> - File `database/migrations/vibehost_pg_044_045.sql` được giữ trong repo làm tài liệu SQL tham khảo.

### Kiểm tra trạng thái 2 bảng (CHỈ ĐỌC) sau khi khởi động

1. **Qua endpoint Admin chuyên dụng (nhanh, chỉ đọc)**:
   ```bash
   curl -sS "https://dvid-api.vibe1.tinhgon.xyz/api/v1/admin/db-migration/tables-044-045" \
     -H "Authorization: Bearer <ADMIN_SESSION_TOKEN>"
   ```
   → Kỳ vọng: `"ok": true`, `"all_ready": true`, trong đó:
   - `flow_consent_audit_logs`: `exists = true`, `rls_enabled = true`, `index_count >= 3`.
   - `payment_webhook_events`: `exists = true`, `rls_enabled = true`, `index_count >= 4`.
2. **Qua giao diện `/vid-admin` → Cơ sở dữ liệu → Kiểm tra lại** (`POST /api/v1/admin/db-migration/probe`): trường `probe.target.migrations_044_045.all_ready` trả `true`.

---

## Bước 3 — Merge vào `main`, push `github/main` VÀ Redeploy tuần tự (= DEPLOY)

> [!CAUTION]
> Chỉ thực hiện khi có xác nhận rõ ràng. Luôn **redeploy `dowload-video-backend` TRƯỚC**, chờ job lên `online` hoàn tất rồi mới redeploy `dowload-video-frontend`.

```bash
git checkout main
git merge --no-ff fix/post-deploy-2026-11-11 -m "Merge branch 'fix/post-deploy-2026-11-11' into main"
git push github main
```

Thứ tự kích hoạt trên Vibe Host (giao diện web hoặc MCP `redeploy_project` + `wait_for_job`):
1. `redeploy_project` cho **`dowload-video-backend`** (`cmsq51nq002be0i5fjsgw8nqh`) → chờ build + healthcheck xong (`status: online`).
2. `redeploy_project` cho **`dowload-video-frontend`** (`cmsq522wp02c00i5fxgb7mtas`) → chờ build xong (`status: online`).

---

## Bước 4 — Kiểm tra Runtime Logs & Telegram `[Startup]`

> [!TIP]
> Công cụ `list_env` của Vibe Host chỉ trả **tên biến** (`id`, `key`, `isSecret`), không bao giờ trả giá trị biến. Do đó cách chuẩn và an toàn nhất để xác nhận `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` hoạt động sau khi redeploy là kiểm tra thông báo `[Startup]`.

Ngay sau khi `dowload-video-backend` lên `online`:
1. Kiểm tra **Runtime Logs** của `dowload-video-backend` (`get_runtime_logs` với `filter: "[Startup]"`):
   - Có dòng `[Startup] Migrations 044/045 ensure: {'ok': True, 'status': 'ensured', ...}` (hoặc `'skipped_locked'` ở worker thứ 2).
   - Có dòng **`[Startup] Telegram notification: sent`**. (Nếu hiện `skipped_or_failed` hoặc `[Telegram] ⚠ API error`, kiểm tra lại `TELEGRAM_BOT_TOKEN` / quyền gửi tin vào `TELEGRAM_CHAT_ID`).
2. Kiểm tra nhóm/chat Telegram của admin: nhận được tin nhắn **`🚀 VidGrab Server Khởi Động`**.

---

## Bước 5 — Smoke Test sau Deploy

1. **Healthcheck Backend**:
   ```bash
   curl -sS -i https://dvid-api.vibe1.tinhgon.xyz/health
   ```
   → Kỳ vọng: HTTP `200 OK`.
2. **Xác nhận 2 bảng `044` & `045` trên `vidgrab-backup`**:
   - Gọi `GET /api/v1/admin/db-migration/tables-044-045` (hoặc bấm **Kiểm tra lại** ở `/vid-admin` → Cơ sở dữ liệu) → `all_ready: true` (`exists: true`, `rls_enabled: true`, đủ index cho cả 2 bảng).
3. **Kiểm tra bảng giá `/api/v1/plans` đúng giá chuẩn**:
   ```bash
   curl -sS https://dvid-api.vibe1.tinhgon.xyz/api/v1/plans
   ```
   → Kỳ vọng (sau khi lưu tại `/vid-admin/plans`):
   - `starter`: `price_month_vnd = 49000`, `price_year_vnd = 590000`, `price_month_usd = 1.99`
   - `pro`: **`price_month_vnd = 99000`**, **`price_year_vnd = 890000`**, `regular_month_vnd = 139000`, `price_month_usd = 3.99`
   - `team`: `price_month_vnd = 279000`, `price_year_vnd = 2790000`, `price_month_usd = 11.99`
4. **Kiểm tra log `[Startup]` gửi Telegram thành công**:
   - Runtime logs có `[Startup] Telegram notification: sent` và nhóm Telegram nhận tin khởi động.

---

## Bước 6 — Quy trình Rollback (khi phát sinh sự cố)

Hai bảng `flow_consent_audit_logs` (`044`) và `payment_webhook_events` (`045`) là bảng độc lập (chỉ thêm mới), nên khi rollback ứng dụng **KHÔNG cần xoá bảng trên `vidgrab-backup`**.

### Cách A — Rollback tức thì trên Vibe Host (nhanh nhất, < 1 phút)
- Mở **Vibe Host → Project `dowload-video-backend` (hoặc `dowload-video-frontend`) → Deployments** (hoặc dùng `rollback_project`) → chọn phiên bản ổn định liền trước để chuyển lưu lượng ngay về image cũ.

### Cách B — Revert commit trên `github/main` + bấm Redeploy trên Vibe Host
*(Tuyệt đối dùng `git revert`, **không** dùng `git reset --hard` + `git push --force` lên `github/main`).*

```bash
git checkout main
git pull github main
git revert -m 1 <MERGE_COMMIT_SHA> --no-edit
git push github main
```

Sau khi push commit revert, bấm **Redeploy** cho `dowload-video-backend` trước rồi đến `dowload-video-frontend` trên Vibe Host và kiểm tra lại `GET https://dvid-api.vibe1.tinhgon.xyz/health` trả `200 OK`.
