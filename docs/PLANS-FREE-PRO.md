# Gói dịch vụ VidGrab — hiện trạng và đề xuất (13/10/2026)

Nguồn: đọc mã nhánh `main` (92d7e84). "Đang chạy" = giá trị mặc định trong mã; biến môi trường production có thể khác (chưa đọc được giá trị env production). Giá tiền dưới đây là **đề xuất để owner chọn**, chưa dựa trên số đo chi phí (kế hoạch 34E yêu cầu giá theo chi phí đo được; trang Admin → Đo lường có số liệu đó).

## 1. Hiện trạng: giao diện nói một đằng, máy chủ làm một nẻo

| Mục | Giao diện đang nói (UpgradeModal / PricingPage) | Máy chủ thực thi | Nơi |
|---|---|---|---|
| Lượt tải/ngày Free | 10 | Khách 5 · có tài khoản 20 | `quotas.py:264-269` |
| Lượt tải/ngày Pro | 100 (chỗ khác ghi "không giới hạn") | 100 | `quotas.py:272-286` |
| YouTube Free | "chỉ MP3" | được tải video (1080p, 5 lượt/ngày) như Pro | `quotas.py:50`, `yt_quota.py` |
| 4K | chỉ Pro | owner chốt 08/10: mọi tài khoản đăng ký được 2K/4K | `FaqPage.jsx:10`, `TermsPage.jsx:58` |
| Hàng đợi ưu tiên, Cloud Save, Chapters | quảng cáo cho Pro | không có mã nào thực thi | `entitlements.py` |
| Đồng thời 10 job Pro | quảng cáo | không thực thi | `entitlements.py:69` |
| Trang giá `/pricing` | — | đã ẩn (về trang chủ) | `App.jsx:90-93` |
| Thanh toán | nút nâng cấp gọi Stripe (USD) | chỉ có Stripe; **không có chuyển khoản/QR VND** | `payments.py` |
| Cấp Pro tay | — | admin bật Pro **không có hạn**, không tự hết | `admin.py:906-914` |

## 2. Đề xuất 3 mức (chỉ dùng thứ máy chủ đã thực thi)

| | Khách (không đăng nhập) | Free (có tài khoản) | Pro |
|---|---|---|---|
| Lượt tải/ngày | 5 | 20 | 100 |
| Chất lượng | đến 1080p | đến 4K | đến 4K |
| YouTube | 1 video/ngày, 720p | 5/ngày, 1080p | 5/ngày, 1080p (YouTube tốn proxy trả phí, giữ chung) |
| Dán nhiều link / kênh | 5 link | 5 link | 100 link, tải cả kênh/playlist đến 100 video |
| Tải ZIP | — | — | ✅ |
| Theo dõi kênh | — | 1 kênh, quét 24 giờ/lần | 30 kênh, quét 6 giờ/lần, **Instagram**, webhook |
| Spotify nghệ sĩ | top 10 | top 10 | album/single/đến 200 bài |
| Phân tích thông minh | 2/ngày | 5/ngày, video ≤10 phút | 50/ngày, video ≤60 phút |
| Ghim tệp | — | 3 tệp / 7 ngày | 50 tệp / 30 ngày |
| Lịch tải, playlist lưu | — | 3 | 20 |
| API key | — | — | ✅ |
| App Windows, tiện ích | ✅ (chung hạn mức trên) | ✅ | ✅ |

Bỏ khỏi quảng cáo cho tới khi có mã thực thi: hàng đợi ưu tiên, Cloud Save, Chapters, "10 job đồng thời", gói Team và API Partner.

## 3. Giá và thanh toán — owner chọn

Giá đề xuất (chưa có số đo chi phí, cần kiểm ở Admin → Đo lường trước khi chốt):
- **Pro tháng: 49.000đ** · **Pro năm: 399.000đ** (≈ 33.000đ/tháng).
- Lý do mức này: nằm trong dải công cụ tải video phổ biến tại Việt Nam; Pro chủ yếu tốn proxy YouTube (≈ 1,5 USD/GB theo hằng số trong mã) và Apify cho Instagram Watch (trần 3 USD/ngày đã đặt).

Thanh toán cho đợt đầu (chưa có cổng VND):
- **(A) Chuyển khoản + admin bật Pro có hạn** — cần làm thêm: nút "Cấp Pro 30/365 ngày" có ngày hết hạn tự động, trang Nâng cấp hiện số tài khoản + nội dung chuyển khoản. Nhanh nhất, không phí cổng.
- **(B) Stripe (USD, thẻ quốc tế)** — mã đã có, cần owner tạo sản phẩm/giá trên Stripe và đặt khoá. Người Việt ít dùng.
- **(C) Cổng VND (PayOS/SePay…)** — chưa có mã, việc lớn nhất; kế hoạch xếp sau G3.

## 4. Việc kỹ thuật để các gói thành thật (sau khi owner chốt)

1. Một nguồn duy nhất: `GET /api/v1/plans` trả bảng ở mục 2 từ chính hằng số máy chủ; UpgradeModal và trang giá đọc từ đó (hết cảnh ghi tay "10/ngày").
2. Cấp Pro có hạn (30/365 ngày) trong admin + tác vụ hằng ngày hạ về Free khi hết hạn (hiện `expire_subscriptions` bỏ sót dòng `active` đã quá hạn).
3. Mở lại trang `/pricing` theo bảng mới, giá VND.
4. Sửa: chọn gói Team/API trên Stripe v2 đang âm thầm bán giá Pro (`payments.py:413-415`) — tắt 2 gói này.
5. Câu chữ: BA duyệt.
