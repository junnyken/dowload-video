# Đề xuất mua VPS Cloud Server CS3 (matbao.net) — khôi phục hạ tầng VidGrab

**Người đề xuất:** Nguyễn Thiên Triều
**Ngày:** 2026-08-11
**Loại đề xuất:** Mua hạ tầng phục vụ vận hành sản phẩm (chi phí định kỳ hàng tháng)

---

## 1. Hiện trạng / Lý do đề xuất

Ứng dụng **VidGrab** (domain `dowloadvideo.io.vn`) — nền tảng tải video/nhạc từ
nhiều nền tảng (YouTube, TikTok, Facebook, Instagram, Spotify, SoundCloud...),
kèm các tính năng AI (dịch phụ đề, tự tạo phụ đề từ giọng nói, xoá watermark,
xoá logo) — hiện **đang mất hoàn toàn (down 100%)**.

**Nguyên nhân:** VPS vận hành trước đó (Oracle Cloud, cấu hình 2 OCPU/16GB) chạy
bằng credit **Free Trial 30 ngày/$300** của Oracle. Trial này đã hết hạn từ
giữa tháng 6/2026, Oracle tự động thu hồi máy chủ trả phí đó — toàn bộ dữ liệu
và cấu hình deploy bị mất, khiến trang ngừng hoạt động từ đó đến nay.

**Đã thử phương án thay thế miễn phí:** Oracle Cloud Always Free (shape
A1.Flex, ARM, tối đa 2 OCPU/12GB, không tốn phí) — nhưng khu vực Singapore
hiện **hết capacity** ("Out of capacity for shape VM.Standard.A1.Flex"),
không tạo được máy, không có ETA rõ ràng khi nào có lại. Phương án miễn phí
này không khả thi để khôi phục dịch vụ đúng thời hạn.

## 2. Đề xuất

Mua gói **Cloud Server CS3 - Linux** tại matbao.net để triển khai lại VidGrab.

| Thông số | Giá trị |
|---|---|
| Gói | CS3 - Linux |
| CPU | 2 Core |
| RAM | 2 GB |
| Ổ đĩa | 30 GB SSD, 4.000 IOPS (Max) |
| Chi phí | **539.000đ/tháng** |
| Truy cập | Auto KVM, RDP/SSH — xác nhận có root/SSH access |

## 3. Ứng dụng / cách sử dụng cụ thể

VPS này sẽ chạy **toàn bộ backend production** của VidGrab dưới dạng Docker
multi-container, gồm:
- **Backend API** (FastAPI) — xử lý request tải video/nhạc, quản lý người dùng.
- **Celery worker** (nền) — thực thi tác vụ tải, xử lý video (ghép phụ đề, xoá
  watermark), dịch thuật AI.
- **Redis** — hàng đợi tác vụ + cache.
- **Frontend** (React) — giao diện web người dùng cuối.
- Các service phụ trợ: bot Telegram thông báo, engine tải hỗ trợ đa nền tảng.

Do cấu hình 2GB RAM thấp hơn đáng kể so với máy chủ cũ (16GB), team đã **chủ
động tinh gọn kiến trúc** trước khi triển khai: gộp bớt số container chạy
song song, giới hạn RAM cứng từng service, giảm bộ nhớ đệm Redis — đảm bảo
vừa hoạt động ổn định trong ngân sách phần cứng của gói CS3, đổi lại chấp
nhận giảm khả năng chịu tải cao điểm so với cấu hình cũ (phù hợp quy mô hiện
tại, nâng cấp gói sau nếu traffic tăng).

## 4. Rủi ro nếu không được duyệt

- Dịch vụ `dowloadvideo.io.vn` tiếp tục **mất hoàn toàn**, không có ETA khôi
  phục qua phương án miễn phí (phụ thuộc Oracle có capacity trở lại hay không).
- Người dùng hiện tại không truy cập được, ảnh hưởng uy tín/traffic đã xây dựng.

## 5. Kế hoạch triển khai sau khi được duyệt

1. Nhận thông tin truy cập VPS (IP, SSH).
2. Cài Docker + docker compose, thêm swap phòng OOM.
3. Deploy stack đã tinh gọn (`docker-compose.small.yml`), khôi phục biến môi
   trường/API key.
4. Trỏ lại DNS `dowloadvideo.io.vn` sang IP mới, cấu hình lại HTTPS (Caddy).
5. Kiểm thử toàn bộ luồng, theo dõi tài nguyên (RAM/CPU) trong tuần đầu để
   xác nhận cấu hình đủ đáp ứng, cân nhắc nâng gói nếu cần.

**Thời gian dự kiến hoàn tất sau khi có VPS:** trong ngày.
