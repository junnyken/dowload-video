# Bộ phát hành — ra mắt mềm (G1), 13/10/2026

Mọi tệp dưới đây đã kiểm trên production ngày 13/10/2026 (UTC). Không có bản app mới: app không đổi gì từ 0.14.1.

## Thành phần

| Thành phần | Phiên bản | Tải ở đâu | Kiểm |
|---|---|---|---|
| Web | `main` 45b7af1 | https://dvid.vibe1.tinhgon.xyz | deploy 13/10; soát giao diện desktop + điện thoại: 0 lỗi console, 0 tràn ngang |
| API | `main` 92d7e84 | https://dvid-api.vibe1.tinhgon.xyz | `/health` 200 |
| App Windows | 0.14.1 | https://github.com/junnyken/vidgrab-releases/releases/tag/v0.14.1 | có `.sig` (cập nhật trong app) và `.sha256` |
| Tiện ích Chrome | 5.6.0 | https://dvid.vibe1.tinhgon.xyz/extension (tải .zip, cài tay; không lên Chrome Web Store theo quyết định owner) | máy chủ phát đúng 5.6.0 |
| Trang trạng thái | — | https://stats.uptimerobot.com/IrnzR8t2Rm | 200, nằm ngoài máy chủ |

## Checksum SHA-256

```
2c1082bc0efef97129171aea2126893789e6d5f92e48c4edcd8b8a941d58903f  VidGrab_0.14.1_x64-setup.exe
052941b882b84282e887e6a3dcfa9e76b9e7d02a8c4988c82302eff46841bb42  VidGrab-extension-5.6.0.zip
```

## Giới hạn đã biết (nói trước với người dùng)
- Rumble: máy chủ bị chặn, có thể không tải được.
- Douyin, Xiaohongshu: chưa quảng bá ở đợt này.
- Instagram, X, Facebook: chỉ nội dung công khai.
- Windows SmartScreen có thể cảnh báo bộ cài (chưa ký mã thương mại): bấm "More info → Run anyway"; đối chiếu checksum ở trên.
- Tiện ích cài tay: Chrome → Tiện ích → bật Chế độ nhà phát triển → "Tải tiện ích đã giải nén". Có bản mới, tiện ích tự báo.

## Trước khi đăng bài
1. Gói Free/Pro: chờ owner chốt `docs/PLANS-FREE-PRO.md`. Đến khi đó **không nhắc giá Pro** trong bài.
2. Câu chữ bài đăng: BA duyệt.
3. Không deploy trong 24 giờ sau khi đăng, trừ sửa lỗi nghiêm trọng (luật phát hành §5).

## Lệnh lùi bản
- Web/API: Vibe Host → dự án → Rollback về bản trước (`rollback_project`).
- App: tắt cập nhật trong app tại Admin → Công tắc khẩn cấp → "Cập nhật app".
