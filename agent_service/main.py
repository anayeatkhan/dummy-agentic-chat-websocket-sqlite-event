"""
Agent Service — port 8001
─────────────────────────
WebSocket /ws/{session_id}

Flow:
  1. Accept a JSON message  {"user_id": "...", "content": "..."}
  2. Publish the user message to the shared local queue.
  3. Create an assistant-message placeholder (status=streaming) in the queue.
  4. Stream a fake response word-by-word over the WebSocket.
     After each word also publish a partial-update event to the queue.
  5. Send a final "done" frame and publish status=complete.
  6. Publish a workflow_event to the queue.
"""
import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from shared.local_queue import event_queue

app = FastAPI(title="Agent Service")

FAKE_RESPONSE = [
    "Sure!", "Here", "is", "a", "detailed", "streaming", "response",
    "from", "the", "mock", "AI", "assistant.", "I", "hope", "this",
    "helps", "you", "understand", "the", "event-driven", "architecture.",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent_service"}


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()

    # ── Receive user message ─────────────────────────────────────────────────
    try:
        data = await websocket.receive_json()
    except Exception:
        await websocket.close(code=1003)
        return

    user_id: str = data.get("user_id") or str(uuid.uuid4())
    content: str = data.get("content", "")

    # ── 1. Publish user message ──────────────────────────────────────────────
    user_msg_id = str(uuid.uuid4())
    await event_queue.put({
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

    # ── 2. Publish assistant placeholder ────────────────────────────────────
    assistant_msg_id = str(uuid.uuid4())
    await event_queue.put({
        "type": "message",
        "session_id": session_id,
        "message_id": assistant_msg_id,
        "user_id": user_id,
        "role": "assistant",
        "content": "",
        "status": "streaming",
        "created_at": _now(),
        "completed_at": None,
    })

    # ── 3. Stream words + publish partial updates ────────────────────────────
    accumulated = ""
    try:
        for word in FAKE_RESPONSE:
            accumulated += word + " "

            await websocket.send_json({
                "event": "token",
                "word": word,
                "message_id": assistant_msg_id,
            })
            await event_queue.put({
                "type": "message_update",
                "message_id": assistant_msg_id,
                "content": accumulated.strip(),
                "status": "partial",
                "completed_at": None,
            })
            await asyncio.sleep(0.15)  # simulate LLM token latency

        # ── 4. Send completion frame ─────────────────────────────────────────
        completed_at = _now()
        await websocket.send_json({
            "event": "done",
            "message_id": assistant_msg_id,
            "full_content": accumulated.strip(),
        })
        await event_queue.put({
            "type": "message_update",
            "message_id": assistant_msg_id,
            "content": accumulated.strip(),
            "status": "complete",
            "completed_at": completed_at,
        })

        # ── 5. Publish workflow event ────────────────────────────────────────
        await event_queue.put({
            "type": "workflow_event",
            "message_id": assistant_msg_id,
            "step_name": "response_generated",
            "payload": json.dumps({
                "model": "mock-llm-v1",
                "word_count": len(FAKE_RESPONSE),
                "session_id": session_id,
                "user_id": user_id,
            }),
            "created_at": _now(),
        })

    except WebSocketDisconnect:
        # Client left mid-stream — mark the message as failed
        await event_queue.put({
            "type": "message_update",
            "message_id": assistant_msg_id,
            "content": accumulated.strip(),
            "status": "failed",
            "completed_at": _now(),
        })
