"""
Shared in-process queue that both agent_service and chat_service use.

When both services are started from run_all.py they share the same asyncio
event loop, so this single asyncio.Queue instance is visible to both.
"""
import asyncio

# Module-level singleton — created once per process.
event_queue: asyncio.Queue = asyncio.Queue()
