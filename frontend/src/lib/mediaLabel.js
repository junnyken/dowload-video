// Quality #3 (27/10/2026): the label under a finished download, built ONLY from
// what the backend measured on the file (ffprobe, `downloaded_media`). Nothing
// measured → the old height/MP3 label, and never a number that was not measured.

const CODEC = { h264: 'H.264', hevc: 'HEVC', vp9: 'VP9', av1: 'AV1', aac: 'AAC', mp3: 'MP3', opus: 'Opus', vorbis: 'Vorbis', flac: 'FLAC' };

const resLabel = (h) => (h >= 2160 ? '4K' : h >= 1440 ? '2K' : `${h}p`);

/** Parts of the quality label, e.g. ["2160p", "VP9", "60 fps", "12 Mbps", "HDR"]. */
export function mediaParts(info) {
  const m = info?.downloaded_media;
  if (m && m.measured) {
    const parts = [];
    if (m.vcodec) {
      if (m.short_side) parts.push(resLabel(m.short_side));
      parts.push(CODEC[m.vcodec] || m.vcodec.toUpperCase());
      if (m.fps) parts.push(`${Math.round(m.fps)} fps`);
      if (m.bitrate_kbps) parts.push(m.bitrate_kbps >= 1000 ? `${(m.bitrate_kbps / 1000).toFixed(1)} Mbps` : `${m.bitrate_kbps} kbps`);
      if (m.hdr) parts.push('HDR');
    } else if (m.acodec) {
      parts.push(CODEC[m.acodec] || m.acodec.toUpperCase());
      const kbps = m.audio_bitrate_kbps || m.bitrate_kbps;
      if (kbps) parts.push(`${kbps} kbps`);
    }
    return parts;
  }
  if (info?.is_audio_only) {
    const q = /^mp3_(\d+)$/.exec(String(info.quality || ''));
    return q ? [`MP3 ${q[1]} kbps`] : [];
  }
  const h = Number(info?.downloaded_height) || 0;
  return h > 0 ? [resLabel(h)] : [];
}

/** True when the measured video codec may not open on older devices (anything but H.264). */
export function needsModernPlayer(info) {
  const m = info?.downloaded_media;
  return !!(m && m.measured && m.vcodec && m.vcodec !== 'h264');
}
