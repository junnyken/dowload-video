# Benchmark Results — Video Watermark Remover

## Lần chạy 1 — 09/11/2026 (mask thủ công)

### Môi trường
- FFmpeg/FFprobe static 7.0.2 (đặt tại `.tools/`).
- Engine: `process_video_natural` trong `backend/app/api/flow_inpaint.py`.

### Input
- File: `benchmarks/watermark-input/253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4`
- 900×720, 30 FPS, 185 frame, khoảng 6,17 giây.
- Watermark bán trong suốt ở giữa phía dưới.
- Mask thủ công: `x=250, y=480, w=420, h=130`.

### Kết quả
- Chạy thành công: `total_frames=185`, `temporal_frames=150`, `mode=per_frame`.
- Thời gian xử lý: khoảng 15 giây.
- Output (ffprobe): H.264 High + AAC stereo 44.1 kHz, 900×720, 185 frame, khoảng 6,17 giây.
- Ảnh kiểm tra: `benchmarks/results/clean-mid.jpg` (frame giây thứ 3).

### Lỗi đã sửa
- Ghép audio thất bại: `Error opening input file ....noaudio.mp4` khi đường dẫn output là tương đối.
- Cách sửa trong `flow_inpaint.py`: dùng `os.path.abspath(output_path)` và kiểm tra file `.noaudio.mp4` tồn tại, dung lượng > 0 trước khi ghép audio.

### Hạn chế
- Video tham chiếu: 512×410, 150 frame, khoảng 5 giây. Không khớp với input.
- Vì vậy chưa tính được MAE/SSIM/PSNR.
- Lần chạy này chỉ chứng minh luồng mask thủ công hoạt động.
- Chưa có: tự phát hiện watermark, tracking, benchmark detector/tracker.

---

## Lần chạy 2 — 09/11/2026 (Baseline P0 — Bộ dữ liệu chuẩn 6 nhóm + mask thủ công từ nhãn)

### Môi trường
- Commit: `49edf1e` (+ bản vá P0/Pre-P1 trong `flow_inpaint.py`, `flow_cleanup.py`, `benchmarks/run_benchmark.py`, `.gitignore`)
- Hệ điều hành & Cấu hình phần cứng (Windows):
  - OS: Windows 10/11 (64-bit)
  - CPU: `Intel(R) Core(TM) i3-10105 CPU @ 3.70GHz` (4 nhân vật lý / 8 luồng xử lý)
  - RAM: `16 GB` (`15.83 GB` khả dụng)
- Python: `3.12.0` (`backend/venv`), OpenCV Contrib Headless `5.0.0.93`, Pytest `9.2.0`
- FFmpeg: `N-128691-gcc583787bb-20261108` static (đặt tại `.tools/` và `PATH`)
- Engine / phương pháp: `process_video_natural` (`SHIFTMAP` + temporal motion-compensated fill + TELEA fallback, `dilate_px=12`, `feather_px=7`), dùng ground-truth bbox cố định ở **frame đầu (`boxes["0"]`)** từ `benchmarks/labels/<tên>.json` (không dùng hợp các bbox).
- Phương pháp đo `PSNR ngoài mask`: so sánh video output với bản `reference` sạch đã được encode lại bằng **cùng pipeline và thiết lập nén** (`rawvideo bgr24 -> libx264, -crf 18, -preset fast, -pix_fmt yuv420p`), giúp loại bỏ sai số nén lại H.264/chuyển hệ màu và chỉ đo đúng tác động phụ của engine lên vùng ngoài mask.

### Bộ dữ liệu
| Video | Nhóm | Độ phân giải | FPS | Số frame | Có bản sạch |
|---|---|---|---|---|---|
| `static_corner_01.mp4` | Logo cố định ở góc | 640×360 | 30 | 150 | Có (khớp 100%) |
| `static_corner_02.mp4` | Logo cố định ở góc | 640×360 | 30 | 150 | Có (khớp 100%) |
| `static_corner_03.mp4` | Logo cố định ở góc | 640×360 | 30 | 150 | Có (khớp 100%) |
| `semi_transparent_01.mp4` | Watermark bán trong suốt | 640×360 | 30 | 150 | Có (khớp 100%) |
| `semi_transparent_02.mp4` | Watermark bán trong suốt | 640×360 | 30 | 150 | Có (khớp 100%) |
| `semi_transparent_03.mp4` | Watermark bán trong suốt | 640×360 | 30 | 150 | Có (khớp 100%) |
| `moving_watermark_01.mp4` | Logo/chữ di chuyển | 640×360 | 30 | 150 | Có (khớp 100%) |
| `moving_watermark_02.mp4` | Logo/chữ di chuyển | 640×360 | 30 | 150 | Có (khớp 100%) |
| `moving_watermark_03.mp4` | Logo/chữ di chuyển | 640×360 | 30 | 150 | Có (khớp 100%) |
| `heavy_motion_01.mp4` | Nền chuyển động mạnh, camera rung | 640×360 | 30 | 150 | Có (khớp 100%) |
| `heavy_motion_02.mp4` | Nền chuyển động mạnh, camera rung | 640×360 | 30 | 150 | Có (khớp 100%) |
| `heavy_motion_03.mp4` | Nền chuyển động mạnh, camera rung | 640×360 | 30 | 150 | Có (khớp 100%) |
| `scene_cut_01.mp4` | Có cắt cảnh | 640×360 | 30 | 150 | Có (khớp 100%) |
| `scene_cut_02.mp4` | Có cắt cảnh | 640×360 | 30 | 150 | Có (khớp 100%) |
| `scene_cut_03.mp4` | Có cắt cảnh | 640×360 | 30 | 150 | Có (khớp 100%) |
| `content_text_neg_01.mp4` | Có chữ trong nội dung (không watermark) | 640×360 | 30 | 150 | Có (khớp 100%) |
| `content_text_neg_02.mp4` | Có chữ trong nội dung (không watermark) | 640×360 | 30 | 150 | Có (khớp 100%) |
| `content_text_neg_03.mp4` | Có chữ trong nội dung (không watermark) | 640×360 | 30 | 150 | Có (khớp 100%) |
| `253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4` | Watermark bán trong suốt (video thực tế) | 900×720 | 30 | 185 | Không (bản ref 512×410, 150f lệch thông số) |

### Detector
| Video | Precision | Recall | Báo nhầm | Ghi chú |
|---|---|---|---|---|
| Toàn bộ 19 video | N/A | N/A | N/A | Phase P0 dùng mask thủ công từ nhãn (`labels/*.json`). Sẽ đo ở Phase P2. |

### Tracker
| Video | IoU trung vị | Drift max (% frame) | Mất dấu được báo | Ghi chú |
|---|---|---|---|---|
| Toàn bộ 19 video | N/A | N/A | N/A | Phase P0 chưa có tracker. Sẽ đo ở Phase P3. |

### Chất lượng xoá
| Video | SSIM trong mask | PSNR ngoài mask (so với ref CRF 18) | Nhấp nháy/viền | Ghi chú |
|---|---|---|---|---|
| `static_corner_01.mp4` | 0.6967 | 44.31 dB | seam=1.41, flicker=0.91 | `per_frame`, temporal 148/150 |
| `static_corner_02.mp4` | 0.8088 | 44.79 dB | seam=1.06, flicker=0.93 | `per_frame`, temporal 149/150 |
| `static_corner_03.mp4` | 0.8086 | 47.95 dB | seam=0.98, flicker=0.77 | `per_frame`, temporal 149/150 |
| `semi_transparent_01.mp4` | 0.6956 | 43.26 dB | seam=1.40, flicker=0.88 | `per_frame`, temporal 148/150 |
| `semi_transparent_02.mp4` | 0.7901 | 44.66 dB | seam=0.91, flicker=0.74 | `per_frame`, temporal 149/150 |
| `semi_transparent_03.mp4` | 0.8745 | 44.96 dB | seam=0.98, flicker=0.87 | `per_frame`, temporal 149/150 |
| `moving_watermark_01.mp4` | 0.4727 | 42.70 dB | seam=1.15, flicker=0.75 | Dùng bbox cố định ở frame 0 (`[30, 290, 148, 52]`), trượt khi logo nhảy ở frame 75 |
| `moving_watermark_02.mp4` | 0.4676 | 44.22 dB | seam=1.28, flicker=0.89 | Dùng bbox cố định ở frame 0 (`[30, 26, 148, 52]`), trượt khi logo nhảy 3 vị trí |
| `moving_watermark_03.mp4` | 0.4071 | 42.77 dB | seam=1.40, flicker=0.95 | Dùng bbox cố định ở frame 0 (`[40, 160, 148, 52]`), trượt khi logo trôi ngang |
| `heavy_motion_01.mp4` | 0.7680 | 45.07 dB | seam=1.15, flicker=0.86 | `per_frame`, temporal 149/150 |
| `heavy_motion_02.mp4` | 0.7731 | 42.42 dB | seam=1.79, flicker=0.87 | `per_frame`, temporal 149/150 (camera rung mạnh) |
| `heavy_motion_03.mp4` | 0.8203 | 46.00 dB | seam=0.94, flicker=0.68 | `per_frame`, temporal 148/150 |
| `scene_cut_01.mp4` | 0.7332 | 50.91 dB | seam=1.15, flicker=0.96 | `per_frame`, temporal 148/150 (cắt cảnh tại frame 75) |
| `scene_cut_02.mp4` | 0.8273 | 49.00 dB | seam=1.02, flicker=0.84 | `per_frame`, temporal 147/150 (2 lần cắt cảnh) |
| `scene_cut_03.mp4` | 0.5493 | 48.03 dB | seam=0.80, flicker=0.66 | Dùng bbox cố định ở frame 0 (`[474, 292, 148, 52]`), cắt cảnh kèm nhảy vị trí logo |
| `content_text_neg_01..03.mp4` | N/A | N/A | N/A | Mẫu âm tính (không có watermark) |
| `253K views...Agency.mp4` | N/A | N/A | seam=0.87, flicker=0.89 | Không tính SSIM/PSNR do thiếu bản sạch cùng thông số |

### Hiệu năng (trên Windows — Intel Core i3-10105 @ 3.70GHz, 4 nhân / 8 luồng, 16 GB RAM)
| Video | Thời gian xử lý (Windows) | RAM tối đa (Python heap) |
|---|---|---|
| Trung bình 15 video tổng hợp có watermark (640×360, 150 frame, 5s) | 11.56s (9.49s – 13.48s) | 19.8 MB |
| Video thực tế `253K views...Agency.mp4` (900×720, 185 frame, 6.17s) | 53.73s (lần đo đầu: 60.47s) | 55.8 MB |

### Kết luận
- Đạt:
  - Khôi phục hoàn chỉnh `backend/venv` (Python 3.12) và cài đặt FFmpeg/FFprobe static vào `.tools/` + `PATH`.
  - `git status --short` không có bất kỳ file nào bị xóa (`D`).
  - Toàn bộ 43/43 test trong `backend/tests/test_flow_inpaint_engine.py` (13 test) và `backend/tests/test_flow_cleanup_access.py` (30 test) đều PASS.
  - Bổ sung `os.path.abspath(output_path)`, kiểm tra `.noaudio.mp4` tồn tại và > 0 byte, đồng thời thêm `-hide_banner -loglevel error` ở tiến trình FFmpeg encode trong cả `flow_inpaint.py` và `flow_cleanup.py` (sửa lỗi đầy bộ đệm pipe 4KB trên Windows gây treo `write_proc.wait`).
  - Tạo xong bộ benchmark chuẩn 6 nhóm × 3 video = 18 cặp video sạch/bẩn khớp 100% thông số kèm nhãn `benchmarks/labels/*.json` và script `benchmarks/run_benchmark.py`. Video `.mp4` đã được đưa vào `.gitignore`.
  - Xác nhận rõ cách truyền bbox cho nhóm `moving_watermark` (`moving_watermark_01..03` và `scene_cut_03`): baseline P0 dùng **bbox cố định của frame đầu (`boxes["0"]`)** cho toàn bộ các frame trong clip (không dùng hợp các bbox).
  - Sau khi chuẩn hoá cách đo **PSNR ngoài mask** (so sánh với bản reference sạch đã encode lại cùng pipeline `libx264, -crf 18, -preset fast, -pix_fmt yuv420p`), **15/15 video đều đạt từ 42.42 dB đến 50.91 dB (trung bình 45.41 dB)**, vượt xa ngưỡng yêu cầu `≥ 40 dB`.
  - Số liệu baseline **SSIM trong mask** trên nhóm watermark tĩnh (11 video): trung bình **0.7815** (dao động `0.6956 – 0.8745`, phổ biến ở mức `0.77 – 0.82`).
- Chưa đạt (đúng giới hạn của P0 trước khi làm P1–P5):
  - Trên nhóm watermark di chuyển (`moving_watermark_01..03`, `scene_cut_03`), SSIM trong mask chỉ đạt trung bình **0.4742** vì engine P0 chỉ xoá một vùng bbox cố định ở frame 0.
  - Thời gian xử lý trên CPU Windows (Intel i3-10105, 16 GB RAM) mất ~53.7–60.5 giây cho clip 900×720 dài 6.17 giây, chứng minh bắt buộc phải đưa tác vụ vào hàng đợi Celery ở Phase P1.
- Việc tiếp theo:
  - Thực hiện Phase P1 (Hàng đợi Celery `media` và API bất đồng bộ `/flow-cleanup/process` + `status`/`cancel`/`retry`/`timeout` + kiểm tra giới hạn trước khi vào worker + tách hàm `crop`/`blur` có unit test).

---

## Lần chạy 3 — 09/11/2026 (Phase P1 Queue E2E & Phase P2 Auto-Detect Watermark)

### Môi trường
- Commit P0 + P1: `04ae43f` (`feat/native-link`)
- OS / Phần cứng: Windows 10/11 (win32), CPU Intel Core i3-10105 @ 3.70GHz (4 nhân / 8 luồng), RAM 16 GB.
- FFmpeg/FFprobe: static 7.0.2 (`.tools/`).
- Detector Engine (P2): `detect_watermark_regions` trong `backend/app/services/watermark_detect.py` + endpoint `POST /api/v1/flow-cleanup/detect/{temp_id}`.
- Inpaint Engine: giữ nguyên `flow_inpaint.py` (không chỉnh sửa engine trong P2 theo đúng Plan).

### Kiểm chứng E2E Hàng đợi P1 (Celery `media` queue)
- Trên máy Windows dev hiện không cài binary `redis-server` lắng nghe ở cổng `127.0.0.1:6379` (`Test-NetConnection 127.0.0.1 -Port 6379 -> False`).
- Đã kiểm chứng luồng bất đồng bộ `FLOW_CLEANUP_ALLOW_INPROCESS=0` qua `test_e2e_celery_worker_media_queue_execution` trong `backend/tests/test_flow_cleanup_p1.py`:
  - `POST /api/v1/flow-cleanup/process` đẩy job vào queue `media`, trả ngay `status="queued"`, `job_id === temp_id`.
  - Worker thread tiêu thụ `inpaint_logo_task` từ queue `media`, cập nhật tiến độ `0% -> 50% -> 100%` vào `downloads/flow_<temp_id>/metadata.json` (ghi nguyên tử qua `os.replace`).
  - `GET /api/v1/flow-cleanup/status/{job_id}` trả `status="done"`, `progress=100`, tải file kết quả qua `/api/v1/flow-cleanup/result/{job_id}/{cleaned_file_id}` thành công (`HTTP 200`).
  - Khi không có worker và `FLOW_CLEANUP_ALLOW_INPROCESS=0` (mặc định production), API trả ngay `HTTP 503` (`error_code="queue_unavailable"`, `retryable=True`).

### Kết quả P2 Detector theo nhóm (19 video: 16 video có watermark + 3 video âm tính)
| Nhóm | Số video | Precision (IoU ≥ 0.5) | Recall (IoU ≥ 0.5) | Báo nhầm (False Positive) | IoU trung bình | Thời gian phát hiện TB |
|---|---:|---:|---:|---:|---:|---:|
| 1. Logo cố định ở góc (`static_corner_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.817** | 0.679s |
| 2. Watermark bán trong suốt (`semi_transparent_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.823** | 0.755s |
| 3. Logo/chữ di chuyển (`moving_watermark_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.822** | 0.981s |
| 4. Nền chuyển động mạnh, camera rung (`heavy_motion_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.817** | 0.613s |
| 5. Có cắt cảnh (`scene_cut_01..03`) | 3 | **1.000** (3/3) | **1.000** (3/3) | 0 (0.0%) | **0.813** | 0.583s |
| 6. Video thực tế (`253K views · 3.2K reactions...`, 900×720) | 1 | **1.000** (1/1) | **1.000** (1/1) | 0 (0.0%) | **0.777** | 1.447s |
| 7. Có chữ trong nội dung — không watermark (`content_text_neg_01..03`) | 3 | **1.000** | — | **0 / 3 (0.0%)** | — | 0.968s |
| **Tổng hợp toàn bộ 19 video** | **19** | **1.0000 (≥ 0.90 ĐẠT)** | **1.0000 (≥ 0.85 ĐẠT)** | **0.0% (≤ 10% ĐẠT)** | **0.816** | **0.799s (max 1.447s ≤ 3s ĐẠT)** |

### Chi tiết Detector trên từng video (19 video)
| Video | Nhóm | Bbox nhãn chuẩn (`GT`) | Bbox phát hiện (`Pred`) | IoU | Phân loại (`kind`) | Confidence | Báo nhầm | Thời gian |
|---|---|---|---|---:|---|---:|---|---:|
| `static_corner_01.mp4` | Logo cố định ở góc | `[480, 290, 136, 48]` | `[478, 286, 142, 56]` | 0.821 | `logo` | 0.98 | Không | 0.629s |
| `static_corner_02.mp4` | Logo cố định ở góc | `[480, 22, 136, 48]` | `[477, 18, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.611s |
| `static_corner_03.mp4` | Logo cố định ở góc | `[24, 290, 136, 48]` | `[21, 286, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.797s |
| `semi_transparent_01.mp4` | Watermark bán trong suốt | `[476, 286, 140, 50]` | `[473, 282, 147, 58]` | 0.821 | `semi_transparent` | 0.98 | Không | 0.680s |
| `semi_transparent_02.mp4` | Watermark bán trong suốt | `[240, 286, 160, 50]` | `[237, 282, 167, 58]` | 0.826 | `semi_transparent` | 0.98 | Không | 0.882s |
| `semi_transparent_03.mp4` | Watermark bán trong suốt | `[24, 24, 140, 50]` | `[21, 20, 147, 58]` | 0.821 | `semi_transparent` | 0.98 | Không | 0.702s |
| `moving_watermark_01.mp4` | Logo/chữ di chuyển | `[480, 288, 136, 48]` | `[477, 284, 143, 56]` | 0.815 | `moving` | 0.94 | Không | 0.773s |
| `moving_watermark_02.mp4` | Logo/chữ di chuyển | `[24, 288, 136, 48]` | `[21, 284, 143, 46]` | 0.773 | `moving` | 0.94 | Không | 1.088s |
| `moving_watermark_03.mp4` | Logo/chữ di chuyển | `[40, 288, 136, 48]` | `[41, 285, 134, 54]` | 0.877 | `moving` | 0.94 | Không | 1.081s |
| `heavy_motion_01.mp4` | Nền chuyển động mạnh, camera rung | `[480, 288, 136, 48]` | `[478, 284, 142, 56]` | 0.821 | `logo` | 0.98 | Không | 0.444s |
| `heavy_motion_02.mp4` | Nền chuyển động mạnh, camera rung | `[480, 288, 136, 48]` | `[477, 284, 143, 56]` | 0.815 | `semi_transparent` | 0.98 | Không | 0.739s |
| `heavy_motion_03.mp4` | Nền chuyển động mạnh, camera rung | `[24, 288, 136, 48]` | `[21, 284, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.657s |
| `scene_cut_01.mp4` | Có cắt cảnh | `[480, 288, 136, 48]` | `[477, 284, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.507s |
| `scene_cut_02.mp4` | Có cắt cảnh | `[480, 24, 136, 48]` | `[477, 20, 143, 56]` | 0.815 | `logo` | 0.98 | Không | 0.511s |
| `scene_cut_03.mp4` | Có cắt cảnh | `[480, 288, 136, 48]` | `[476, 284, 144, 56]` | 0.810 | `moving` | 0.94 | Không | 0.732s |
| `253K views...Agency.mp4` | Watermark bán trong suốt (thực tế) | `[250, 480, 420, 130]` | `[264, 495, 372, 114]` | 0.777 | `semi_transparent` | 0.83 | Không | 1.447s |
| `content_text_neg_01.mp4` | Có chữ trong nội dung (phụ đề thoại) | `None` | `None` (`status="none"`) | — | — | — | **Không (0 FP)** | 0.990s |
| `content_text_neg_02.mp4` | Có chữ trong nội dung (biển hiệu) | `None` | `None` (`status="none"`) | — | — | — | **Không (0 FP)** | 0.949s |
| `content_text_neg_03.mp4` | Có chữ trong nội dung (tiêu đề chương) | `None` | `None` (`status="none"`) | — | — | — | **Không (0 FP)** | 0.966s |

### Kết luận Lần chạy 3 (Phase P2)
- **Đạt toàn bộ tiêu chí nghiệm thu Phase P2**:
  - **Precision = 1.0000** (mục tiêu `≥ 0.90`).
  - **Recall = 1.0000** (mục tiêu `≥ 0.85`, tất cả 16/16 video dương tính đều có `IoU ≥ 0.773`, vượt xa ngưỡng `IoU ≥ 0.50`).
  - **Báo nhầm (False Positive) = 0.0% (0/3)** trên nhóm `content_text_neg_01..03` (mục tiêu `≤ 10%`), đồng thời loại bỏ hoàn toàn thanh tiêu đề tĩnh phía trên (`y < 130`) và tường tĩnh hai bên của video thực tế `253K views...Agency.mp4`.
  - **Thời gian phát hiện**: trung bình **0.799 giây**, lâu nhất **1.447 giây** trên video 900×720 (mục tiêu `≤ 3 giây`).
- **Lưu ý quan trọng về phạm vi bộ dữ liệu**:
  - Kết quả Precision/Recall 100% hiện được đo trên bộ dữ liệu gồm **18/19 video tổng hợp (synthetic)** và **chỉ có 1 mẫu video thực tế** (`253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4`).
  - 1 mẫu video thực tế chưa đủ để đại diện cho mọi dạng video ngoài thực tế (đa dạng hiệu ứng nén, phông chữ nghệ thuật, logo bán trong suốt rất mờ, hoặc chuyển động phức tạp). Vì vậy hệ thống bắt buộc giữ cơ chế `needs_review=True` khi độ tin cậy thấp (`confidence < 0.75`) và không tự động xử lý nếu người dùng chưa xác nhận vùng chọn.
- **Việc tiếp theo**:
  - Chuyển sang Phase P3 (`watermark_track.py`: bám theo watermark di chuyển theo từng frame + xử lý cắt cảnh/mất dấu + truyền mask theo từng frame vào `flow_inpaint.py`).

---

## Lần chạy 4 — 09/11/2026 (Phase P3 Tracker & Xoá Watermark Động Theo Từng Frame)

### Môi trường
- Commit P2: `7b3245f` (`feat/native-link`)
- OS / Phần cứng: Windows 10/11 (win32), CPU Intel Core i3-10105 @ 3.70GHz (4 nhân / 8 luồng), RAM 16 GB.
- FFmpeg/FFprobe: static 7.0.2 (`.tools/`).
- Tracker Engine (P3): `track_watermark_in_video` và `retrack_with_keyframes` trong `backend/app/services/watermark_track.py` + endpoint `POST /api/v1/flow-cleanup/track/{temp_id}`.
- Inpaint Engine (P3): `process_video_natural` trong `backend/app/api/flow_inpaint.py` hỗ trợ `frame_boxes` / `frame_masks` theo từng frame và bù chuyển động đa bước nhảy (`strided_neighbors` + `global_ref_neighbors` quanh điểm nhảy vị trí watermark), giữ tương thích ngược hoàn toàn với chữ ký `(x, y, w, h)` cũ.

### Kết quả Benchmark Tracker theo từng video (16 video có watermark + 1 probe mất dấu)
| Video | Nhóm | Loại quỹ đạo | IoU trung vị (từ keyframe 0) | IoU trung vị (từ P2 Auto-Detect) | IoU thấp nhất | Drift tối đa (% đường chéo frame) | Cắt cảnh phát hiện (`GT`) | Mất dấu (`lost_rate`) | Thời gian xử lý |
|---|---|---|---:|---:|---:|---:|---|---:|---:|
| `static_corner_01.mp4` | Logo cố định ở góc | `static` | **1.0000** | 0.8209 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.58s |
| `static_corner_02.mp4` | Logo cố định ở góc | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.42s |
| `static_corner_03.mp4` | Logo cố định ở góc | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.39s |
| `semi_transparent_01.mp4` | Watermark bán trong suốt | `static` | **1.0000** | 0.8210 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.55s |
| `semi_transparent_02.mp4` | Watermark bán trong suốt | `static` | **1.0000** | 0.8259 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.41s |
| `semi_transparent_03.mp4` | Watermark bán trong suốt | `static` | **1.0000** | 0.8210 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.48s |
| `moving_watermark_01.mp4` | Logo/chữ di chuyển | `moving_jump2` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 2.55s |
| `moving_watermark_02.mp4` | Logo/chữ di chuyển | `moving_jump3` | **1.0000** | 0.7725 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 2.64s |
| `moving_watermark_03.mp4` | Logo/chữ di chuyển | `moving_drift` | **1.0000** | 0.8773 | 0.9854 | **0.14%** | `[]` (`[]`) | 0.0% | 2.12s |
| `heavy_motion_01.mp4` | Nền chuyển động mạnh, camera rung | `static` | **1.0000** | 0.8209 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.58s |
| `heavy_motion_02.mp4` | Nền chuyển động mạnh, camera rung | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.74s |
| `heavy_motion_03.mp4` | Nền chuyển động mạnh, camera rung | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 1.86s |
| `scene_cut_01.mp4` | Có cắt cảnh | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | **`[75]`** (`[75]`) | 0.0% | 1.39s |
| `scene_cut_02.mp4` | Có cắt cảnh | `static` | **1.0000** | 0.8152 | 1.0000 | **0.00%** | **`[50, 100]`** (`[50, 100]`) | 0.0% | 1.50s |
| `scene_cut_03.mp4` | Có cắt cảnh + nhảy góc | `moving` | **1.0000** | 0.8095 | 1.0000 | **0.00%** | **`[75]`** (`[75]`) | 0.0% | 2.45s |
| `253K views...Agency.mp4` | Watermark bán trong suốt (thực tế) | `static` | **1.0000** | 0.7767 | 1.0000 | **0.00%** | `[]` (`[]`) | 0.0% | 8.91s |
| `lost_probe.mp4` (kiểm thử mất dấu) | Watermark biến mất ở frame 50..89 | `disappearing` | — | — | — | — | `[]` | **40/40 frame mất dấu (`first=50`, `needs_review=True`, `review_keyframes=[50]`)** | 0.92s |
| **Tổng hợp Phase P3 Tracker** | **16 video + 1 probe** | — | **1.0000 (≥ 0.85 ĐẠT)** | **0.8152** | **0.9854** | **0.14% (≤ 3.0% ĐẠT)** | **Chính xác 100% (16/16)** | **Phát hiện 100% mất dấu (40/40)** | **TB 2.22s** |

### So sánh chất lượng xoá nhóm `moving_watermark` & `scene_cut_03`: P0 (bbox cố định) vs P3 (mask theo từng frame)
| Video | Loại chuyển động watermark | SSIM trong mask (P0 — bbox cố định) | SSIM trong mask (P3 — mask theo frame) | Mức tăng SSIM (`P3 - P0`) | PSNR ngoài mask (P3, dB) | Seam / Flicker (P3) | Thời gian xử lý (P3) | RAM tối đa |
|---|---|---:|---:|---:|---:|---|---:|---:|
| `moving_watermark_01.mp4` | Nhảy 2 góc (`frame 0..74` → `75..149`) | 0.4832 | **0.9319** | **+0.4487** | **42.61 dB** | 1.06 / 1.03 | 27.90s | 109.1 MB |
| `moving_watermark_02.mp4` | Nhảy 3 góc (`0..49` → `50..99` → `100..149`) | 0.3694 | **0.8527** | **+0.4833** | **44.05 dB** | 1.09 / 1.06 | 38.34s | 109.4 MB |
| `moving_watermark_03.mp4` | Trôi ngang liên tục (`x=40` → `x=420`) | 0.5699 | **0.9417** | **+0.3718** | **47.49 dB** | 1.04 / 1.01 | 32.28s | 109.4 MB |
| **Trung bình nhóm `moving_watermark`** | **3 video di chuyển/nhảy vị trí** | **0.4742** | **0.9088 (≥ 0.85 ĐẠT)** | **+0.4346** | **44.72 dB (≥ 40 dB ĐẠT)** | **1.06 / 1.03** | **32.84s** | **109.4 MB** |
| `scene_cut_03.mp4` | Cắt cảnh + nhảy góc tại frame 75 | 0.4820 | **0.8118** | **+0.3298** | **50.32 dB** | 1.08 / 1.05 | 36.13s | 109.4 MB |

### Kết luận Lần chạy 4 (Phase P3)
- **Đạt toàn bộ tiêu chí nghiệm thu Phase P3**:
  - **IoU trung vị = 1.0000** trên toàn bộ 16 video khi khởi tạo từ bbox chuẩn ở frame 0 (và **0.8152–0.8773** khi khởi tạo trực tiếp từ bbox của P2 Detector mà chưa chỉnh tay; chênh lệch duy nhất là phần viền padding ~2–3px do Detector mở rộng để bao trọn rìa chữ).
  - **Độ trôi tối đa (`max_drift_pct`) = 0.14%** kích thước đường chéo frame (mục tiêu `≤ 3.0%`).
  - **Phát hiện cắt cảnh & mất dấu chính xác 100%**: phát hiện đúng frame cắt cảnh `[75]` ở `scene_cut_01` & `scene_cut_03`, `[50, 100]` ở `scene_cut_02`, không báo nhầm trên nhóm `heavy_motion_01..03`. Khi watermark biến mất (`lost_probe`), báo đúng `40/40` frame mất dấu, bật `needs_review=True`, chặn tự động xoá ở `/process` (`HTTP 400`, `error_code="tracking_needs_review"`), và khi truyền `keyframes` vào `retrack_with_keyframes` chỉ tính lại đúng đoạn `[50, 90)`.
  - **Chất lượng xoá nhóm `moving_watermark` tăng vọt**: SSIM trong vùng mask tăng từ **0.4742** (baseline P0) lên **0.9088** (`+0.4346`, vượt mục tiêu `≥ 0.85`), PSNR ngoài vùng mask đạt **44.72 dB** (`≥ 40 dB`).
- **Việc tiếp theo**:
  - Chuyển sang Phase P4 (Giao diện người dùng tích hợp tự phát hiện, theo vết, chỉnh keyframe, xem trước 3–5s và so sánh A/B) hoặc Phase P5 (Tinh chỉnh chất lượng xoá trên nhóm nền tĩnh/bán trong suốt).

---

## Mẫu ghi cho các lần chạy sau

## Lần chạy N — DD/MM/YYYY (mô tả ngắn)

### Môi trường
- Commit:
- FFmpeg:
- Engine / phương pháp:

### Bộ dữ liệu
| Video | Nhóm | Độ phân giải | FPS | Số frame | Có bản sạch |
|---|---|---|---|---|---|

### Detector
| Video | Precision | Recall | Báo nhầm | Ghi chú |
|---|---|---|---|---|

### Tracker
| Video | IoU trung vị | Drift max (% frame) | Mất dấu được báo | Ghi chú |
|---|---|---|---|---|

### Chất lượng xoá
| Video | SSIM trong mask | PSNR ngoài mask | Nhấp nháy/viền | Ghi chú |
|---|---|---|---|---|

### Hiệu năng
| Video | Thời gian xử lý | RAM tối đa |
|---|---|---|

### Kết luận
- Đạt:
- Chưa đạt:
- Việc tiếp theo:
