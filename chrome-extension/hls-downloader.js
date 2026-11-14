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

  async function fetchBytes(url, range, job, state) {
    await ensureHostReferer(url, job);
    const maxRetries = Math.max(1, Number(job && job.retries) || RETRIES);
    const baseDelay = Math.max(5, Number(job && job.retryBaseMs) || 400);
    let lastErr;
    for (let attempt = 0; attempt < maxRetries; attempt++) {
      if (job && job.cancelled) throw new JobError('cancelled');
      try {
        const headers = range ? { Range: `bytes=${range[0]}-${range[0] + range[1] - 1}` } : {};
        const r = await fetch(url, { credentials: 'include', headers, cache: 'no-store' });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return new Uint8Array(await r.arrayBuffer());
      } catch (e) {
        lastErr = e;
        if (attempt + 1 < maxRetries) {
          await interruptibleWait(baseDelay * (attempt + 1) ** 2, state);
          if (state && state.stopAndSave) break;
        }
      }
    }
    throw new JobError('segment_failed', String((lastErr && lastErr.message) || lastErr));
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

  /** The variant to download (by height) and whether its audio is a separate playlist. */
  async function pickMedia(job, state) {
    const text = await fetchText(job.url, job, state);
    if (!/#EXT-X-STREAM-INF/.test(text)) return { url: job.url, text };
    const lines = text.split(/\r?\n/).map((l) => l.trim());
    const audioWithUri = new Set();
    for (const l of lines) {
      if (l.startsWith('#EXT-X-SESSION-KEY')) parseKeyAttr(attrs(l), job.url);
    }
    const variants = [];
    lines.forEach((line, i) => {
      if (line.startsWith('#EXT-X-MEDIA:')) {
        const a = attrs(line);
        if ((a.TYPE || '').toUpperCase() === 'AUDIO' && a.URI) audioWithUri.add(a['GROUP-ID']);
      } else if (line.startsWith('#EXT-X-STREAM-INF')) {
        const a = attrs(line);
        let j = i + 1;
        while (j < lines.length && (!lines[j] || lines[j].startsWith('#'))) j++;
        if (j < lines.length) {
          variants.push({ url: resolve(job.url, lines[j]), height: Number((a.RESOLUTION || '').split('x')[1]) || 0,
                          bandwidth: Number(a.BANDWIDTH) || 0, audio: a.AUDIO || '' });
        }
      }
    });
    if (!variants.length) throw new JobError('not_hls', 'Không thấy chất lượng nào trong danh sách.');
    variants.sort((a, b) => b.height - a.height || b.bandwidth - a.bandwidth);
    const v = (job.height && variants.find((x) => x.height === job.height)) || variants[0];
    if (v.audio && audioWithUri.has(v.audio)) {
      throw new JobError('separate_audio', 'Hình và tiếng nằm riêng — hãy dùng "App" (app VidGrab trên máy) hoặc "Qua VidGrab".');
    }
    return { url: v.url, text: await fetchText(v.url, job, state), height: v.height };
  }

  /** Writes chunks in order to an OPFS file. */
  async function opfsWriter(name) {
    const dir = await navigator.storage.getDirectory();
    const handle = await dir.getFileHandle(name, { create: true });
    const w = await handle.createWritable();
    let bytes = 0;
    return {
      async write(u8) { if (u8 && u8.byteLength) { await w.write(u8); bytes += u8.byteLength; } },
      async close() { await w.close(); return handle.getFile(); },
      async abort() { try { await w.abort(); } catch { /* closed */ } try { await dir.removeEntry(name); } catch { /* gone */ } },
      get bytes() { return bytes; },
    };
  }

  /** TS -> fragmented MP4 through mux.js; output is written as it comes. */
  function tsRemuxer(writer) {
    const t = new root.muxjs.mp4.Transmuxer({ remux: true });
    let initDone = false;
    let pending = Promise.resolve();
    let produced = 0;
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
    try {
      const media = await pickMedia(job, state);
      const allowLive = job.allowLive !== false;
      const plan = mediaPlan(media.text, media.url, { allowLive });
      writer = await opfsWriter(fileName);

      const rawSink = { push: (u8) => writer.write(u8), end: async () => {} };
      let fmp4Mode = plan.fmp4;
      let sink = fmp4Mode ? rawSink : null;

      if (plan.live) {
        const startedAt = Date.now();
        const maxEmptyPolls = Math.max(1, Number(job.maxEmptyPolls) || DEFAULT_MAX_EMPTY_POLLS);
        let curPlan = plan;
        let lastSeq = -1;
        let lastMapKey = '';
        const seenUrls = [];
        const seenUrlSet = new Set();
        let written = 0;
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

      const total = plan.segs.length;
      send({ type: 'HLS_PROGRESS', id: job.id, done: 0, total, bytes: 0, height: media.height || null });

      if (plan.map) {
        fmp4Mode = true;
        sink = rawSink;
        await sink.push(await loadSegment(plan.map, true));
      }

      // parallel fetch + decrypt, in-order write: at most POOL segments in flight or waiting
      const ready = new Map();
      let next = 0;          // next index to start
      let written = 0;       // next index to write
      let failure = null;
      await new Promise((done) => {
        const pump = () => {
          if (failure || state.cancelled) { done(); return; }
          while (next < total && next - written < POOL) {
            const i = next++;
            loadSegment(plan.segs[i], false).then((u8) => {
              ready.set(i, u8);
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
              const rawU8 = ready.get(written);
              ready.delete(written);
              if (!sink) {
                fmp4Mode = fmp4Mode || isIsoBmffSegment(rawU8);
                sink = fmp4Mode ? rawSink : tsRemuxer(writer);
              }
              const u8 = fmp4Mode ? rawU8 : stripDisguisedTsPrefix(rawU8);
              await sink.push(u8);
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
      await sink.end();
      const file = await writer.close();
      writer = null;
      const url = URL.createObjectURL(file);
      jobs.set(job.id, { ...state, url, name: fileName });
      send({ type: 'HLS_DONE', id: job.id, url, bytes: file.size, ext: 'mp4' });
    } catch (e) {
      if (writer) await writer.abort();
      jobs.delete(job.id);
      send({
        type: 'HLS_FAILED',
        id: job.id,
        reason: e.reason || 'error',
        drmSystem: e.drmSystem || null,
        encryptionReason: e.encryptionReason || null,
        message: e instanceof JobError ? e.message : String((e && e.message) || e).slice(0, 120),
      });
    }
  }

  async function release(id) {
    const j = jobs.get(id);
    jobs.delete(id);
    if (!j) return;
    if (j.url) URL.revokeObjectURL(j.url);
    if (j.name && typeof navigator !== 'undefined' && navigator.storage && navigator.storage.getDirectory) {
      try { await (await navigator.storage.getDirectory()).removeEntry(j.name); } catch { /* gone */ }
    }
  }

  const exported = {
    mediaPlan, byterange, seqToIv, parseHexIv, parseKeyAttr,
    decryptSegment, decryptAesCbcRaw, decryptSampleAesTs, mpeg2Crc32,
    stripDisguisedTsPrefix, isIsoBmffSegment,
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
    // leftovers of a run the browser closed mid-way
    navigator.storage.getDirectory().then(async (dir) => {
      for await (const [name] of dir.entries()) {
        if (/^hls-.*\.mp4$/.test(name) && !jobs.size) { try { await dir.removeEntry(name); } catch { /* busy */ } }
      }
    }).catch(() => {});
  }
})(typeof self !== 'undefined' ? self : globalThis);
