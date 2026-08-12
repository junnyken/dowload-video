/**
 * usePresets — Phase 21 Preset Management Hook
 * ==============================================
 * Manages user presets with:
 *   - localStorage cache for anonymous users
 *   - API sync for authenticated users
 *   - System presets (always available, not editable)
 *
 * Usage:
 *   const { presets, systemPresets, loading, createPreset, deletePreset, setDefault } = usePresets();
 */

import { useState, useEffect, useCallback } from 'react';
import { supabase } from '../lib/supabaseClient';

const LOCAL_KEY = 'vg_user_presets';
const API = `${import.meta.env.VITE_API_URL || ''}/api/v1`;

const SYSTEM_PRESETS = [
  {
    id: 'sys_tiktok_clean',
    name: 'TikTok sạch watermark',
    platform: 'tiktok',
    settings: { remove_watermark: true, quality: 'video' },
    is_system: true,
    sort_order: 0,
  },
  {
    id: 'sys_spotify_mp3',
    name: 'Spotify → MP3',
    platform: 'spotify',
    settings: { quality: 'mp3_320' },
    is_system: true,
    sort_order: 1,
  },
  {
    id: 'sys_hd_video',
    name: 'Video HD 1080p',
    platform: null,
    settings: { quality: '1080', format: 'mp4' },
    is_system: true,
    sort_order: 2,
  },
  {
    id: 'sys_mp3_fast',
    name: 'Trích âm thanh MP3',
    platform: null,
    settings: { quality: 'mp3_128' },
    is_system: true,
    sort_order: 3,
  },
  {
    id: 'sys_thumbnail',
    name: 'Chỉ lấy thumbnail',
    platform: null,
    settings: { quality: 'thumbnail_only' },
    is_system: true,
    sort_order: 4,
  },
];

async function getAuthToken() {
  try {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || null;
  } catch {
    return null;
  }
}

async function apiRequest(path, opts = {}) {
  const token = await getAuthToken();
  if (!token) throw new Error('Not authenticated');
  const res = await fetch(`${API}${path}`, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
      ...(opts.headers || {}),
    },
  });
  if (!res.ok) throw new Error(`API ${res.status}`);
  return res.status === 204 ? null : res.json();
}

function loadLocalPresets() {
  try {
    return JSON.parse(localStorage.getItem(LOCAL_KEY) || '[]');
  } catch {
    return [];
  }
}

function saveLocalPresets(presets) {
  try {
    localStorage.setItem(LOCAL_KEY, JSON.stringify(presets));
  } catch {}
}

export function usePresets() {
  const [userPresets, setUserPresets] = useState([]);
  const [loading, setLoading] = useState(false);
  const [isAuth, setIsAuth] = useState(false);

  // Check auth state
  useEffect(() => {
    supabase.auth.getSession().then(({ data: { session } }) => {
      setIsAuth(!!session);
    });
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setIsAuth(!!session);
    });
    return () => subscription.unsubscribe();
  }, []);

  // Load presets
  const loadPresets = useCallback(async () => {
    if (isAuth) {
      setLoading(true);
      try {
        const data = await apiRequest('/presets');
        setUserPresets(data.user || []);
      } catch {
        // Fall back to local
        setUserPresets(loadLocalPresets());
      } finally {
        setLoading(false);
      }
    } else {
      setUserPresets(loadLocalPresets());
    }
  }, [isAuth]);

  useEffect(() => {
    loadPresets();
  }, [loadPresets]);

  const createPreset = useCallback(async (preset) => {
    if (isAuth) {
      const created = await apiRequest('/presets', {
        method: 'POST',
        body: JSON.stringify(preset),
      });
      setUserPresets((prev) => [...prev, created]);
      return created;
    } else {
      const newPreset = {
        ...preset,
        id: `local_${Date.now()}`,
        created_at: new Date().toISOString(),
        is_system: false,
      };
      setUserPresets((prev) => {
        const next = [...prev, newPreset];
        saveLocalPresets(next);
        return next;
      });
      return newPreset;
    }
  }, [isAuth]);

  const updatePreset = useCallback(async (id, updates) => {
    if (isAuth) {
      const updated = await apiRequest(`/presets/${id}`, {
        method: 'PUT',
        body: JSON.stringify(updates),
      });
      setUserPresets((prev) => prev.map((p) => (p.id === id ? updated : p)));
      return updated;
    } else {
      setUserPresets((prev) => {
        const next = prev.map((p) => p.id === id ? { ...p, ...updates } : p);
        saveLocalPresets(next);
        return next;
      });
    }
  }, [isAuth]);

  const deletePreset = useCallback(async (id) => {
    if (isAuth) {
      await apiRequest(`/presets/${id}`, { method: 'DELETE' });
    }
    setUserPresets((prev) => {
      const next = prev.filter((p) => p.id !== id);
      if (!isAuth) saveLocalPresets(next);
      return next;
    });
  }, [isAuth]);

  const setDefaultForPlatform = useCallback(async (id, platform) => {
    if (isAuth) {
      await apiRequest(`/presets/${id}/default`, { method: 'POST' });
    }
    setUserPresets((prev) => {
      const next = prev.map((p) => ({
        ...p,
        is_default: p.platform === platform ? p.id === id : p.is_default,
      }));
      if (!isAuth) saveLocalPresets(next);
      return next;
    });
  }, [isAuth]);

  const createFromJob = useCallback(async (jobId, name) => {
    if (!isAuth) return null;
    const created = await apiRequest(`/presets/from-job?job_id=${encodeURIComponent(jobId)}&name=${encodeURIComponent(name)}`, {
      method: 'POST',
    });
    setUserPresets((prev) => [...prev, created]);
    return created;
  }, [isAuth]);

  const getPresetForPlatform = useCallback((platform) => {
    return userPresets.find((p) => p.platform === platform && p.is_default)
      || userPresets.find((p) => p.platform === platform)
      || null;
  }, [userPresets]);

  return {
    systemPresets:         SYSTEM_PRESETS,
    userPresets,
    allPresets:            [...SYSTEM_PRESETS, ...userPresets],
    loading,
    isAuth,
    createPreset,
    updatePreset,
    deletePreset,
    setDefaultForPlatform,
    createFromJob,
    getPresetForPlatform,
    reload: loadPresets,
  };
}
