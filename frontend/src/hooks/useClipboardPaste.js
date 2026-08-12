import { useState, useCallback } from 'react';

const SUPPORTED_HOSTS = [
  'youtube.com', 'youtu.be', 'tiktok.com', 'vm.tiktok.com', 'douyin.com',
  'facebook.com', 'fb.watch', 'instagram.com', 'threads.net',
  'spotify.com', 'soundcloud.com', 'reddit.com', 'twitter.com', 'x.com',
  'bilibili.com', 'vk.com', 'twitch.tv', 'rumble.com', 'dailymotion.com',
  'pinterest.com', 'snapchat.com', 'lemon8-app.com',
];

function extractSupportedURL(text) {
  if (!text) return null;
  const m = text.match(/https?:\/\/[^\s<>"{}|\\^[\]]+/);
  if (!m) return null;
  try {
    const { hostname } = new URL(m[0]);
    return SUPPORTED_HOSTS.some((h) => hostname === h || hostname.endsWith('.' + h))
      ? m[0]
      : null;
  } catch {
    return null;
  }
}

export function useClipboardPaste() {
  const [clipboardURL, setClipboardURL] = useState(null);
  const [hasSuggestion, setHasSuggestion] = useState(false);

  const checkClipboard = useCallback(async () => {
    try {
      if (!navigator.clipboard?.readText) return null;
      const text = await navigator.clipboard.readText();
      const url = extractSupportedURL(text);
      if (url) {
        setClipboardURL(url);
        setHasSuggestion(true);
        return url;
      }
    } catch {
      // Permission denied — silent fail
    }
    setHasSuggestion(false);
    setClipboardURL(null);
    return null;
  }, []);

  const clearSuggestion = useCallback(() => {
    setHasSuggestion(false);
    setClipboardURL(null);
  }, []);

  return { clipboardURL, hasSuggestion, checkClipboard, clearSuggestion };
}
