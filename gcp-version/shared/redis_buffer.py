"""
shared/redis_buffer.py
──────────────────────
Redis-backed streaming buffer for in-progress AI responses.

Why Redis and not the database?
  During streaming, the assistant message is updated on every token (~150 ms).
  Writing every partial update to Cloud SQL would mean dozens of UPDATE
  statements per response under load. Redis handles this in microseconds with
  no disk I/O pressure on the primary database.

Key pattern:   stream:buffer:{message_id}
Value:         current accumulated text (plain string)
TTL:           STREAM_BUFFER_TTL seconds (default 300 — 5 minutes)
               Auto-expires so stale buffers from crashed streams are cleaned up.

Lifecycle:
  1. agent_service SETEX on every token  (overwrites previous value)
  2. agent_service DEL when stream completes / fails
  3. chat_service GET in the messages endpoint — only for status=streaming rows
     so the caller sees live partial text while generation is ongoing.
"""
import logging
import os

import redis.asyncio as aioredis

logger   = logging.getLogger(__name__)
REDIS_URL   = os.environ.get("REDIS_URL", "redis://localhost:6379")
BUFFER_TTL  = int(os.environ.get("STREAM_BUFFER_TTL", "300"))

# Module-level singleton Redis client.
# redis.asyncio.Redis is safe to share across coroutines.
_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _client


def _key(message_id: str) -> str:
    return f"stream:buffer:{message_id}"


async def set_buffer(message_id: str, text: str) -> None:
    """
    Write the current accumulated streaming text to Redis.
    Resets the TTL on each call so the key lives at least BUFFER_TTL
    seconds beyond the last token.
    """
    await get_redis().setex(_key(message_id), BUFFER_TTL, text)
    logger.debug("Redis SET stream:buffer:%s (%d chars)", message_id, len(text))


async def get_buffer(message_id: str) -> str | None:
    """
    Read the latest partial text for a streaming message.
    Returns None if the key has expired or was never written.
    """
    value = await get_redis().get(_key(message_id))
    logger.debug("Redis GET stream:buffer:%s → %s", message_id, "hit" if value else "miss")
    return value


async def delete_buffer(message_id: str) -> None:
    """
    Remove the Redis buffer once the message is complete or failed.
    The final text has been (or will be) written to Cloud SQL by the consumer.
    """
    await get_redis().delete(_key(message_id))
    logger.debug("Redis DEL stream:buffer:%s", message_id)
