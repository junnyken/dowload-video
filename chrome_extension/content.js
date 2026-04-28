// content.js
(function() {
  // Prevent multiple injections
  if (document.getElementById('vd-download-nowatermark-btn')) return;

  const btn = document.createElement('button');
  btn.id = 'vd-download-nowatermark-btn';
  btn.innerText = '✨ Download No Watermark';
  
  Object.assign(btn.style, {
    position: 'fixed',
    bottom: '80px',
    right: '20px',
    zIndex: '9999999',
    padding: '12px 24px',
    backgroundColor: '#ef4444', // Red color
    color: '#ffffff',
    border: 'none',
    borderRadius: '12px',
    fontWeight: 'bold',
    cursor: 'pointer',
    boxShadow: '0 10px 15px -3px rgba(0, 0, 0, 0.1)',
    transition: 'all 0.2s',
    fontFamily: 'system-ui, sans-serif'
  });

  btn.addEventListener('mouseover', () => btn.style.backgroundColor = '#dc2626');
  btn.addEventListener('mouseout', () => btn.style.backgroundColor = '#ef4444');

  btn.addEventListener('click', async () => {
    btn.innerText = '⏳ Extracting...';
    btn.disabled = true;

    try {
      const { apiKey } = await chrome.storage.local.get('apiKey');
      if (!apiKey) {
        alert('Please open the extension popup and set your API Key first.');
        btn.innerText = '✨ Download No Watermark';
        btn.disabled = false;
        return;
      }

      // Determine backend URL (fallback to localhost for dev)
      const backendUrl = 'http://localhost:8000/api/v1/fetch-link';

      const response = await fetch(backendUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          url: window.location.href,
          remove_watermark: true,
          quality: 'video'
        })
      });

      const data = await response.json();
      if (data.success && data.direct_mp4_url) {
        btn.innerText = '✅ Success! Opening...';
        window.open(data.direct_mp4_url, '_blank');
      } else {
        alert('Download failed: ' + (data.detail || 'Unknown error'));
        btn.innerText = '❌ Failed';
      }
    } catch (err) {
      alert('Error connecting to backend API: ' + err.message);
      btn.innerText = '✨ Download No Watermark';
    }

    setTimeout(() => {
      btn.innerText = '✨ Download No Watermark';
      btn.disabled = false;
    }, 4000);
  });

  document.body.appendChild(btn);
})();
