"""
Redis Client Singleton
========================
Single process-level Redis connection instead of creating a new client
on every request. Shared across all in-process call sites.
"""

import os
import redis

_client: "redis.Redis | None" = None


def get_redis() -> "redis.Redis":
    global _client
    if _client is None:
        _client = redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/0"),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        )
    return _client
