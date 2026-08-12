"""
Storage Abstraction
===================
Unified interface for file storage: local disk (default) or S3-compatible
object storage (Cloudflare R2, MinIO, AWS S3, Backblaze B2).

Backend selected by env:
  STORAGE_BACKEND=local (default) | s3
  STORAGE_S3_ENDPOINT=https://...
  STORAGE_S3_BUCKET=vidgrab-downloads
  STORAGE_S3_ACCESS_KEY=...
  STORAGE_S3_SECRET_KEY=...
  STORAGE_S3_PUBLIC_URL=https://cdn.example.com  (optional, for signed URL base)
  STORAGE_S3_SIGNED_URL_TTL=3600  (signed URL TTL in seconds, default 3600)
  STORAGE_LOCAL_DIR=/app/downloads  (overrides DOWNLOAD_DIR)
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from app.core.structured_log import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class StorageBackend(ABC):
    """
    Abstract interface for file storage operations.

    All methods are async so implementations can use async I/O or thread
    pool offloading for blocking operations.
    """

    @abstractmethod
    async def save_file(self, src_path: str, dest_key: str) -> str:
        """
        Persist *src_path* to storage under *dest_key*.

        Returns the publicly accessible URL (or local path) for the file.
        """

    @abstractmethod
    async def get_download_url(self, key: str, ttl_seconds: int = 3600) -> str:
        """
        Return a URL that allows the caller to download *key*.

        For S3 this is a presigned URL (valid for *ttl_seconds*).
        For local storage this is the API-relative path.
        """

    @abstractmethod
    async def delete_file(self, key: str) -> bool:
        """Delete *key* from storage.  Returns True if the file was deleted."""

    @abstractmethod
    async def file_exists(self, key: str) -> bool:
        """Return True if *key* exists in storage."""

    @abstractmethod
    async def get_file_size(self, key: str) -> int:
        """Return the size of *key* in bytes."""

    @abstractmethod
    async def list_files(self, prefix: str = "") -> list[str]:
        """Return all keys whose name starts with *prefix*."""


# ---------------------------------------------------------------------------
# Local-disk backend
# ---------------------------------------------------------------------------

class LocalStorageBackend(StorageBackend):
    """
    Store files on the local filesystem under *local_dir*.

    The directory is created on first use if it does not already exist.
    Files are served via the FastAPI ``/api/v1/download/{key}`` route so
    the application must mount that endpoint separately.
    """

    def __init__(self) -> None:
        self._dir = Path(
            os.getenv("STORAGE_LOCAL_DIR")
            or os.getenv("DOWNLOAD_DIR", "/app/downloads")
        ).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        logger.info("local_storage_ready", extra={"path": str(self._dir)})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _abs(self, key: str) -> Path:
        """Resolve *key* to an absolute path inside the storage root."""
        # Sanitise to prevent path traversal
        safe_key = Path(key).name if "/" not in key else key.lstrip("/")
        return self._dir / safe_key

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    async def save_file(self, src_path: str, dest_key: str) -> str:
        dest = self._abs(dest_key)
        dest.parent.mkdir(parents=True, exist_ok=True)

        def _move() -> None:
            if src_path != str(dest):
                shutil.move(src_path, str(dest))

        await asyncio.get_event_loop().run_in_executor(None, _move)
        logger.info("file_saved", extra={"key": dest_key, "path": str(dest)})
        return f"/api/v1/download/{dest_key}"

    async def get_download_url(self, key: str, ttl_seconds: int = 3600) -> str:
        # Local: serve via API route; ttl_seconds is ignored
        return f"/api/v1/download/{key}"

    async def delete_file(self, key: str) -> bool:
        target = self._abs(key)
        try:
            target.unlink()
            logger.info("file_deleted", extra={"key": key})
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            logger.warning("file_delete_failed", extra={"key": key, "error": str(exc)})
            return False

    async def file_exists(self, key: str) -> bool:
        return self._abs(key).is_file()

    async def get_file_size(self, key: str) -> int:
        target = self._abs(key)
        try:
            return target.stat().st_size
        except FileNotFoundError:
            return 0

    async def list_files(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        for dirpath, _, filenames in os.walk(self._dir):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, self._dir)
                if not prefix or rel.startswith(prefix):
                    keys.append(rel)
        return keys

    # ------------------------------------------------------------------
    # Local-only helpers
    # ------------------------------------------------------------------

    def get_free_bytes(self) -> int:
        """Return bytes of free disk space on the storage partition."""
        usage = shutil.disk_usage(self._dir)
        return usage.free


# ---------------------------------------------------------------------------
# S3-compatible backend
# ---------------------------------------------------------------------------

class S3StorageBackend(StorageBackend):
    """
    Store files on an S3-compatible object store (AWS S3, Cloudflare R2,
    MinIO, Backblaze B2, etc.).

    Required env vars:
        STORAGE_S3_ENDPOINT      — e.g. https://account-id.r2.cloudflarestorage.com
        STORAGE_S3_BUCKET        — bucket name
        STORAGE_S3_ACCESS_KEY
        STORAGE_S3_SECRET_KEY

    Optional:
        STORAGE_S3_PUBLIC_URL    — CDN base URL; when set, get_download_url
                                   returns a direct CDN URL instead of presigned.
        STORAGE_S3_SIGNED_URL_TTL — presigned URL TTL in seconds (default 3600)
    """

    def __init__(self) -> None:
        try:
            import boto3  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for S3 storage. "
                "Install it with: pip install boto3"
            ) from exc

        self._bucket = os.environ["STORAGE_S3_BUCKET"]
        self._public_url: Optional[str] = os.getenv("STORAGE_S3_PUBLIC_URL")
        self._signed_ttl = int(os.getenv("STORAGE_S3_SIGNED_URL_TTL", "3600"))

        self._client = boto3.client(
            "s3",
            endpoint_url=os.getenv("STORAGE_S3_ENDPOINT"),
            aws_access_key_id=os.environ["STORAGE_S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["STORAGE_S3_SECRET_KEY"],
        )
        logger.info(
            "s3_storage_ready",
            extra={
                "bucket": self._bucket,
                "endpoint": os.getenv("STORAGE_S3_ENDPOINT", "aws"),
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_sync(self, fn, *args, **kwargs):
        """Run a blocking boto3 call in the default executor."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    async def save_file(self, src_path: str, dest_key: str) -> str:
        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.upload_file(src_path, self._bucket, dest_key),
        )
        logger.info("s3_file_uploaded", extra={"key": dest_key, "bucket": self._bucket})
        return await self.get_download_url(dest_key, ttl_seconds=self._signed_ttl)

    async def get_download_url(self, key: str, ttl_seconds: int = 3600) -> str:
        if self._public_url:
            return f"{self._public_url.rstrip('/')}/{key}"

        url: str = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl_seconds,
            ),
        )
        return url

    async def delete_file(self, key: str) -> bool:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.delete_object(Bucket=self._bucket, Key=key),
            )
            logger.info("s3_file_deleted", extra={"key": key})
            return True
        except Exception as exc:
            logger.warning("s3_delete_failed", extra={"key": key, "error": str(exc)})
            return False

    async def file_exists(self, key: str) -> bool:
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.head_object(Bucket=self._bucket, Key=key),
            )
            return True
        except Exception:
            return False

    async def get_file_size(self, key: str) -> int:
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.head_object(Bucket=self._bucket, Key=key),
            )
            return result.get("ContentLength", 0)
        except Exception:
            return 0

    async def list_files(self, prefix: str = "") -> list[str]:
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")

        def _paginate() -> list[str]:
            result: list[str] = []
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    result.append(obj["Key"])
            return result

        keys = await asyncio.get_event_loop().run_in_executor(None, _paginate)
        return keys


# ---------------------------------------------------------------------------
# StorageManager — singleton facade
# ---------------------------------------------------------------------------

class StorageManager:
    """
    Singleton facade that selects and lazily initialises the correct
    storage backend based on the ``STORAGE_BACKEND`` env variable.

    Usage::

        from app.core.storage import storage

        url = await storage.storage.save_file(local_path, "video/abc123.mp4")
        ok  = storage.estimate_fits(file_size_bytes)
        n   = await storage.cleanup_expired(max_age_seconds=86400)
    """

    def __init__(self) -> None:
        self._backend: Optional[StorageBackend] = None

    def get_backend(self) -> StorageBackend:
        """Instantiate (once) and return the configured storage backend."""
        if self._backend is None:
            backend_name = os.getenv("STORAGE_BACKEND", "local").lower()
            if backend_name == "s3":
                self._backend = S3StorageBackend()
            else:
                if backend_name not in ("local", ""):
                    logger.warning(
                        "unknown_storage_backend",
                        extra={"value": backend_name, "fallback": "local"},
                    )
                self._backend = LocalStorageBackend()
        return self._backend

    @property
    def storage(self) -> StorageBackend:
        """Lazy-init singleton backend accessor."""
        return self.get_backend()

    def estimate_fits(self, size_bytes: int) -> bool:
        """
        Return True if *size_bytes* can likely be stored without filling the disk.

        For local storage: requires 1.5× headroom over the file size.
        For S3: always True (object stores are effectively unbounded).
        """
        backend = self.get_backend()
        if isinstance(backend, LocalStorageBackend):
            free = backend.get_free_bytes()
            required = int(size_bytes * 1.5)
            fits = free > required
            if not fits:
                logger.warning(
                    "disk_estimate_tight",
                    extra={
                        "free_bytes": free,
                        "required_bytes": required,
                        "file_bytes": size_bytes,
                    },
                )
            return fits
        # S3 / other backends — assume it fits
        return True

    async def cleanup_expired(self, max_age_seconds: int = 86400) -> int:
        """
        Delete files older than *max_age_seconds* from the downloads directory.

        Only operates on the local backend; returns the count of files deleted.
        For S3, returns 0 (lifecycle policies should be used instead).
        """
        backend = self.get_backend()
        if not isinstance(backend, LocalStorageBackend):
            return 0

        cutoff = time.time() - max_age_seconds
        deleted = 0
        keys = await backend.list_files()

        for key in keys:
            target = backend._abs(key)
            try:
                mtime = target.stat().st_mtime
                if mtime < cutoff:
                    target.unlink()
                    deleted += 1
                    logger.info("cleanup_deleted", extra={"key": key, "age_seconds": int(time.time() - mtime)})
            except FileNotFoundError:
                pass  # already gone — race condition, ignore
            except OSError as exc:
                logger.warning("cleanup_error", extra={"key": key, "error": str(exc)})

        if deleted:
            logger.info("cleanup_complete", extra={"deleted_count": deleted, "max_age_seconds": max_age_seconds})
        return deleted


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

storage = StorageManager()
