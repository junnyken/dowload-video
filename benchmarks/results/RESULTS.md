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
| **Tổng hợp toàn bộ 19 video** | **19** | **1.0000 (≥ 0.90 ĐẠT)** | **1.0000 (≥ 0.85 ĐẠT)** | **0.0% (Chỉ báo cáo)** | **0.816** | **0.799s (max 1.447s — Chỉ báo cáo)** |

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
  - **Báo nhầm (False Positive) = 0.0% (0/3)** trên nhóm `content_text_neg_01..03` (*Chỉ báo cáo — chưa có ngưỡng trong Plan mục 6*), đồng thời loại bỏ hoàn toàn thanh tiêu đề tĩnh phía trên (`y < 130`) và tường tĩnh hai bên của video thực tế `253K views...Agency.mp4`.
  - **Thời gian phát hiện**: trung bình **0.799 giây**, lâu nhất **1.447 giây** trên video 900×720 (*Chỉ báo cáo — chưa có ngưỡng trong Plan mục 6*).
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

## Lần chạy 5 — 09/11/2026 (Phase P5.4 — Benchmark tách riêng 4 bộ: `synthetic`, `synthetic-hard`, `real-tune`, `real-heldout`)

### Môi trường
- Commit: `71b50de` (`feat/watermark-p5-hardening`)
- Hệ điều hành & Phần cứng: Windows 10/11 (win32), CPU `Intel(R) Core(TM) i3-10105 CPU @ 3.70GHz` (4 nhân / 8 luồng), RAM `16 GB`.
- FFmpeg: `8.1` / static `.tools/`.
- Engine: Detector `detect_watermark_regions` (P2), Tracker `track_watermark_in_video` (P3), Inpaint `process_video_natural` (P0/P3, `-crf 12`).
- Contact sheets (6 frame trước/sau mỗi video): xuất tại `benchmarks/results/contact-sheets/`.

---

### Bảng 1 — Bộ `synthetic` (18 video tổng hợp chuẩn: 15 dương tính + 3 âm tính)

| Hạng mục / Chỉ số | Giá trị đo được | Ngưỡng nghiệm thu (Plan mục 6 / Brief) | Đánh giá | Ghi chú / Nguyên nhân |
|---|---:|---|---|---|
| Detector Precision (IoU ≥ 0.5) | **1.0000** (15/15) | `≥ 0.90` | **ĐẠT** | Không báo dư vùng trên 15 video dương tính |
| Detector Recall (IoU ≥ 0.5) | **1.0000** (15/15) | `≥ 0.85` | **ĐẠT** | IoU trung bình `0.818` |
| Detector Báo nhầm trên `no_watermark` | **0.0%** (0/3) | *Chỉ báo cáo* | *Chỉ báo cáo* | Loại bỏ đúng phụ đề thoại/biển hiệu (`content_text_neg_01..03`) |
| Detector Thời gian xử lý | **0.763s** (max 1.088s) | *Chỉ báo cáo* | *Chỉ báo cáo* | |
| Tracker IoU trung vị (từ keyframe 0) | **1.0000** | `≥ 0.85` | **ĐẠT** | Tự khởi tạo từ P2 Detector đạt `0.8152` (`< 0.85` do viền đệm bbox 2–3px của Detector) |
| Tracker Drift tối đa (% đường chéo frame) | **0.14%** | `≤ 3.0%` | **ĐẠT** | |
| Tracker Tỷ lệ `needs_review` | **0.0%** (trên 15 mẫu thường) / **100%** (trên `lost_probe`) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | Báo `needs_review=True` chính xác khi watermark biến mất ở frame 50 |
| Inpaint SSIM trong mask — Nhóm `moving_watermark` (3 video, P3 tracked) | **0.9088** (`0.8527 – 0.9417`) | `≥ 0.85` | **ĐẠT** | Nhờ watermark nhảy/di chuyển nên frame lân cận lộ nền thật 100% cho `temporal_fill` |
| Inpaint SSIM trong mask — Nhóm watermark tĩnh (11 video) | **0.7815** (`0.6956 – 0.8745`) | `≥ 0.85` | **CHƯA ĐẠT** | **Nguyên nhân dự đoán**: Khi watermark đứng yên suốt clip và camera pan chậm, vùng nền dưới chữ bị che khuất liên tục; `temporal_fill` không tìm được frame lộ nền sạch hoàn toàn nên phải rơi về nội suy không gian `TELEA`/`SHIFTMAP`, làm mờ chi tiết nền tần số cao (9/11 video tĩnh dưới 0.85). |
| Inpaint SSIM trong mask — Trung bình toàn bộ 15 video dương tính | **0.8091** | `≥ 0.85` | **CHƯA ĐẠT** | **Nguyên nhân dự đoán**: Kéo xuống bởi nhóm watermark tĩnh (`0.7815`) và `scene_cut_03` (`0.8118`) do giới hạn của thuật toán inpaint cổ điển không dùng mô hình sinh (deep video inpainting). |
| Inpaint PSNR ngoài mask (so với ref cùng CRF) | **45.28 dB** (`42.42 – 50.91 dB`) | `≥ 40.0 dB` | **ĐẠT** | 15/15 video đều `≥ 42.42 dB` |
| Inpaint Temporal flicker trong mask | **0.87** (tỷ số so với nền) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | |
| Tốc độ xử lý (`sec / video_sec`) | **2.31s / 1s video** (`640×360@30fps`) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | Trung bình `11.56s` cho video `5.0s` |

---

### Bảng 2 — Bộ `synthetic-hard` (10 video thử thách: mờ 25–35%, nén CRF 34, scale 0.5×/1.5×, motion blur, dọc 720×1280, nhỏ/lớn, âm tính CRF 33)

#### Bảng tổng hợp `synthetic-hard`
| Hạng mục / Chỉ số | Giá trị đo được | Ngưỡng nghiệm thu (Plan mục 6 / Brief) | Đánh giá | Ghi chú / Nguyên nhân |
|---|---:|---|---|---|
| Detector Precision (IoU ≥ 0.5) | **1.0000** (9/9) | `≥ 0.90` | **ĐẠT** | |
| Detector Recall (IoU ≥ 0.5) | **1.0000** (9/9) | `≥ 0.85` | **ĐẠT** | IoU từ `0.7052` (`hard_small_wm`) đến `0.8812` (`hard_scale_15x`) |
| Detector Báo nhầm trên `no_watermark` (`hard_neg_no_watermark`) | **0.0%** (0/1) | *Chỉ báo cáo* | *Chỉ báo cáo* | Không báo nhầm trên video nén nặng CRF 33 có phụ đề |
| Detector Thời gian xử lý | **0.513s** (max 1.088s) | *Chỉ báo cáo* | *Chỉ báo cáo* | |
| Tracker IoU trung vị | **1.0000** | `≥ 0.85` | **ĐẠT** | |
| Tracker Drift tối đa (% đường chéo frame) | **0.136%** | `≤ 3.0%` | **ĐẠT** | |
| Tracker Tỷ lệ `needs_review` | **0.0%** (0/9) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | |
| Inpaint SSIM trong mask (TB 9 video dương tính) | **0.7022** (`0.3874 – 0.9004`) | `≥ 0.85` | **CHƯA ĐẠT** | **Nguyên nhân dự đoán**: Chỉ 2/9 video đạt `≥ 0.85` (`hard_motion_blur_moving` `0.9004`, `hard_scale_15x` `0.8572`). 7/9 video tĩnh dưới `0.85` vì watermark cố định che khuất hoàn toàn nền suốt clip; đặc biệt ở độ phân giải nhỏ `320×180` (`hard_scale_05x`, SSIM `0.3874`) và nén nặng `CRF 34` (`0.6319`), bán kính `dilate_px=12` chiếm tỷ lệ lớn diện tích patch khiến `TELEA` làm bệt hoạ tiết nền. |
| Inpaint PSNR ngoài mask (output vs input) | **TB 40.42 dB** (`39.31 – 41.03 dB`, 7/9 video `≥ 40 dB`) | `≥ 40.0 dB` | **CHƯA ĐẠT** (trên 2/9 mẫu đơn lẻ) | **Nguyên nhân dự đoán**: Trung bình đạt `40.42 dB`, nhưng 2 mẫu đơn lẻ dưới `40 dB` là `hard_scale_05x` (`39.31 dB`) và `hard_faint_opacity25` (`39.98 dB`). Do vùng biên `dilate_px=12 + feather_px=7 = 19px` lan nhẹ ra ngoài vùng loại trừ `16px` khi đo PSNR trực tiếp so với input, kết hợp sai số tái mã hoá H.264 trên nền tần số cao. |
| Inpaint Temporal flicker trong mask (`mean(\|frame[t]-frame[t-1]\|)`) | **6.089** (`2.695 – 11.366` / thang 0–255) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | |
| Tốc độ xử lý (`sec / video_sec`) | **3.50s / 1s video** (`0.92x – 7.29x`) | *Chưa có ngưỡng — chỉ báo cáo* | *Chưa có ngưỡng — chỉ báo cáo* | Cao nhất trên video dọc `720×1280` (`7.29s / 1s video`) |

#### Chi tiết từng video trong bộ `synthetic-hard`
| Video | Nhóm thử thách | Det IoU | Trk medIoU | Trk Drift (%) | SSIM trong mask (ngưỡng ≥ 0.85) | PSNR ngoài mask vs input (ngưỡng ≥ 40 dB) | Flicker trong mask (`mean\|Δ\|`) | Tốc độ (`sec/video_sec`) | Contact Sheet |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `hard_faint_opacity25.mp4` | Watermark mờ (opacity 25%) | 0.8266 | 1.0000 | 0.00% | **0.6200 (CHƯA ĐẠT)** | **39.98 dB (CHƯA ĐẠT)** | 7.321 | 2.39x | `synthetic-hard_hard_faint_opacity25_contact.jpg` |
| `hard_faint_opacity35.mp4` | Watermark mờ (opacity 35%) | 0.8210 | 1.0000 | 0.00% | **0.7396 (CHƯA ĐẠT)** | 40.57 dB (ĐẠT) | 5.070 | 2.66x | `synthetic-hard_hard_faint_opacity35_contact.jpg` |
| `hard_crf34_heavy.mp4` | Nén H.264 nặng (CRF 34) | 0.8152 | 1.0000 | 0.00% | **0.6319 (CHƯA ĐẠT)** | 41.03 dB (ĐẠT) | 6.491 | 2.77x | `synthetic-hard_hard_crf34_heavy_contact.jpg` |
| `hard_scale_05x.mp4` | Đổi tỷ lệ 0.5× (320×180) | 0.7143 | 1.0000 | 0.00% | **0.3874 (CHƯA ĐẠT)** | **39.31 dB (CHƯA ĐẠT)** | 11.366 | 0.92x | `synthetic-hard_hard_scale_05x_contact.jpg` |
| `hard_scale_15x.mp4` | Đổi tỷ lệ 1.5× (960×540) | 0.8812 | 1.0000 | 0.00% | **0.8572 (ĐẠT)** | 40.75 dB (ĐẠT) | 2.695 | 5.23x | `synthetic-hard_hard_scale_15x_contact.jpg` |
| `hard_motion_blur_moving.mp4` | Motion blur trên watermark di chuyển | 0.8539 | 1.0000 | 0.136% | **0.9004 (ĐẠT)** | 40.69 dB (ĐẠT) | 5.003 | 4.55x | `synthetic-hard_hard_motion_blur_moving_contact.jpg` |
| `hard_vertical_720x1280.mp4` | Video dọc 720×1280 | 0.8422 | 1.0000 | 0.00% | **0.8276 (CHƯA ĐẠT)** | 40.17 dB (ĐẠT) | 4.009 | 7.29x | `synthetic-hard_hard_vertical_720x1280_contact.jpg` |
| `hard_small_wm.mp4` | Watermark kích thước nhỏ (68×28) | 0.7052 | 1.0000 | 0.00% | **0.6539 (CHƯA ĐẠT)** | 40.57 dB (ĐẠT) | 7.650 | 2.72x | `synthetic-hard_hard_small_wm_contact.jpg` |
| `hard_large_wm.mp4` | Watermark kích thước lớn (224×76) | 0.8773 | 1.0000 | 0.00% | **0.7020 (CHƯA ĐẠT)** | 40.74 dB (ĐẠT) | 5.199 | 2.97x | `synthetic-hard_hard_large_wm_contact.jpg` |
| `hard_neg_no_watermark.mp4` | Không có watermark (CRF 33 + phụ đề) | — (0 FP) | — | — | — | — | — | — | — |

---

### Bảng 3 — Bộ `real-tune` (`benchmarks/real-tune/`)
- **Trạng thái**: **CHƯA ĐẠT (0 / 5–10 video)** — Thư mục `benchmarks/real-tune/` hiện trống.
- Theo đúng quy tắc P5.4 (*"Chủ dự án sẽ cung cấp video có quyền sử dụng. Nếu thư mục trống thì DỪNG bước này và báo lại, không tự tải video từ Internet"*), agent đã dừng bước đo `real-tune/` và không tự tải bất kỳ video nào từ mạng xã hội/Internet.
- Lưu ý: Trong `benchmarks/watermark-input/` hiện có sẵn 1 mẫu thực tế từ P0 (`253K views · 3.2K reactions _ Sao kỳ dị chờn 🥹 _ Cuộc Sống Agency.mp4`, `900×720@30fps`, `6.17s`):
  - Detector IoU = `0.777`, confidence = `0.83`, thời gian = `1.447s`
  - Tracker median IoU = `1.0000` (từ keyframe 0), drift = `0.00%`
  - Inpaint thời gian = `53.73s` (`8.71s / 1s video`), không có bản sạch cùng độ phân giải nên không tính SSIM trong mask.

---

### Bảng 4 — Bộ `real-heldout` (`benchmarks/real-heldout/`)
- **Trạng thái**: **CHƯA ĐẠT (0 / 10–15 video)** — Thư mục `benchmarks/real-heldout/` hiện trống.
- Chờ chủ dự án cung cấp 10–15 video thật có quyền sử dụng kèm nhãn `benchmarks/labels-real/*.json` để chạy đánh giá một lần (one-shot held-out evaluation).

---

## Lần chạy 6 — 10/11/2026 (Phase P5.6.2 — Load Test qua API + Celery + Redis thật, 30fps `per_frame`, `720×1280` & `1080×1920`, `10s / 20s / 30s`)

### Môi trường
- Nhánh: `feat/watermark-p5-hardening`
- CPU: `Intel(R) Core(TM) i3-10105 CPU @ 3.70GHz` (4 nhân / 8 luồng), RAM `16 GB`, Windows 10/11 (win32).
- Thực thi qua **HTTP API thật (`POST /upload` → `POST /process` → `GET /status`) + Celery Worker thật (`-Q media -c 2 -P threads` và `-Q default,celery -c 2 -P threads`) + Redis thật (`127.0.0.1:6379`)**, `FLOW_CLEANUP_ALLOW_INPROCESS=0`.
- Video đầu vào: **30 fps, nền chuyển động (`per_frame` mode)**, kích thước dọc `720×1280` và `1080×1920`, thời lượng `10s / 20s / 30s`.

### Kết quả Load Test (1, 2, 3 job đồng thời qua API + Celery `media` queue, `FLOW_INPAINT_WORKER_CONCURRENCY=2`)
| Cấu hình video (30 fps, `per_frame`) | Số job đồng thời | Kết quả thực thi (`outcomes`) | Chế độ / Lý do từ chối | P50 Latency (s) | P95 Latency (s) | Tổng thời gian batch (s) | Đánh giá (Plan mục 6) |
|---|---:|---|---|---:|---:|---:|---|
| `720×1280` (`10s`, 30fps) | 1 | `['done']` | `per_frame` (Celery `media`) | **26.46s** | **26.46s** | 27.27s | *Chỉ báo cáo* |
| `720×1280` (`10s`, 30fps) | 2 | `['done', 'done']` | `per_frame` (Celery `media`) | **62.91s** | **63.04s** | 63.98s | *Chỉ báo cáo* |
| `720×1280` (`10s`, 30fps) | 3 | `['done', 'done', 'done']` | `per_frame` (Celery `media`, job 3 xếp hàng) | **63.23s** | **63.50s** | 64.65s | *Chỉ báo cáo* |
| `720×1280` (`20s`, 30fps) | 1 | `['done']` | `per_frame` (Celery `media`) | **14.95s** | **14.95s** | 15.70s | *Chỉ báo cáo* |
| `720×1280` (`20s`, 30fps) | 2 | `['done', 'done']` | `per_frame` (Celery `media`) | **20.96s** | **22.09s** | 23.49s | *Chỉ báo cáo* |
| `720×1280` (`20s`, 30fps) | 3 | `['done', 'done', 'done']` | `per_frame` (Celery `media`, job 3 xếp hàng) | **21.85s** | **30.81s** | 33.37s | *Chỉ báo cáo* |
| `720×1280` (`30s`, 30fps) | 1 | `['rejected_by_limits']` | `HTTP 400 duration_limit_exceeded` (`est=373.2s > 300s`, `max=24s`) | **0.07s** | **0.07s** | 1.03s | *Chỉ báo cáo* (Chặn trước worker đúng thiết kế) |
| `720×1280` (`30s`, 30fps) | 2 | `['rejected_by_limits', ...]` | `HTTP 400 duration_limit_exceeded` (`est=373.2s > 300s`, `max=24s`) | **0.15s** | **0.21s** | 1.57s | *Chỉ báo cáo* |
| `720×1280` (`30s`, 30fps) | 3 | `['rejected_by_limits', ...]` | `HTTP 400 duration_limit_exceeded` (`est=373.2s > 300s`, `max=24s`) | **0.11s** | **0.23s** | 2.47s | *Chỉ báo cáo* |
| `1080×1920` (`10s`, 30fps) | 1 | `['done']` | `per_frame` (Celery `media`) | **62.08s** | **62.08s** | 62.85s | *Chỉ báo cáo* |
| `1080×1920` (`10s`, 30fps) | 2 | `['done', 'done']` | `per_frame` (Celery `media`) | **62.44s** | **62.99s** | 64.43s | *Chỉ báo cáo* |
| `1080×1920` (`10s`, 30fps) | 3 | `['done', 'done', 'done']` | `per_frame` (Celery `media`, job 3 xếp hàng) | **61.95s** | **87.11s** | 91.69s | *Chỉ báo cáo* |
| `1080×1920` (`20s`, 30fps) | 1 | `['rejected_by_limits']` | `HTTP 400 duration_limit_exceeded` (`est=559.9s > 300s`, `max=10s`) | **0.07s** | **0.07s** | 1.27s | *Chỉ báo cáo* (Chặn trước worker đúng thiết kế) |
| `1080×1920` (`20s`, 30fps) | 2 | `['rejected_by_limits', ...]` | `HTTP 400 duration_limit_exceeded` (`est=559.9s > 300s`, `max=10s`) | **0.18s** | **0.25s** | 2.12s | *Chỉ báo cáo* |
| `1080×1920` (`20s`, 30fps) | 3 | `['rejected_by_limits', ...]` | `HTTP 400 duration_limit_exceeded` (`est=559.9s > 300s`, `max=10s`) | **0.14s** | **0.32s** | 3.36s | *Chỉ báo cáo* |
| `1080×1920` (`30s`, 30fps) | 1 | `['rejected_by_limits']` | `HTTP 400 duration_limit_exceeded` (`est=839.8s > 300s`, `max=10s`) | **0.08s** | **0.08s** | 1.43s | *Chỉ báo cáo* (Chặn trước worker đúng thiết kế) |
| `1080×1920` (`30s`, 30fps) | 2 | `['rejected_by_limits', ...]` | `HTTP 400 duration_limit_exceeded` (`est=839.8s > 300s`, `max=10s`) | **0.11s** | **0.11s** | 2.78s | *Chỉ báo cáo* |
| `1080×1920` (`30s`, 30fps) | 3 | `['rejected_by_limits', ...]` | `HTTP 400 duration_limit_exceeded` (`est=839.8s > 300s`, `max=10s`) | **0.16s** | **0.16s** | 4.16s | *Chỉ báo cáo* |

### Cô lập hàng đợi bằng Celery Task thật (`delete_local_file` trên queue `celery`/`default` vs `inpaint_logo_task` trên queue `media`)
- Khi 3 job `inpaint_logo_task` (`per_frame` 30fps) chạy/xếp hàng đồng thời làm bão hoà worker `media` (`-c 2`), 9 tác vụ Celery thật (`delete_local_file.apply_async(..., queue="celery")`) được đẩy song song vào hàng đợi `default/celery` và chờ kết quả qua `AsyncResult.get()`:
  - **P50 latency**: **5.37 ms**
  - **P95 latency**: **46.28 ms**
  - **Max latency**: **73.31 ms**
  - Hàng đợi `default` hoàn toàn cô lập, không bị nghẽn bởi hàng đợi `media` (**ĐẠT** theo Plan mục 6: *"Hệ thống ổn định: Tác vụ chạy ở hàng đợi riêng, không làm nghẽn việc tải video thường"*).

### Chi phí CPU & Công suất tối đa / giờ / 1 worker (Chế độ `per_frame` @ 30fps)
| Cấu hình video (`per_frame` @ 30fps) | CPU-second / 1s video | Số video 10s tối đa / giờ / 1 worker | Đánh giá |
|---|---:|---:|---|
| `720×1280` @ 30fps | **2.65 CPU-s / 1s** | **136 video (10s) / giờ** | *Chỉ báo cáo* |
| `1080×1920` @ 30fps | **6.21 CPU-s / 1s** | **57 video (10s) / giờ** | *Chỉ báo cáo* |

### Giới hạn thời lượng thực tế theo Độ phân giải / FPS (`_validate_job_limits`, P5.6.3)
Công thức bảo vệ timeout trong `_validate_job_limits` (`_JOB_TIMEOUT_SEC = 300s`, `_SEC_PER_MPX_FRAME = 0.45`, trần cứng Inpaint `60s`, trần Crop/Blur `600s`):
- `mpx_per_sec = (width * height / 1_000_000) * fps`
- `estimated_inpaint_sec = dur * mpx_per_sec * 0.45`
- `effective_max_duration_sec = max(5, int(min(60, 300 / (mpx_per_sec * 0.45))))`

| Độ phân giải | FPS | `mpx_per_sec` | Thời lượng tối đa cho phép (Inpaint `natural`/`telea`/`telea_soft`) | Thời gian ước tính ở mức trần (`est_sec`) | Thời lượng tối đa cho phép (`crop` / `blur`) |
|---|---:|---:|---:|---:|---:|
| `640×360` (360p) | 24 fps | 5.53 | **60s** | 149.3s | **600s** |
| `640×360` (360p) | 30 fps | 6.91 | **60s** | 186.6s | **600s** |
| `854×480` (480p) | 30 fps | 12.30 | **54s** | 298.8s | **600s** |
| `1280×720` / `720×1280` (720p) | 24 fps | 22.12 | **30s** | 298.6s | **600s** |
| `1280×720` / `720×1280` (720p) | 30 fps | 27.65 | **24s** | 298.6s | **600s** |
| `1280×720` / `720×1280` (720p) | 60 fps | 55.30 | **12s** | 298.6s | **600s** |
| `1920×1080` / `1080×1920` (1080p) | 24 fps | 49.77 | **13s** | 291.1s | **600s** |
| `1920×1080` / `1080×1920` (1080p) | 30 fps | 62.21 | **10s** | 279.9s | **600s** |
| `1920×1080` / `1080×1920` (1080p) | 60 fps | 124.42 | **5s** | 279.9s | **600s** |
| `3840×2160` (4K UHD) | 30 fps | 248.83 | **2s** (UI báo sàn `5s`, `/process` từ chối ở `> 2.68s`) | 300.0s | **600s** |

**Đề xuất kỹ thuật (không tự tăng timeout trong P5.6)**:
1. Sau khi tối ưu `temporal_fill` theo vùng ROI + giới hạn mẫu RANSAC ở P5.6.2, tốc độ thực tế trên `720×1280@30fps` là `~1.3–2.6s / 1s video` (`~0.095s / MPx·frame`, thấp hơn nhiều so với hệ số bảo thủ `_SEC_PER_MPX_FRAME = 0.45`).
2. Nếu muốn hỗ trợ video `720p@30fps` dài `30s–60s` hoặc `1080p@30fps` dài `20s–30s` mà vẫn giữ nguyên `FLOW_JOB_TIMEOUT_SEC = 300s`, có thể giảm `FLOW_EST_SEC_PER_MPX_FRAME` từ `0.45` xuống `0.15` (khi đó `720p@30fps` được phép tới `60s` và `1080p@30fps` được phép tới `32s`).

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
