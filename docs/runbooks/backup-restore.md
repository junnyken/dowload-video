# Runbook: Sao lưu và khôi phục (CSDL + cấu hình Redis)

> Dành cho chủ sản phẩm. Task #6332 (Phase 34A-2). Viết 13/10/2026.
>
> **CHƯA DIỄN TẬP TRÊN PRODUCTION.** Mọi lệnh khôi phục dưới đây mới chỉ được
> kiểm bằng test tự động (fakeredis, máy chủ giả). Chưa ai khôi phục thật một
> bảng hay một Redis từ bản sao lưu production. Lần diễn tập đầu tiên phải làm
> trên một Redis/Supabase **trống, riêng** — không phải production.

---

## 1. Sao lưu gồm những gì, nằm ở đâu

Mỗi ngày lúc **19:00 UTC (02:00 giờ VN)**, tác vụ `backup_database_daily` tạo 2 tệp:

| Tệp | Nội dung |
|---|---|
| `vidgrab-<ngày>.json.gz` | Dữ liệu mọi bảng Supabase (qua PostgREST) + danh sách `auth.users`. **Không có** schema — schema ở `database/migrations/` trong git. |
| `redis-config-<ngày>.json.gz` | Cấu hình admin lưu trong Redis: System Config, mục tiêu dò (probe), giới hạn đã biết, chu kỳ dò trả phí, duyệt yt-dlp cho app Windows, công tắc YouTube/ASR/China, yêu cầu gỡ (takedown), trọng số/tắt cookie, metadata nhóm Apify… Danh sách đầy đủ: docstring của `backend/app/core/redis_config_backup.py`. |

**Không** sao lưu: cache, khoá (lock), bộ đếm, hạn mức, phiên đăng nhập admin, trạng thái
ngắt mạch, hàng đợi — những thứ này tự dựng lại.

### Bí mật (cookie, proxy, khoá ScraperAPI, token Apify, tài khoản throwaway)

- **Mặc định: KHÔNG lưu giá trị.** Tệp Redis chỉ ghi *số lượng* khoá mỗi nhóm
  (`secret_meta`), tên khoá đã băm. Mất Redis thì phải nhập lại các mục này trong
  admin (Cookies, Proxy, ScraperAPI, China/Apify) — tệp sao lưu cho biết cần nhập bao nhiêu.
- Muốn lưu cả bí mật: đặt System Config `backup_redis_include_secrets` = `true`
  (tắt: đặt giá trị khác hoặc xoá). Khi bật, bản **có** bí mật chỉ vào kho Supabase
  riêng tư; S3 và Postgres **luôn** nhận bản không có bí mật.
- **Cookie được mã hoá khi lưu (task #6402).** Phần tử `cookie_pool:*` có dạng
  `enc:v1:…` (AES-256-GCM); khoá suy ra bằng HKDF từ `SUPABASE_SERVICE_KEY` (hoặc
  `COOKIE_POOL_ENCRYPTION_KEY` nếu có đặt — base64 của 32 byte, không bắt buộc).
  Bản sao lưu kèm bí mật vì thế chứa **bản mã**, không phải cookie rõ.
  **Khôi phục cookie mã hoá cần đúng khoá nguồn đó**: nếu `SUPABASE_SERVICE_KEY` đã
  đổi, cookie khôi phục hiện "không giải mã được" ở trang Cookies, bị bỏ qua khi
  tải (không lỗi, không lộ) → xoá và tải lên lại, hoặc đặt
  `COOKIE_POOL_ENCRYPTION_KEY` = khoá suy ra từ giá trị cũ. Cookie cũ dạng chưa mã
  hoá vẫn đọc được; nút "Mã hoá lại kho cookie" (hoặc
  `POST /api/v1/admin/cookies/encrypt-all`) mã hoá chúng, worker cũng tự chạy một
  lần khi khởi động.

```bash
curl -X POST "$API/api/v1/admin/config/backup_redis_include_secrets" \
  -H "X-Admin-Token: $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"value": "true", "description": "Sao lưu Redis kèm bí mật (chỉ kho Supabase)"}'
```

### Ba nơi lưu

| Nơi | Điều kiện | Ghi chú |
|---|---|---|
| Supabase Storage, bucket `db-backups` (đổi bằng `BACKUP_BUCKET`) | luôn có | Riêng tư. Mất cả project Supabase thì mất luôn bản này. Giữ `BACKUP_RETENTION_DAYS` ngày (mặc định 14). |
| Postgres trên nền tảng host, bảng `db_backups` | khi có `BACKUP_PG_DSN` | Mỗi tệp một dòng (`name`, `data` bytea). Đọc ngược lại để kiểm ngay sau khi ghi. |
| S3/R2, khoá `vidgrab/<tên tệp>` | khi đủ 4 biến `BACKUP_S3_*` | Bản duy nhất thật sự nằm ngoài. |

Lấy tệp từ Postgres:

```bash
psql "$BACKUP_PG_DSN" -At -c "SELECT encode(data,'base64') FROM db_backups WHERE name='redis-config-2026-10-13.json.gz'" \
  | base64 -d > redis-config-2026-10-13.json.gz
```

Lấy tệp từ Supabase: Dashboard → Storage → `db-backups` → tải tệp về.

---

## 2. "Sao lưu ngay"

Trang admin **chưa có nút** cho việc này. Gọi API (chạy ngay, trả kết quả thật, mất vài giây):

```bash
curl -X POST "$API/api/v1/admin/backup/run" -H "X-Admin-Token: $ADMIN_TOKEN"
```

Kết quả có `rows`, `tables`, `bytes`, `offsite`, `postgres`, `warnings` cho CSDL và mục
`redis`: `keys` (tổng số khoá), `bytes`, `secrets_included`, `groups` (số khoá + dung lượng
từng nhóm). Telegram cũng nhận một tin có dòng "🧠 Cấu hình Redis".

`warnings` không rỗng = có nơi lưu bị lỗi. Đọc kỹ, đừng bỏ qua.

---

## 3. Khôi phục cấu hình Redis

Chạy trong thư mục `backend/` (container backend hoặc máy có Python + gói `redis`).

```bash
# 1) Xem trước — KHÔNG ghi gì
RESTORE_REDIS_URL=redis://host:6379/0 python scripts/restore_redis_config.py redis-config-2026-10-13.json.gz
#    thêm --show-keys để xem danh sách khoá (khoá bí mật chỉ hiện dạng <nhóm #mã-băm>)

# 2) Ghi thật — bỏ qua khoá đang tồn tại
python scripts/restore_redis_config.py redis-config-2026-10-13.json.gz --redis-url redis://host:6379/0 --apply

# 3) Chỉ khi chắc chắn muốn đè giá trị hiện có
python scripts/restore_redis_config.py FILE --redis-url ... --apply --overwrite
```

- Bảng in ra: mỗi nhóm có bao nhiêu khoá trong tệp, sẽ ghi, đã tồn tại (bị bỏ qua), sẽ đè.
- TTL được khôi phục bằng thời hạn **còn lại lúc sao lưu** (vd metadata cookie).
- Dòng "NOT in backup (secret…)" = nhóm bí mật chỉ có số lượng → nhập lại trong admin.
- Không bao giờ in giá trị.
- Mã thoát 2 khi xem trước có xung đột (chỉ để báo, không phải lỗi).

## 4. Khôi phục dữ liệu bảng (CSDL)

Trước đây **không có** đường khôi phục cho bản xuất JSON. Nay có `backend/scripts/restore_db_export.py`.

```bash
export SUPABASE_URL=...  SUPABASE_SERVICE_KEY=...   # của project ĐÍCH
python scripts/restore_db_export.py vidgrab-2026-10-13.json.gz                      # liệt kê bảng + số dòng
python scripts/restore_db_export.py FILE --table download_jobs                      # xem trước: bao nhiêu dòng đã có
python scripts/restore_db_export.py FILE --table download_jobs --apply              # ghi, bỏ qua dòng đã có
python scripts/restore_db_export.py FILE --table download_jobs --apply --overwrite  # ghi đè dòng trùng khoá chính
python scripts/restore_db_export.py FILE --table download_jobs --offline            # xem trước không cần kết nối
```

Lưu ý:

- **Schema trước, dữ liệu sau.** Project trống thì chạy `database/migrations/` trước.
- **`auth.users` không được script khôi phục.** Tệp có danh sách (`auth_users`) nhưng
  mật khẩu đã băm không đưa lại qua API thường được. Nếu mất cả người dùng: dùng tính
  năng Backups/PITR của Supabase (Dashboard → Database → Backups) hoặc liên hệ hỗ trợ
  Supabase. Bảng nào trỏ tới người dùng (`profiles`, …) chỉ khôi phục được sau khi người
  dùng đã có.
- Trường hợp hay gặp nhất — **một bảng bị xoá nhầm** (như `download_jobs` từng bị) — chỉ
  cần `--table <tên bảng> --apply`.
- Không bao giờ in nội dung dòng; chỉ tên bảng và số lượng.

## 5. Diễn tập và bấm giờ

Làm mỗi quý, ghi kết quả vào cuối tệp này.

1. Bấm giờ bắt đầu (`date`).
2. "Sao lưu ngay" (mục 2), kiểm `warnings` rỗng.
3. Tải `redis-config-<ngày>.json.gz` và `vidgrab-<ngày>.json.gz` về.
4. Dựng Redis trống: `docker run -d --name redis-drill -p 6390:6379 redis:7`.
5. `restore_redis_config.py FILE --redis-url redis://localhost:6390/0` (xem trước) → `--apply`.
   So số khoá theo nhóm với mục `redis.groups` của bước 2.
6. CSDL: chỉ dùng một project Supabase **trống, riêng cho diễn tập** (đã chạy migrations).
   `restore_db_export.py FILE --table <bảng nhỏ> --apply`, so số dòng với dashboard.
7. Bấm giờ kết thúc. Ghi: ngày, người làm, thời gian, sai lệch, việc phải sửa.
8. Dọn: `docker rm -f redis-drill`.

### Nhật ký diễn tập

| Ngày | Người | Redis (phút) | CSDL (phút) | Ghi chú |
|---|---|---|---|---|
| — | — | — | — | **Chưa diễn tập lần nào trên dữ liệu production.** |

## 6. Những gì CHƯA được kiểm

- Chưa chạy khôi phục từ một tệp sao lưu production thật (Redis lẫn CSDL).
- Chưa đo thời gian khôi phục thật (RTO trong `docs/RUNBOOK.md` là mục tiêu, chưa phải số đo).
- Chế độ `backup_redis_include_secrets` mới chỉ kiểm bằng test.
- `restore_db_export.py` chưa thử trên bảng có khoá chính không phải `id` hay bảng lớn.
- Chưa có nút "Sao lưu ngay" trên giao diện admin.
