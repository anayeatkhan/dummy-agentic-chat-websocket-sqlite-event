"""
Agent Service — Cloud Run, port 8001
─────────────────────────────────────
WebSocket /ws/{session_id}

What changed vs the mock version
  asyncio.Queue  →  GCP Pub/Sub  (shared across all Cloud Run instances)
  SQLite writes  →  Redis buffer  (fast partial-text storage during stream)
  Static words   →  same fake list (swap for real LLM call here)

Flow
  1. Client connects via WebSocket and sends {"user_id":"...","content":"..."}
  2. Publish user message event  → Pub/Sub  (consumer will INSERT to Cloud SQL)
  3. Publish assistant placeholder event → Pub/Sub  (status=streaming)
  4. For each token:
       a. Accumulate text
       b. SETEX accumulated text → Redis  (instant, no DB pressure)
       c. Send {"event":"token","word":...} over WebSocket
  5. Stream done:
       a. Send {"event":"done",...} over WebSocket
       b. Publish message_update (status=complete) → Pub/Sub
          (consumer will UPDATE Cloud SQL with final text)
       c. DEL Redis buffer  (no longer needed)
       d. Publish workflow_event → Pub/Sub
  6. If client disconnects mid-stream:
       a. Publish message_update (status=failed) → Pub/Sub
       b. DEL Redis buffer
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from shared.pubsub import publish_event, ensure_topic_and_subscription
from shared.redis_buffer import set_buffer, delete_buffer

app = FastAPI(title="Agent Service (GCP)")

# Replace this list with a real LLM streaming call (Vertex AI / Anthropic API)
FAKE_RESPONSE = [
    "Sure!", "Here", "is", "a", "detailed", "streaming", "response",
    "from", "the", "GCP-backed", "AI", "assistant.", "I", "hope", "this",
    "helps", "you", "understand", "the", "production", "architecture.",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Startup ───────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup() -> None:
    # Ensure the Pub/Sub topic exists (safe on every boot — idempotent)
    await asyncio.to_thread(ensure_topic_and_subscription)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent_service"}


# ── WebSocket endpoint ────────────────────────────────────────────────────────

@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    try:
        data = await websocket.receive_json()
    except Exception:
        await websocket.close(code=1003)
        return

    user_id: str = data.get("user_id") or str(uuid.uuid4())
    content: str = data.get("content", "")

    # ── 1. Publish user message to Pub/Sub ───────────────────────────────────
    user_msg_id = str(uuid.uuid4())
    await publish_event({
        "type": "message",
        "session_id": session_id,
        "message_id": user_msg_id,
        "user_id": user_id,
        "role": "user",
        "content": content,
        "status": "complete",
        "created_at": _now(),
        "completed_at": _now(),
    })

    # ── 2. Publish assistant placeholder to Pub/Sub ──────────────────────────
    assistant_msg_id = str(uuid.uuid4())
    await publish_event({
        "type": "message",
        "session_id": session_id,
        "message_id": assistant_msg_id,
        "user_id": user_id,
        "role": "assistant",
        "content": "",           # empty — will be filled via Redis during stream
        "status": "streaming",
        "created_at": _now(),
        "completed_at": None,
    })

    # ── 3. Stream tokens — buffer in Redis, send over WebSocket ─────────────
    accumulated = ""
    try:
        for word in FAKE_RESPONSE:
            accumulated += word + " "

            # Fast write to Redis — no Cloud SQL pressure per token
            await set_buffer(assistant_msg_id, accumulated.strip())

            # Send token frame to the connected WebSocket client
            await websocket.send_json({
                "event": "token",
                "word": word,
                "message_id": assistant_msg_id,
            })

            await asyncio.sleep(0.15)  # simulates LLM token generation latency

        # ── 4. Stream complete ───────────────────────────────────────────────
        completed_at = _now()

        await websocket.send_json({
            "event": "done",
            "message_id": assistant_msg_id,
            "full_content": accumulated.strip(),
        })

        # Tell the consumer to UPDATE the DB row with final text + status=complete
        await publish_event({
            "type": "message_update",
            "message_id": assistant_msg_id,
            "content": accumulated.strip(),
            "status": "complete",
            "completed_at": completed_at,
        })

        # Buffer no longer needed — final text is in Pub/Sub → Cloud SQL
        await delete_buffer(assistant_msg_id)

        # ── 5. Publish workflow event ────────────────────────────────────────
        await publish_event({
            "type": "workflow_event",
            "message_id": assistant_msg_id,
            "step_name": "response_generated",
            "payload": json.dumps({
                "model": "mock-llm-v1",   # replace with actual model name
                "word_count": len(FAKE_RESPONSE),
                "session_id": session_id,
                "user_id": user_id,
            }),
            "created_at": _now(),
        })

    except WebSocketDisconnect:
        # Client disconnected mid-stream — mark the message as failed in DB
        await publish_event({
            "type": "message_update",
            "message_id": assistant_msg_id,
            "content": accumulated.strip(),
            "status": "failed",
            "completed_at": _now(),
        })
        await delete_buffer(assistant_msg_id)
