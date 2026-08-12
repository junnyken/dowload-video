import { useState, useCallback } from 'react';

const API = import.meta.env.VITE_API_URL || '';

const FRIENDLY_ERRORS = {
  no_subtitles: 'Nguồn này không có phụ đề/captions',
  file_expired: 'File đã hết hạn, hãy tải lại từ nguồn gốc',
  invalid_path: 'Đường dẫn file không hợp lệ',
};

function friendlyMsg(err) {
  if (!err) return 'Xử lý thất bại';
  if (typeof err === 'string') return FRIENDLY_ERRORS[err] || err;
  return FRIENDLY_ERRORS[err.error] || err.message || err.detail || 'Xử lý thất bại';
}

async function callAPI(path, body) {
  const res = await fetch(`${API}/api/v1/${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(friendlyMsg(data.detail || data));
  return data;
}

export function useProcessing() {
  const [states, setStates] = useState({});

  const run = useCallback(async (key, path, body) => {
    setStates(prev => ({ ...prev, [key]: { loading: true, error: null, result: null } }));
    try {
      const result = await callAPI(path, body);
      setStates(prev => ({ ...prev, [key]: { loading: false, error: null, result } }));
      return result;
    } catch (e) {
      setStates(prev => ({ ...prev, [key]: { loading: false, error: e.message, result: null } }));
      throw e;
    }
  }, []);

  const getState = useCallback((key) => states[key] || { loading: false, error: null, result: null }, [states]);

  return {
    runTrim:      (body) => run('trim',     'trim',                   body),
    runAudio:     (body) => run('audio',    'process/extract-audio',  body),
    runGif:       (body) => run('gif',      'to-gif',                 body),
    runMp4Loop:   (body) => run('loop',     'process/mp4-loop',       body),
    runSubtitle:  (body) => run('subtitle', 'process/subtitle',       body),
    runBurnSub:   (body) => run('burn',     'process/burn-subtitle',  body),
    runFrameThumb:(body) => run('thumb',    'process/frame-thumb',    body),
    getState,
    reset: (key) => setStates(prev => ({ ...prev, [key]: { loading: false, error: null, result: null } })),
  };
}
