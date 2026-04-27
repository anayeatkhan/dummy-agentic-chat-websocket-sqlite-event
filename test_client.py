"""
Quick end-to-end smoke test.

Run AFTER `python run_all.py` is already running:

    python test_client.py

Steps
  1. Open a WebSocket to agent_service and send a user message.
  2. Print every streamed token as it arrives.
  3. After the stream ends, call chat_service to fetch the saved messages.
  4. Fetch the workflow events for the assistant message.
"""
import asyncio
import json
import sys
import uuid

import websockets
import urllib.request

AGENT_WS   = "ws://localhost:8001/ws/{session_id}"
CHAT_HTTP  = "http://localhost:8002"


async def run() -> None:
    session_id = str(uuid.uuid4())
    user_id    = str(uuid.uuid4())

    ws_url = AGENT_WS.format(session_id=session_id)
    print(f"\n[1] Connecting to {ws_url}")

    assistant_msg_id = None

    async with websockets.connect(ws_url) as ws:
        payload = {"user_id": user_id, "content": "Tell me something interesting!"}
        await ws.send(json.dumps(payload))
        print(f"    Sent: {payload}\n")

        print("[2] Streaming tokens:")
        print("    ", end="", flush=True)

        async for raw in ws:
            frame = json.loads(raw)
            if frame.get("event") == "token":
                print(frame["word"], end=" ", flush=True)
                assistant_msg_id = frame["message_id"]
            elif frame.get("event") == "done":
                print("\n\n[3] Stream complete.")
                break

    # Give the consumer a moment to flush the last events
    await asyncio.sleep(0.5)

    # ── Fetch messages via chat_service ──────────────────────────────────────
    url = f"{CHAT_HTTP}/sessions/{session_id}/messages"
    print(f"[4] GET {url}")
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())

    print(f"    {data['count']} message(s) in session:\n")
    for msg in data["messages"]:
        role   = msg["role"].upper().ljust(10)
        status = msg["status"]
        seq    = msg["seq"]
        preview = (msg["content"] or "")[:60]
        print(f"    seq={seq}  {role}  status={status}  content={preview!r}")

    # ── Fetch workflow events ─────────────────────────────────────────────────
    if assistant_msg_id:
        url2 = f"{CHAT_HTTP}/sessions/{session_id}/messages/{assistant_msg_id}/events"
        print(f"\n[5] GET {url2}")
        with urllib.request.urlopen(url2) as resp:
            edata = json.loads(resp.read())
        print(f"    {edata['count']} workflow event(s):")
        for ev in edata["events"]:
            print(f"    step={ev['step_name']}  payload={ev['payload']}")

    print("\nDone.\n")


if __name__ == "__main__":
    asyncio.run(run())
