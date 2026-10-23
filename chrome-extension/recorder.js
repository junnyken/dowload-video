/**
 * VidGrab recording mode (5.20.0) — runs in the offscreen page.
 * The last resort when a page has no downloadable file: the tab's picture and
 * sound (chrome.tabCapture stream id from the service worker) are recorded
 * with MediaRecorder while the video plays, then handed back as a blob: URL
 * for chrome.downloads. The tab keeps playing its sound for the person
 * (captured audio is routed back to the speakers). DRM players come out
 * black — that protection is not worked around.
 */
(function () {
  'use strict';
  let rec = null, stream = null, chunks = [], audioCtx = null, lastUrl = null;

  async function start(streamId) {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId } },
      video: { mandatory: { chromeMediaSource: 'tab', chromeMediaSourceId: streamId, maxWidth: 1920, maxHeight: 1080, maxFrameRate: 30 } },
    });
    // tab capture mutes the tab; play its sound back so the person still hears it
    if (stream.getAudioTracks().length) {
      audioCtx = new AudioContext();
      audioCtx.createMediaStreamSource(stream).connect(audioCtx.destination);
    }
    const type = ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t));
    chunks = [];
    rec = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 5_000_000 });
    rec.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
    rec.start(1000);
  }

  function stop() {
    return new Promise((resolve) => {
      if (!rec) { resolve(null); return; }
      rec.onstop = () => {
        const blob = new Blob(chunks, { type: 'video/webm' });
        chunks = [];
        stream.getTracks().forEach((t) => t.stop());
        if (audioCtx) audioCtx.close().catch(() => {});
        rec = null; stream = null; audioCtx = null;
        lastUrl = URL.createObjectURL(blob);
        resolve({ url: lastUrl, bytes: blob.size });
      };
      rec.stop();
    });
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg || msg.target !== 'offscreen') return false;
    if (msg.type === 'REC_START') {
      start(msg.streamId).then(() => sendResponse({ ok: true }), (e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
      return true;
    }
    if (msg.type === 'REC_STOP') { stop().then((r) => sendResponse(r || {})); return true; }
    if (msg.type === 'REC_RELEASE') { if (lastUrl) URL.revokeObjectURL(lastUrl); lastUrl = null; sendResponse({ ok: true }); }
    return false;
  });
})();
