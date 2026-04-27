"""
Queue consumer for chat_service.

Reads events published by agent_service from the shared local_queue and
persists them to SQLite.

Event types handled
───────────────────
  "message"        → upsert session, INSERT into messages
  "message_update" → UPDATE messages (content / status / completed_at)
  "workflow_event" → INSERT into workflow_events
"""
import asyncio
import logging

from shared.local_queue import event_queue
from chat_service import database as db

logger = logging.getLogger(__name__)


async def consume() -> None:
    logger.info("[consumer] started — waiting for events")
    while True:
        event = await event_queue.get()
        try:
            await _dispatch(event)
        except Exception as exc:
            logger.error("[consumer] unhandled error: %s | event=%s", exc, event)
        finally:
            event_queue.task_done()


async def _dispatch(event: dict) -> None:
    etype = event.get("type")

    if etype == "message":
        session_id = event["session_id"]
        user_id    = event["user_id"]
        msg_id     = event["message_id"]

        # Ensure the session row exists before inserting the FK-referencing message
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
        msg_id = event["message_id"]
        await db.update_message(
            message_id   = msg_id,
            content      = event["content"],
            status       = event["status"],
            completed_at = event.get("completed_at"),
        )
        logger.debug(
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
