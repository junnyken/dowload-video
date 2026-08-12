"""
Centralized error code definitions for VidGrab.

Every API error must use a code from this module so frontend can
map codes to actionable, user-friendly messages.

Each entry has:
  user_message    — Vietnamese UI copy (keep it honest, no jargon)
  retryable       — True = show Retry button; False = show alt action
  suggested_action — One-sentence next step for the user
"""

# ─── Re-export legacy constants so existing import sites still work ────
ERR_QUOTA_DAILY  = "quota_exceeded_daily"
ERR_QUALITY      = "tier_limit_quality"
ERR_BATCH        = "tier_limit_batch"
ERR_FEATURE      = "tier_required_feature"

# ─── Full error catalogue ──────────────────────────────────────────────
ERROR_META: dict[str, dict] = {
    # ── Download / extraction ──────────────────────────────────────────
    "unsupported_url": {
        "user_message": "URL này không được hỗ trợ.",
        "retryable": False,
        "suggested_action": "Thử URL từ YouTube, TikTok, Instagram, hoặc Facebook.",
    },
    "private_or_login_required": {
        "user_message": "Video này là riêng tư hoặc yêu cầu đăng nhập.",
        "retryable": False,
        "suggested_action": "Kiểm tra xem video có công khai không.",
    },
    "rate_limited": {
        "user_message": "Nền tảng đang giới hạn tốc độ. Vui lòng đợi.",
        "retryable": True,
        "suggested_action": "Thử lại sau 30–60 giây.",
    },
    "temporary_blocked": {
        "user_message": "Yêu cầu bị chặn tạm thời bởi nền tảng.",
        "retryable": True,
        "suggested_action": "Thử lại sau vài phút. Nếu vẫn bị chặn, thử URL khác.",
    },
    "no_media_found": {
        "user_message": "Không tìm thấy nội dung video trong URL này.",
        "retryable": False,
        "suggested_action": "Kiểm tra lại URL hoặc thử link khác.",
    },
    "processing_failed": {
        "user_message": "Xử lý video thất bại do lỗi không xác định.",
        "retryable": True,
        "suggested_action": "Thử lại. Nếu lỗi tiếp tục, liên hệ hỗ trợ.",
    },
    "drm_protected": {
        "user_message": "Video được bảo vệ DRM, không thể tải xuống.",
        "retryable": False,
        "suggested_action": "Tìm phiên bản không có DRM hoặc nguồn khác.",
    },
    "geo_blocked": {
        "user_message": "Video không khả dụng ở khu vực server.",
        "retryable": False,
        "suggested_action": "Thử nguồn video khác.",
    },
    # ── File / storage ─────────────────────────────────────────────────
    "file_expired": {
        "user_message": "File đã hết hạn và bị xóa khỏi server.",
        "retryable": False,
        "suggested_action": "Tải lại từ Archive hoặc nhập URL gốc.",
    },
    "storage_limit_reached": {
        "user_message": "Đã đạt giới hạn lưu trữ tài khoản.",
        "retryable": False,
        "suggested_action": "Bỏ ghim file cũ hoặc nâng cấp lên Pro.",
    },
    # ── Quota / tier ───────────────────────────────────────────────────
    ERR_QUOTA_DAILY: {
        "user_message": "Đã đạt giới hạn tải xuống hôm nay.",
        "retryable": False,
        "suggested_action": "Thử lại vào ngày mai hoặc nâng cấp Pro.",
    },
    ERR_QUALITY: {
        "user_message": "Chất lượng này chỉ dành cho tài khoản Pro.",
        "retryable": False,
        "suggested_action": "Chọn chất lượng thấp hơn hoặc nâng cấp Pro.",
    },
    ERR_BATCH: {
        "user_message": "Số lượng URL vượt quá giới hạn cho phép.",
        "retryable": False,
        "suggested_action": "Chia nhỏ danh sách và gửi từng đợt.",
    },
    ERR_FEATURE: {
        "user_message": "Tính năng này chỉ dành cho tài khoản Pro.",
        "retryable": False,
        "suggested_action": "Nâng cấp Pro để mở khóa tính năng này.",
    },
    # ── Archive ────────────────────────────────────────────────────────
    "archive_sync_failed": {
        "user_message": "Không thể đồng bộ dữ liệu Archive.",
        "retryable": True,
        "suggested_action": "Làm mới trang hoặc thử lại sau.",
    },
    "archive_item_not_found": {
        "user_message": "Không tìm thấy mục trong Archive.",
        "retryable": False,
        "suggested_action": "Mục có thể đã bị xóa.",
    },
    # ── Schedule ───────────────────────────────────────────────────────
    "schedule_failed": {
        "user_message": "Lịch tải đã gặp lỗi khi chạy.",
        "retryable": True,
        "suggested_action": 'Chạy thủ công bằng nút "Chạy ngay" hoặc đợi lần tự động tiếp theo.',
    },
    "schedule_rate_too_frequent": {
        "user_message": "Khoảng cách giữa các lần chạy phải tối thiểu 6 giờ.",
        "retryable": False,
        "suggested_action": "Tăng khoảng thời gian giữa các lần chạy lên ít nhất 6 giờ.",
    },
    "schedule_limit": {
        "user_message": "Đã đạt giới hạn số lịch tải.",
        "retryable": False,
        "suggested_action": "Xóa lịch cũ không dùng hoặc nâng cấp Pro.",
    },
    # ── General ────────────────────────────────────────────────────────
    "invalid_url": {
        "user_message": "URL không hợp lệ.",
        "retryable": False,
        "suggested_action": "Kiểm tra lại URL và thử lại.",
    },
    "server_error": {
        "user_message": "Lỗi server không xác định.",
        "retryable": True,
        "suggested_action": "Thử lại sau vài giây. Liên hệ hỗ trợ nếu vẫn lỗi.",
    },
    "concurrency_limit": {
        "user_message": "Server đang xử lý quá nhiều yêu cầu đồng thời.",
        "retryable": True,
        "suggested_action": "Thử lại sau 15–30 giây.",
    },
    "unauthorized": {
        "user_message": "Bạn cần đăng nhập để thực hiện hành động này.",
        "retryable": False,
        "suggested_action": "Đăng nhập và thử lại.",
    },
    # ── Phase 6 — standardized public API errors ──────────────────────
    "provider_unavailable": {
        "user_message": "Nền tảng nguồn tạm thời không phản hồi.",
        "retryable": True,
        "suggested_action": "Thử lại sau vài phút. Nếu vẫn lỗi, thử URL khác từ cùng nền tảng.",
    },
    "queue_busy": {
        "user_message": "Hàng đợi xử lý đang quá tải. Yêu cầu của bạn sẽ được xử lý sau.",
        "retryable": True,
        "suggested_action": "Thử lại sau 30–60 giây hoặc dùng chế độ Bulk với ít URL hơn.",
    },
    "validation_failed": {
        "user_message": "Dữ liệu đầu vào không hợp lệ.",
        "retryable": False,
        "suggested_action": "Kiểm tra lại định dạng yêu cầu và thử lại.",
    },
    "job_expired": {
        "user_message": "Job đã hết hạn và không còn khả dụng.",
        "retryable": False,
        "suggested_action": "Gửi lại yêu cầu tải xuống từ URL gốc.",
    },
    "export_failed": {
        "user_message": "Xuất dữ liệu thất bại.",
        "retryable": True,
        "suggested_action": "Thử lại. Nếu vẫn lỗi, thử với khoảng thời gian ngắn hơn.",
    },
    "notification_permission_denied": {
        "user_message": "Quyền thông báo bị từ chối bởi trình duyệt.",
        "retryable": False,
        "suggested_action": "Cho phép thông báo trong cài đặt trình duyệt và thử lại.",
    },
    # ── Logo Inpaint (experimental) ───────────────────────────────────
    "inpaint_input_invalid": {
        "user_message": "File đầu vào không hợp lệ hoặc không thể đọc.",
        "retryable": False,
        "suggested_action": "Kiểm tra file video, đảm bảo định dạng được hỗ trợ (MP4, MOV, MKV) và thử lại.",
    },
    "inpaint_mask_missing": {
        "user_message": "Chưa xác định vùng logo cần xoá.",
        "retryable": False,
        "suggested_action": "Chọn vị trí logo (góc phải/trái, trên/dưới) trước khi xử lý.",
    },
    "inpaint_processing_failed": {
        "user_message": "Xử lý xoá logo thất bại. Kết quả thử nghiệm có thể thay đổi tùy video.",
        "retryable": True,
        "suggested_action": "Thử lại với preset góc khác, hoặc thử method crop thay vì inpaint.",
    },
    "inpaint_unsupported_format": {
        "user_message": "Định dạng video không được hỗ trợ cho logo inpaint.",
        "retryable": False,
        "suggested_action": "Chuyển đổi video sang MP4 (H.264) và thử lại.",
    },
    "inpaint_result_unavailable": {
        "user_message": "File kết quả không còn khả dụng (đã hết hạn hoặc bị xoá).",
        "retryable": False,
        "suggested_action": "Chạy lại quá trình xoá logo từ đầu.",
    },

    # ── Instagram ─────────────────────────────────────────────────────
    "ig_login_required": {
        "user_message": "Instagram yêu cầu đăng nhập để xem nội dung này.",
        "retryable": False,
        "suggested_action": "Nội dung có thể là tài khoản riêng tư hoặc story bị giới hạn.",
    },
    "ig_rate_limited": {
        "user_message": "Instagram đang giới hạn tốc độ. Vui lòng thử lại sau.",
        "retryable": True,
        "suggested_action": "Thử lại sau 1–2 phút.",
    },
    "ig_content_removed": {
        "user_message": "Nội dung Instagram đã bị xoá hoặc không còn khả dụng.",
        "retryable": False,
        "suggested_action": "Kiểm tra lại URL — bài đăng có thể đã bị gỡ.",
    },
    "ig_private_account": {
        "user_message": "Tài khoản Instagram này là riêng tư.",
        "retryable": False,
        "suggested_action": "Chỉ có thể tải nội dung từ tài khoản công khai.",
    },
    "ig_story_expired": {
        "user_message": "Story Instagram đã hết hạn (story chỉ tồn tại 24 giờ).",
        "retryable": False,
        "suggested_action": "Story không thể phục hồi sau khi hết hạn.",
    },
    "ig_carousel_partial": {
        "user_message": "Một số ảnh/video trong carousel không tải được.",
        "retryable": True,
        "suggested_action": "Thử lại. Các file tải thành công vẫn có trong kết quả.",
    },

    # ── Twitter / X ────────────────────────────────────────────────────
    "tw_login_required": {
        "user_message": "Twitter/X yêu cầu đăng nhập để xem nội dung này.",
        "retryable": False,
        "suggested_action": "Nội dung bị giới hạn cho tài khoản đã đăng nhập.",
    },
    "tw_rate_limited": {
        "user_message": "Twitter/X đang giới hạn tốc độ. Vui lòng thử lại sau.",
        "retryable": True,
        "suggested_action": "Thử lại sau 5–10 phút.",
    },
    "tw_media_not_found": {
        "user_message": "Tweet này không chứa video hoặc GIF.",
        "retryable": False,
        "suggested_action": "Chỉ hỗ trợ tweet có đính kèm video hoặc GIF.",
    },
    "tw_suspended_account": {
        "user_message": "Tài khoản Twitter/X này đã bị tạm ngưng.",
        "retryable": False,
        "suggested_action": "Không thể tải nội dung từ tài khoản bị tạm ngưng.",
    },

    # ── Reddit ────────────────────────────────────────────────────────
    "reddit_login_required": {
        "user_message": "Reddit yêu cầu đăng nhập để xem nội dung này.",
        "retryable": False,
        "suggested_action": "Cộng đồng này có thể là NSFW hoặc riêng tư.",
    },
    "reddit_video_unavailable": {
        "user_message": "Video Reddit không còn khả dụng hoặc đã bị xoá.",
        "retryable": False,
        "suggested_action": "Video có thể đã bị chủ bài xoá hoặc bị Reddit gỡ.",
    },
    "reddit_crosspost_external": {
        "user_message": "Post Reddit này nhúng nội dung từ nền tảng khác.",
        "retryable": False,
        "suggested_action": "Tải trực tiếp từ URL gốc (YouTube, Imgur...) sẽ đáng tin hơn.",
    },
    "reddit_private_community": {
        "user_message": "Cộng đồng Reddit này là riêng tư.",
        "retryable": False,
        "suggested_action": "Chỉ thành viên được phê duyệt mới xem được nội dung.",
    },
    "reddit_no_videos_found": {
        "user_message": "Không tìm thấy bài đăng video nào trong cộng đồng này.",
        "retryable": False,
        "suggested_action": "Thử subreddit khác hoặc đổi bộ lọc (hot/top/new).",
    },

    # ── Bilibili ──────────────────────────────────────────────────────
    "bili_login_required": {
        "user_message": "Video Bilibili này yêu cầu đăng nhập (nội dung 18+ hoặc hội viên).",
        "retryable": False,
        "suggested_action": "Thêm cookie Bilibili vào Admin → Cookie Pool.",
    },
    "bili_geo_blocked": {
        "user_message": "Video Bilibili không khả dụng ở khu vực này.",
        "retryable": False,
        "suggested_action": "Nội dung bị giới hạn địa lý bởi Bilibili.",
    },
    "bili_vip_only": {
        "user_message": "Video Bilibili này chỉ dành cho hội viên (大会员).",
        "retryable": False,
        "suggested_action": "Cần tài khoản Bilibili Premium để tải nội dung này.",
    },

    # ── Xiaohongshu / RedNote ─────────────────────────────────────────
    "xhs_geo_restricted": {
        "user_message": "Nội dung Xiaohongshu bị giới hạn địa lý.",
        "retryable": False,
        "suggested_action": "Một số nội dung XHS chỉ khả dụng tại Trung Quốc đại lục.",
    },
    "xhs_login_required": {
        "user_message": "Xiaohongshu yêu cầu đăng nhập để xem nội dung này.",
        "retryable": False,
        "suggested_action": "Thêm cookie XHS vào Admin → Cookie Pool.",
    },
    "xhs_extract_failed": {
        "user_message": "Không thể trích xuất nội dung từ Xiaohongshu.",
        "retryable": True,
        "suggested_action": "XHS thay đổi API thường xuyên. Thử lại hoặc liên hệ hỗ trợ.",
    },

    # ── Lemon8 ────────────────────────────────────────────────────────
    "lemon8_extract_failed": {
        "user_message": "Không thể trích xuất nội dung từ Lemon8.",
        "retryable": True,
        "suggested_action": "Thử lại. Lemon8 đôi khi chặn yêu cầu tự động.",
    },
    "lemon8_private_post": {
        "user_message": "Bài đăng Lemon8 này là riêng tư.",
        "retryable": False,
        "suggested_action": "Chỉ hỗ trợ bài đăng công khai.",
    },

    # ── Snapchat ──────────────────────────────────────────────────────
    "snap_private_content": {
        "user_message": "Snap này là riêng tư hoặc đã hết hạn.",
        "retryable": False,
        "suggested_action": "Chỉ hỗ trợ nội dung Spotlight/Story công khai.",
    },
    "snap_extract_failed": {
        "user_message": "Không thể trích xuất từ Snapchat.",
        "retryable": True,
        "suggested_action": "Thử lại hoặc dùng URL Spotlight trực tiếp.",
    },

    # ── Alt platforms (VK/Twitch/Rumble/Odysee/Dailymotion) ──────────
    "platform_not_supported": {
        "user_message": "Nền tảng này chưa được hỗ trợ.",
        "retryable": False,
        "suggested_action": "Xem danh sách nền tảng được hỗ trợ tại trang /platforms.",
    },
    "vk_login_required": {
        "user_message": "VK yêu cầu đăng nhập để xem video này.",
        "retryable": False,
        "suggested_action": "Video VK có thể bị giới hạn cho người dùng đã đăng nhập.",
    },
    "twitch_vod_unavailable": {
        "user_message": "VOD Twitch không còn khả dụng (có thể đã bị xoá bởi streamer).",
        "retryable": False,
        "suggested_action": "VOD Twitch bị xoá tự động sau 14–60 ngày.",
    },

    # ── Podcast ───────────────────────────────────────────────────────
    "podcast_rss_invalid": {
        "user_message": "Không thể đọc RSS feed từ URL này.",
        "retryable": False,
        "suggested_action": "Kiểm tra lại URL feed hoặc thử URL trực tiếp của episode.",
    },
    "podcast_episode_not_found": {
        "user_message": "Không tìm thấy episode podcast.",
        "retryable": False,
        "suggested_action": "Thử URL trực tiếp của episode thay vì URL feed.",
    },
}


def get_error_meta(code: str) -> dict:
    """Return error metadata for a given code, with safe fallback."""
    return ERROR_META.get(code, {
        "user_message": "Đã có lỗi xảy ra.",
        "retryable": True,
        "suggested_action": "Vui lòng thử lại.",
    })


def make_error(
    code: str,
    *,
    override_message: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Build a structured error dict suitable for HTTPException(detail=...)."""
    meta = get_error_meta(code)
    payload = {
        "error_code":       code,
        "user_message":     override_message or meta["user_message"],
        "retryable":        meta["retryable"],
        "suggested_action": meta["suggested_action"],
    }
    if extra:
        payload.update(extra)
    return payload
