"""Shared, atomic fixed-window rate limiting backed by Redis TTL keys."""

import hashlib
import logging
import time

import redis

from app.core.config import settings
from app.shared.redis_client import client

logger = logging.getLogger(__name__)

_INCREMENT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


class RateLimitUnavailable(RuntimeError):
    """The shared limiter could not enforce policy; callers must fail closed."""


class RedisRateLimiter:
    def __init__(self, service: str, limit: int | None = None, window: int | None = None):
        self.service = service
        self.limit = limit if limit is not None else settings.rate_limit
        self.window = window if window is not None else settings.rate_window_seconds

    def check(self, subject: str) -> bool:
        # Hash subjects so usernames are absent from Redis key names.
        identity = hashlib.sha256(subject.encode("utf-8")).hexdigest()[:24]
        window = int(time.time()) // self.window
        key = f"ratelimit:{self.service}:{identity}:{window}"
        try:
            count = int(client.eval(_INCREMENT, 1, key, self.window + 2))
        except redis.RedisError as exc:
            logger.exception("Shared rate limit unavailable service=%s", self.service)
            raise RateLimitUnavailable("Shared rate limit is unavailable") from exc
        return count <= self.limit
