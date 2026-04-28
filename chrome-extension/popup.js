// Đổi thành domain thật của server bạn
const API_BASE_URL = "https://dowload-video-trieunt.dev.matbao.ai";

document.getElementById('downloadBtn').addEventListener('click', async () => {
    const btnText = document.getElementById('btnText');
    const spinner = document.getElementById('spinner');
    const icon = document.getElementById('icon-download');
    const statusMsg = document.getElementById('statusMsg');
    const downloadBtn = document.getElementById('downloadBtn');

    const quality = document.getElementById('qualitySelect').value;
    const removeWatermark = document.getElementById('removeWatermark').checked;

    // Hiệu ứng Loading
    downloadBtn.disabled = true;
    downloadBtn.classList.add('opacity-70');
    icon.style.display = 'none';
    spinner.style.display = 'block';
    btnText.textContent = 'Đang bóc tách...';
    statusMsg.textContent = 'Server đang xử lý, vui lòng đợi...';
    statusMsg.classList.remove('text-red-400', 'text-green-400');
    statusMsg.classList.add('text-orange-400');

    try {
        // 1. Lấy URL của tab đang mở
        let [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        
        if (!tab || !tab.url || tab.url.startsWith('chrome://')) {
            throw new Error("Không thể lấy link ở trang này.");
        }

        const videoUrl = tab.url;

        // 2. Gửi request đến máy chủ VidGrab
        const response = await fetch(`${API_BASE_URL}/api/v1/fetch-link`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                url: videoUrl,
                quality: quality,
                remove_watermark: removeWatermark
            })
        });

        const data = await response.json();

        if (response.ok && data.success) {
            statusMsg.textContent = 'Thành công! Bắt đầu tải file...';
            statusMsg.classList.remove('text-orange-400');
            statusMsg.classList.add('text-green-400');

            // 3. Tải file về máy người dùng
            let downloadUrl = data.direct_mp4_url || data.local_mp3_path || data.local_file_path;
            
            // Xử lý proxy để lách chặn CORS từ trình duyệt
            if (!downloadUrl.includes("matbao.ai")) {
                 const ext = quality.includes('mp3') ? 'mp3' : 'mp4';
                 downloadUrl = `${API_BASE_URL}/api/v1/proxy-download?url=${encodeURIComponent(downloadUrl)}&filename=${encodeURIComponent(data.title || 'video')}&ext=${ext}`;
            } else if (downloadUrl.startsWith('/app/downloads/')) {
                 // Xử lý file lưu ở local server
                 const ext = quality.includes('mp3') ? 'mp3' : 'mp4';
                 downloadUrl = `${API_BASE_URL}/api/v1/download-local?filepath=${encodeURIComponent(downloadUrl)}&filename=${encodeURIComponent(data.title || 'video')}.${ext}`;
            }

            const safeName = (data.title || 'video').replace(/[/\\?%*:|"<>]/g, '-');
            const fileExt = quality.includes('mp3') ? '.mp3' : '.mp4';

            chrome.downloads.download({
                url: downloadUrl,
                filename: `VidGrab/${safeName}${fileExt}`,
                saveAs: true
            });

        } else {
            throw new Error(data.detail || "Server báo lỗi hoặc link không hỗ trợ.");
        }
    } catch (err) {
        console.error(err);
        statusMsg.textContent = `Lỗi: ${err.message}`;
        statusMsg.classList.remove('text-orange-400');
        statusMsg.classList.add('text-red-400');
    } finally {
        // Phục hồi nút bấm
        downloadBtn.disabled = false;
        downloadBtn.classList.remove('opacity-70');
        icon.style.display = 'block';
        spinner.style.display = 'none';
        btnText.textContent = 'Tải Video Ngay';
    }
});
