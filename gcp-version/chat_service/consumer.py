"""
chat_service/consumer.py
─────────────────────────
GCP Pub/Sub pull consumer.

What changed vs the mock version
  asyncio.Queue.get()  →  pubsub.pull_messages()  (polls the Pub/Sub subscription)
  Always-ack           →  selective ack:
                            - ack on success  (Pub/Sub removes the message)
                            - don't ack on error  (Pub/Sub redelivers after deadline)

Pull vs Push subscription
  This uses the PULL model: the consumer asks Pub/Sub for messages.
  The alternative PUSH model has Pub/Sub call an HTTP endpoint — easier for
  Cloud Run but harder to test locally. Pull is simpler here.

Retry behaviour
  If _dispatch() raises an exception the ack_id is not added to the ack list.
  Pub/Sub will redeliver the message after ack_deadline_seconds (60 s).
  In production you would add a dead-letter topic after N redelivery attempts
  so poison-pill messages don't loop forever.
"""
import asyncio
import logging

from shared.pubsub import pull_messages, acknowledge
from chat_service import database as db

logger = logging.getLogger(__name__)

POLL_INTERVAL_EMPTY = 1.0   # seconds to wait when subscription is idle
MAX_MESSAGES        = 20    # messages per pull call


async def consume() -> None:
    """
    Infinite loop that polls the Pub/Sub subscription and dispatches events.
    Runs as a background asyncio task started by chat_service lifespan.
    """
    logger.info("[consumer] started — polling Pub/Sub subscription")

    while True:
        try:
            messages = await pull_messages(max_messages=MAX_MESSAGES)
        except Exception as exc:
            logger.error("[consumer] pull failed: %s — retrying in 5 s", exc)
            await asyncio.sleep(5)
            continue

        if not messages:
            # No messages — yield to other coroutines, then poll again
            await asyncio.sleep(POLL_INTERVAL_EMPTY)
            continue

        ack_ids: list[str] = []

        for ack_id, event in messages:
            try:
                await _dispatch(event)
                # Only ack after successful processing
                ack_ids.append(ack_id)
            except Exception as exc:
                # Log the error but DO NOT ack — Pub/Sub will redeliver
                logger.error(
                    "[consumer] dispatch error: %s | event=%s", exc, event
                )

        if ack_ids:
            await acknowledge(ack_ids)


async def _dispatch(event: dict) -> None:
    """
    Route an event to the correct database operation.

    Event types:
      "message"        → upsert session + INSERT message row
      "message_update" → UPDATE message (only complete / failed, not per-token)
      "workflow_event" → INSERT workflow_events row
    """
    etype = event.get("type")

    if etype == "message":
        session_id = event["session_id"]
        user_id    = event["user_id"]
        msg_id     = event["message_id"]

        # Ensure the FK-referenced session row exists first
        await db.upsert_session(session_id, user_id)
        await db.insert_message(
            message_id   = msg_id,
            session_id   = session_id,
            user_id      = user_id,
            role         = event["role"],
            content      = event.get("content", ""),
            status       = event["status"],
            created_at   = event["created_at"],
            completed_at = event.get("completed_at"),
        )
        logger.info(
            "[consumer] INSERT message id=%s role=%s status=%s",
            msg_id, event["role"], event["status"],
        )

    elif etype == "message_update":
        # agent_service only publishes this for status=complete or status=failed
        # so we are NOT hammering Cloud SQL with one UPDATE per streaming token
        msg_id = event["message_id"]
        await db.update_message(
            message_id   = msg_id,
            content      = event["content"],
            status       = event["status"],
            completed_at = event.get("completed_at"),
        )
        logger.info(
            "[consumer] UPDATE message id=%s status=%s",
            msg_id, event["status"],
        )

    elif etype == "workflow_event":
        msg_id = event["message_id"]
        await db.insert_workflow_event(
            message_id = msg_id,
            step_name  = event["step_name"],
            payload    = event.get("payload"),
            created_at = event["created_at"],
        )
        logger.info(
            "[consumer] INSERT workflow_event message_id=%s step=%s",
            msg_id, event["step_name"],
        )

    else:
        logger.warning("[consumer] unknown event type=%r — skipping", etype)
