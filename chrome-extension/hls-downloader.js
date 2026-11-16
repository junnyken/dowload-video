/**
 * VidGrab HLS downloader — runs in offscreen.html.
 * ================================================
 * Downloads or records an HLS stream in the user's own browser, the way the
 * page's player fetches it, and saves one MP4:
 *   - media playlist (the chosen variant of a master), RFC 8216
 *   - segments fetched in parallel (POOL at a time for VOD), each retried with backoff
 *   - standard RFC 8216 AES-128-CBC and Apple SAMPLE-AES (KEYFORMAT="identity",
 *     HTTP/S key URI) decryption via WebCrypto, cached per key URI
 *   - non-DRM live HLS recording (sliding window, media-sequence deduplication,
 *     user "Stop & save", #EXT-X-ENDLIST, or configurable idle-poll stop)
 *   - fMP4 streams (EXT-X-MAP or ISO-BMFF boxes): init + segments, appended directly
 *   - MPEG-TS streams (including disguised image/text segment extensions with
 *     fake headers before 0x47 sync bytes): remuxed to MP4 with mux.js (no re-encode)
 *   - written to the extension's private disk (OPFS), not held in RAM
 * Never: DRM streams (Widevine, PlayReady, FairPlay skd:// or
 * com.apple.streamingkeydelivery, SAMPLE-AES-CTR/CENC, or any KEYFORMAT !== "identity"),
 * or variants whose audio is a separate playlist (sent to VidGrab instead).
 *
 * Messages (chrome.runtime, target 'offscreen'):
 *   in : HLS_START {job}, HLS_STOP {id}, HLS_CANCEL {id}, HLS_RELEASE {id}
 *   out: HLS_PROGRESS {id, done, total, bytes, live?, elapsedSec?, height?},
 *        HLS_DONE {id, url, bytes, ext, live?, elapsedSec?},
 *        HLS_FAILED {id, reason, drmSystem?, encryptionReason?, message}
 */
(function (root) {
  'use strict';

  const POOL = 6;
  const RETRIES = 3;
  const DEFAULT_MAX_EMPTY_POLLS = 3;
  const MAX_SEGMENTS = 20000;
  const MAX_FAKE_HEADER_SCAN = 8192;
  const jobs = new Map();          // id -> {cancelled, stopAndSave, wake, url (blob), name}

  class JobError extends Error {
    constructor(reason, message, extra) {
      super(message || reason);
      this.reason = reason;
      if (extra && typeof extra === 'object') Object.assign(this, extra);
    }
  }

  const hasChrome = typeof chrome !== 'undefined' && chrome.runtime && typeof chrome.runtime.sendMessage === 'function';
  const send = (msg) => (hasChrome ? chrome.runtime.sendMessage({ ...msg, target: 'sw' }).catch(() => {}) : Promise.resolve());

  function interruptibleWait(ms, state) {
    if (!ms || ms <= 0) return Promise.resolve();
    return new Promise((res) => {
      const done = () => {
        clearTimeout(timer);
        if (state && state._waiters) state._waiters.delete(done);
        res();
      };
      const timer = setTimeout(done, ms);
      if (state) {
        if (!state._waiters) state._waiters = new Set();
        state._waiters.add(done);
        if (state.cancelled || state.stopAndSave) done();
      }
    });
  }

  function wakeJob(state) {
    if (!state || !state._waiters) return;
    for (const fn of [...state._waiters]) fn();
  }

  async function ensureHostReferer(url, job) {
    if (!hasChrome || !job || !job.referer) return;
    let host = '';
    try { host = new URL(url).hostname.toLowerCase(); } catch { return; }
    if (!host) return;
    if (!job._seenHosts) job._seenHosts = new Set();
    if (job._seenHosts.has(host)) return;
    job._seenHosts.add(host);
    await chrome.runtime.sendMessage({ type: 'VG_ENSURE_REFERER', url, referer: job.referer }).catch(() => {});
  }

  function parseRetryAfterMs(headerVal, nowMs = Date.now()) {
    const raw = String(headerVal || '').trim();
    if (!raw) return 0;
    if (/^\d+(?:\.\d+)?$/.test(raw)) {
      const sec = parseFloat(raw);
      if (!Number.isFinite(sec) || sec <= 0) return 500;
      return Math.min(15000, Math.max(500, Math.round(sec * 1000)));
    }
    const at = Date.parse(raw);
    if (Number.isFinite(at)) {
      const diff = at - nowMs;
      return diff > 0 ? Math.min(15000, Math.max(500, Math.round(diff))) : 500;
    }
    return 0;
  }

  async function fetchBytes(url, range, job, state) {
    await ensureHostReferer(url, job);
    const maxRetries = Math.max(1, Number(job && job.retries) || RETRIES);
    const baseDelay = Math.max(5, Number(job && job.retryBaseMs) || 400);
    const segTimeoutMs = Math.max(500, Number(job && job.segmentTimeoutMs) || 25_000);
    let lastErr;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      if (job && job.cancelled) throw new JobError('cancelled');
      const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
      const timer = ctrl ? setTimeout(() => ctrl.abort(), segTimeoutMs) : null;
      try {
        const t0 = Date.now();
        const headers = range ? { Range: `bytes=${range[0]}-${range[0] + range[1] - 1}` } : {};
        const r = await fetch(url, {
          credentials: 'include',
          headers,
          cache: 'no-store',
          ...(ctrl ? { signal: ctrl.signal } : {}),
        });
        if (r.status === 429 || r.status === 503) {
          const raHeader = r.headers && typeof r.headers.get === 'function' ? r.headers.get('retry-after') : '';
          const raMs = parseRetryAfterMs(raHeader);
          if (state && typeof state.onThrottle === 'function') {
            state.onThrottle(raMs || baseDelay * (attempt + 1) ** 2, r.status);
          }
          throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status, retryAfterMs: raMs });
        }
        if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status });
        const ab = await (ctrl ? new Promise((resolveBuf, rejectBuf) => {
          if (ctrl.signal.aborted) { rejectBuf(new Error('segment timeout')); return; }
          const onAbort = () => rejectBuf(new Error('segment timeout'));
          ctrl.signal.addEventListener('abort', onAbort, { once: true });
          r.arrayBuffer().then(
            (buf) => { ctrl.signal.removeEventListener('abort', onAbort); resolveBuf(buf); },
            (err) => { ctrl.signal.removeEventListener('abort', onAbort); rejectBuf(err); },
          );
        }) : r.arrayBuffer());
        if (job && job.cancelled) throw new JobError('cancelled');
        const bytes = new Uint8Array(ab);
        if (attempt === 0 && state && typeof state.onCleanSegment === 'function') {
          state.onCleanSegment(Date.now() - t0, bytes.byteLength);
        }
        return bytes;
      } catch (e) {
        if (job && job.cancelled) throw new JobError('cancelled');
        if (e instanceof JobError && e.reason === 'cancelled') throw e;
        lastErr = e;
        if (attempt === 0 && !(e && e.retryAfterMs) && state && typeof state.onSegmentRetry === 'function') {
          state.onSegmentRetry((e && e.status) || 0);
        }
        if (attempt + 1 < maxRetries) {
          const waitMs = (e && e.retryAfterMs) ? e.retryAfterMs : baseDelay * (attempt + 1) ** 2;
          await interruptibleWait(waitMs, state);
          if (state && state.stopAndSave) break;
        }
      } finally {
        if (timer) clearTimeout(timer);
      }
    }
    throw new JobError('segment_failed', String((lastErr && lastErr.message) || lastErr), {
      status: (lastErr && lastErr.status) || 0,
    });
  }

  async function fetchText(url, job, state) {
    return new TextDecoder().decode(await fetchBytes(url, null, job, state));
  }

  function resolve(base, ref) { return new URL(ref, base).toString(); }

  function attrs(line) {
    const out = {};
    const body = line.slice(line.indexOf(':') + 1);
    const re = /([A-Z0-9-]+)=("[^"]*"|[^,]*)/g;
    let m;
    while ((m = re.exec(body))) out[m[1]] = m[2].replace(/^"|"$/g, '');
    return out;
  }

  /** "len@offset" -> [offset, len]; a missing offset continues the previous range. */
  function byterange(v, prevEnd) {
    const [len, off] = String(v).split('@').map(Number);
    return [Number.isFinite(off) ? off : prevEnd, len];
  }

  /** RFC 8216 §4.3.2.4: 128-bit big-endian IV from a segment's media sequence number. */
  function seqToIv(seq) {
    const iv = new Uint8Array(16);
    const n = Math.max(0, Math.floor(Number(seq) || 0));
    const hi = Math.floor(n / 0x100000000) >>> 0;
    const lo = n >>> 0;
    iv[8] = (hi >>> 24) & 0xff;
    iv[9] = (hi >>> 16) & 0xff;
    iv[10] = (hi >>> 8) & 0xff;
    iv[11] = hi & 0xff;
    iv[12] = (lo >>> 24) & 0xff;
    iv[13] = (lo >>> 16) & 0xff;
    iv[14] = (lo >>> 8) & 0xff;
    iv[15] = lo & 0xff;
    return iv;
  }

  /** Parses `0x...` hex IV into a 16-byte Uint8Array. */
  function parseHexIv(raw) {
    const m = /^0x([0-9a-f]{1,32})$/i.exec(String(raw || '').trim());
    if (!m) throw new JobError('encrypted', 'IV không hợp lệ.');
    const hex = m[1].padStart(32, '0');
    const out = new Uint8Array(16);
    for (let i = 0; i < 16; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
    return out;
  }

  function detectDrmSystem(fmt, uri) {
    const f = String(fmt || '').toLowerCase();
    const u = String(uri || '').toLowerCase();
    if (f === 'com.apple.streamingkeydelivery' || /^skd:/.test(u) || f.includes('94ce86fb-07ff-4f43-adb8-93d2fa968ca2')) {
      return 'FairPlay';
    }
    if (f.includes('edef8ba9-79d6-4ace-a3c8-27dcd51d21ed') || f.includes('widevine') || u.includes('widevine')) {
      return 'Widevine';
    }
    if (f.includes('9a04f079-9840-4286-ab92-e65be0885f95') || f.includes('playready') || f.includes('com.microsoft')) {
      return 'PlayReady';
    }
    if (f.includes('e2719d58-a985-b3c9-781a-b030af78d30e') || f.includes('1077efec-c0b2-4d02-ace3-3c1e52e2fb4b') || f.includes('clearkey')) {
      return 'ClearKey DRM';
    }
    return null;
  }

  /**
   * Classifies `#EXT-X-KEY` / `#EXT-X-SESSION-KEY`:
   *  - METHOD=NONE -> null
   *  - AES-128 or SAMPLE-AES with KEYFORMAT="identity" (or omitted) and http(s) URI
   *    -> { method: 'aes128' | 'sample_aes', uri, iv, encryptionReason }
   *  - FairPlay (skd://, com.apple.streamingkeydelivery), Widevine/PlayReady,
   *    SAMPLE-AES-CTR/CENC, or any KEYFORMAT !== "identity" / non-http(s) URI
   *    -> throws JobError('drm', ..., { drmSystem, encryptionReason })
   */
  function parseKeyAttr(a, baseUrl) {
    const method = String((a && a.METHOD) || 'NONE').trim().toUpperCase();
    if (method === 'NONE') return null;
    const rawFmt = String((a && a.KEYFORMAT) || '').trim();
    const fmt = (rawFmt || 'identity').toLowerCase();
    const rawUri = String((a && a.URI) || '').trim();

    if (fmt === 'com.apple.streamingkeydelivery' || /^skd:/i.test(rawUri)) {
      const encryptionReason = /^skd:/i.test(rawUri)
        ? 'FairPlay (URI skd://)'
        : 'FairPlay (KEYFORMAT=com.apple.streamingkeydelivery)';
      throw new JobError('drm', 'Nội dung được bảo vệ bằng FairPlay DRM.', { drmSystem: 'FairPlay', encryptionReason });
    }
    if (fmt !== 'identity') {
      const drmSystem = detectDrmSystem(fmt, rawUri) || 'DRM';
      const encryptionReason = `${drmSystem} (KEYFORMAT=${rawFmt})`;
      throw new JobError('drm', `Nội dung được bảo vệ bằng ${drmSystem}.`, { drmSystem, encryptionReason });
    }
    if (method === 'SAMPLE-AES-CTR' || method === 'SAMPLE-AES-CENC') {
      const drmSystem = detectDrmSystem(fmt, rawUri) || 'CENC DRM';
      const encryptionReason = `${drmSystem} (METHOD=${method})`;
      throw new JobError('drm', `Nội dung được bảo vệ bằng ${drmSystem}.`, { drmSystem, encryptionReason });
    }
    if (!rawUri) {
      throw new JobError('encrypted', 'Thiếu đường dẫn khóa giải mã.');
    }
    let resolvedUri = '';
    try { resolvedUri = resolve(baseUrl || 'https://example.invalid/', rawUri); } catch { resolvedUri = rawUri; }
    if (!/^https?:\/\//i.test(resolvedUri)) {
      const drmSystem = detectDrmSystem(fmt, rawUri) || 'DRM';
      const scheme = (rawUri.match(/^([a-z0-9+.-]+):/i) || [])[1] || 'non-http';
      const encryptionReason = `${drmSystem} (URI ${scheme}://)`;
      throw new JobError('drm', `Nội dung được bảo vệ bằng ${drmSystem}.`, { drmSystem, encryptionReason });
    }
    if (method === 'AES-128') {
      return {
        method: 'aes128',
        uri: resolvedUri,
        iv: a.IV ? parseHexIv(a.IV) : null,
        encryptionReason: `AES-128 (${rawFmt ? 'KEYFORMAT=identity' : 'default identity'}, HTTP/S key)`,
      };
    }
    if (method === 'SAMPLE-AES') {
      return {
        method: 'sample_aes',
        uri: resolvedUri,
        iv: a.IV ? parseHexIv(a.IV) : null,
        encryptionReason: `SAMPLE-AES (${rawFmt ? 'KEYFORMAT=identity' : 'default identity'}, HTTP/S key)`,
      };
    }
    throw new JobError('drm', 'Phương thức bảo vệ DRM không thể tải.', {
      drmSystem: 'DRM',
      encryptionReason: `DRM (METHOD=${method})`,
    });
  }

  async function ensureCryptoKey(keyOrRaw, needEncrypt) {
    const subtle = root.crypto && root.crypto.subtle;
    if (!subtle) throw new JobError('encrypted', 'WebCrypto unavailable.');
    if (keyOrRaw instanceof Uint8Array || ArrayBuffer.isView(keyOrRaw)) {
      if (keyOrRaw.byteLength !== 16) throw new JobError('encrypted', 'Invalid AES-128 key length.');
      const usages = needEncrypt ? ['encrypt', 'decrypt'] : ['decrypt'];
      return subtle.importKey('raw', keyOrRaw, { name: 'AES-CBC' }, false, usages);
    }
    return keyOrRaw;
  }

  /** Decrypts a PKCS#7-padded AES-128-CBC segment using WebCrypto (`CryptoKey` or raw 16-byte `Uint8Array`). */
  async function decryptSegment(u8, keyOrRaw, iv) {
    const subtle = root.crypto && root.crypto.subtle;
    const ck = await ensureCryptoKey(keyOrRaw, false);
    try {
      const plain = await subtle.decrypt({ name: 'AES-CBC', iv }, ck, u8);
      return new Uint8Array(plain);
    } catch {
      throw new JobError('encrypted', 'Không giải mã được đoạn video.');
    }
  }

  /**
   * Decrypts `16 * N` bytes of unpadded AES-128-CBC ciphertext using WebCrypto
   * by synthesizing the exact 16-byte `0x10` PKCS#7 ciphertext block from `lastBlock`.
   */
  async function decryptAesCbcRaw(u8, keyOrRaw, iv) {
    if (!u8 || u8.byteLength === 0) return new Uint8Array(0);
    if (u8.byteLength % 16 !== 0) throw new JobError('encrypted', 'Invalid unpadded AES-CBC block length.');
    const subtle = root.crypto && root.crypto.subtle;
    const ck = await ensureCryptoKey(keyOrRaw, true);
    try {
      const lastBlock = u8.subarray(u8.byteLength - 16);
      const padPlain = new Uint8Array(16);
      padPlain.fill(16);
      const encPad = new Uint8Array(await subtle.encrypt({ name: 'AES-CBC', iv: lastBlock }, ck, padPlain));
      const withPad = new Uint8Array(u8.byteLength + 16);
      withPad.set(u8, 0);
      withPad.set(encPad.subarray(0, 16), u8.byteLength);
      const plain = await subtle.decrypt({ name: 'AES-CBC', iv }, ck, withPad);
      return new Uint8Array(plain);
    } catch {
      throw new JobError('encrypted', 'Không giải mã được mẫu SAMPLE-AES.');
    }
  }

  /** MPEG-2 PSI CRC-32 (poly 0x04C11DB7, init 0xFFFFFFFF, MSB-first, no XOR out). */
  function mpeg2Crc32(bytes) {
    let crc = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) {
      crc ^= (bytes[i] & 0xff) << 24;
      for (let b = 0; b < 8; b++) {
        crc = (crc & 0x80000000) ? (((crc << 1) ^ 0x04c11db7) >>> 0) : ((crc << 1) >>> 0);
      }
    }
    return crc >>> 0;
  }

  function unescapeRbsp(nal) {
    const tmp = new Uint8Array(nal.length);
    tmp[0] = nal[0];
    let w = 1;
    for (let r = 1; r < nal.length; r++) {
      if (r + 3 < nal.length && nal[r] === 0 && nal[r + 1] === 0 && nal[r + 2] === 3 && nal[r + 3] <= 3) {
        tmp[w++] = 0;
        tmp[w++] = 0;
        r += 2;
      } else {
        tmp[w++] = nal[r];
      }
    }
    return tmp.subarray(0, w);
  }

  function escapeRbsp(uNal) {
    const out = [uNal[0]];
    let zeros = 0;
    for (let i = 1; i < uNal.length; i++) {
      const b = uNal[i];
      if (zeros === 2 && b <= 3) {
        out.push(3);
        zeros = 0;
      }
      out.push(b);
      zeros = b === 0 ? zeros + 1 : 0;
    }
    return new Uint8Array(out);
  }

  function splitAnnexBNals(es) {
    const starts = [];
    let i = 0;
    while (i + 3 <= es.length) {
      if (es[i] === 0 && es[i + 1] === 0) {
        if (es[i + 2] === 1) {
          starts.push({ scOff: i, scLen: 3 });
          i += 3;
          continue;
        }
        if (i + 4 <= es.length && es[i + 2] === 0 && es[i + 3] === 1) {
          starts.push({ scOff: i, scLen: 4 });
          i += 4;
          continue;
        }
      }
      i++;
    }
    if (!starts.length) return null;
    const prefix = es.subarray(0, starts[0].scOff);
    const units = starts.map((s, idx) => {
      const bodyStart = s.scOff + s.scLen;
      const bodyEnd = idx + 1 < starts.length ? starts[idx + 1].scOff : es.length;
      return { sc: es.subarray(s.scOff, bodyStart), body: es.subarray(bodyStart, bodyEnd) };
    });
    return { prefix, units };
  }

  async function decryptAvcPes(pes, ck, iv) {
    if (pes.length < 9 || pes[0] !== 0 || pes[1] !== 0 || pes[2] !== 1) return pes;
    const pesHdrLen = 9 + pes[8];
    if (pesHdrLen >= pes.length) return pes;
    const split = splitAnnexBNals(pes.subarray(pesHdrLen));
    if (!split) return pes;

    let changed = false;
    let totalEs = split.prefix.length;
    const outUnits = [];
    for (const u of split.units) {
      let body = u.body;
      if (body.length > 48) {
        const nalType = body[0] & 0x1f;
        if (nalType === 1 || nalType === 5) {
          const unesc = new Uint8Array(unescapeRbsp(body));
          if (unesc.length > 48) {
            const offsets = [];
            for (let pos = 32; unesc.length - pos > 16; pos += 160) offsets.push(pos);
            if (offsets.length) {
              const cipher = new Uint8Array(offsets.length * 16);
              offsets.forEach((off, idx) => cipher.set(unesc.subarray(off, off + 16), idx * 16));
              const plain = await decryptAesCbcRaw(cipher, ck, iv);
              offsets.forEach((off, idx) => unesc.set(plain.subarray(idx * 16, (idx + 1) * 16), off));
              body = escapeRbsp(unesc);
              changed = true;
            }
          }
        }
      }
      outUnits.push({ sc: u.sc, body });
      totalEs += u.sc.length + body.length;
    }
    if (!changed) return pes;

    const out = new Uint8Array(pesHdrLen + totalEs);
    out.set(pes.subarray(0, pesHdrLen), 0);
    // Update PES_packet_length (0 = unbounded for video elementary streams in TS)
    const origPesLen = (pes[4] << 8) | pes[5];
    if (origPesLen !== 0) {
      const newPesLen = out.length - 6;
      if (newPesLen <= 0xffff) {
        out[4] = (newPesLen >> 8) & 0xff;
        out[5] = newPesLen & 0xff;
      } else {
        out[4] = 0;
        out[5] = 0;
      }
    }
    let w = pesHdrLen;
    if (split.prefix.length) { out.set(split.prefix, w); w += split.prefix.length; }
    for (const u of outUnits) {
      out.set(u.sc, w); w += u.sc.length;
      out.set(u.body, w); w += u.body.length;
    }
    return out;
  }

  async function decryptAdtsPes(pes, ck, iv) {
    if (pes.length < 9 || pes[0] !== 0 || pes[1] !== 0 || pes[2] !== 1) return pes;
    const pesHdrLen = 9 + pes[8];
    const out = new Uint8Array(pes);
    let pos = pesHdrLen;
    while (pos + 7 <= out.length) {
      if (out[pos] !== 0xff || (out[pos + 1] & 0xf0) !== 0xf0) { pos++; continue; }
      const adtsHdr = (out[pos + 1] & 0x01) ? 7 : 9;
      const frameLen = ((out[pos + 3] & 0x03) << 11) | (out[pos + 4] << 3) | ((out[pos + 5] >> 5) & 0x07);
      if (frameLen < adtsHdr || pos + frameLen > out.length) break;
      const clearLead = adtsHdr + 16;
      if (frameLen >= clearLead + 16) {
        const encLen = Math.floor((frameLen - clearLead) / 16) * 16;
        const encStart = pos + clearLead;
        const plain = await decryptAesCbcRaw(out.subarray(encStart, encStart + encLen), ck, iv);
        out.set(plain, encStart);
      }
      pos += frameLen;
    }
    return out;
  }

  function packetizePes(pid, pesBytes, firstPktTemplate) {
    const pkts = [];
    let offset = 0;
    let first = true;
    while (offset < pesBytes.length) {
      const pkt = new Uint8Array(188);
      pkt[0] = 0x47;
      pkt[1] = (first ? 0x40 : 0x00) | ((pid >> 8) & 0x1f);
      pkt[2] = pid & 0xff;
      let payStart = 4;
      if (first && firstPktTemplate) {
        const tplAfc = (firstPktTemplate[3] >> 4) & 0x03;
        if (tplAfc === 3) {
          const afLen = firstPktTemplate[4];
          if (5 + afLen < 188) {
            // Keep PCR / random_access_indicator flags, strip any old stuffing bytes if PES grew
            const flags = firstPktTemplate[5];
            const minAfBytes = (flags & 0x10) ? 7 : 1; // flags (1) + optional PCR (6)
            const keepAfLen = Math.min(afLen, minAfBytes);
            pkt[3] = 0x30;
            pkt[4] = keepAfLen;
            pkt.set(firstPktTemplate.subarray(5, 5 + keepAfLen), 5);
            payStart = 5 + keepAfLen;
          }
        }
      }
      const rem = pesBytes.length - offset;
      const cap = 188 - payStart;
      if (rem >= cap) {
        if (payStart === 4) pkt[3] = 0x10;
        pkt.set(pesBytes.subarray(offset, offset + cap), payStart);
        offset += cap;
      } else {
        const stuff = cap - rem;
        if (payStart === 4) {
          pkt[3] = 0x30;
          if (stuff === 1) {
            pkt[4] = 0;
          } else {
            pkt[4] = stuff - 1;
            pkt[5] = 0x00;
            pkt.fill(0xff, 6, 4 + stuff);
          }
        } else {
          const oldAfLen = pkt[4];
          const newAfLen = oldAfLen + stuff;
          pkt[4] = newAfLen;
          pkt.fill(0xff, 5 + oldAfLen, 5 + newAfLen);
        }
        pkt.set(pesBytes.subarray(offset), 188 - rem);
        offset += rem;
      }
      first = false;
      pkts.push(pkt);
    }
    return pkts;
  }

  /**
   * Decrypts an Apple HLS `SAMPLE-AES` (`KEYFORMAT="identity"`) MPEG-TS segment:
   *  - Rewrites PMT stream types `0xDB -> 0x1B` (H.264) & `0xCF -> 0x0F` (ADTS AAC) + CRC32
   *  - Decrypts H.264 NAL types 1 & 5 (32B clear + 1:9 AES-128-CBC + RBSP unescape/escape)
   *  - Decrypts ADTS AAC frames (header + 16B clear + AES-128-CBC)
   */
  async function decryptSampleAesTs(tsU8, keyOrRaw, iv) {
    const clean = stripDisguisedTsPrefix(tsU8);
    const numPkts = Math.floor(clean.length / 188);
    if (!numPkts) return clean;
    const ck = await ensureCryptoKey(keyOrRaw, true);
    const buf = new Uint8Array(clean.subarray(0, numPkts * 188));

    const payloadOff = (pOff) => {
      if (buf[pOff] !== 0x47) return -1;
      const afc = (buf[pOff + 3] >> 4) & 0x03;
      if (afc === 0 || afc === 2) return -1;
      const off = afc === 3 ? pOff + 5 + buf[pOff + 4] : pOff + 4;
      return off < pOff + 188 ? off : -1;
    };

    let pmtPid = -1;
    const videoPids = new Set();
    const audioPids = new Set();

    // Pass 1: PAT -> PMT -> elementary PIDs + normalize 0xDB/0xCF stream_type + PSI CRC32
    for (let p = 0; p < numPkts; p++) {
      const pOff = p * 188;
      const pid = ((buf[pOff + 1] & 0x1f) << 8) | buf[pOff + 2];
      const pusi = (buf[pOff + 1] & 0x40) !== 0;
      const pay = payloadOff(pOff);
      if (pay < 0 || !pusi) continue;
      if (pid === 0) {
        const sec = pay + 1 + buf[pay];
        if (sec + 12 <= pOff + 188 && buf[sec] === 0x00) {
          const secLen = ((buf[sec + 1] & 0x0f) << 8) | buf[sec + 2];
          const end = Math.min(pOff + 188, sec + 3 + secLen - 4);
          for (let k = sec + 8; k + 4 <= end; k += 4) {
            const prog = (buf[k] << 8) | buf[k + 1];
            if (prog !== 0) { pmtPid = ((buf[k + 2] & 0x1f) << 8) | buf[k + 3]; break; }
          }
        }
      } else if (pid === pmtPid) {
        const sec = pay + 1 + buf[pay];
        if (sec + 16 <= pOff + 188 && buf[sec] === 0x02) {
          const secLen = ((buf[sec + 1] & 0x0f) << 8) | buf[sec + 2];
          const secEnd = sec + 3 + secLen;
          if (secEnd <= pOff + 188) {
            const progInfoLen = ((buf[sec + 10] & 0x0f) << 8) | buf[sec + 11];
            let pos = sec + 12 + progInfoLen;
            let modified = false;
            while (pos + 5 <= secEnd - 4) {
              let st = buf[pos];
              const ePid = ((buf[pos + 1] & 0x1f) << 8) | buf[pos + 2];
              const esInfoLen = ((buf[pos + 3] & 0x0f) << 8) | buf[pos + 4];
              if (st === 0xdb) { buf[pos] = 0x1b; st = 0x1b; modified = true; }
              else if (st === 0xcf) { buf[pos] = 0x0f; st = 0x0f; modified = true; }
              if (st === 0x1b) videoPids.add(ePid);
              else if (st === 0x0f) audioPids.add(ePid);
              pos += 5 + esInfoLen;
            }
            if (modified) {
              const crc = mpeg2Crc32(buf.subarray(sec, secEnd - 4));
              buf[secEnd - 4] = (crc >>> 24) & 0xff;
              buf[secEnd - 3] = (crc >>> 16) & 0xff;
              buf[secEnd - 2] = (crc >>> 8) & 0xff;
              buf[secEnd - 1] = crc & 0xff;
            }
          }
        }
      }
    }

    if (!videoPids.size && !audioPids.size) return buf;

    const collectPesGroups = (pidSet) => {
      const groups = [];
      const curByPid = new Map();
      for (let p = 0; p < numPkts; p++) {
        const pOff = p * 188;
        const pid = ((buf[pOff + 1] & 0x1f) << 8) | buf[pOff + 2];
        if (!pidSet.has(pid)) continue;
        const pay = payloadOff(pOff);
        if (pay < 0) continue;
        const pusi = (buf[pOff + 1] & 0x40) !== 0;
        let g = curByPid.get(pid);
        if (pusi || !g) {
          g = { pid, pkts: [], slices: [], total: 0 };
          curByPid.set(pid, g);
          groups.push(g);
        }
        g.pkts.push(p);
        g.slices.push({ off: pay, len: pOff + 188 - pay });
        g.total += pOff + 188 - pay;
      }
      return groups;
    };

    // Pass 2: Audio ADTS AAC in-place decryption
    for (const g of collectPesGroups(audioPids)) {
      const pes = new Uint8Array(g.total);
      let off = 0;
      for (const s of g.slices) { pes.set(buf.subarray(s.off, s.off + s.len), off); off += s.len; }
      const dec = await decryptAdtsPes(pes, ck, iv);
      off = 0;
      for (const s of g.slices) { buf.set(dec.subarray(off, off + s.len), s.off); off += s.len; }
    }

    // Pass 3: Video H.264 AVC decryption (in-place when length unchanged, re-packetized when RBSP length changes)
    const replacements = new Map(); // firstPktIdx -> Uint8Array[]
    const droppedPkts = new Set();
    let needsRebuild = false;
    for (const g of collectPesGroups(videoPids)) {
      const pes = new Uint8Array(g.total);
      let off = 0;
      for (const s of g.slices) { pes.set(buf.subarray(s.off, s.off + s.len), off); off += s.len; }
      const dec = await decryptAvcPes(pes, ck, iv);
      if (dec.length === pes.length) {
        off = 0;
        for (const s of g.slices) { buf.set(dec.subarray(off, off + s.len), s.off); off += s.len; }
      } else {
        needsRebuild = true;
        const firstIdx = g.pkts[0];
        const firstTpl = buf.subarray(firstIdx * 188, (firstIdx + 1) * 188);
        replacements.set(firstIdx, packetizePes(g.pid, dec, firstTpl));
        for (let i = 1; i < g.pkts.length; i++) droppedPkts.add(g.pkts[i]);
      }
    }

    if (!needsRebuild) return buf;

    const outPkts = [];
    for (let p = 0; p < numPkts; p++) {
      if (droppedPkts.has(p)) continue;
      if (replacements.has(p)) {
        for (const np of replacements.get(p)) outPkts.push(np);
      } else {
        outPkts.push(buf.subarray(p * 188, (p + 1) * 188));
      }
    }
    const rebuilt = new Uint8Array(outPkts.length * 188);
    const ccByPid = new Map();
    for (let i = 0; i < outPkts.length; i++) {
      const pkt = outPkts[i];
      const dstOff = i * 188;
      rebuilt.set(pkt, dstOff);
      const pid = ((pkt[1] & 0x1f) << 8) | pkt[2];
      if (videoPids.has(pid) && ((pkt[3] >> 4) & 0x01)) {
        const cc = ccByPid.get(pid) || 0;
        rebuilt[dstOff + 3] = (pkt[3] & 0xf0) | (cc & 0x0f);
        ccByPid.set(pid, (cc + 1) & 0x0f);
      }
    }
    return rebuilt;
  }

  const ISO_BMFF_BOXES = new Set(['ftyp', 'styp', 'moof', 'sidx', 'emsg', 'free', 'skip']);

  /** True when `u8` starts with a valid ISO BMFF / fMP4 box (`ftyp`, `styp`, `moof`…) rather than MPEG-TS. */
  function isIsoBmffSegment(u8) {
    if (!u8 || u8.length < 12) return false;
    if (u8[0] === 0x47 && (u8.length < 189 || u8[188] === 0x47)) return false;
    const size = ((u8[0] << 24) >>> 0) + (u8[1] << 16) + (u8[2] << 8) + u8[3];
    if (size !== 1 && size < 8) return false;
    const box = String.fromCharCode(u8[4], u8[5], u8[6], u8[7]);
    return ISO_BMFF_BOXES.has(box);
  }

  function readUint32BE(u8, off) {
    return (((u8[off] << 24) >>> 0) + (u8[off + 1] << 16) + (u8[off + 2] << 8) + u8[off + 3]) >>> 0;
  }

  function writeUint32BE(u8, off, val) {
    u8[off] = (val >>> 24) & 0xff;
    u8[off + 1] = (val >>> 16) & 0xff;
    u8[off + 2] = (val >>> 8) & 0xff;
    u8[off + 3] = val & 0xff;
  }

  function iterMp4Boxes(u8, start = 0, end = u8.length) {
    const boxes = [];
    let pos = start;
    while (pos + 8 <= end) {
      let size = readUint32BE(u8, pos);
      let hdr = 8;
      if (size === 1) {
        if (pos + 16 > end) break;
        const hi = readUint32BE(u8, pos + 8);
        const lo = readUint32BE(u8, pos + 12);
        size = hi * 4294967296 + lo;
        hdr = 16;
      } else if (size === 0) {
        size = end - pos;
      }
      if (size < hdr || pos + size > end) break;
      const type = String.fromCharCode(u8[pos + 4], u8[pos + 5], u8[pos + 6], u8[pos + 7]);
      boxes.push({ type, start: pos, hdr, size, end: pos + size });
      pos += size;
    }
    return boxes;
  }

  function makeMp4Box(type, children) {
    let bodyLen = 0;
    for (const c of children) bodyLen += c.byteLength;
    const out = new Uint8Array(8 + bodyLen);
    writeUint32BE(out, 0, out.byteLength);
    for (let i = 0; i < 4; i++) out[4 + i] = type.charCodeAt(i);
    let off = 8;
    for (const c of children) { out.set(c, off); off += c.byteLength; }
    return out;
  }

  function setTrakTrackId(trakBytes, trackId) {
    const copy = new Uint8Array(trakBytes);
    for (const b of iterMp4Boxes(copy, 8, copy.length)) {
      if (b.type === 'tkhd' && b.size >= b.hdr + 16) {
        const version = copy[b.start + b.hdr];
        const idOff = b.start + b.hdr + 4 + (version === 1 ? 16 : 8);
        if (idOff + 4 <= b.end) writeUint32BE(copy, idOff, trackId);
      }
    }
    return copy;
  }

  function makeTrexBox(trackId, templateBytes) {
    if (templateBytes && templateBytes.byteLength >= 32) {
      const copy = new Uint8Array(templateBytes);
      writeUint32BE(copy, 12, trackId);
      return copy;
    }
    const out = new Uint8Array(32);
    writeUint32BE(out, 0, 32);
    out[4] = 0x74; out[5] = 0x72; out[6] = 0x65; out[7] = 0x78; // 'trex'
    writeUint32BE(out, 12, trackId);
    writeUint32BE(out, 16, 1); // default_sample_description_index = 1
    return out;
  }

  /**
   * Merges a single-track video fMP4 init segment (`ftyp` + `moov`) and a single-track
   * audio fMP4 init segment into one multi-track fMP4 init segment (video = track 1, audio = track 2).
   */
  function mergeMp4InitSegments(videoInit, audioInit) {
    if (!videoInit || !videoInit.byteLength) return audioInit || new Uint8Array(0);
    if (!audioInit || !audioInit.byteLength) return videoInit;
    const vBoxes = iterMp4Boxes(videoInit);
    const aBoxes = iterMp4Boxes(audioInit);
    const vMoovBox = vBoxes.find((b) => b.type === 'moov');
    const aMoovBox = aBoxes.find((b) => b.type === 'moov');
    if (!vMoovBox || !aMoovBox) return videoInit;

    const vChildren = iterMp4Boxes(videoInit, vMoovBox.start + vMoovBox.hdr, vMoovBox.end);
    const aChildren = iterMp4Boxes(audioInit, aMoovBox.start + aMoovBox.hdr, aMoovBox.end);
    const vTrakBox = vChildren.find((b) => b.type === 'trak');
    const aTrakBox = aChildren.find((b) => b.type === 'trak');
    if (!vTrakBox || !aTrakBox) return videoInit;

    const vTrak = setTrakTrackId(videoInit.subarray(vTrakBox.start, vTrakBox.end), 1);
    const aTrak = setTrakTrackId(audioInit.subarray(aTrakBox.start, aTrakBox.end), 2);

    let vTrexTemplate = null;
    const mvexExtras = [];
    const vMvex = vChildren.find((b) => b.type === 'mvex');
    if (vMvex) {
      for (const mb of iterMp4Boxes(videoInit, vMvex.start + vMvex.hdr, vMvex.end)) {
        if (mb.type === 'trex' && !vTrexTemplate) vTrexTemplate = videoInit.subarray(mb.start, mb.end);
        else if (mb.type !== 'trex') mvexExtras.push(videoInit.subarray(mb.start, mb.end));
      }
    }
    let aTrexTemplate = null;
    const aMvex = aChildren.find((b) => b.type === 'mvex');
    if (aMvex) {
      const tb = iterMp4Boxes(audioInit, aMvex.start + aMvex.hdr, aMvex.end).find((x) => x.type === 'trex');
      if (tb) aTrexTemplate = audioInit.subarray(tb.start, tb.end);
    }
    const mergedMvex = makeMp4Box('mvex', [
      ...mvexExtras,
      makeTrexBox(1, vTrexTemplate),
      makeTrexBox(2, aTrexTemplate),
    ]);

    const moovParts = [];
    for (const b of vChildren) {
      if (b.type === 'mvhd') {
        const mvhd = new Uint8Array(videoInit.subarray(b.start, b.end));
        const version = mvhd[b.hdr];
        const nextIdOff = b.hdr + 4 + (version === 1 ? 28 : 16) + 76;
        if (nextIdOff + 4 <= mvhd.length) writeUint32BE(mvhd, nextIdOff, 3);
        moovParts.push(mvhd);
      } else if (b.type === 'trak') {
        moovParts.push(vTrak);
        moovParts.push(aTrak);
      } else if (b.type === 'mvex') {
        // appended cleanly after tracks
      } else {
        moovParts.push(videoInit.subarray(b.start, b.end));
      }
    }
    moovParts.push(mergedMvex);
    const mergedMoov = makeMp4Box('moov', moovParts);

    const ftypBox = vBoxes.find((b) => b.type === 'ftyp');
    const ftyp = ftypBox ? videoInit.subarray(ftypBox.start, ftypBox.end) : new Uint8Array(0);
    const out = new Uint8Array(ftyp.byteLength + mergedMoov.byteLength);
    out.set(ftyp, 0);
    out.set(mergedMoov, ftyp.byteLength);
    return out;
  }

  /**
   * Rewrites `tfhd.track_ID` (plus `mfhd.sequence_number` and `sidx.reference_ID` if present)
   * in-place on a copy of an fMP4 media fragment (`moof` + `mdat`), keeping box lengths and
   * `trun.data_offset` intact. Needed for both track 1 and track 2 because `mux.js` (`remux: false`)
   * emits the MPEG-TS PID (e.g. `0x100 = 256`) as `tfhd.track_ID` while `mergeMp4InitSegments`
   * normalizes `moov/trak` IDs to `1` and `2`.
   */
  function rewriteMoofTrackId(fragmentU8, newTrackId, seqCounter) {
    const out = new Uint8Array(fragmentU8);
    for (const top of iterMp4Boxes(out)) {
      if (top.type === 'sidx' && top.size >= top.hdr + 8) {
        writeUint32BE(out, top.start + top.hdr + 4, newTrackId);
      } else if (top.type === 'moof') {
        for (const child of iterMp4Boxes(out, top.start + top.hdr, top.end)) {
          if (child.type === 'mfhd' && seqCounter && child.size >= child.hdr + 8) {
            writeUint32BE(out, child.start + child.hdr + 4, seqCounter.next++);
          } else if (child.type === 'traf') {
            for (const leaf of iterMp4Boxes(out, child.start + child.hdr, child.end)) {
              if (leaf.type === 'tfhd' && leaf.size >= leaf.hdr + 8) {
                writeUint32BE(out, leaf.start + leaf.hdr + 4, newTrackId);
              }
            }
          }
        }
      }
    }
    return out;
  }

  /**
   * Strips a fake image/header prefix prepended before 188-byte MPEG-TS packets
   * (e.g. a PNG/JPEG/BMP header before `0x47` sync bytes). Leaves valid TS or
   * fMP4 buffers untouched.
   */
  function stripDisguisedTsPrefix(u8) {
    if (!u8 || u8.length < 188) return u8;
    if (u8[0] === 0x47 && (u8.length < 189 || u8[188] === 0x47)) return u8;
    const limit = Math.min(MAX_FAKE_HEADER_SCAN, u8.length - 188);
    for (let off = 1; off <= limit; off++) {
      if (u8[off] !== 0x47) continue;
      if (off + 376 < u8.length) {
        if (u8[off + 188] === 0x47 && u8[off + 376] === 0x47) return u8.subarray(off);
      } else if (u8[off + 188] === 0x47 && (u8.length - off) % 188 === 0) {
        return u8.subarray(off);
      }
    }
    return u8;
  }

  /**
   * Segments of a media playlist, with its init map and AES-128 / SAMPLE-AES keys.
   * Throws `JobError('drm')` on DRM; throws `JobError('live')` on live playlists
   * unless `opts && opts.allowLive` is true.
   */
  function mediaPlan(text, baseUrl, opts) {
    const lines = String(text || '').split(/\r?\n/).map((l) => l.trim());
    if (!lines[0] || !lines[0].startsWith('#EXTM3U')) throw new JobError('not_hls', 'Không phải danh sách HLS.');
    const segs = [];
    let map = null;
    let activeKey = null;
    let mediaSeq = 0;
    let hasMediaSeq = false;
    let targetDuration = 0;
    let pendingRange = null;
    let prevEnd = 0;
    let ended = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!line) continue;
      if (line.startsWith('#EXT-X-TARGETDURATION:')) {
        const td = parseFloat(line.slice(22).trim());
        if (Number.isFinite(td) && td > 0) targetDuration = td;
      } else if (line.startsWith('#EXT-X-MEDIA-SEQUENCE:')) {
        const n = parseInt(line.slice(22).trim(), 10);
        if (Number.isFinite(n) && n >= 0) { mediaSeq = n; hasMediaSeq = true; }
      } else if (line.startsWith('#EXT-X-KEY') || line.startsWith('#EXT-X-SESSION-KEY')) {
        activeKey = parseKeyAttr(attrs(line), baseUrl);
      } else if (line.startsWith('#EXT-X-MAP')) {
        const a = attrs(line);
        const key = activeKey ? { method: activeKey.method, uri: activeKey.uri, iv: activeKey.iv || seqToIv(mediaSeq) } : null;
        map = { url: resolve(baseUrl, a.URI), range: a.BYTERANGE ? byterange(a.BYTERANGE, 0) : null, key };
      } else if (line.startsWith('#EXT-X-BYTERANGE')) {
        pendingRange = byterange(line.slice(17), prevEnd);
      } else if (line.startsWith('#EXT-X-ENDLIST')) {
        ended = true;
      } else if (!line.startsWith('#')) {
        const seq = mediaSeq + segs.length;
        const key = activeKey ? { method: activeKey.method, uri: activeKey.uri, iv: activeKey.iv || seqToIv(seq) } : null;
        segs.push({ url: resolve(baseUrl, line), range: pendingRange, key, seq });
        if (pendingRange) prevEnd = pendingRange[0] + pendingRange[1];
        pendingRange = null;
      }
    }
    const allowLive = Boolean(opts && opts.allowLive);
    if (!ended && !allowLive) throw new JobError('live', 'Đang phát trực tiếp — chưa hỗ trợ.');
    if (!segs.length && !allowLive) throw new JobError('empty', 'Danh sách không có đoạn nào.');
    if (segs.length > MAX_SEGMENTS) throw new JobError('too_long', 'Video quá dài để tải trong trình duyệt.');
    const fmp4 = !!map || (segs.length > 0 && /\.(m4s|mp4|m4f|cmfv)(\?|$)/i.test(segs[0].url));
    return { segs, map, fmp4, live: !ended, targetDuration, mediaSequence: mediaSeq, hasMediaSeq };
  }

  function selectAudioRendition(group, audioTrack) {
    if (!Array.isArray(group) || !group.length) return null;
    if (audioTrack) {
      const wantName = typeof audioTrack === 'object' ? String(audioTrack.name || '').trim().toLowerCase() : '';
      const rawLang = typeof audioTrack === 'string'
        ? audioTrack
        : String((audioTrack && (audioTrack.lang || audioTrack.language)) || '');
      const wantLang = rawLang.trim().toLowerCase().split(/[-_]/)[0] || '';
      if (wantName) {
        const byName = group.find((r) => String(r.name || '').trim().toLowerCase() === wantName);
        if (byName) return byName;
      }
      if (wantLang) {
        const byLang = group.find((r) => (String(r.language || r.lang || '').trim().toLowerCase().split(/[-_]/)[0] || '') === wantLang);
        if (byLang) return byLang;
        const byNameSub = group.find((r) => String(r.name || '').toLowerCase().includes(wantLang));
        if (byNameSub) return byNameSub;
      }
    }
    return group.find((r) => r.default) || group.find((r) => r.autoselect) || group[0];
  }

  /** The variant to download (by height) and its optional separate `#EXT-X-MEDIA:TYPE=AUDIO` playlist. */
  async function pickMedia(job, state) {
    if (job.resume && job.resume.mediaUrl && !job.resume.rebindMaster) {
      try {
        const text = await fetchText(job.resume.mediaUrl, job, state);
        let audioMedia = null;
        if (job.resume.audioMediaUrl) {
          audioMedia = {
            url: job.resume.audioMediaUrl,
            text: await fetchText(job.resume.audioMediaUrl, job, state),
          };
        }
        return { url: job.resume.mediaUrl, text, height: job.resume.height || job.height || null, audioMedia };
      } catch (resumeErr) {
        // Signed variant playlist URL expired (401/403/410): if master URL is distinct, fall back to re-resolving master!
        if (!job.url || job.url === job.resume.mediaUrl) throw resumeErr;
      }
    }
    const text = await fetchText(job.url, job, state);
    if (!/#EXT-X-STREAM-INF/.test(text)) return { url: job.url, text, audioMedia: null };
    const lines = text.split(/\r?\n/).map((l) => l.trim());
    const audioGroups = new Map(); // groupId -> array of renditions
    for (const l of lines) {
      if (l.startsWith('#EXT-X-SESSION-KEY')) parseKeyAttr(attrs(l), job.url);
    }
    const variants = [];
    lines.forEach((line, i) => {
      if (line.startsWith('#EXT-X-MEDIA:')) {
        const a = attrs(line);
        if ((a.TYPE || '').toUpperCase() === 'AUDIO' && a.URI && a['GROUP-ID']) {
          const gid = a['GROUP-ID'];
          const list = audioGroups.get(gid) || [];
          list.push({
            url: resolve(job.url, a.URI),
            name: a.NAME || '',
            language: a.LANGUAGE || '',
            default: a.DEFAULT === 'YES',
            autoselect: a.AUTOSELECT === 'YES',
          });
          audioGroups.set(gid, list);
        }
      } else if (line.startsWith('#EXT-X-STREAM-INF')) {
        const a = attrs(line);
        let j = i + 1;
        while (j < lines.length && (!lines[j] || lines[j].startsWith('#'))) j++;
        if (j < lines.length) {
          variants.push({
            url: resolve(job.url, lines[j]),
            height: Number((a.RESOLUTION || '').split('x')[1]) || 0,
            bandwidth: Number(a.BANDWIDTH) || 0,
            codecs: a.CODECS || '',
            audio: a.AUDIO || '',
          });
        }
      }
    });
    if (!variants.length) throw new JobError('not_hls', 'Không thấy chất lượng nào trong danh sách.');
    variants.sort((a, b) => b.height - a.height || b.bandwidth - a.bandwidth);

    const isHevcOrAv1 = (c) => /\b(?:hvc1|hev1|av01)\b/i.test(c || '');
    const isAvc = (c) => /\bavc1\b/i.test(c || '');
    const targetHeight = (job.resume && job.resume.height) || job.height || 0;
    let v = null;
    if (targetHeight) {
      const sameHeight = variants.filter((x) => x.height === targetHeight);
      v = sameHeight.find((x) => isAvc(x.codecs)) || sameHeight[0] || null;
      if (!v && job.maxHeightCap) {
        const capped = variants.filter((x) => x.height > 0 && x.height <= targetHeight);
        v = capped.find((x) => isAvc(x.codecs)) || capped[0] || null;
      }
    }
    if (!v) {
      // In-browser mux.js TS transmuxer only remuxes H.264 AVC; prefer AVC when an AVC rendition is offered.
      const top = variants[0];
      if (isHevcOrAv1(top.codecs)) {
        const bestAvc = variants.find((x) => isAvc(x.codecs) && x.height === top.height)
          || variants.find((x) => isAvc(x.codecs));
        v = bestAvc || top;
      } else {
        v = top;
      }
    }

    let audioMedia = null;
    if (v.audio && audioGroups.has(v.audio)) {
      const group = audioGroups.get(v.audio);
      const targetAudio = job.audioTrack || (job.resume && job.resume.audioTrack) || null;
      const picked = selectAudioRendition(group, targetAudio);
      if (picked && picked.url && picked.url !== v.url) {
        audioMedia = { url: picked.url, text: await fetchText(picked.url, job, state) };
      }
    }
    return { url: v.url, text: await fetchText(v.url, job, state), height: v.height, audioMedia };
  }

  const OPFS_FLUSH_BYTES = 1024 * 1024;

  /** Writes chunks in 1 MiB RAM-batched blocks to an OPFS file, optionally resuming at `resumeBytes`. */
  async function opfsWriter(name, resumeBytes = 0) {
    const dir = await navigator.storage.getDirectory();
    const handle = await dir.getFileHandle(name, { create: true });
    const keep = resumeBytes > 0;
    const w = await handle.createWritable(keep ? { keepExistingData: true } : undefined);
    if (keep && typeof w.seek === 'function') {
      await w.seek(resumeBytes);
    }
    let bytes = keep ? resumeBytes : 0;
    let bufParts = [];
    let bufLen = 0;

    async function flush() {
      if (!bufLen) return;
      let block;
      if (bufParts.length === 1) {
        block = bufParts[0];
      } else {
        block = new Uint8Array(bufLen);
        let offset = 0;
        for (let i = 0; i < bufParts.length; i++) {
          block.set(bufParts[i], offset);
          offset += bufParts[i].byteLength;
        }
      }
      bufParts = [];
      bufLen = 0;
      await w.write(block);
    }

    return {
      async write(u8) {
        if (!u8 || !u8.byteLength) return;
        const view = u8 instanceof Uint8Array ? u8.slice() : new Uint8Array(u8);
        bufParts.push(view);
        bufLen += view.byteLength;
        bytes += view.byteLength;
        if (bufLen >= OPFS_FLUSH_BYTES) {
          await flush();
        }
      },
      async close() { await flush(); await w.close(); return handle.getFile(); },
      async abort() {
        bufParts = [];
        bufLen = 0;
        try { await w.abort(); } catch { /* closed */ }
        try { await dir.removeEntry(name); } catch { /* gone */ }
      },
      async sealPartial() { await flush(); await w.close(); return handle.getFile(); },
      get bytes() { return bytes; },
    };
  }

  /** TS -> fragmented MP4 through mux.js; output is written as it comes. */
  function tsRemuxer(writer, opts = {}) {
    const t = new root.muxjs.mp4.Transmuxer({ remux: true, keepOriginalTimestamps: true });
    let initDone = Boolean(opts.skipInit);
    let pending = Promise.resolve();
    let produced = initDone ? 1 : 0;
    t.on('data', (seg) => {
      const parts = [];
      if (!initDone) { parts.push(seg.initSegment); initDone = true; }
      parts.push(seg.data);
      produced += 1;
      pending = pending.then(() => Promise.all(parts.map((p) => writer.write(new Uint8Array(p)))));
    });
    return {
      async push(u8) { t.push(u8); t.flush(); await pending; },
      async end() { await pending; if (!produced) throw new JobError('remux_failed', 'Không chuyển được sang MP4.'); },
    };
  }

  /**
   * Muxes separate HLS video + audio playlists (fMP4 `EXT-X-MAP` or TS/ADTS via `mux.js`
   * `remux: false`) into a single fragmented MP4 file with track 1 = video and track 2 = audio.
   */
  function dualTrackMuxer(writer, opts = {}) {
    let vInit = opts.vInit ? new Uint8Array(opts.vInit) : null;
    let aInit = opts.aInit ? new Uint8Array(opts.aInit) : null;
    let initWritten = Boolean(opts.skipInit);
    let produced = initWritten ? 1 : 0;
    let vFmp4 = Boolean(opts.videoFmp4 || vInit);
    let aFmp4 = Boolean(opts.audioFmp4 || aInit);
    const pendingFrags = []; // { track: 1 | 2, data: Uint8Array }

    const makeTrackTransmuxer = (expectedType) => {
      if (!root.muxjs || !root.muxjs.mp4 || !root.muxjs.mp4.Transmuxer) {
        throw new JobError('separate_audio', 'Trình duyệt thiếu bộ ghép kênh — hãy dùng App Windows.');
      }
      const tm = new root.muxjs.mp4.Transmuxer({ remux: false, keepOriginalTimestamps: true });
      const out = [];
      tm.on('data', (seg) => {
        if (!seg.type || seg.type === expectedType) out.push(seg);
      });
      return {
        feed(u8) {
          out.length = 0;
          tm.push(u8);
          tm.flush();
          return out.slice();
        },
      };
    };

    let vTrans = null;
    let aTrans = null;
    const seqCounter = { next: (Number(opts.startSeq) || 0) + 1 };

    const flushReady = async () => {
      if (!initWritten) {
        if (!vInit || !aInit) return;
        const mergedInit = mergeMp4InitSegments(vInit, aInit);
        await writer.write(mergedInit);
        initWritten = true;
      }
      while (pendingFrags.length) {
        const f = pendingFrags.shift();
        const bytes = rewriteMoofTrackId(f.data, f.track === 2 ? 2 : 1, seqCounter);
        await writer.write(bytes);
        produced += 1;
      }
    };

    return {
      async pushPair(rawVideo, rawAudio) {
        if (rawVideo && rawVideo.byteLength) {
          if (!vFmp4 && isIsoBmffSegment(rawVideo)) vFmp4 = true;
          if (vFmp4) {
            pendingFrags.push({ track: 1, data: rawVideo });
          } else {
            if (!vTrans) vTrans = makeTrackTransmuxer('video');
            const segs = vTrans.feed(stripDisguisedTsPrefix(rawVideo));
            for (const s of segs) {
              if (!vInit && s.initSegment) vInit = new Uint8Array(s.initSegment);
              if (s.data && s.data.byteLength) pendingFrags.push({ track: 1, data: new Uint8Array(s.data) });
            }
          }
        }
        if (rawAudio && rawAudio.byteLength) {
          if (!aFmp4 && isIsoBmffSegment(rawAudio)) aFmp4 = true;
          if (aFmp4) {
            pendingFrags.push({ track: 2, data: rawAudio });
          } else {
            if (!aTrans) aTrans = makeTrackTransmuxer('audio');
            const segs = aTrans.feed(stripDisguisedTsPrefix(rawAudio));
            for (const s of segs) {
              if (!aInit && s.initSegment) aInit = new Uint8Array(s.initSegment);
              if (s.data && s.data.byteLength) pendingFrags.push({ track: 2, data: new Uint8Array(s.data) });
            }
          }
        }
        await flushReady();
      },
      async end() {
        if (!initWritten && (vInit || aInit)) {
          await writer.write(vInit && aInit ? mergeMp4InitSegments(vInit, aInit) : (vInit || aInit));
          initWritten = true;
        }
        while (pendingFrags.length) {
          const f = pendingFrags.shift();
          await writer.write(rewriteMoofTrackId(f.data, f.track === 2 ? 2 : 1, seqCounter));
          produced += 1;
        }
        if (!produced) throw new JobError('remux_failed', 'Không ghép được luồng hình và tiếng sang MP4.');
      },
      get lastSeq() {
        return Math.max(0, seqCounter.next - 1);
      },
    };
  }

  async function run(job) {
    const state = { cancelled: false, stopAndSave: false, _waiters: new Set() };
    jobs.set(job.id, state);
    Object.defineProperty(job, 'cancelled', { get: () => state.cancelled, configurable: true });
    const fileName = `hls-${job.id}.mp4`;
    const keyCache = new Map();
    const loadCryptoKey = (uri) => {
      if (!keyCache.has(uri)) {
        keyCache.set(uri, fetchBytes(uri, null, job, state).then((raw) => {
          if (raw.byteLength !== 16) throw new JobError('encrypted', 'Invalid AES-128 key.');
          return root.crypto.subtle.importKey('raw', raw, { name: 'AES-CBC' }, false, ['encrypt', 'decrypt']);
        }));
      }
      return keyCache.get(uri);
    };
    const loadSegment = async (item, isInitMap = false) => {
      let u8 = await fetchBytes(item.url, item.range, job, state);
      if (item.key) {
        const ck = await loadCryptoKey(item.key.uri);
        if (item.key.method === 'sample_aes') {
          if (!isInitMap) u8 = await decryptSampleAesTs(u8, ck, item.key.iv);
        } else {
          u8 = await decryptSegment(u8, ck, item.key.iv);
        }
      }
      return u8;
    };

    let writer = null;
    let media = null;
    let dualSink = null;
    let isLive = false;
    let total = 0;
    let written = 0;
    let fmp4Mode = false;
    try {
      media = await pickMedia(job, state);
      const allowLive = job.allowLive !== false;
      const plan = mediaPlan(media.text, media.url, { allowLive });
      isLive = Boolean(plan.live);
      const audioPlan = (!isLive && media.audioMedia)
        ? mediaPlan(media.audioMedia.text, media.audioMedia.url, { allowLive: false })
        : null;

      total = audioPlan ? Math.max(plan.segs.length, audioPlan.segs.length) : plan.segs.length;
      const startIdx = (!isLive && job.resume && Number(job.resume.written) > 0 && Number(job.resume.bytes) > 0)
        ? Math.min(Number(job.resume.written), total)
        : 0;
      const resumeBytes = startIdx > 0 ? Number(job.resume.bytes) : 0;

      writer = await opfsWriter(fileName, resumeBytes);

      const rawSink = { push: (u8) => writer.write(u8), end: async () => {} };
      fmp4Mode = Boolean(plan.fmp4 || (startIdx > 0 && job.resume && job.resume.fmp4Mode));
      let sink = fmp4Mode && !audioPlan ? rawSink : null;

      if (plan.live) {
        const startedAt = Date.now();
        const maxEmptyPolls = Math.max(1, Number(job.maxEmptyPolls) || DEFAULT_MAX_EMPTY_POLLS);
        let curPlan = plan;
        let lastSeq = -1;
        let lastMapKey = '';
        const seenUrls = [];
        const seenUrlSet = new Set();
        let idlePolls = 0;

        send({
          type: 'HLS_PROGRESS',
          id: job.id,
          live: true,
          done: 0,
          total: 0,
          bytes: 0,
          elapsedSec: 0,
          height: media.height || null,
        });

        for (;;) {
          if (state.cancelled) throw new JobError('cancelled', 'Đã huỷ.');
          if (curPlan.map) {
            const mapId = `${curPlan.map.url}|${curPlan.map.range ? curPlan.map.range.join(':') : ''}`;
            if (mapId !== lastMapKey) {
              fmp4Mode = true;
              sink = rawSink;
              await sink.push(await loadSegment(curPlan.map, true));
              lastMapKey = mapId;
            }
          }

          const newSegs = [];
          for (const seg of curPlan.segs) {
            if (curPlan.hasMediaSeq) {
              if (seg.seq <= lastSeq) continue;
              lastSeq = seg.seq;
              newSegs.push(seg);
            } else {
              const keyId = `${seg.url}|${seg.range ? seg.range.join(':') : ''}`;
              if (seenUrlSet.has(keyId)) continue;
              seenUrlSet.add(keyId);
              seenUrls.push(keyId);
              if (seenUrls.length > 600) seenUrlSet.delete(seenUrls.shift());
              newSegs.push(seg);
            }
          }

          if (!newSegs.length) {
            idlePolls += 1;
          } else {
            idlePolls = 0;
            for (const seg of newSegs) {
              if (state.cancelled) throw new JobError('cancelled', 'Đã huỷ.');
              if (state.stopAndSave && written > 0) break;
              let rawU8;
              try {
                rawU8 = await loadSegment(seg, false);
              } catch (segErr) {
                if (state.cancelled) throw new JobError('cancelled', 'Đã huỷ.');
                if (state.stopAndSave && written > 0) break;
                throw segErr;
              }
              if (!sink) {
                fmp4Mode = fmp4Mode || isIsoBmffSegment(rawU8);
                sink = fmp4Mode ? rawSink : tsRemuxer(writer);
              }
              const u8 = fmp4Mode ? rawU8 : stripDisguisedTsPrefix(rawU8);
              await sink.push(u8);
              written += 1;
              send({
                type: 'HLS_PROGRESS',
                id: job.id,
                live: true,
                done: written,
                total: 0,
                bytes: writer.bytes,
                elapsedSec: Math.floor((Date.now() - startedAt) / 1000),
                height: media.height || null,
              });
              if (written >= MAX_SEGMENTS) break;
            }
          }

          if (!curPlan.live || state.stopAndSave || idlePolls >= maxEmptyPolls || written >= MAX_SEGMENTS) {
            break;
          }

          const pollMs = job.pollIntervalMs
            ? Math.max(10, Number(job.pollIntervalMs))
            : Math.max(((curPlan.targetDuration || 4) / 2) * 1000, 2000);
          await interruptibleWait(pollMs, state);
          if (state.cancelled) throw new JobError('cancelled', 'Đã huỷ.');
          if (state.stopAndSave) break;

          const nextText = await fetchText(media.url, job, state);
          if (state.stopAndSave && written > 0) break;
          curPlan = mediaPlan(nextText, media.url, { allowLive: true });
        }

        if (state.cancelled) throw new JobError('cancelled', 'Đã huỷ.');
        if (!written || !sink) throw new JobError('empty', 'Chưa ghi được đoạn video nào từ luồng trực tiếp.');
        await sink.end();
        const file = await writer.close();
        writer = null;
        const url = URL.createObjectURL(file);
        jobs.set(job.id, { ...state, url, name: fileName });
        send({
          type: 'HLS_DONE',
          id: job.id,
          url,
          bytes: file.size,
          ext: 'mp4',
          live: true,
          elapsedSec: Math.floor((Date.now() - startedAt) / 1000),
        });
        return;
      }

      written = startIdx;
      send({
        type: 'HLS_PROGRESS',
        id: job.id,
        done: written,
        total,
        bytes: writer.bytes,
        height: media.height || null,
      });

      if (audioPlan) {
        const [vInit, aInit] = startIdx === 0
          ? await Promise.all([
            plan.map ? loadSegment(plan.map, true) : Promise.resolve(null),
            audioPlan.map ? loadSegment(audioPlan.map, true) : Promise.resolve(null),
          ])
          : [null, null];
        dualSink = dualTrackMuxer(writer, {
          vInit,
          aInit,
          videoFmp4: plan.fmp4,
          audioFmp4: audioPlan.fmp4,
          skipInit: startIdx > 0,
          startSeq: (startIdx > 0 && job.resume && Number(job.resume.seq) > 0) ? Number(job.resume.seq) : 0,
        });
      } else if (plan.map && startIdx === 0) {
        fmp4Mode = true;
        sink = rawSink;
        await sink.push(await loadSegment(plan.map, true));
      } else if (startIdx > 0 && !fmp4Mode) {
        sink = tsRemuxer(writer, { skipInit: true });
      }

      // Adaptive parallel fetch + decrypt, in-order write:
      // scales up to 8 on clean fast segments (< 900ms), immediately drops to 2 on HTTP 429/503 Retry-After.
      let poolLimit = Math.max(1, Math.min(8, Number(job.pool) || (audioPlan ? 4 : POOL)));
      const maxPool = audioPlan ? 6 : 8;
      let cleanStreak = 0;
      state.onThrottle = () => { poolLimit = 2; cleanStreak = 0; };
      state.onSegmentRetry = () => { poolLimit = Math.max(2, Math.floor(poolLimit / 2)); cleanStreak = 0; };
      state.onCleanSegment = (elapsedMs) => {
        if (elapsedMs <= 900) {
          cleanStreak += 1;
          if (cleanStreak >= 5 && poolLimit < maxPool) {
            poolLimit += 1;
            cleanStreak = 0;
          }
        }
      };

      const ready = new Map();
      let next = startIdx;   // next index to start
      let failure = null;
      await new Promise((done) => {
        const pump = () => {
          if (failure || state.cancelled) { done(); return; }
          while (next < total && next - written < poolLimit) {
            const i = next++;
            const fetchTask = audioPlan
              ? Promise.all([
                i < plan.segs.length ? loadSegment(plan.segs[i], false) : Promise.resolve(null),
                i < audioPlan.segs.length ? loadSegment(audioPlan.segs[i], false) : Promise.resolve(null),
              ])
              : loadSegment(plan.segs[i], false);
            fetchTask.then((payload) => {
              ready.set(i, payload);
              drain();
            }, (e) => { failure = failure || e; done(); });
          }
          if (written >= total) done();
        };
        let draining = false;
        const drain = async () => {
          if (draining) return;
          draining = true;
          try {
            while (ready.has(written)) {
              const payload = ready.get(written);
              ready.delete(written);
              if (dualSink) {
                await dualSink.pushPair(payload[0], payload[1]);
              } else {
                const rawU8 = payload;
                if (!sink) {
                  fmp4Mode = fmp4Mode || isIsoBmffSegment(rawU8);
                  sink = fmp4Mode ? rawSink : tsRemuxer(writer);
                }
                const u8 = fmp4Mode ? rawU8 : stripDisguisedTsPrefix(rawU8);
                await sink.push(u8);
              }
              written += 1;
              if (written % 3 === 0 || written === total) {
                send({ type: 'HLS_PROGRESS', id: job.id, done: written, total, bytes: writer.bytes });
              }
            }
          } catch (e) { failure = failure || e; }
          draining = false;
          pump();
        };
        pump();
      });
      if (state.cancelled) throw new JobError('cancelled', 'Đã huỷ.');
      if (failure) throw failure;
      if (dualSink) await dualSink.end();
      else if (sink) await sink.end();
      const file = await writer.close();
      writer = null;
      const url = URL.createObjectURL(file);
      jobs.set(job.id, { ...state, url, name: fileName });
      send({ type: 'HLS_DONE', id: job.id, url, bytes: file.size, ext: 'mp4' });
    } catch (e) {
      const reason = (e && e.reason) || 'error';
      let checkpoint = null;
      let partialBytes = 0;
      if (writer) {
        const canCheckpoint = !isLive && written > 0 && total > written
          && reason !== 'cancelled' && reason !== 'drm' && media && media.url;
        if (canCheckpoint) {
          try {
            const partialFile = await writer.sealPartial();
            writer = null;
            partialBytes = partialFile ? partialFile.size : 0;
            if (partialBytes > 0) {
              checkpoint = {
                mediaUrl: media.url,
                audioMediaUrl: media.audioMedia ? media.audioMedia.url : null,
                height: media.height || job.height || null,
                written,
                total,
                bytes: partialBytes,
                fmp4Mode,
                seq: dualSink ? dualSink.lastSeq : written,
                audioTrack: job.audioTrack || (job.resume && job.resume.audioTrack) || null,
              };
            }
          } catch { /* fall back to abort */ }
        }
        if (writer) await writer.abort();
      }
      jobs.delete(job.id);
      send({
        type: 'HLS_FAILED',
        id: job.id,
        reason,
        drmSystem: e.drmSystem || null,
        encryptionReason: e.encryptionReason || null,
        resumable: Boolean(checkpoint),
        checkpoint,
        done: checkpoint ? written : undefined,
        total: checkpoint ? total : undefined,
        bytes: checkpoint ? partialBytes : undefined,
        message: e instanceof JobError ? e.message : String((e && e.message) || e).slice(0, 120),
      });
    }
  }

  async function release(id) {
    const j = jobs.get(id);
    jobs.delete(id);
    if (j && j.url) URL.revokeObjectURL(j.url);
    const name = (j && j.name) || (id ? `hls-${id}.mp4` : '');
    if (name && typeof navigator !== 'undefined' && navigator.storage && navigator.storage.getDirectory) {
      try { await (await navigator.storage.getDirectory()).removeEntry(name); } catch { /* gone */ }
    }
  }

  const exported = {
    mediaPlan, byterange, seqToIv, parseHexIv, parseKeyAttr, parseRetryAfterMs, selectAudioRendition,
    decryptSegment, decryptAesCbcRaw, decryptSampleAesTs, mpeg2Crc32,
    stripDisguisedTsPrefix, isIsoBmffSegment, mergeMp4InitSegments, rewriteMoofTrackId, opfsWriter,
  };
  root.__vgHls = exported;
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;

  if (hasChrome && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
      if (!msg || msg.target !== 'offscreen') return false;
      if (msg.type === 'HLS_START') { run(msg.job); sendResponse({ ok: true }); }
      else if (msg.type === 'HLS_STOP') {
        const j = jobs.get(msg.id);
        if (j) { j.stopAndSave = true; wakeJob(j); }
        sendResponse({ ok: true });
      } else if (msg.type === 'HLS_CANCEL') {
        const j = jobs.get(msg.id);
        if (j) { j.cancelled = true; wakeJob(j); }
        sendResponse({ ok: true });
      } else if (msg.type === 'HLS_RELEASE') { release(msg.id).then(() => sendResponse({ ok: true })); return true; }
      else if (msg.type === 'HLS_PING') { sendResponse({ ok: true, jobs: jobs.size }); }
      return false;
    });
  }

  if (typeof navigator !== 'undefined' && navigator.storage && typeof navigator.storage.getDirectory === 'function') {
    // Clean up orphaned OPFS files not retained by a resumable checkpoint in the service worker.
    navigator.storage.getDirectory().then(async (dir) => {
      let keepSet = null;
      if (hasChrome && typeof chrome.runtime.sendMessage === 'function') {
        try {
          const resp = await chrome.runtime.sendMessage({ type: 'VG_HLS_KEEP_FILES' });
          if (resp && Array.isArray(resp.keep)) keepSet = new Set(resp.keep);
        } catch { /* SW asleep or unavailable */ }
      }
      if (!keepSet) return;
      for await (const [name] of dir.entries()) {
        if (/^hls-.*\.mp4$/.test(name) && !jobs.size && !keepSet.has(name)) {
          try { await dir.removeEntry(name); } catch { /* busy */ }
        }
      }
    }).catch(() => {});
  }
})(typeof self !== 'undefined' ? self : globalThis);
