# PLAN — Video Watermark Remover cho VidGrab (Dowload-video)

Phiên bản: 09/11/2026 — bản bàn giao
Project: `share-projects/Dowload-video`
Tham khảo sản phẩm: https://dewatermark.ai/video-watermark-remover

> **Gửi agent thực hiện:** Đọc hết file này trước khi sửa code. Làm lần lượt từng phase.
> Mỗi phase chỉ được coi là xong khi đạt đủ "Điều kiện xong" và có bằng chứng
> (log test, số liệu, file kết quả). Không báo "đạt" nếu chưa đo được.
> Trao đổi với chủ dự án bằng tiếng Việt.

---

## 0. Tóm tắt

VidGrab đang tải video tốt. Mục tiêu là thêm tính năng xoá watermark/logo hiển thị để giữ chân khách hàng.
Luồng mong muốn: tải video → **tự phát hiện watermark** → **theo dõi watermark di chuyển** →
xem trước → xử lý → tải kết quả.

Engine xoá với vùng chọn thủ công **đã chạy được**. Phần còn thiếu:
1. Tự phát hiện watermark.
2. Tracking watermark di chuyển.
3. Benchmark định lượng cho detector và tracker.
4. So sánh chất lượng với bản video sạch cùng timeline.

Ngoài phạm vi: watermark vô hình (SynthID, watermark AI ẩn). Không dùng câu chữ kiểu "xoá 100%" hay "AI watermark removed".

---

## 1. Cách dewatermark.ai hoạt động (để tham chiếu)

1. Người dùng upload video. Hệ thống tự tìm vùng logo, chữ, watermark.
2. Tạo mask theo từng frame và theo dõi vùng di chuyển.
3. Lấp vùng mask bằng video inpainting theo thời gian, lấy thông tin từ các frame lân cận.
4. Cho xem trước, rồi render và tải về.

---

## 2. Hiện trạng code (đã kiểm tra)

| Thành phần | File | Trạng thái |
|---|---|---|
| API xoá logo (7 endpoint, chỉ gói Pro qua `_require_pro`) | `backend/app/api/flow_cleanup.py` | Có |
| Engine SHIFTMAP + bù chuyển động theo thời gian | `backend/app/api/flow_inpaint.py` | Có |
| Các phương pháp `natural`, `telea`, `crop`, `blur` | `flow_cleanup.py` | Có. `crop`/`blur` viết thẳng trong route, chưa có test |
| Chèn watermark (embed, không phải xoá) | `backend/app/api/watermark.py` | Có. Dùng để tạo dữ liệu benchmark |
| UI | `frontend/src/components/FlowVeoCleanup.jsx`, nút "Xoá Logo" trong `DashboardContent.jsx` | Có, chỉ chọn vùng thủ công |
| Test engine | `backend/tests/test_flow_inpaint_engine.py` (13 test, ~62s) | Có |
| Test phân quyền | `backend/tests/test_flow_cleanup_access.py` | Có |
| Hàng đợi Celery `media` | `backend/app/tasks/media_tasks.py` | Có, nhưng `/flow-cleanup/process` vẫn chạy đồng bộ trong request (tối đa 300s) |
| Tài liệu QA | `docs/qa-flow-cleanup.md`, `docs/AI-HANDOFF.md` | Có. Đọc trước khi sửa |
| Tự phát hiện watermark | — | **Chưa có** |
| Tracking | — | **Chưa có** |

Lưu ý từ `docs/AI-HANDOFF.md`:
- Luồng inpaint có HAI chỗ gọi (`DashboardContent` và `FlowVeoCleanup`). Sửa một chỗ thì phải sửa cả chỗ còn lại.
- Không dùng heuristic MÀU để kiểm tra "logo đã xoá chưa", vì `testsrc2` của ffmpeg có sẵn màu dễ gây nhầm.
- `docs/qa-flow-cleanup.md` hiện cấm câu chữ "tự động phát hiện logo" vì tính năng chưa có. Khi P2 xong, cập nhật lại tài liệu này cho khớp.

---

## 3. Việc đã làm và kết quả (09/11/2026)

### 3.1 Benchmark với mask thủ công
- FFmpeg/FFprobe: bản static 7.0.2, đặt ở `.tools/` (môi trường không có sudo).
- Video vào: 900×720, 30 FPS, 185 frame (~6,17 giây). Watermark bán trong suốt ở giữa phía dưới.
- Mask: `x=250, y=480, w=420, h=130`.
- Lệnh gọi:
  ```python
  process_video_natural(input_path, out_path, 250, 480, 420, 130, 900, 720, 30.0, 6.17)
  ```
- Kết quả: `{'total_frames': 185, 'temporal_frames': 150, 'mode': 'per_frame'}`, khoảng 15 giây.
- Đầu ra hợp lệ theo ffprobe: H.264 High + AAC, 900×720, 185 frame.
- Hạn chế: video tham chiếu là 512×410, 150 frame, không khớp với đầu vào nên **chưa tính được MAE/SSIM**.

### 3.2 Lỗi đã sửa trong `flow_inpaint.py` (kiểm tra lại, bản mới có thể chưa có)
Hàm `process_video_natural`: ghép audio thất bại với lỗi
`Error opening input file ....noaudio.mp4` khi `output_path` là đường dẫn tương đối.
Cách sửa:
```python
output_path = os.path.abspath(output_path)        # ngay trước khi tạo video_only
video_only = output_path + ".noaudio.mp4"
...
# sau khi write_proc kết thúc, trước bước mux audio:
if not os.path.isfile(video_only) or os.path.getsize(video_only) == 0:
    raise ValueError(f"Natural inpaint output missing: {video_only}")
```
Nên áp dụng tương tự cho `_telea_video` trong `flow_cleanup.py`.

---

## 4. Vấn đề đang chặn (phải xử lý trước tiên)

1. **Thiếu file:** bản upload gần nhất thiếu khoảng 110 file trong `backend/app/core/`, cùng nhiều file trong
   `frontend/src/`, `desktop/src/`, `desktop/src-tauri/src/`. Test dừng ngay ở:
   `ModuleNotFoundError: No module named 'app.core.client_ip'`.
   Kiểm tra bằng: `git status --short | grep '^ D'`. Kết quả phải rỗng.
2. **Lỗi upload EMFILE:** do gửi kèm `backend/venv`, `node_modules`, `__pycache__`. Không đưa các thư mục này vào upload.
   Thêm vào `.gitignore` nếu chưa có: `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.ruff_cache/`, `node_modules/`, `venv/`, `.venv/`, `dist/`, `.tools/`.
3. **Thiếu FFmpeg** trong môi trường dev. Dockerfile đã cài sẵn. Trên máy dev thì cài hoặc dùng bản static.
4. **`backend/venv` hỏng** (`cannot execute: required file not found`). Tạo lại venv từ `requirements.txt`.

---

## 5. Lộ trình thực hiện

### P0 — Khôi phục môi trường và benchmark nền (1–2 ngày)

Việc cần làm:
- Bổ sung đủ file bị thiếu, tạo lại venv, cài FFmpeg.
- Chạy `pytest backend/tests/test_flow_inpaint_engine.py backend/tests/test_flow_cleanup_access.py` cho pass.
- Tạo bộ benchmark:
  ```
  benchmarks/
    watermark-input/        video có watermark
    watermark-reference/    bản sạch, CÙNG tên, độ phân giải, FPS, số frame
    labels/<tên>.json       ground truth bbox theo frame
    results/RESULTS.md      kết quả từng lần chạy
    run_benchmark.py        script chạy và chấm điểm
  ```
- Định dạng nhãn:
  ```json
  {"width": 1080, "height": 1920, "fps": 30, "frames": 300,
   "regions": [{"id": "wm1", "type": "moving",
                "boxes": {"0": [x, y, w, h], "1": [x, y, w, h]}}]}
  ```
- **Tạo mẫu tổng hợp (ưu tiên):** lấy video sạch, chèn watermark bằng FFmpeg (`overlay`/`drawtext`, có thể dùng biểu thức `x=...t...` để watermark di chuyển). Như vậy luôn có sẵn bản sạch và nhãn chính xác.
- Bộ mẫu tối thiểu, mỗi nhóm ≥ 3 video, 5–30 giây:

  | Nhóm | Mục đích |
  |---|---|
  | Logo cố định ở góc | Detector cơ bản |
  | Watermark bán trong suốt | Detector khó |
  | Logo/chữ di chuyển (kiểu TikTok nhảy vị trí) | Tracking |
  | Nền chuyển động mạnh, camera rung | Độ ổn định |
  | Có cắt cảnh | Phát hiện mất dấu |
  | Có chữ trong nội dung (phụ đề, biển hiệu) nhưng không có watermark | Chống báo nhầm |

- Đo baseline của engine hiện tại với mask đúng từ nhãn: SSIM/PSNR, thời gian, RAM.

Điều kiện xong: test pass, `git status` không còn file bị xoá, `RESULTS.md` có số baseline.

### P1 — Hàng đợi và API an toàn (2–4 ngày)

- Chuyển `/flow-cleanup/process` sang Celery queue `media`. API trả `job_id`, client poll `/status/{job_id}`.
- Job có trạng thái `queued` / `running` / `done` / `failed` / `cancelled`, kèm phần trăm tiến độ, retry, hủy, timeout.
- Từ chối job vượt giới hạn (dung lượng, thời lượng, độ phân giải) trước khi đưa vào worker.
- Dọn file theo TTL, dùng cơ chế `periodic_cleanup_downloads` sẵn có.
- Tách `crop`/`blur` thành hàm riêng và viết test.
- Giữ nguyên kiểm tra `_require_pro` và cách đặt tên file ngẫu nhiên (xem `test_unguessable_names_and_no_paths.py`).

Điều kiện xong: test API mới pass, video 60 giây không làm request bị timeout.

### P2 — Tự phát hiện watermark (4–5 ngày)

Đề xuất module mới: `backend/app/services/watermark_detect.py`.

Thuật toán đề xuất (CPU, không cần GPU):
1. Lấy mẫu 30–60 frame trải đều video, thu nhỏ để tính nhanh.
2. **Vùng tĩnh:** tính median và độ lệch chuẩn theo thời gian cho từng pixel, cùng độ lệch chuẩn của bản đồ cạnh (Canny/Sobel). Watermark là vùng có cạnh ổn định trong khi nền thay đổi.
3. **Bán trong suốt:** ước lượng gradient trung bình qua nhiều frame. Các cạnh xuất hiện lặp lại ở cùng vị trí chính là watermark.
4. **Chữ:** dùng detector chữ nhẹ (OpenCV EAST hoặc tương đương) để bổ sung ứng viên, chỉ giữ chữ xuất hiện ổn định qua nhiều frame.
5. Gộp ứng viên bằng morphology và NMS, giữ tối đa 3 vùng, ưu tiên vùng gần mép/góc.
6. Mỗi vùng trả về `bbox`, `confidence` (0–1), `kind` (`static` / `moving` / `text`).
7. `confidence` dưới ngưỡng (ví dụ 0,6, tinh chỉnh bằng benchmark) thì **không tự xoá**, trả về `needs_review`.

API mới: `POST /api/v1/flow-cleanup/detect/{temp_id}` → danh sách vùng, chỉ cho gói Pro.

Điều kiện xong: precision ≥ 0,90, recall ≥ 0,85 trên bộ benchmark (IoU ≥ 0,5 tính là đúng). Nhóm "có chữ trong nội dung" không bị báo nhầm quá 10%.

### P3 — Tracking watermark di chuyển (4–6 ngày)

Đề xuất module mới: `backend/app/services/watermark_track.py`.

1. Khởi tạo từ kết quả detector hoặc từ vùng người dùng chọn ở keyframe.
2. Theo dõi theo từng frame bằng template matching đa tỉ lệ, kết hợp optical flow Lucas–Kanade hoặc tracker CSRT.
3. Với watermark nhảy vị trí (kiểu TikTok): khi điểm khớp tụt, quét lại toàn frame bằng template để tìm vị trí mới.
4. Làm mượt quỹ đạo bằng Kalman hoặc EMA, nới mask theo `dilate_px`.
5. Phát hiện cắt cảnh (chênh lệch histogram) và mất dấu (điểm khớp dưới ngưỡng). Đánh dấu các frame đó là `needs_review`.
6. Cho phép người dùng sửa tại keyframe, sau đó chỉ tính lại đoạn bị ảnh hưởng.
7. Sửa engine `flow_inpaint.py` để nhận **mask theo từng frame**, thay vì một bbox cố định. Vẫn giữ tương thích với cách gọi cũ.

Điều kiện xong: IoU trung vị ≥ 0,85 trên frame có watermark di chuyển, độ trôi ≤ 3% kích thước frame, cắt cảnh/mất dấu đều được báo.

### P4 — UI (2–4 ngày)

- Nút "Xoá watermark" ngay sau khi tải video xong, đồng thời cho upload từ máy.
- Hiển thị vùng phát hiện được kèm mức tin cậy. Cho kéo chỉnh, xoá, thêm vùng, sửa tại keyframe.
- Xem trước 3–5 giây, so sánh A/B, thanh tiến độ, nút tải về.
- Tiếng Việt/Anh, dùng được trên mobile.
- Cập nhật cả hai luồng `FlowVeoCleanup.jsx` và `DashboardContent.jsx`.
- Cập nhật `docs/qa-flow-cleanup.md` cho khớp tính năng tự phát hiện.

Điều kiện xong: chạy hết luồng tải → phát hiện → chỉnh → xem trước → xử lý → tải về trên trình duyệt.

### P5 — Hoàn thiện chất lượng (2–3 ngày)

- Tinh chỉnh `dilate_px` (hiện đang để tạm 12px) và các ngưỡng bằng benchmark.
- Kiểm tra giữ audio, video xoay, codec lạ (HEVC, VP9), file hỏng, quota, hết hạn.
- Load test 1–3 job đồng thời, giới hạn CPU/RAM worker.
- Nếu SHIFTMAP chưa đạt chất lượng: đánh giá model video inpainting (ProPainter, E2FGVI). Cần GPU, nên tính chi phí trước khi chọn.

### P6 — Beta có kiểm soát (1–2 ngày)

- Chạy E2E trên beta/staging **chuẩn đã có** của dự án, dùng tài khoản Pro. Không dựng thêm beta riêng.
- Đo tỷ lệ thành công, thời gian P95, tỷ lệ retry và phản hồi chất lượng trước khi mở public.

---

## 6. Tiêu chí nghiệm thu (bắt buộc)

### Chức năng
- [ ] Upload và xem trước đúng hướng, có audio.
- [ ] Job chạy nền, có tiến độ, hủy được, retry được. Không giữ HTTP request quá lâu.
- [ ] File kết quả mở được bằng ffprobe, Chrome và VLC. Giữ audio, hoặc báo rõ nếu phải encode lại.
- [ ] Job vượt giới hạn bị từ chối trước khi chiếm worker. File hết hạn không tải được nữa.
- [ ] User Free nhận lỗi entitlement nhất quán. User Pro chạy được toàn bộ luồng.

### Detector
- [ ] Precision ≥ 0,90, recall ≥ 0,85 (IoU ≥ 0,5).
- [ ] Báo nhầm ≤ 10% trên nhóm video có chữ trong nội dung.
- [ ] Confidence thấp luôn chuyển sang chỉnh tay.

### Tracker
- [ ] IoU trung vị ≥ 0,85, độ trôi ≤ 3% kích thước frame.
- [ ] Cắt cảnh và mất dấu được phát hiện và yêu cầu chỉnh lại.
- [ ] Sửa tại keyframe thì đoạn liên quan được tính lại.

### Chất lượng xoá (chỉ đo trên cặp video cùng thông số)
- [ ] SSIM trong vùng mask so với bản sạch: baseline P0 = 0,77–0,79. Mục tiêu cuối vẫn là ≥ 0,85, đo lại sau P3/P5.
- [ ] PSNR ngoài vùng mask: đo so với reference đã encode lại cùng thiết lập (`libx264`, `crf 18`, `yuv420p`), ngưỡng ≥ 40 dB (gần như không đổi).
- [ ] Không nhấp nháy hay lộ viền rõ. Có ≥ 2 người đánh giá A/B.

### Hiệu năng
- [ ] Theo số đo baseline trên CPU (~0,45s cho mỗi megapixel-frame, tương đương ~9× thời lượng ở 900×720@30fps): giới hạn thời lượng cho `natural`/`telea`/`telea_soft` là tối đa 60 giây và thời gian xử lý ước tính theo `width × height × fps × duration × 0,45s/MPx-frame` phải ≤ `soft_time_limit` (300 giây, tương đương ~24–33 giây ở 720p@30fps). Chế độ `crop`/`blur` cho phép tối đa 600 giây. Job vượt ngưỡng bị từ chối ngay tại API (HTTP 400 kèm `user_message` rõ ràng) trước khi vào worker. Giới hạn số lần retry tối đa là 3 lần/job (`_MAX_RETRY_COUNT = 3`).
- [ ] RAM worker không vượt giới hạn cấu hình.

### Bảo mật và pháp lý
- [ ] Không lộ đường dẫn nội bộ, token, URL lưu trữ hay dữ liệu của job khác.
- [ ] Có điều khoản bản quyền, kênh báo vi phạm, lưu file ngắn hạn.
- [ ] Không dùng câu chữ quảng cáo quá mức ("xoá 100%", "xoá toàn bộ watermark", "AI watermark removed").

### Bằng chứng
- [ ] Có test engine, detector, tracker, API, cleanup và E2E.
- [ ] Mỗi lần benchmark ghi vào `benchmarks/results/RESULTS.md`: ngày, commit, số liệu, file mẫu.

---

## 7. Rủi ro

| Rủi ro | Cách xử lý |
|---|---|
| Thiếu file core nên không chạy được | Xử lý ở P0, chặn mọi phase sau |
| Nền bị che mất thông tin nên kết quả nhòe | Bắt buộc xem trước, có phương án crop/blur |
| Detector nhận nhầm chữ trong nội dung | Ngưỡng confidence, xem trước, chỉnh tay, nhóm test chống báo nhầm |
| Watermark di chuyển gây trôi hoặc nhấp nháy | Tracker có confidence, sửa tại keyframe, làm mượt |
| Video dài làm nghẽn worker | Queue riêng, giới hạn thời lượng và số job đồng thời |
| OpenCV đổi API làm mất SHIFTMAP | Ghim phiên bản `opencv-contrib-python`, có phương án dự phòng TELEA, có test |
| SHIFTMAP chưa đủ chất lượng | Đánh giá model AI ở P5, cân nhắc chi phí GPU |
| Lạm dụng, vi phạm bản quyền | Điều khoản, kênh báo vi phạm, lưu file ngắn hạn |
| Xung đột beta dùng chung | Chỉ dùng beta chuẩn, preview có hạn tự tắt |

---

## 8. Quy tắc cho agent thực hiện

1. Làm theo thứ tự P0 → P6. Không nhảy sang phase sau khi phase trước chưa đạt điều kiện xong.
2. Đọc `docs/AI-HANDOFF.md` và `docs/qa-flow-cleanup.md` trước khi sửa.
3. Không phá các test hiện có. Chạy lại toàn bộ test liên quan sau mỗi thay đổi.
4. Không dùng hai video khác thông số để công bố MAE/SSIM.
5. Không dựng beta/staging mới. Chỉ dùng môi trường chuẩn của dự án.
6. Không commit `venv`, `node_modules`, `__pycache__`, `.tools/`, video lớn.
7. Cuối mỗi phase báo lại: đã làm gì, file nào thay đổi, số liệu đo được, còn lại gì.

---

## 9. Việc chủ dự án cần cung cấp

- [ ] Bổ sung đủ `backend/app/core/` và các file đang thiếu (không gửi kèm venv/node_modules/.git/__pycache__).
- [ ] 5–10 video **sạch** dài 5–30 giây để tạo mẫu tổng hợp, hoặc cặp video có/không watermark **cùng thông số**.
- [ ] Vài video TikTok/Douyin có watermark di chuyển (chỉ video có quyền xử lý).
- [ ] Chốt giới hạn: dung lượng tối đa, thời lượng tối đa, độ phân giải tối đa, gói nào được dùng.
- [ ] Xác nhận beta/staging chuẩn để chạy P6.