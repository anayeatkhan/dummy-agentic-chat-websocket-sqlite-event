"""
Chat Service — Cloud Run, port 8002
─────────────────────────────────────
REST API + Pub/Sub consumer background task.

What changed vs the mock version
  SQLite / aiosqlite   →  Cloud SQL (PostgreSQL) / asyncpg pool
  asyncio.Queue        →  GCP Pub/Sub pull consumer
  No Redis             →  Reads Redis buffer for status=streaming messages
                          so callers see live partial text while the stream
                          is still in progress on another Cloud Run instance.

Endpoints
  GET /health
  GET /sessions/{session_id}/messages
  GET /sessions/{session_id}/messages/{message_id}/events

Redis enrichment (GET /sessions/.../messages)
  When a message row has status='streaming', the final text is not yet in
  Cloud SQL (agent_service only publishes the final update when done).
  We enrich the response with whatever is currently in the Redis buffer
  so the UI can show partial text even across Cloud Run instances.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from chat_service import database as db
from chat_service.consumer import consume
from shared.pubsub import ensure_topic_and_subscription
from shared.redis_buffer import get_buffer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    # 1. Ensure Cloud SQL schema exists (via schema.sql, run separately)
    # 2. Initialise asyncpg connection pool
    await db.init_pool()

    # 3. Ensure Pub/Sub topic + subscription exist (idempotent)
    await asyncio.to_thread(ensure_topic_and_subscription)

    # 4. Start the Pub/Sub consumer as a background asyncio task
    consumer_task = asyncio.create_task(consume(), name="pubsub-consumer")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await db.close_pool()


app = FastAPI(title="Chat Service (GCP)", lifespan=lifespan)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "chat_service"}


# ── Messages ──────────────────────────────────────────────────────────────────

@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    """
    Return all messages for *session_id* ordered by seq ascending.

    Redis enrichment:
      For any message still in status=streaming, the final text has not been
      written to Cloud SQL yet (agent_service only flushes on complete).
      We check Redis and overlay the latest partial content so the client
      always sees the most up-to-date text regardless of which Cloud Run
      instance is serving this request.

    content_source field:
      "db"           — message is complete, content is from Cloud SQL
      "redis_buffer" — message is streaming, content is live from Redis
      "db_partial"   — message is streaming but Redis key has expired (edge case)
    """
    messages = await db.get_session_messages(session_id)
    if not messages:
        raise HTTPException(
            status_code=404,
            detail=f"No messages found for session '{session_id}'.",
        )

    for msg in messages:
        if msg["status"] == "streaming":
            buffered = await get_buffer(msg["id"])
            if buffered:
                msg["content"]        = buffered
                msg["content_source"] = "redis_buffer"
            else:
                msg["content_source"] = "db_partial"
        else:
            msg["content_source"] = "db"

    return {
        "session_id": session_id,
        "count": len(messages),
        "messages": messages,
    }


# ── Workflow events ───────────────────────────────────────────────────────────

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
