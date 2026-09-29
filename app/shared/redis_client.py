"""One pooled Redis client per process. Connection failures are surfaced to callers."""

import logging

import redis

from app.core.config import settings

logger = logging.getLogger(__name__)
client = redis.Redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
    health_check_interval=10,
)


def redis_health() -> bool:
    try:
        return bool(client.ping())
    except redis.RedisError:
        logger.exception("Redis health check failed")
        return False
