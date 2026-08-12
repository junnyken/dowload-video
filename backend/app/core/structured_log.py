"""
Structured Logging
==================
JSON-formatted logs with correlation ID, tenant_id, platform, user_id
for easy log aggregation (CloudWatch, Loki, Papertrail, etc.).

Usage:
    from app.core.structured_log import get_logger
    logger = get_logger(__name__)
    logger.info("job_started", job_id=job_id, platform="youtube", user_id=user_id)

In FastAPI routes: correlation_id auto-injected via request.state.request_id.
In Celery tasks: set_task_context(task_id, job_id, tenant_id) to propagate IDs.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import traceback
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# ---------------------------------------------------------------------------
# Context variables — propagated automatically inside asyncio tasks / threads
# ---------------------------------------------------------------------------
_correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)
_task_id: ContextVar[Optional[str]] = ContextVar("task_id", default=None)
_job_id: ContextVar[Optional[str]] = ContextVar("job_id", default=None)
_tenant_id: ContextVar[Optional[str]] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[Optional[str]] = ContextVar("user_id", default=None)
_platform: ContextVar[Optional[str]] = ContextVar("platform", default=None)


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------

class JsonFormatter(logging.Formatter):
    """
    Render every log record as a single-line JSON object.

    Standard log fields emitted:
        ts            — ISO-8601 UTC timestamp
        level         — CRITICAL / ERROR / WARNING / INFO / DEBUG
        logger        — logger name (dotted module path)
        msg           — the log message

    Context fields (omitted when None):
        correlation_id, task_id, job_id, tenant_id, user_id, platform, error_code

    Optional fields (only when present on the record):
        duration_ms   — float, milliseconds
        exc           — single-line escaped traceback
    """

    # Fields to strip from the raw record.__dict__ before dumping extras
    _SKIP_FIELDS = frozenset(
        {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process",
            "taskName", "message",
            # Added by our formatter
            "ts", "level", "logger",
        }
    )

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003
        # Resolve message (handles %-style and lazy string args)
        record.message = record.getMessage()

        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.message,
        }

        # --- correlation / context fields (omit when None) ---
        ctx_fields = {
            "correlation_id": _correlation_id.get(),
            "task_id": _task_id.get(),
            "job_id": _job_id.get(),
            "tenant_id": _tenant_id.get(),
            "user_id": _user_id.get(),
            "platform": _platform.get(),
        }
        for k, v in ctx_fields.items():
            if v is not None:
                doc[k] = v

        # --- per-record optional fields ---
        rd = record.__dict__
        if "error_code" in rd and rd["error_code"] is not None:
            doc["error_code"] = rd["error_code"]
        if "duration_ms" in rd and rd["duration_ms"] is not None:
            doc["duration_ms"] = rd["duration_ms"]

        # --- exception ---
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info).replace("\n", "\\n")
        elif record.exc_text:
            doc["exc"] = record.exc_text.replace("\n", "\\n")

        # --- any extra kwargs passed via logger.info("msg", extra={...}) ---
        extra_skip = self._SKIP_FIELDS | set(doc.keys())
        for k, v in rd.items():
            if not k.startswith("_") and k not in extra_skip:
                try:
                    json.dumps(v)  # only include JSON-serialisable extras
                    doc[k] = v
                except (TypeError, ValueError):
                    doc[k] = repr(v)

        return json.dumps(doc, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------

def set_request_context(
    correlation_id: Optional[str],
    user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> None:
    """Set context variables for the current request scope."""
    _correlation_id.set(correlation_id)
    _user_id.set(user_id)
    _tenant_id.set(tenant_id)


def set_task_context(
    task_id: Optional[str],
    job_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    platform: Optional[str] = None,
) -> None:
    """Set context variables for a Celery task scope."""
    _task_id.set(task_id)
    _job_id.set(job_id)
    _tenant_id.set(tenant_id)
    _platform.set(platform)


def clear_context() -> None:
    """Reset all context variables to None (call after each request/task)."""
    _correlation_id.set(None)
    _task_id.set(None)
    _job_id.set(None)
    _tenant_id.set(None)
    _user_id.set(None)
    _platform.set(None)


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

_CONFIGURED_LOGGERS: set[str] = set()


def get_logger(name: str) -> logging.Logger:
    """
    Return a logger that writes JSON to stdout.

    Idempotent — calling get_logger with the same name twice is safe.
    """
    logger = logging.getLogger(name)
    if name not in _CONFIGURED_LOGGERS:
        _attach_json_handler(logger)
        _CONFIGURED_LOGGERS.add(name)
    return logger


def _attach_json_handler(logger: logging.Logger) -> None:
    """Add a stdout StreamHandler with JsonFormatter if not already present."""
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and isinstance(h.formatter, JsonFormatter):
            return  # already wired
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False  # avoid double-printing via root logger


# ---------------------------------------------------------------------------
# Global configure_logging — call once at startup
# ---------------------------------------------------------------------------

_logging_configured = False


def configure_logging(level: str = "INFO") -> None:
    """
    Idempotent global logging setup.

    Sets root logger and well-known framework loggers (uvicorn, celery,
    fastapi) to emit JSON via stdout.  Must be called once during app
    startup (e.g. in FastAPI lifespan or Celery worker_init signal).
    """
    global _logging_configured
    if _logging_configured:
        return
    _logging_configured = True

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    json_handler = logging.StreamHandler(sys.stdout)
    json_handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    # Remove any pre-existing handlers to avoid plain-text duplicates
    root.handlers.clear()
    root.addHandler(json_handler)
    root.setLevel(numeric_level)

    # Silence verbose third-party loggers to WARNING to reduce noise
    for noisy in ("urllib3", "boto3", "botocore", "s3transfer", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Ensure uvicorn / celery also use JSON — they add their own handlers;
    # propagate=True (default) means our root handler picks them up.
    for fw in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.task"):
        fw_logger = logging.getLogger(fw)
        fw_logger.handlers.clear()
        fw_logger.propagate = True


# ---------------------------------------------------------------------------
# log_duration context manager
# ---------------------------------------------------------------------------

@contextmanager
def log_duration(
    logger: logging.Logger,
    event: str,
    level: int = logging.INFO,
    **kwargs: Any,
) -> Generator[None, None, None]:
    """
    Context manager that logs *event* with duration_ms on exit.

    Example::

        with log_duration(logger, "file_upload", key="video.mp4"):
            upload(...)
    """
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.log(
            level,
            event,
            extra={"duration_ms": round(elapsed_ms, 2), **kwargs},
        )


# ---------------------------------------------------------------------------
# FastAPI middleware
# ---------------------------------------------------------------------------

class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    """
    FastAPI / Starlette middleware that:
    1. Extracts ``request.state.request_id`` set upstream by CorrelationIDMiddleware.
    2. Populates structured-log context vars for the request lifetime.
    3. On response: emits an ``http_request`` log line with method, path,
       status_code, and duration_ms.
    4. Clears context vars after the response, even on exception.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._logger = get_logger("vidgrab.http")

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        correlation_id: Optional[str] = getattr(request.state, "request_id", None)
        # Optional: extract tenant / user from auth headers / JWT if present
        user_id: Optional[str] = None
        tenant_id: Optional[str] = None

        set_request_context(
            correlation_id=correlation_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

        start = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            self._logger.error(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": elapsed_ms,
                },
                exc_info=True,
            )
            clear_context()
            raise
        else:
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
            self._logger.info(
                "http_request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": elapsed_ms,
                },
            )
            clear_context()
            return response
