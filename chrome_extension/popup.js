document.addEventListener('DOMContentLoaded', async () => {
  const apiKeyInput = document.getElementById('apiKey');
  const saveKeyBtn = document.getElementById('saveKeyBtn');
  const downloadBtn = document.getElementById('downloadBtn');
  const qualitySelect = document.getElementById('qualitySelect');
  const removeWatermarkCheckbox = document.getElementById('removeWatermark');
  
  const progressContainer = document.getElementById('progressContainer');
  const progressBar = document.getElementById('progressBar');
  const statusText = document.getElementById('statusText');
  const countdownTimer = document.getElementById('countdownTimer');
  const timeRemaining = document.getElementById('timeRemaining');
  const resultContainer = document.getElementById('resultContainer');
  const downloadLink = document.getElementById('downloadLink');

  let countdownInterval;

  // Load saved API key and state
  const data = await chrome.storage.local.get(['apiKey', 'lastDownloadUrl', 'downloadExpiry']);
  if (data.apiKey) apiKeyInput.value = data.apiKey;
  
  // Check if there is a recently completed download
  if (data.lastDownloadUrl && data.downloadExpiry) {
    const now = new Date().getTime();
    if (now < data.downloadExpiry) {
      showResultUI(data.lastDownloadUrl, data.downloadExpiry);
    }
  }

  saveKeyBtn.addEventListener('click', () => {
    chrome.storage.local.set({ apiKey: apiKeyInput.value }, () => {
      saveKeyBtn.innerText = 'Saved!';
      saveKeyBtn.classList.replace('bg-gray-700', 'bg-green-600');
      setTimeout(() => {
        saveKeyBtn.innerText = 'Save';
        saveKeyBtn.classList.replace('bg-green-600', 'bg-gray-700');
      }, 2000);
    });
  });

  function startCountdown(expiryTime) {
    countdownTimer.classList.remove('hidden');
    clearInterval(countdownInterval);
    
    countdownInterval = setInterval(() => {
      const now = new Date().getTime();
      const distance = expiryTime - now;
      
      if (distance < 0) {
        clearInterval(countdownInterval);
        timeRemaining.innerText = "EXPIRED";
        resultContainer.classList.add('hidden');
        chrome.storage.local.remove(['lastDownloadUrl', 'downloadExpiry']);
        return;
      }
      
      const minutes = Math.floor((distance % (1000 * 60 * 60)) / (1000 * 60));
      const seconds = Math.floor((distance % (1000 * 60)) / 1000);
      timeRemaining.innerText = `${minutes}:${seconds.toString().padStart(2, '0')}`;
    }, 1000);
  }

  function showResultUI(url, expiryTime) {
    progressContainer.classList.remove('hidden');
    progressBar.style.width = '100%';
    progressBar.classList.replace('bg-red-500', 'bg-green-500');
    statusText.innerText = 'Ready!';
    statusText.classList.replace('text-red-400', 'text-green-400');
    
    resultContainer.classList.remove('hidden');
    downloadLink.href = url;
    
    startCountdown(expiryTime);
  }

  downloadBtn.addEventListener('click', async () => {
    const apiKey = apiKeyInput.value;
    if (!apiKey) {
      alert('Please enter and save your API Key first.');
      return;
    }

    // Get current active tab
    let [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.url) return;

    // UI Updates
    downloadBtn.disabled = true;
    downloadBtn.classList.add('opacity-50', 'cursor-not-allowed');
    progressContainer.classList.remove('hidden');
    resultContainer.classList.add('hidden');
    countdownTimer.classList.add('hidden');
    progressBar.style.width = '30%';
    statusText.innerText = 'Extracting media link...';
    statusText.classList.replace('text-green-400', 'text-red-400');
    progressBar.classList.replace('bg-green-500', 'bg-red-500');
    clearInterval(countdownInterval);

    try {
      const backendUrl = 'http://localhost:8000/api/v1/fetch-link'; // Change to https://your-api-link.com/api/v1/fetch-link for production

      const response = await fetch(backendUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          url: tab.url,
          remove_watermark: removeWatermarkCheckbox.checked,
          quality: qualitySelect.value
        })
      });

      progressBar.style.width = '80%';
      const result = await response.json();

      if (result.success) {
        // Prefer local_mp3_path if Audio, otherwise direct_mp4_url
        let finalUrl = result.direct_mp4_url;
        if (qualitySelect.value.startsWith('mp3') && result.local_mp3_path) {
            finalUrl = `http://localhost:8000/api/v1/download-local?filepath=${encodeURIComponent(result.local_mp3_path)}&filename=audio.mp3`;
        }

        const expiryTime = new Date().getTime() + 15 * 60 * 1000; // 15 mins expiry
        
        // Save state for popup reopening
        chrome.storage.local.set({ 
            lastDownloadUrl: finalUrl,
            downloadExpiry: expiryTime
        });

        showResultUI(finalUrl, expiryTime);
      } else {
        throw new Error(result.detail || 'Failed to extract video');
      }
    } catch (err) {
      statusText.innerText = 'Error: ' + err.message;
      progressBar.classList.replace('bg-red-500', 'bg-gray-500');
    } finally {
      downloadBtn.disabled = false;
      downloadBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    }
  });
});
