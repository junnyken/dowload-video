"""
Mega.nz Public Share Link Extractor
====================================
Resolves public Mega.nz links using Mega's client-server API and the
decryption key embedded in the share URL fragment:
  - https://mega.nz/file/<id>#<key>
  - https://mega.nz/#!<id>!<key>
  - https://mega.nz/folder/<id>#<key>

Features:
  - Calls https://g.api.mega.co.nz/cs with [{"a": "g", "g": 1, "p": file_id}]
  - Derives AES-128 key and AES-CTR IV from the 256-bit URL key
  - Decrypts file attributes ('at' field, AES-CBC with zero IV -> 'MEGA{"n": ...}')
  - Exposes AES-CTR parameters in metadata for stream decryption
  - Surfaces share_quota_exceeded on -17 or HTTP 509 bandwidth limit
  - Surfaces share_expired on -9 / -2 / -16
  - Provides helper to download & decrypt AES-CTR stream to a file
"""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, Optional

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.error_codes import get_error_meta
from app.services.base_extractor import (
    BaseExtractor,
    BatchEntry,
    BatchResult,
    ExtractResult,
    FormatInfo,
)
from app.services.extractor_registry import REGISTRY

logger = logging.getLogger(__name__)

_API_URL = "https://g.api.mega.co.nz/cs"
_TIMEOUT = 20.0

_NEW_FILE_RE = re.compile(
    r"mega\.(?:co\.)?nz/file/([A-Za-z0-9_-]+)(?:#([A-Za-z0-9_-]+))?",
    re.I,
)
_OLD_FILE_RE = re.compile(
    r"mega\.(?:co\.)?nz/#!([A-Za-z0-9_-]+)(?:!([A-Za-z0-9_-]+))?",
    re.I,
)
_NEW_FOLDER_FILE_RE = re.compile(
    r"mega\.(?:co\.)?nz/folder/([A-Za-z0-9_-]+)#([A-Za-z0-9_-]+)/file/([A-Za-z0-9_-]+)",
    re.I,
)
_NEW_FOLDER_RE = re.compile(
    r"mega\.(?:co\.)?nz/folder/([A-Za-z0-9_-]+)(?:#([A-Za-z0-9_-]+))?",
    re.I,
)
_OLD_FOLDER_RE = re.compile(
    r"mega\.(?:co\.)?nz/#F!([A-Za-z0-9_-]+)(?:!([A-Za-z0-9_-]+))?",
    re.I,
)


def _run_sync(coro):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def is_mega_url(url: str) -> bool:
    return parse_mega_url(url) is not None


def parse_mega_url(url: str) -> Optional[dict[str, str]]:
    """
    Parse a Mega.nz URL into {'kind': 'file'|'folder'|'folder_file', 'id': ..., 'key': ..., 'file_id': ...}.
    Returns None if not a valid Mega URL.
    """
    raw = (url or "").strip()
    if not raw:
        return None

    m_ff = _NEW_FOLDER_FILE_RE.search(raw)
    if m_ff:
        return {
            "kind": "folder_file",
            "id": m_ff.group(1),
            "key": m_ff.group(2) or "",
            "file_id": m_ff.group(3),
        }

    for kind, pattern in (
        ("file", _NEW_FILE_RE),
        ("file", _OLD_FILE_RE),
        ("folder", _NEW_FOLDER_RE),
        ("folder", _OLD_FOLDER_RE),
    ):
        m = pattern.search(raw)
        if m:
            return {
                "kind": kind,
                "id": m.group(1),
                "key": m.group(2) or "",
            }
    return None


def mega_b64decode(s: str) -> bytes:
    """Decode Mega's unpadded URL-safe base64 string."""
    clean = (s or "").strip().replace("-", "+").replace("_", "/")
    pad = (4 - len(clean) % 4) % 4
    return base64.b64decode(clean + ("=" * pad))


def mega_b64encode(data: bytes) -> str:
    """Encode bytes to Mega's unpadded URL-safe base64 string."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def derive_mega_file_key(key_b64: str) -> tuple[bytes, bytes, bytes]:
    """
    Derive (aes_key_16B, iv_8B, meta_mac_8B) from a 32-byte Mega file URL key.
    Rejects 16-byte folder keys or truncated keys with ValueError('invalid_mega_key')
    so corrupted plaintext is never produced silently.
    """
    try:
        raw = mega_b64decode(key_b64)
    except Exception:
        raise ValueError("invalid_mega_key")
    if len(raw) < 32:
        raise ValueError("invalid_mega_key")
    aes_key = bytes(a ^ b for a, b in zip(raw[:16], raw[16:32]))
    iv = raw[16:24]
    meta_mac = raw[24:32]
    return aes_key, iv, meta_mac


def derive_mega_folder_key(key_b64: str) -> bytes:
    """Derive 16-byte AES-128 folder key from a Mega folder URL key."""
    try:
        raw = mega_b64decode(key_b64)
    except Exception:
        raise ValueError("invalid_mega_key")
    if len(raw) < 16:
        raise ValueError("invalid_mega_key")
    return raw[:16]


def decrypt_mega_node_key(encrypted_k_field: str, folder_key: bytes) -> bytes:
    """
    Decrypt a Mega folder node's 'k' field (format '<owner>:<b64_ct>')
    using AES-128-ECB with the 16-byte folder key.
    Returns 32 bytes for file nodes (t=0) or 16 bytes for folder nodes (t=1).
    """
    raw_field = (encrypted_k_field or "").strip()
    if ":" in raw_field:
        _, _, b64_part = raw_field.partition(":")
    else:
        b64_part = raw_field
    ct = mega_b64decode(b64_part)
    if len(ct) not in (16, 32):
        raise ValueError("invalid_mega_key")
    cipher = Cipher(algorithms.AES(folder_key), modes.ECB())
    decryptor = cipher.decryptor()
    return decryptor.update(ct) + decryptor.finalize()


def decrypt_mega_attributes(at_b64: str, aes_key: bytes) -> dict[str, Any]:
    """
    Decrypt Mega 'at' attribute blob using AES-128-CBC (zero IV).
    Plaintext MUST have format: b'MEGA{"n":"filename.mp4",...}\\x00...'
    Raises ValueError('invalid_mega_key') on wrong key or malformed payload.
    """
    try:
        ct = mega_b64decode(at_b64)
    except Exception:
        raise ValueError("invalid_mega_key")
    if not ct or len(ct) % 16 != 0:
        raise ValueError("invalid_mega_key")
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b"\x00" * 16))
    decryptor = cipher.decryptor()
    pt = decryptor.update(ct) + decryptor.finalize()
    try:
        pt_str = pt.rstrip(b"\x00").decode("utf-8", errors="strict") if pt else ""
    except UnicodeDecodeError:
        raise ValueError("invalid_mega_key")
    if not pt_str.startswith("MEGA"):
        raise ValueError("invalid_mega_key")
    try:
        obj = json.loads(pt_str[4:].strip())
    except Exception:
        raise ValueError("invalid_mega_key")
    if not isinstance(obj, dict):
        raise ValueError("invalid_mega_key")
    return obj


def encrypt_mega_attributes(attrs: dict[str, Any], aes_key: bytes) -> str:
    """Encrypt a Mega attribute dict into an 'at' base64url string (AES-128-CBC, zero IV)."""
    raw = ("MEGA" + json.dumps(attrs, separators=(",", ":"))).encode("utf-8")
    pad_len = (16 - (len(raw) % 16)) % 16
    padded = raw + (b"\x00" * pad_len)
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b"\x00" * 16))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return mega_b64encode(ct)


def mega_counter_at_offset(iv: bytes, byte_offset: int = 0) -> bytes:
    """Compute Mega's 16-byte AES-CTR counter at a 16-byte-aligned byte offset."""
    if byte_offset < 0 or byte_offset % 16 != 0:
        raise ValueError("byte_offset must be a non-negative multiple of 16")
    iv8 = iv[:8] if len(iv) >= 8 else iv.ljust(8, b"\x00")
    block_idx = byte_offset // 16
    return iv8 + block_idx.to_bytes(8, "big")


def decrypt_mega_stream(encrypted_bytes: bytes, aes_key: bytes, iv: bytes, byte_offset: int = 0) -> bytes:
    """Decrypt a Mega file stream using AES-128-CTR at `byte_offset`."""
    counter_init = mega_counter_at_offset(iv, byte_offset)
    cipher = Cipher(algorithms.AES(aes_key), modes.CTR(counter_init))
    decryptor = cipher.decryptor()
    return decryptor.update(encrypted_bytes) + decryptor.finalize()


def _mega_chunk_sizes():
    """Yield Mega's official CBC-MAC chunk schedule: 128KB, 256KB, ..., 896KB, 1MB, 1MB, ..."""
    for i in range(1, 8):
        yield i * 131072
    while True:
        yield 1048576


def _cbc_mac_chunk(data: bytes, aes_key: bytes, iv16: bytes) -> bytes:
    """Compute 16-byte AES-128-CBC-MAC of `data` (zero-padded to 16 bytes) with `iv16`."""
    pad = (16 - (len(data) % 16)) % 16
    padded = data + (b"\x00" * pad) if pad else data
    if not padded:
        return iv16
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv16))
    enc = cipher.encryptor()
    ct = enc.update(padded) + enc.finalize()
    return ct[-16:]


def compute_mega_meta_mac_bytes(plaintext: bytes, aes_key: bytes, iv: bytes) -> bytes:
    """Compute Mega's 8-byte meta_mac over in-memory plaintext bytes."""
    iv8 = iv[:8] if len(iv) >= 8 else iv.ljust(8, b"\x00")
    chunk_iv = iv8 + iv8
    pos = 0
    total = len(plaintext)
    chunk_macs = bytearray()
    for csize in _mega_chunk_sizes():
        if pos >= total:
            break
        chunk = plaintext[pos : pos + csize]
        chunk_macs.extend(_cbc_mac_chunk(chunk, aes_key, chunk_iv))
        pos += len(chunk)
    if not chunk_macs:
        final_mac = b"\x00" * 16
    else:
        final_mac = _cbc_mac_chunk(bytes(chunk_macs), aes_key, b"\x00" * 16)
    return bytes(a ^ b for a, b in zip(final_mac[0:4], final_mac[4:8])) + bytes(
        a ^ b for a, b in zip(final_mac[8:12], final_mac[12:16])
    )


def compute_mega_meta_mac_file(path: str, aes_key: bytes, iv: bytes) -> bytes:
    """Compute Mega's 8-byte meta_mac streaming from disk (<= 1 MB RAM)."""
    iv8 = iv[:8] if len(iv) >= 8 else iv.ljust(8, b"\x00")
    chunk_iv = iv8 + iv8
    chunk_macs = bytearray()
    with open(path, "rb") as fh:
        for csize in _mega_chunk_sizes():
            chunk = fh.read(csize)
            if not chunk:
                break
            chunk_macs.extend(_cbc_mac_chunk(chunk, aes_key, chunk_iv))
    if not chunk_macs:
        final_mac = b"\x00" * 16
    else:
        final_mac = _cbc_mac_chunk(bytes(chunk_macs), aes_key, b"\x00" * 16)
    return bytes(a ^ b for a, b in zip(final_mac[0:4], final_mac[4:8])) + bytes(
        a ^ b for a, b in zip(final_mac[8:12], final_mac[12:16])
    )


def verify_mega_file_mac(path: str, aes_key: bytes, iv: bytes, expected_meta_mac: bytes) -> bool:
    """Verify a decrypted Mega file against its 8-byte URL meta_mac."""
    import hmac as _hmac
    if len(expected_meta_mac) != 8:
        return False
    actual = compute_mega_meta_mac_file(path, aes_key, iv)
    return _hmac.compare_digest(actual, expected_meta_mac)


def _format_mega_509_error(resp: httpx.Response) -> ValueError:
    """Build a clear Mega 509 quota error, surfacing X-MEGA-Time-Left when provided."""
    tl = (resp.headers.get("X-MEGA-Time-Left") or resp.headers.get("x-mega-time-left") or "").strip()
    if tl.isdigit():
        secs = int(tl)
        mins = max(1, (secs + 59) // 60)
        return ValueError(
            f"share_quota_exceeded: Hết hạn mức băng thông Mega.nz trên mạng này, thử lại sau ~{mins} phút."
        )
    return ValueError("share_quota_exceeded")


async def download_and_decrypt_mega_file(
    download_url: str,
    aes_key: bytes,
    iv: bytes,
    dest_path: str,
    client: Optional[httpx.AsyncClient] = None,
    progress_token: str = "",
    total_size: int = 0,
    expected_meta_mac: bytes = b"",
) -> str:
    """
    Download and decrypt an encrypted Mega file from `download_url` into `dest_path`.
    - For files > 4 MB with known `total_size`: 6-connection parallel Range via Mega's
      `/start-end` suffix (4 MB aligned chunks, 3 retries per chunk, streaming 256 KB
      pieces directly to disk at offset with AES-128-CTR at exact block offset).
    - For small files (<= 4 MB or unknown size): single stream with 3 retries.
    - Verifies Mega's 8-byte CBC-MAC (`expected_meta_mac`) after download; deletes file
      and raises ValueError('mega_integrity_failed') on any corruption or wrong key.
    """
    import asyncio
    import json as _json
    import os
    import time as _t

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0), follow_redirects=True)

    def _report(status: str, done: int, total: int, elapsed: float) -> None:
        if not progress_token:
            return
        try:
            from app.core.redis_client import get_redis
            speed = (done / elapsed) if elapsed > 0.05 else 0.0
            pct = 100.0 if status == "finished" else (round(done / total * 100, 1) if total > 0 else 0.0)
            eta = int((total - done) / speed) if (total > done and speed > 0) else 0
            get_redis().setex(f"dl_progress:{progress_token}", 300, _json.dumps({
                "status": status,
                "percent": min(100.0, pct),
                "speed_kbps": round(speed / 1024, 1),
                "eta_seconds": eta,
                "total_bytes": total,
                "downloaded_bytes": done,
            }))
        except Exception:
            pass

    t0 = _t.monotonic()
    downloaded = 0
    last_report = 0.0
    chunk_size = 4 * 1024 * 1024  # 4 MiB, multiple of 16
    base_url = download_url.rstrip("/")

    try:
        if total_size > chunk_size:
            ranges = [
                (s, min(s + chunk_size - 1, total_size - 1))
                for s in range(0, total_size, chunk_size)
            ]
            sem = asyncio.Semaphore(6)
            _report("downloading", 0, total_size, 0.0)
            with open(dest_path, "wb") as fh:
                fh.truncate(total_size)

                async def _fetch_chunk(start: int, end: int) -> None:
                    nonlocal downloaded, last_report
                    expected = end - start + 1
                    chunk_url = f"{base_url}/{start}-{end}"
                    async with sem:
                        for attempt in range(3):
                            written = 0
                            try:
                                dec = Cipher(
                                    algorithms.AES(aes_key),
                                    modes.CTR(mega_counter_at_offset(iv, start)),
                                ).decryptor()
                                async with client.stream(
                                    "GET", chunk_url, timeout=httpx.Timeout(60.0)
                                ) as resp:
                                    if resp.status_code == 509:
                                        raise _format_mega_509_error(resp)
                                    resp.raise_for_status()
                                    async for piece in resp.aiter_bytes(256 * 1024):
                                        rem = expected - written
                                        if rem <= 0:
                                            break
                                        sl = piece[:rem]
                                        pt = dec.update(sl)
                                        fh.seek(start + written)
                                        fh.write(pt)
                                        written += len(sl)
                                    tail = dec.finalize()
                                    if tail and written < expected:
                                        fh.seek(start + written)
                                        fh.write(tail[: expected - written])
                                if written != expected:
                                    raise RuntimeError(f"mega short read {written} != {expected}")
                                downloaded += written
                                now = _t.monotonic()
                                if progress_token and (now - last_report >= 0.4 or downloaded >= total_size):
                                    last_report = now
                                    _report("downloading", downloaded, total_size, now - t0)
                                return
                            except ValueError:
                                raise
                            except Exception:
                                if attempt == 2:
                                    raise
                                await asyncio.sleep(0.5 * (attempt + 1))

                await asyncio.gather(*(_fetch_chunk(s, e) for s, e in ranges))
        else:
            for attempt in range(3):
                downloaded = 0
                try:
                    dec = Cipher(
                        algorithms.AES(aes_key),
                        modes.CTR(mega_counter_at_offset(iv, 0)),
                    ).decryptor()
                    async with client.stream("GET", download_url, timeout=httpx.Timeout(60.0)) as resp:
                        if resp.status_code == 509:
                            raise _format_mega_509_error(resp)
                        resp.raise_for_status()
                        cl = int(resp.headers.get("content-length") or 0) or total_size
                        with open(dest_path, "wb") as fh:
                            async for chunk in resp.aiter_bytes(256 * 1024):
                                fh.write(dec.update(chunk))
                                downloaded += len(chunk)
                                now = _t.monotonic()
                                if progress_token and now - last_report >= 0.4:
                                    last_report = now
                                    _report("downloading", downloaded, cl, now - t0)
                            tail = dec.finalize()
                            if tail:
                                fh.write(tail)
                    break
                except ValueError:
                    raise
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(0.5 * (attempt + 1))

        if os.path.getsize(dest_path) <= 0:
            raise ValueError("empty_mega_file")
        if len(expected_meta_mac) == 8 and not verify_mega_file_mac(
            dest_path, aes_key, iv, expected_meta_mac
        ):
            raise ValueError("mega_integrity_failed")
        _report("finished", downloaded, downloaded, _t.monotonic() - t0)
        return dest_path
    except Exception:
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except OSError:
            pass
        raise
    finally:
        if own_client:
            await client.aclose()


def _check_mega_error(code: int) -> None:
    """Map Mega negative integer API error codes to VidGrab error codes."""
    if code in (-17, -18):
        raise ValueError("share_quota_exceeded")
    if code in (-9, -2, -16, -11, -14):
        raise ValueError("share_expired")
    if code < 0:
        raise ValueError(f"mega_api_error_{code}")


def _decrypt_folder_nodes(
    files: list[dict[str, Any]], folder_id: str, folder_key_b64: str, folder_key: bytes
) -> tuple[str, list[dict[str, Any]]]:
    """
    Decrypt Mega folder node list `f` using `folder_key` (16B AES-128).
    Returns (real_folder_title, list_of_decrypted_file_entries).
    """
    folder_title = ""
    entries: list[dict[str, Any]] = []
    for node in files:
        if not isinstance(node, dict):
            continue
        ntype = node.get("t")
        handle = (node.get("h") or "").strip()
        k_field = node.get("k") or ""
        a_field = node.get("a") or ""
        if not handle or not k_field:
            continue
        try:
            node_key = decrypt_mega_node_key(k_field, folder_key)
        except ValueError:
            continue
        if ntype == 1 and not folder_title and len(node_key) >= 16 and a_field:
            try:
                attrs = decrypt_mega_attributes(a_field, node_key[:16])
                name = (attrs.get("n") or "").strip()
                if name:
                    folder_title = name
            except ValueError:
                pass
        elif ntype == 0 and len(node_key) == 32:
            aes_key = bytes(a ^ b for a, b in zip(node_key[:16], node_key[16:32]))
            iv = node_key[16:24]
            meta_mac = node_key[24:32]
            if not a_field:
                continue
            try:
                attrs = decrypt_mega_attributes(a_field, aes_key)
            except ValueError:
                continue
            filename = (attrs.get("n") or "").strip()
            if not filename:
                continue
            size_bytes = int(node.get("s") or 0)
            ext = filename.rsplit(".", 1)[1].lower() if "." in filename else "mp4"
            title = filename.rsplit(".", 1)[0] if "." in filename else filename
            entries.append({
                "id": handle,
                "title": title,
                "filename": filename,
                "ext": ext,
                "size": size_bytes,
                "file_size_mb": round(size_bytes / (1024 * 1024), 2),
                "url": f"https://mega.nz/folder/{folder_id}#{folder_key_b64}/file/{handle}",
                "mega_crypto": {
                    "cipher": "AES-128-CTR",
                    "aes_key_hex": aes_key.hex(),
                    "iv_hex": iv.hex(),
                    "counter_init_hex": (iv + (b"\x00" * 8)).hex(),
                    "meta_mac_hex": meta_mac.hex(),
                    "filename": filename,
                },
            })
    return folder_title, entries


async def resolve_mega_share(
    url: str,
    quality: str = "video",
    client: Optional[httpx.AsyncClient] = None,
    progress_token: str = "",
    decrypt_on_server: bool = False,
) -> Dict[str, Any]:
    """
    Resolve a public Mega.nz share link (file, folder, or file-in-folder) into a
    dict compatible with extract_video_info() in routes.py.
    """
    parsed = parse_mega_url(url)
    if not parsed:
        raise ValueError("invalid_url")

    kind = parsed["kind"]
    item_id = parsed["id"]
    key_b64 = parsed["key"]
    if not key_b64:
        raise ValueError("share_password_required")

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT), follow_redirects=True)

    try:
        if kind in ("folder", "folder_file"):
            folder_key = derive_mega_folder_key(key_b64)
            resp = await client.post(
                _API_URL,
                params={"id": "1", "n": item_id},
                json=[{"a": "f", "c": 1, "r": 1}],
            )
            if resp.status_code == 509:
                raise _format_mega_509_error(resp)
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, int):
                _check_mega_error(payload)
            if isinstance(payload, list) and payload and isinstance(payload[0], int):
                _check_mega_error(payload[0])

            node_block = payload[0] if isinstance(payload, list) and payload else {}
            files = (node_block.get("f") or []) if isinstance(node_block, dict) else []
            folder_title, entries = _decrypt_folder_nodes(files, item_id, key_b64, folder_key)
            if not entries and files:
                raise ValueError("invalid_mega_key")

            if kind == "folder":
                return {
                    "title": folder_title or f"Mega Folder {item_id}",
                    "thumbnail_url": "",
                    "direct_mp4_url": url,
                    "original_url": url,
                    "quality": quality,
                    "duration": 0,
                    "file_size_mb": round(sum(e["size"] for e in entries) / (1024 * 1024), 2),
                    "available_formats": [],
                    "entries": entries,
                    "max_merge_height": 0,
                    "downloaded_height": 0,
                    "downloaded_vcodec": "",
                    "downloaded_universal": None,
                    "downloaded_media": {"measured": False},
                    "is_audio_only": False,
                    "uploader": "Mega.nz",
                    "has_subtitles": False,
                    "has_auto_captions": False,
                    "available_subtitle_languages": [],
                    "subtitle_source": "none",
                    "chapters": [],
                    "tags": [],
                    "images": [],
                    "audio_url": "",
                }

            # kind == "folder_file": resolve the specific file inside this folder
            target_handle = parsed["file_id"]
            target = next((e for e in entries if e["id"] == target_handle), None)
            if not target:
                raise ValueError("share_expired")
            mc = target["mega_crypto"]
            aes_key = bytes.fromhex(mc["aes_key_hex"])
            iv = bytes.fromhex(mc["iv_hex"])
            meta_mac = bytes.fromhex(mc["meta_mac_hex"])
            counter_init = iv + (b"\x00" * 8)

            g_resp = await client.post(
                _API_URL,
                params={"id": "2", "n": item_id},
                json=[{"a": "g", "g": 1, "n": target_handle}],
            )
            if g_resp.status_code == 509:
                raise _format_mega_509_error(g_resp)
            g_resp.raise_for_status()
            g_payload = g_resp.json()
            if isinstance(g_payload, int):
                _check_mega_error(g_payload)
            if isinstance(g_payload, list) and g_payload and isinstance(g_payload[0], int):
                _check_mega_error(g_payload[0])
            item_data = g_payload[0] if isinstance(g_payload, list) and g_payload else g_payload
            if not isinstance(item_data, dict):
                raise ValueError("share_expired")
            if "e" in item_data and isinstance(item_data["e"], int):
                _check_mega_error(item_data["e"])
            dl_url = (item_data.get("g") or "").strip()
            if not dl_url:
                raise ValueError("share_quota_exceeded")
            size_bytes = int(item_data.get("s") or target["size"] or 0)
            size_mb = round(size_bytes / (1024 * 1024), 2)
            filename = target["filename"]
            title = target["title"]
            ext = target["ext"]
            mega_crypto = mc
        else:
            # Single file request
            aes_key, iv, meta_mac = derive_mega_file_key(key_b64)
            counter_init = iv + (b"\x00" * 8)

            resp = await client.post(
                _API_URL,
                params={"id": "1"},
                json=[{"a": "g", "g": 1, "p": item_id}],
            )
            if resp.status_code == 509:
                raise _format_mega_509_error(resp)
            resp.raise_for_status()
            payload = resp.json()

            if isinstance(payload, int):
                _check_mega_error(payload)
            if isinstance(payload, list) and payload and isinstance(payload[0], int):
                _check_mega_error(payload[0])

            item_data = payload[0] if isinstance(payload, list) and payload else payload
            if not isinstance(item_data, dict):
                raise ValueError("share_expired")

            if "e" in item_data and isinstance(item_data["e"], int):
                _check_mega_error(item_data["e"])

            dl_url = (item_data.get("g") or "").strip()
            if not dl_url:
                raise ValueError("share_quota_exceeded")

            size_bytes = int(item_data.get("s") or 0)
            size_mb = round(size_bytes / (1024 * 1024), 2)

            at_b64 = item_data.get("at") or ""
            attrs = decrypt_mega_attributes(at_b64, aes_key) if at_b64 else {}
            filename = (attrs.get("n") or "").strip()
            if not filename:
                raise ValueError("invalid_mega_key")
            title = filename.rsplit(".", 1)[0] if "." in filename else filename
            ext = filename.rsplit(".", 1)[1].lower() if "." in filename else "mp4"

            mega_crypto = {
                "cipher": "AES-128-CTR",
                "aes_key_hex": aes_key.hex(),
                "iv_hex": iv.hex(),
                "counter_init_hex": counter_init.hex(),
                "meta_mac_hex": meta_mac.hex(),
                "filename": filename,
            }

        local_file_path = ""
        if decrypt_on_server:
            import os
            from app.core.local_download import new_download_path
            from app.services.downloader import DOWNLOAD_DIR

            try:
                max_server_mb = float(os.getenv("MEGA_SERVER_MAX_MB", "500"))
            except ValueError:
                max_server_mb = 500.0
            if size_mb > max_server_mb:
                raise ValueError(
                    f"mega_file_too_large_for_server: Tệp Mega.nz nặng {size_mb:.0f} MB vượt ngưỡng {max_server_mb:.0f} MB "
                    f"của máy chủ (nhằm tránh chạm giới hạn 5 GB/6h của Mega). Hãy dùng Tiện ích Chrome hoặc App Windows để giải mã trực tiếp bằng mạng máy bạn."
                )
            os.makedirs(DOWNLOAD_DIR, exist_ok=True)
            safe_ext = re.sub(r"[^a-z0-9]", "", ext)[:8] or "mp4"
            dest_path = new_download_path(DOWNLOAD_DIR, "mega_", safe_ext)
            local_file_path = await download_and_decrypt_mega_file(
                dl_url, aes_key, iv, dest_path,
                client=client, progress_token=progress_token,
                total_size=size_bytes, expected_meta_mac=meta_mac,
            )

        return {
            "title": title,
            "filename": filename,
            "thumbnail_url": "",
            "direct_mp4_url": None if local_file_path else dl_url,
            "local_file_path": local_file_path,
            "original_url": url,
            "quality": quality,
            "duration": 0,
            "file_size_mb": size_mb,
            "available_formats": [
                {
                    "quality": "original",
                    "height": 0,
                    "width": 0,
                    "url": dl_url,
                    "ext": ext,
                }
            ],
            "mega_crypto": mega_crypto,
            "max_merge_height": 0,
            "downloaded_height": 0,
            "downloaded_vcodec": "",
            "downloaded_universal": None,
            "downloaded_media": {"measured": False},
            "is_audio_only": ext in ("mp3", "m4a", "wav", "flac", "ogg", "aac"),
            "uploader": "Mega.nz",
            "has_subtitles": False,
            "has_auto_captions": False,
            "available_subtitle_languages": [],
            "subtitle_source": "none",
            "chapters": [],
            "tags": [],
            "images": [],
            "audio_url": "",
        }
    finally:
        if own_client:
            await client.aclose()


class MegaExtractor(BaseExtractor):
    platform_name = "mega"
    supported_features = frozenset({"video", "audio", "batch"})
    cost_tier = "low"
    requires_cookie = False
    supports_proxy = False

    _URL_PATTERNS = [
        r"mega\.(?:co\.)?nz/file/[A-Za-z0-9_-]+",
        r"mega\.(?:co\.)?nz/#![A-Za-z0-9_-]+",
        r"mega\.(?:co\.)?nz/folder/[A-Za-z0-9_-]+",
        r"mega\.(?:co\.)?nz/#F![A-Za-z0-9_-]+",
    ]

    def detect_url(self, url: str) -> bool:
        return is_mega_url(url)

    def extract_single(self, url: str, **kwargs) -> ExtractResult:
        try:
            info = _run_sync(
                resolve_mega_share(
                    url,
                    quality=kwargs.get("quality", "video"),
                    client=kwargs.get("client"),
                )
            )
        except ValueError as e:
            code = str(e)
            meta = get_error_meta(code)
            return self._make_error_result(code, meta.get("user_message", code))
        except Exception as e:
            return self._make_error_result("processing_failed", str(e))

        dl_url = info.get("direct_mp4_url", "")
        return ExtractResult(
            platform="mega",
            source_type="audio" if info.get("is_audio_only") else "video",
            title=info.get("title", ""),
            author="Mega.nz",
            thumbnail=info.get("thumbnail_url", ""),
            direct_url=dl_url,
            formats=[FormatInfo(quality="original", url=dl_url, size_estimate_mb=info.get("file_size_mb", 0.0))],
            raw_metadata=info,
            extraction_method="mega_cs_api",
        )


REGISTRY.register(MegaExtractor())
