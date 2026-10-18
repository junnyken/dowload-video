# Runbook: Cấp Partner API cho khách gọi từ backend

> Khi khách muốn gọi VidGrab từ máy chủ của họ (không qua web/extension/app).
> Tài liệu gửi khách: [docs/PARTNER-API.md](../PARTNER-API.md).

## Chọn loại kết nối

| Khách cần | Dùng |
|---|---|
| Ít, tự phục vụ, chờ kết quả trong một request | API key thường `vidgrab_` — khách tự mua Pro/Team và tạo key ở trang API Keys; tài liệu ở `/api-docs` |
| Nhiều job, webhook, hạn mức theo hợp đồng, khoá IP | **Partner API** `vgp_` — admin cấp theo các bước dưới |

## Các bước (admin `/vid-admin`)

1. **Tenants → Tạo khách partner**: tên công ty, gói (starter 1.000 / growth 50.000 /
   enterprise không giới hạn job/tháng), hoặc nhập hạn mức tháng riêng theo hợp đồng.
2. **Cấp API key** cho khách đó: nhãn (vd `production`), request/phút (mặc định 60),
   job/ngày (mặc định 10.000), IP được phép (khuyến nghị: IP máy chủ của khách), ngày hết hạn.
3. Key `vgp_…` chỉ hiện **một lần**: gửi cho khách qua kênh an toàn (không gửi qua chat công khai),
   kèm `docs/PARTNER-API.md`.
4. Khách lộ key → **API Keys → Đổi key** (key cũ chết ngay) hoặc tắt key.
5. Khách ngưng hợp đồng → **Tenants → status = suspended** (mọi key trả `403 tenant_inactive`).

API tương ứng (cùng phiên admin): `POST /api/v1/admin/enterprise/tenants`,
`POST …/tenants/{id}/limits`, `POST …/tenants/{id}/api-keys`,
`POST …/api-keys/{id}/rotate`, `PATCH …/api-keys/{id}`.

## Giới hạn đang áp

- Request/phút theo key (Redis `partner:rpm:{key}:{phút}`), 429 + `Retry-After`.
- Job/ngày theo key (`partner:day:{key}:{YYYY-MM-DD}`), job/tháng theo khách
  (`partner:month:{tenant}:{YYYY-MM}`) — ngày/tháng UTC. Chỉ `POST /jobs` bị trừ.
- IP gửi ≥ 20 key sai trong 1 phút → 429 `too_many_invalid_keys` (không tra DB).
- Mọi bộ đếm **mở khi Redis lỗi** (không chặn khách vì lỗi hạ tầng).

## Cần biết về sức chứa

Job partner dùng chung hàng đợi tải với người dùng web (`MAX_CONCURRENT_DOWNLOADS`, mặc định 10
đồng thời, 1 worker Celery). Khách lớn đẩy nhiều job cùng lúc sẽ làm chậm cả web —
trước khi nhận khách > vài nghìn job/ngày, kiểm lại sức chứa máy chủ.
