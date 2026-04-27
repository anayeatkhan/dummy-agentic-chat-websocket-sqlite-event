"""
Chat Service — port 8002
────────────────────────
Endpoints
  GET  /sessions/{session_id}/messages
       Returns all messages for a session ordered by seq.

  GET  /sessions/{session_id}/messages/{message_id}/events
       Returns workflow_events for a specific message.

  GET  /health

Background task
  Runs the queue consumer (chat_service.consumer.consume) which reads events
  published by agent_service and persists them to SQLite.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from chat_service import database as db
from chat_service.consumer import consume

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    consumer_task = asyncio.create_task(consume(), name="queue-consumer")
    yield
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Chat Service", lifespan=lifespan)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "chat_service"}


@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    """
    Return all messages for *session_id* ordered by seq ascending.

    Mock user-isolation: in production this would enforce
    `user_id = current_setting('app.user_id')` via RLS; here we simply return
    all rows for the session (the agent already scopes by session_id).
    """
    messages = await db.get_session_messages(session_id)
    if not messages:
        raise HTTPException(
            status_code=404,
            detail=f"No messages found for session '{session_id}'.",
        )
    return {
        "session_id": session_id,
        "count": len(messages),
        "messages": messages,
    }


@app.get("/sessions/{session_id}/messages/{message_id}/events")
async def get_workflow_events(session_id: str, message_id: str):
    """Return workflow_events attached to *message_id*."""
    events = await db.get_message_workflow_events(message_id)
    if not events:
        raise HTTPException(
            status_code=404,
            detail=f"No workflow events found for message '{message_id}'.",
        )
    return {
        "message_id": message_id,
        "count": len(events),
        "events": events,
    }
