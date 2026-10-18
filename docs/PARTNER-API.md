# VidGrab Partner API — Hướng dẫn tích hợp

Tài liệu dành cho đội kỹ thuật của đối tác muốn gọi VidGrab từ **backend** của mình
để tải video (TikTok, Douyin, YouTube, Facebook, Instagram, X, …).

---

## 1. Thông tin kết nối

| Mục | Giá trị |
|---|---|
| Địa chỉ API | `https://dvid-api.vibe1.tinhgon.xyz` (VidGrab sẽ báo trước nếu đổi) |
| Đường dẫn gốc | `/api/v1/partner` |
| Xác thực | Header `X-API-Key: vgp_…` (hoặc `Authorization: Bearer vgp_…`) |
| Định dạng | JSON, UTF-8 |

**API key do VidGrab cấp**, mỗi key dài 68 ký tự, bắt đầu bằng `vgp_`. Key chỉ được hiển thị
**một lần** lúc cấp. Hãy lưu key trong biến môi trường hoặc kho bí mật của máy chủ, không đưa
vào mã nguồn hay ứng dụng phía người dùng. Nếu nghi ngờ lộ key, báo VidGrab để đổi key
(key cũ hết hiệu lực ngay).

Có thể yêu cầu VidGrab giới hạn mỗi key chỉ nhận request từ một số IP cố định của bạn
(khuyến nghị cho môi trường production).

---

## 2. Luồng xử lý

Tải video là việc **bất đồng bộ**: bạn tạo job, VidGrab xử lý trong nền, rồi bạn nhận kết quả
bằng cách hỏi lại trạng thái hoặc bằng webhook.

```
POST /jobs  ──►  job "queued"  ──►  (xử lý vài giây đến vài phút)  ──►  "done" | "failed"
                      │                                                    │
                      └── GET /jobs/{id} để hỏi trạng thái ◄───────────────┤
                                                                           └── webhook job.completed / job.failed
```

Một job thường xong trong 5–60 giây; video dài hoặc chất lượng cao có thể tới vài phút
(tối đa khoảng 7 phút trước khi bị tính là lỗi).

---

## 3. Endpoint

### 3.1. Tạo job tải — `POST /api/v1/partner/jobs`

```bash
curl -X POST https://dvid-api.vibe1.tinhgon.xyz/api/v1/partner/jobs \
  -H "X-API-Key: $VIDGRAB_KEY" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.tiktok.com/@user/video/123", "quality": "video"}'
```

| Trường | Bắt buộc | Mô tả |
|---|---|---|
| `url` | có | Link video (http/https) |
| `quality` | không | `video` (mặc định, tối đa 1080p), `video_fast`, `video_720` / `video_1080` / …, `video_4k`, `mp3_128`, `mp3_320` |

Phản hồi `202`:

```json
{
  "id": "6f1c…",
  "status": "queued",
  "url": "https://www.tiktok.com/@user/video/123",
  "quality": "video",
  "priority": 5,
  "created_at": "2026-10-18T12:00:00Z",
  "result": null,
  "error": null
}
```

Mỗi lần gọi endpoint này tính **1 job** vào hạn mức (mục 4). Đừng gửi lại cùng link khi job
cũ chưa xong, vì mỗi lần gửi là một job mới.

### 3.2. Xem trạng thái job — `GET /api/v1/partner/jobs/{id}`

`status` là một trong `queued`, `processing`, `done`, `failed`, `expired`.

Khi `done`:

```json
{
  "id": "6f1c…",
  "status": "done",
  "result": {
    "direct_mp4_url": "https://dvid-api.vibe1.tinhgon.xyz/api/v1/download-local?file=…",
    "title": "Tên video",
    "file_size_mb": 12.4,
    "thumbnail_url": "https://…"
  },
  "error": null
}
```

Khi `failed`, trường `error` chứa lý do.

> **Link file chỉ sống khoảng 20 phút** sau khi job xong. Hãy tải file về hệ thống của bạn
> ngay khi nhận được kết quả.

Gợi ý: hỏi lại mỗi 3–5 giây. Hỏi trạng thái **không** trừ hạn mức job, nhưng vẫn tính vào
giới hạn request/phút.

### 3.3. Danh sách job — `GET /api/v1/partner/jobs?status=done&limit=50`

Trả tối đa 100 job gần nhất của bạn, mới nhất trước.

### 3.4. Huỷ job đang chờ — `DELETE /api/v1/partner/jobs/{id}`

Chỉ huỷ được job còn `queued`. Job đã chạy thì không bị ảnh hưởng.

### 3.5. Xem hạn mức đã dùng — `GET /api/v1/partner/usage`

```json
{
  "plan": "growth",
  "limits": {"api_calls_per_month": 50000, "batch_size_max": 100, "seats_max": 10},
  "this_month": {"api_calls": 1234},
  "remaining": 48766,
  "month_resets_at": "2026-11-01T00:00:00+00:00",
  "rate_limit": {
    "per_minute": 60,
    "per_day": 10000,
    "used_today_this_key": 87,
    "day_resets_at": "2026-10-19T00:00:00+00:00"
  },
  "daily_30d": [{"date": "2026-10-18", "api_calls": 87}]
}
```

`api_calls_per_month = -1` nghĩa là không giới hạn.

### 3.6. Kiểm tra link trước khi tải — `POST /api/v1/partner/validate-urls`

Body `{"urls": ["…", "…"]}`, tối đa 100 link một lần. Cho biết link nào hợp lệ và thuộc nền tảng nào.
Không tạo job, không trừ hạn mức job.

---

## 4. Giới hạn

| Giới hạn | Áp cho | Mặc định | Khi vượt |
|---|---|---|---|
| Request mỗi phút | từng key | 60 | `429` `rate_limited`, có header `Retry-After` (giây) |
| Job mỗi ngày | từng key | 10.000 | `429` `rate_limit_exceeded`, kèm `reset_at` |
| Job mỗi tháng | cả tài khoản | theo hợp đồng | `429` `monthly_quota_exceeded`, kèm `reset_at` |

- Ngày và tháng tính theo giờ **UTC**: ngày mới bắt đầu lúc 07:00 giờ Việt Nam, tháng mới
  bắt đầu lúc 07:00 ngày 1.
- Các giới hạn tính theo **key**, không theo IP. Nhiều máy chủ của bạn dùng chung một IP vẫn
  được tính riêng theo từng key.
- Cần mức cao hơn mặc định? Báo VidGrab để điều chỉnh theo key hoặc theo tài khoản.

Ví dụ phản hồi `429`:

```json
{"detail": {"code": "monthly_quota_exceeded", "message": "Monthly job limit reached for this account",
            "limit": 50000, "reset_at": "2026-11-01T00:00:00+00:00"}}
```

---

## 5. Webhook (khuyến nghị)

Thay vì hỏi trạng thái liên tục, đăng ký một địa chỉ **HTTPS** để VidGrab gọi khi job xong.

```bash
curl -X POST https://dvid-api.vibe1.tinhgon.xyz/api/v1/partner/webhooks \
  -H "X-API-Key: $VIDGRAB_KEY" -H "Content-Type: application/json" \
  -d '{"url": "https://your-domain.com/hooks/vidgrab", "events": ["job.completed", "job.failed"]}'
```

Phản hồi có trường `secret`, **chỉ hiện một lần**. Hãy lưu lại để kiểm chữ ký.

Mỗi lần gọi webhook, VidGrab gửi `POST` với body JSON và các header:

| Header | Ý nghĩa |
|---|---|
| `X-VidGrab-Event` | `job.completed` hoặc `job.failed` |
| `X-VidGrab-Signature-256` | `sha256=<HMAC-SHA256 của body, khoá là secret>` |
| `X-VidGrab-Delivery` | Mã lần gửi (dùng để bỏ qua bản gửi trùng) |

Body:

```json
{"job_id": "6f1c…", "status": "done",
 "result": {"direct_mp4_url": "…", "title": "…", "file_size_mb": 12.4, "thumbnail_url": "…"}}
```

Kiểm chữ ký (Python):

```python
import hmac, hashlib

def valid(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

Webhook của bạn cần trả `2xx` trong vòng 10 giây; nếu không, VidGrab gửi lại sau 30 giây,
5 phút, 30 phút và 2 giờ (tối đa 5 lần). Vì có thể nhận trùng, hãy bỏ qua bản gửi đã xử lý
theo `X-VidGrab-Delivery` hoặc `job_id`. Xem và xoá webhook:
`GET /api/v1/partner/webhooks`, `DELETE /api/v1/partner/webhooks/{id}`.

---

## 6. Mã lỗi

| HTTP | `code` | Ý nghĩa / cách xử lý |
|---|---|---|
| 401 | `partner_auth_required` | Thiếu header `X-API-Key` |
| 401 | `invalid_api_key` | Key sai hoặc đã bị thu hồi |
| 401 | `api_key_expired` | Key đã hết hạn, liên hệ VidGrab |
| 403 | `api_key_inactive` | Key đã bị tắt |
| 403 | `ip_not_allowed` | IP gọi không nằm trong danh sách cho phép của key |
| 403 | `tenant_inactive` | Tài khoản đang tạm ngưng |
| 403 | `insufficient_scope` | Key không có quyền cho thao tác này |
| 404 | `job_not_found` | Không có job này (hoặc job của tài khoản khác) |
| 422 | `invalid_url` | Link không hợp lệ |
| 429 | `rate_limited` | Vượt request/phút, đợi theo `Retry-After` |
| 429 | `rate_limit_exceeded` | Hết job trong ngày của key |
| 429 | `monthly_quota_exceeded` | Hết job trong tháng của tài khoản |
| 429 | `too_many_invalid_keys` | IP gửi quá nhiều key sai, đợi 1 phút |
| 5xx | — | Lỗi phía VidGrab, thử lại sau vài giây (tăng dần thời gian chờ) |

Nguyên tắc thử lại: chỉ thử lại với `429` (theo `Retry-After` / `reset_at`) và `5xx`
(chờ 1s → 2s → 4s → 8s). Các lỗi `4xx` khác cần sửa request, không nên thử lại.

---

## 7. Ví dụ đầy đủ (Python)

```python
import os, time, requests

BASE = "https://dvid-api.vibe1.tinhgon.xyz/api/v1/partner"
H = {"X-API-Key": os.environ["VIDGRAB_KEY"]}

def download(url: str, quality: str = "video") -> dict:
    r = requests.post(f"{BASE}/jobs", json={"url": url, "quality": quality}, headers=H, timeout=30)
    if r.status_code == 429:
        raise RuntimeError(r.json()["detail"])
    r.raise_for_status()
    job_id = r.json()["id"]

    for _ in range(150):                      # tối đa ~7,5 phút
        time.sleep(3)
        s = requests.get(f"{BASE}/jobs/{job_id}", headers=H, timeout=30)
        if s.status_code == 429:
            time.sleep(int(s.headers.get("Retry-After", "5")))
            continue
        job = s.json()
        if job["status"] == "done":
            return job["result"]              # tải direct_mp4_url về ngay (link sống ~20 phút)
        if job["status"] in ("failed", "expired"):
            raise RuntimeError(job.get("error") or job["status"])
    raise TimeoutError(job_id)
```

## 8. Ví dụ (Node.js)

```js
const BASE = "https://dvid-api.vibe1.tinhgon.xyz/api/v1/partner";
const H = { "X-API-Key": process.env.VIDGRAB_KEY, "Content-Type": "application/json" };

async function download(url, quality = "video") {
  const r = await fetch(`${BASE}/jobs`, { method: "POST", headers: H, body: JSON.stringify({ url, quality }) });
  if (!r.ok) throw new Error(JSON.stringify(await r.json()));
  const { id } = await r.json();
  for (let i = 0; i < 150; i++) {
    await new Promise((s) => setTimeout(s, 3000));
    const s = await fetch(`${BASE}/jobs/${id}`, { headers: H });
    if (s.status === 429) { await new Promise((x) => setTimeout(x, (+s.headers.get("Retry-After") || 5) * 1000)); continue; }
    const job = await s.json();
    if (job.status === "done") return job.result;
    if (job.status === "failed" || job.status === "expired") throw new Error(job.error || job.status);
  }
  throw new Error(`timeout ${id}`);
}
```

---

## 9. Liên hệ

Cấp key, đổi key, tăng hạn mức, giới hạn IP, hoặc báo lỗi: liên hệ đầu mối kỹ thuật của
VidGrab. Gửi kèm `id` của job và thời điểm gọi để tra cứu nhanh.
