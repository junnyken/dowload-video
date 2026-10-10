# Phase 33-0 — Audit lịch tải + chặn tải trùng + vá SSRF webhook (Task #6254)

Nhánh `feat/phase33-0`, gốc `36322f0` (= GitHub main = production). Chưa push, chưa deploy.
Đây là bước đầu của Phase 33 (Channel Watch, Delivery, Webhook Automation); chưa làm
watch_sources / watch_subscriptions / watch_items_seen / watch_deliveries / hàng đợi watch_scan / outbox ký.

## 1. Luồng lịch tải (đã lần từ code)

| Bước | Ở đâu | Hàng đợi Celery |
|---|---|---|
| API tạo/sửa/xoá/bật-tắt lịch | `backend/app/api/schedule.py` (`get_required_user`, giới hạn `_SCHEDULE_LIMITS` free 3 / pro 20) | — (API) |
| Nhịp quét | beat `scan-scheduled-jobs-every-1min` → `scan_scheduled_jobs` (60 s) | `celery` |
| Kích hoạt | `_trigger_job` → `_trigger_single` / `_trigger_channel` / `_trigger_keyword`, rồi `_compute_next_run` (UTC) | `celery` (chạy trong nhịp quét) |
| Quét kênh | `scrape_channel_task` → `scrape_channel_entries_sync` (yt-dlp / TikWM / Douyin / XHS / Kuaishou…) → tạo job con → phát theo đợt (wave) | `bulk` |
| Tải từng video | `process_video_task` | `downloads` |
| Lịch sử | dòng `download_jobs`; `/history` lọc `user_id` | — |

Production chạy một container (`backend/docker-entrypoint.sh`): **một** worker `-Q downloads,bulk,light,media,analysis,celery --concurrency=2` + beat.
`docker-compose.yml` (nhiều worker) **không có worker nào nghe `bulk`/`light`** — nếu ai đó deploy bằng file đó thì quét kênh (thủ công lẫn theo lịch) sẽ nằm chờ mãi.

### Độ đúng của bộ lập lịch
- Lỗi thiếu `import uuid` **đã được vá** (mỗi hàm `_trigger_*` tự `import uuid`). Test mới chạy thật nhịp quét → trigger → quét kênh, chạy được.
- `next_run_at` tính theo UTC, `daily` = lần kế tiếp của `run_at`; `weekly` luôn +7 ngày nếu trùng thứ. Đúng như code viết; giờ người dùng nhập được hiểu là UTC (frontend phải tự đổi múi giờ — chưa kiểm).
- **Lỗi đang có**: `POST /schedule/{id}/run` gọi `_trigger_job(job)` thiếu 2 tham số → luôn `TypeError` → 500 "Không thể khởi động job". Chưa sửa (ngoài phạm vi, báo lại).
- Không có khoá/claim dòng: nếu một nhịp quét chạy lâu (lịch `keyword` gọi yt-dlp **đồng bộ** ngay trong nhịp quét) thì nhịp kế có thể kích hoạt lại cùng lịch trước khi `next_run_at` được đẩy. Với lịch kênh, sổ ghi (bên dưới) dùng `ZADD NX` nên hai lượt chồng nhau không tải trùng một video.
- `_trigger_channel` bỏ qua `auto_collection_id`; `duplicate_suppression` / `min_interval_hours` được lưu nhưng không nơi nào đọc.
- Job con của một lượt quét kênh (thủ công lẫn theo lịch) **không có `user_id`** → không hiện trong `/history`, chỉ dòng "kênh" hiện.
- `_check_limit` đếm cả lịch đã tắt/đã chạy xong `once`; tier `team`/`enterprise` rơi về giới hạn 3 và câu báo ghi "Pro plan".

## 2. Tải trùng — trước / sau (số đo từ test)

`backend/tests/test_schedule_channel_dedupe.py` — lịch daily, kênh có 5 video, lượt 2 kênh có thêm 1 video mới.

| | Lượt 1 | Lượt 2 | Video tải lại ở lượt 2 | Tổng job con |
|---|---|---|---|---|
| Trước (commit `bb8a185` ghim hành vi cũ) | 5 | 5 | 4 | 10 |
| Sau | 5 | 1 (đúng video mới) | 0 | 6 |
| Sau, lượt 3 (không có gì mới) | — | 0 | 0 | 6 |

### Cách chặn (chỉ lượt chạy theo lịch kiểu `channel`; tải kênh/bulk thủ công không đổi)
- `backend/app/core/schedule_ledger.py`: một Redis sorted set mỗi lịch `vidgrab:sched:seen:<schedule_id>`, phần tử = `(platform, id video)`
  (lấy từ entry hoặc từ URL: YouTube `v=`/shorts/youtu.be, TikTok/Douyin `/video/<id>`, Kuaishou, XHS, X); không có id ổn định → `url:<sha256 URL chuẩn hoá>`.
  Giữ tối đa 2000 phần tử mới nhất, TTL 180 ngày tính từ lượt chạy gần nhất.
- `scrape_channel_task` nhận thêm `_schedule_id`, `_schedule_first_run` (chỉ `_trigger_channel` truyền). Sau khi quét, bỏ video đã biết **trước khi tạo job**, giành từng video bằng `ZADD NX`.
- Giới hạn mỗi lượt: `min(max_videos của lịch (mặc định 10), SCHEDULE_CHANNEL_MAX_ITEMS_PER_RUN, 20)`; con số này cũng là số video yêu cầu khi quét kênh (giảm chi phí quét).
  Trước đây `max_videos` của lịch không bị chặn trên.
- **Lịch đã chạy trước khi deploy** (có `last_run_at`, chưa có sổ): dựng sổ từ các job con `success` thuộc các batch trước của cùng người dùng + cùng URL kênh.
  Chỉ tin khi có giao với danh sách hiện tại; không có lịch sử dùng được → **lượt baseline**: ghi nhận mọi video đang có, không tải gì, lượt sau chỉ tải video mới.
  Lịch mới tạo (chưa từng chạy) → tải bình thường lượt đầu rồi ghi sổ.
- Redis lỗi → **đóng an toàn**: lượt đó không tải gì, dòng kênh `failed` kèm câu báo; lượt sau thử lại.
- Ghi sổ lúc **tạo job** (không đợi tải xong): video tải hỏng sẽ không được lịch tải lại lần sau (process_video_task vẫn tự retry như cũ).

### Vì sao Redis, không migration
Phase này không được thêm migration; dữ liệu là "đã xử lý" — mất thì đi đường seed/baseline chứ không tải lại hàng loạt.
Redis production chạy `appendonly yes`, `maxmemory 512mb`, `volatile-lru` → khoá có TTL có thể bị đẩy ra khi thiếu RAM; khi đó lượt kế là baseline (mất tối đa một lượt video mới, không tải trùng).
Phase 33 sau sẽ thay bằng bảng `watch_items_seen`.

### Công tắc
- `SCHEDULE_DEDUPE_ENABLED` mặc định **true**. Đây là bản vá lỗi chứ không phải tính năng mới, nên bật sẵn; `false` = quay về cách chọn cũ (mọi lượt tải N video mới nhất) nhưng vẫn giữ trần 20.
- `SCHEDULE_CHANNEL_MAX_ITEMS_PER_RUN` (tuỳ chọn) — hạ trần mỗi lượt; trần cứng 20 không tắt được.

## 3. Tác động CPU/đĩa của một lượt lịch kênh
- 1 task `bulk` quét kênh: chặn 1 trong 2 slot worker production tới khi quét xong (timeout 120 s YouTube; Douyin/XHS/Kuaishou ≥ 90 s + thời gian actor), giới hạn mềm 300 s.
- N task `downloads` (mỗi cái tới 5–6 phút), có thể ghi file tạm xuống đĩa (dọn theo `periodic_cleanup_downloads` 5 phút/lần, hạn theo tier).
- Trước bản vá: N task mỗi ngày mãi mãi dù kênh không có gì mới. Sau: chỉ video mới (thường 0–2/ngày), tối đa 20.

## 4. Chi tiền cho nền tảng tính phí (Douyin / Kuaishou / XHS qua lớp China access)
- Lượt theo lịch truyền `user_id` → requester `user:<id>` (giống tải thủ công). Quét kênh XHS/Kuaishou đi qua `profile_listing.list_profile`: cờ bật/tắt, chỉ người đăng nhập,
  trần = min(max_videos, 20, phần còn lại trong ngày), khoá chống trùng, bộ đệm danh sách, rồi **một lượt Apify có đặt trước ngân sách** (budget pre-check + reservation).
  Douyin: `list_douyin_channel_via_access_layer` cùng kiểu (task #6055); không có cookie/Apify → dòng kênh fail sớm, không tạo job.
- Vậy **một lượt lịch kênh China có thể tốn một lượt Apify mỗi lần chạy** (trừ khi trúng bộ đệm), có chặn trần ngân sách. Sổ ghi không làm giảm chi phí quét danh sách; nó chặn chi phí tải lại từng video. Trần 20 + max_videos giới hạn ước tính mỗi lượt.
- Không có test nào ở đây gọi Apify.

## 5. Webhook — SSRF đã vá
Có **hai** hệ webhook:
1. Webhook người dùng (Pro): `api/webhook.py` (đăng ký/thu hồi/log), `tasks/webhook_tasks.py::deliver_webhook_task`, gọi từ `process_video_task` khi tải xong.
2. Webhook partner/tenant: `api/partner.py::register_webhook` (`webhook_endpoints`), `services/webhook_dispatcher.py` (`dispatch_event`, `retry_webhook_delivery`).

Trước: (1) `requests.post` thẳng URL người dùng, theo redirect, timeout 15 s; (2) `httpx.post` không kiểm địa chỉ và ghi 200 ký tự thân phản hồi vào log.

Sau (`backend/app/core/webhook_guard.py`, dùng chung bộ luật IP của `ssrf_guard`):
- chỉ `https` (code cũ vốn đã bắt https, không có ngoại lệ localhost dev), cấm `user:pass@`, cấm tên nội bộ compose;
- phân giải DNS **mới, không cache** (cache 30 s của `ssrf_guard._resolve` bị né cố ý) — mọi địa chỉ phải công khai: private, loopback, link-local, multicast, reserved, unspecified, CGNAT, metadata `169.254.169.254` / `fd00:ec2::254` / `100.100.100.200`, IPv4-mapped IPv6; tên không phân giải được → từ chối (ssrf_guard cũ thì cho qua);
- kiểm lúc đăng ký (400 cho webhook người dùng, 422 cho partner) **và** ngay trước mỗi lần gửi; request được **ghim vào đúng IP đã kiểm** (urllib3, SNI + kiểm chứng chỉ theo hostname) → DNS rebinding không lách được;
- không theo redirect (3xx = thất bại "redirect not followed"), timeout 10 s, đọc tối đa 4 KB rồi bỏ, không lưu/ghi thân phản hồi;
- địa chỉ bị chặn → không retry (lỗi DNS tạm thời vẫn retry).
- Đã thử thật 1 lần tới `https://example.com/` (ghim IP + TLS): nhận HTTP 405 — đường gửi thật chạy được.

**Phát hiện thêm (chưa sửa):** `app.tasks.webhook_tasks` và `app.services.webhook_dispatcher` **không có trong `include` của Celery** — kiểm trong registry sau `import_default_modules()`: không có task webhook nào. Tiến trình worker chính sẽ coi message `deliver_webhook_task` / `retry_webhook_delivery` là "unregistered task" và bỏ → nhiều khả năng webhook người dùng **chưa từng được gửi** trên production (chưa đối chiếu log production).

## 6. Push (Web Push)
- `pywebpush>=2.0.0` có trong `requirements.txt` nhưng **không được import ở đâu**. `api/push.py::_send_push_notification` POST JSON trần (không mã hoá, không VAPID) → dịch vụ push thật sẽ từ chối.
- `video_tasks` (ZIP xong) import `send_push_notification` từ `app.api.push` — **hàm này không tồn tại** → ImportError bị nuốt.
- Biến môi trường cần (chỉ tên): `PUSH_VAPID_PUBLIC_KEY` (hoặc `VAPID_PUBLIC_KEY`) cho `/vapid-key`; code nhắc `PUSH_VAPID_PRIVATE_KEY` nhưng chưa đọc. Thường cần thêm một "subject" (mailto:) khi wiring pywebpush.
- **Rủi ro SSRF chưa vá:** endpoint push do người dùng gửi lên (`/push/subscribe`) được server POST tới mà không kiểm địa chỉ (`/push/test`, `notify_user_job_done`). Nên đưa vào Phase 33 (dùng `webhook_guard` hoặc pywebpush với allowlist host dịch vụ push).

## 7. Telegram
- `app/core/notifications.py` chỉ gửi tới **một** `TELEGRAM_CHAT_ID` của vận hành (token `TELEGRAM_BOT_TOKEN`): lỗi quét kênh (trừ lỗi hướng người dùng), job lỗi, ZIP xong, báo cáo ngày.
- `api/telegram_link.py` chỉ liên kết tài khoản cho bot (`TELEGRAM_BOT_SECRET`); **không có đường báo cho từng người dùng khi lịch chạy xong**. Production đang trả 403 (owner đã biết).

## 8. Quyền & đối tượng
- Khách (chưa đăng nhập) **không** tạo được lịch: mọi route lịch dùng `get_required_user` → 401. Không có đăng nhập ẩn danh Supabase.
  Nhưng API key cá nhân (`X-API-Key`, `vidgrab_…`) và Bearer API key cũ được coi là người dùng → tạo được lịch qua API.
- Giới hạn: free 3, pro 20 (đếm mọi dòng, kể cả đã tắt). Không kiểm `max_videos`, nền tảng, hay tần suất thực (`min_interval_hours` không được dùng).
- Xác thực email: backend **không đọc `email_confirmed_at`** ở đâu cả; `get_optional_user` chỉ lấy `id`, `email` từ `supabase.auth.get_user`. Việc chặn chưa xác thực hoàn toàn phụ thuộc cấu hình Supabase "Confirm email" (không cấp phiên trước khi bấm link).

## 9. Kho API key / webhook hiện có
- `api/api_keys.py` — key cá nhân `vidgrab_` (Pro), header `X-API-Key`, quota chung với web.
- `api/tenant_api_keys.py` — key partner `vgp_` theo tenant (scope, xoay vòng).
- `api/partner.py` — API partner + đăng ký webhook tenant (`webhook_endpoints`, secret lưu thô — "MVP").
- `api/webhook.py` + `tasks/webhook_tasks.py` — webhook người dùng (HMAC dùng `secret_hash` làm khoá, theo tài liệu).
- `services/webhook_dispatcher.py` — gửi + retry webhook partner (`webhook_deliveries`).

## 10. Chưa kiểm / còn mở
- Chưa đối chiếu log production (unregistered task webhook, lượt lịch thật). Chưa deploy.
- Frontend nhập giờ lịch theo múi nào — chưa kiểm.
- Lịch `keyword` và `single` vẫn tải lại mỗi lượt (ngoài phạm vi chặn của phase này).
- Push SSRF, `/schedule/{id}/run` 500, worker compose thiếu `bulk`/`light`, task webhook chưa đăng ký — báo lại, chưa sửa.
