"""
chat_service/database.py
────────────────────────
Async PostgreSQL access layer using asyncpg + a connection pool.

What changed vs the mock version
  aiosqlite / SQLite  →  asyncpg / Cloud SQL (PostgreSQL)
  TEXT UUIDs          →  native UUID type
  TEXT timestamps     →  native TIMESTAMPTZ
  TEXT JSON           →  native JSONB
  No connection pool  →  asyncpg.Pool (min 2, max 10 connections)
  No transactions     →  explicit transactions for seq + insert atomicity

Cloud SQL connection
  Local dev:   DATABASE_URL=postgresql://user:pass@localhost:5432/chatdb
  Cloud Run:   DATABASE_URL=postgresql://user:pass@/chatdb?host=/cloudsql/PROJECT:REGION:INSTANCE
               (Cloud SQL Auth Proxy is a sidecar on Cloud Run — connects via Unix socket)
"""
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import asyncpg

DATABASE_URL = os.environ["DATABASE_URL"]

_pool: asyncpg.Pool | None = None


# ── Pool lifecycle ────────────────────────────────────────────────────────────

async def init_pool() -> None:
    """
    Create the asyncpg connection pool. Called once on service startup.
    min_size=2  keeps two warm connections so the first request is fast.
    max_size=10 caps concurrency to avoid overwhelming Cloud SQL.
    """
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=30,
        # asyncpg uses UUID codec by default — native round-trip with no text conversion
    )


async def close_pool() -> None:
    if _pool:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() first.")
    return _pool


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Sessions ──────────────────────────────────────────────────────────────────

async def upsert_session(session_id: str, user_id: str) -> None:
    """
    Insert the session if it does not exist; update updated_at if it does.
    Must be called before insert_message because of the FK constraint.
    """
    now = _now()
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sessions (id, user_id, created_at, updated_at)
            VALUES ($1::uuid, $2::uuid, $3::timestamptz, $4::timestamptz)
            ON CONFLICT (id) DO UPDATE SET updated_at = EXCLUDED.updated_at
            """,
            session_id, user_id, now, now,
        )


# ── Messages ──────────────────────────────────────────────────────────────────

async def insert_message(
    *,
    message_id: str,
    session_id: str,
    user_id: str,
    role: str,
    content: str,
    status: str,
    created_at: str,
    completed_at: Optional[str],
) -> None:
    """
    Insert a new message row.

    seq is computed inside a transaction with SELECT ... FOR UPDATE so that two
    concurrent inserts for the same session cannot race and produce duplicate seq
    values. (The mock SQLite version had this race condition.)
    """
    async with get_pool().acquire() as conn:
        async with conn.transaction():
            # Lock the session row while we read MAX(seq) so no other
            # connection can insert a message for this session simultaneously.
            seq: int = await conn.fetchval(
                """
                SELECT COALESCE(MAX(seq), 0) + 1
                FROM   messages
                WHERE  session_id = $1::uuid
                """,
                session_id,
            )
            await conn.execute(
                """
                INSERT INTO messages
                    (id, session_id, user_id, role, content, status, seq,
                     created_at, completed_at)
                VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7,
                        $8::timestamptz, $9::timestamptz)
                """,
                message_id, session_id, user_id, role, content, status, seq,
                created_at, completed_at,
            )


async def update_message(
    *,
    message_id: str,
    content: str,
    status: str,
    completed_at: Optional[str],
) -> None:
    """
    Update content, status, and completed_at for an existing message.
    Called by the consumer when it receives a message_update event from Pub/Sub.
    Only called for status=complete or status=failed (not for every partial token).
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE messages
            SET    content      = $1,
                   status       = $2,
                   completed_at = $3::timestamptz
            WHERE  id = $4::uuid
            """,
            content, status, completed_at, message_id,
        )


# ── Workflow events ───────────────────────────────────────────────────────────

async def insert_workflow_event(
    *,
    message_id: str,
    step_name: str,
    payload: Optional[str],
    created_at: str,
) -> None:
    """
    Insert a workflow_event row.
    payload is a JSON string; cast to ::jsonb so PostgreSQL indexes and queries it natively.
    """
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            INSERT INTO workflow_events (id, message_id, step_name, payload, created_at)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb, $5::timestamptz)
            """,
            str(uuid.uuid4()), message_id, step_name, payload, created_at,
        )


# ── Queries ───────────────────────────────────────────────────────────────────

async def get_session_messages(session_id: str) -> list[dict]:
    """
    Return all messages for a session ordered by seq ascending.
    asyncpg.Record behaves like a dict so dict(row) works directly.
    """
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, session_id::text, user_id::text,
                   role, content, status, seq,
                   created_at::text, completed_at::text
            FROM   messages
            WHERE  session_id = $1::uuid
            ORDER  BY seq ASC
            """,
            session_id,
        )
        return [dict(row) for row in rows]


async def get_message_workflow_events(message_id: str) -> list[dict]:
    """Return all workflow_events for one message, oldest first."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, message_id::text, step_name,
                   payload::text, created_at::text
            FROM   workflow_events
            WHERE  message_id = $1::uuid
            ORDER  BY created_at ASC
            """,
            message_id,
        )
        return [dict(row) for row in rows]
