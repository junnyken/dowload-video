import asyncio
from app.services.downloader import extract_video_info_sync

try:
    print("Bắt đầu lấy thông tin và tải HD video...")
    info = extract_video_info_sync("https://www.youtube.com/watch?v=C5yYEAcE_cI", quality="video")
    print("\n✅ KẾT QUẢ TẢI:")
    print(f"Tiêu đề: {info.get('title')}")
    print(f"Direct MP4: {info.get('direct_mp4_url')}")
    print(f"Local File Path: {info.get('local_file_path')}")
    print(f"File Size (MB): {info.get('file_size_mb')}")
except Exception as e:
    print(f"Lỗi: {e}")
