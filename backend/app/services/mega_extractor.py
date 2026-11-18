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
    Parse a Mega.nz URL into {'kind': 'file'|'folder', 'id': ..., 'key': ...}.
    Returns None if not a valid Mega URL.
    """
    raw = (url or "").strip()
    if not raw:
        return None

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
    Derive (aes_key_16B, iv_8B, meta_mac_8B) from a Mega URL key.
    For 32-byte file keys, the 128-bit AES key is the XOR of the first 16 bytes
    and the second 16 bytes; bytes 16..24 are the 64-bit CTR nonce/IV; bytes 24..32
    are the meta MAC.
    """
    raw = mega_b64decode(key_b64)
    if len(raw) >= 32:
        aes_key = bytes(a ^ b for a, b in zip(raw[:16], raw[16:32]))
        iv = raw[16:24]
        meta_mac = raw[24:32]
        return aes_key, iv, meta_mac
    if len(raw) >= 16:
        return raw[:16], b"\x00" * 8, b"\x00" * 8
    raise ValueError("invalid_url")


def decrypt_mega_attributes(at_b64: str, aes_key: bytes) -> dict[str, Any]:
    """
    Decrypt Mega 'at' attribute blob using AES-128-CBC (zero IV).
    Plaintext has format: b'MEGA{"n":"filename.mp4",...}\\x00...'
    """
    ct = mega_b64decode(at_b64)
    if len(ct) % 16 != 0:
        ct = ct + (b"\x00" * (16 - (len(ct) % 16)))
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b"\x00" * 16))
    decryptor = cipher.decryptor()
    pt = decryptor.update(ct) + decryptor.finalize()
    pt_str = pt.rstrip(b"\x00").decode("utf-8", errors="replace")
    if pt_str.startswith("MEGA"):
        json_part = pt_str[4:].strip()
        return json.loads(json_part)
    # Fallback: locate JSON object
    idx = pt_str.find("{")
    ridx = pt_str.rfind("}")
    if idx != -1 and ridx != -1 and ridx > idx:
        return json.loads(pt_str[idx : ridx + 1])
    return {"n": "mega_file"}


def encrypt_mega_attributes(attrs: dict[str, Any], aes_key: bytes) -> str:
    """Encrypt a Mega attribute dict into an 'at' base64url string (AES-128-CBC, zero IV)."""
    raw = ("MEGA" + json.dumps(attrs, separators=(",", ":"))).encode("utf-8")
    pad_len = (16 - (len(raw) % 16)) % 16
    padded = raw + (b"\x00" * pad_len)
    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(b"\x00" * 16))
    encryptor = cipher.encryptor()
    ct = encryptor.update(padded) + encryptor.finalize()
    return mega_b64encode(ct)


def decrypt_mega_stream(encrypted_bytes: bytes, aes_key: bytes, iv: bytes) -> bytes:
    """Decrypt a Mega file stream using AES-128-CTR with initial counter (iv_8B + 8 zero bytes)."""
    counter_init = (iv[:8] if len(iv) >= 8 else iv.ljust(8, b"\x00")) + (b"\x00" * 8)
    cipher = Cipher(algorithms.AES(aes_key), modes.CTR(counter_init))
    decryptor = cipher.decryptor()
    return decryptor.update(encrypted_bytes) + decryptor.finalize()


async def download_and_decrypt_mega_file(
    download_url: str,
    aes_key: bytes,
    iv: bytes,
    dest_path: str,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """
    Download an encrypted Mega stream from `download_url`, decrypt it on the fly
    using AES-128-CTR, and write the plaintext file to `dest_path`.
    """
    counter_init = (iv[:8] if len(iv) >= 8 else iv.ljust(8, b"\x00")) + (b"\x00" * 8)
    cipher = Cipher(algorithms.AES(aes_key), modes.CTR(counter_init))
    decryptor = cipher.decryptor()

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT), follow_redirects=True)

    try:
        resp = await client.get(download_url)
        if resp.status_code == 509:
            raise ValueError("share_quota_exceeded")
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            fh.write(decryptor.update(resp.content) + decryptor.finalize())
        return dest_path
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


async def resolve_mega_share(
    url: str,
    quality: str = "video",
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Resolve a public Mega.nz share link into a dict compatible with
    extract_video_info() in routes.py.
    """
    parsed = parse_mega_url(url)
    if not parsed:
        raise ValueError("invalid_url")

    kind = parsed["kind"]
    item_id = parsed["id"]
    key_b64 = parsed["key"]
    if not key_b64:
        raise ValueError("share_password_required")

    aes_key, iv, meta_mac = derive_mega_file_key(key_b64)
    counter_init = iv + (b"\x00" * 8)

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT), follow_redirects=True)

    try:
        if kind == "folder":
            resp = await client.post(
                _API_URL,
                params={"id": "1", "n": item_id},
                json=[{"a": "f", "c": 1, "r": 1}],
            )
            if resp.status_code == 509:
                raise ValueError("share_quota_exceeded")
            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, int):
                _check_mega_error(payload)
            if isinstance(payload, list) and payload and isinstance(payload[0], int):
                _check_mega_error(payload[0])

            node_Block = payload[0] if isinstance(payload, list) and payload else {}
            files = (node_Block.get("f") or []) if isinstance(node_Block, dict) else []
            entries = []
            for f in files:
                if f.get("t") == 0:  # file node
                    entries.append({
                        "id": f.get("h", ""),
                        "title": f.get("h", "mega_file"),
                        "size": int(f.get("s") or 0),
                    })
            return {
                "title": f"Mega Folder {item_id}",
                "thumbnail_url": "",
                "direct_mp4_url": url,
                "original_url": url,
                "quality": quality,
                "duration": 0,
                "file_size_mb": 0.0,
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

        # Single file request
        resp = await client.post(
            _API_URL,
            params={"id": "1"},
            json=[{"a": "g", "g": 1, "p": item_id}],
        )
        if resp.status_code == 509:
            raise ValueError("share_quota_exceeded")
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
        filename = attrs.get("n") or f"mega_{item_id}.mp4"
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

        return {
            "title": title,
            "filename": filename,
            "thumbnail_url": "",
            "direct_mp4_url": dl_url,
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
