import { useState, useCallback } from 'react';
import i18n from '../i18n';
import { supabase } from '../lib/supabaseClient';
import { API_BASE } from '../lib/apiBase';

// Server error codes -> text in locales/*/common.json (processing.errors.<code>).
const FRIENDLY_CODES = ['no_subtitles', 'file_expired', 'invalid_path'];
const friendly = (code) => (FRIENDLY_CODES.includes(code) ? i18n.t(`processing.errors.${code}`, { ns: 'common' }) : undefined);

function friendlyMsg(err) {
  const failed = i18n.t('processing.failed', { ns: 'common' });
  if (!err) return failed;
  if (typeof err === 'string') return friendly(err) || err;
  if (err.detail && typeof err.detail === 'object') {
    return friendly(err.detail.error || err.detail.error_code) || err.detail.message || err.detail.user_message || failed;
  }
  return friendly(err.error || err.error_code) || err.message || err.user_message || (typeof err.detail === 'string' ? err.detail : '') || failed;
}

async function callAPI(path, body) {
  let token = null;
  try {
    const { data: { session } } = await supabase.auth.getSession();
    token = session?.access_token || null;
  } catch { /* guest */ }
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const res = await fetch(`${API_BASE}/api/v1/${path}`, {
    method: 'POST',
    headers,
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
    runSubtitle:   (body) => run('subtitle', 'process/subtitle',       body),
    runBurnSub:    (body) => run('burn',     'process/burn-subtitle',  body),
    runReupClean:  (body) => run('reup',     'process/reup-clean',     body),
    runPackageZip: (body) => run('zip',      'process/package-zip',    body),
    runFrameThumb: (body) => run('thumb',    'process/frame-thumb',    body),
    getState,
    reset: (key) => setStates(prev => ({ ...prev, [key]: { loading: false, error: null, result: null } })),
  };
}
