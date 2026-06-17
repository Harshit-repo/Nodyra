import logging

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

# Connection pool tuning — a slow or partitioned Redis must not exhaust the
# API's file descriptors or hang the event loop indefinitely.
#   socket_connect_timeout: TCP handshake deadline
#   socket_timeout:         per-command response deadline
#   max_connections:        hard ceiling on concurrent pooled connections
#   retry_on_timeout:       auto-retry idempotent commands (GET, PUBLISH…)
_REDIS_SOCKET_CONNECT_TIMEOUT = 5
_REDIS_SOCKET_TIMEOUT = 10
_REDIS_MAX_CONNECTIONS = 50


def _create_redis_client() -> redis.Redis:
    try:
        return redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=_REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=_REDIS_SOCKET_TIMEOUT,
            max_connections=_REDIS_MAX_CONNECTIONS,
            retry_on_timeout=True,
        )
    except Exception:
        logger.warning("Redis URL is configured but the client could not be created")
        raise


redis_client: redis.Redis = _create_redis_client()
