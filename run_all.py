"""
run_all.py — start both services in a single process on the same event loop.

    python run_all.py

  agent_service → http://localhost:8001
  chat_service  → http://localhost:8002

Because both servers share the same asyncio event loop they can also share the
asyncio.Queue defined in shared/local_queue.py — no real message broker needed.
"""
import asyncio
import logging

import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


async def main() -> None:
    # Import apps here so they are created inside the running event loop,
    # which ensures asyncio.Queue() in shared/local_queue.py is bound to it.
    from agent_service.main import app as agent_app
    from chat_service.main import app as chat_app

    agent_cfg = uvicorn.Config(
        app=agent_app,
        host="0.0.0.0",
        port=8001,
        log_level="info",
    )
    chat_cfg = uvicorn.Config(
        app=chat_app,
        host="0.0.0.0",
        port=8002,
        log_level="info",
    )

    agent_server = uvicorn.Server(agent_cfg)
    chat_server  = uvicorn.Server(chat_cfg)

    print("─" * 55)
    print("  agent_service  →  ws://localhost:8001/ws/{session_id}")
    print("  chat_service   →  http://localhost:8002/sessions/{id}/messages")
    print("─" * 55)

    await asyncio.gather(
        agent_server.serve(),
        chat_server.serve(),
    )


if __name__ == "__main__":
    asyncio.run(main())
