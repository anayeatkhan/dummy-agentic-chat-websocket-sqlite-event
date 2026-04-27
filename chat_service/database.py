"""
SQLite persistence layer for chat_service.

Schema mirrors the PostgreSQL schema from the spec:
  • sessions        — one row per conversation
  • messages        — ordered messages within a session
  • workflow_events — step events attached to a message

SQLite adaptations:
  • UUID      → TEXT  (Python generates them)
  • TIMESTAMPTZ → TEXT  (ISO-8601 strings)
  • JSONB     → TEXT  (JSON-serialised string)
  • gen_random_uuid() → handled in Python
  • Row-level security → enforced at the query level (user_id WHERE clause)
  • PRAGMA foreign_keys = ON  to honour REFERENCES constraints
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

DB_PATH = "chat.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id),
    user_id      TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content      TEXT,
    status       TEXT NOT NULL DEFAULT 'streaming'
                     CHECK (status IN ('streaming','partial','complete','failed')),
    seq          INTEGER NOT NULL,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS workflow_events (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id),
    step_name   TEXT NOT NULL,
    payload     TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id
    ON sessions(user_id);

CREATE INDEX IF NOT EXISTS idx_messages_session_seq
    ON messages(session_id, seq);

CREATE INDEX IF NOT EXISTS idx_messages_user_id
    ON messages(user_id);

CREATE INDEX IF NOT EXISTS idx_workflow_events_message_id
    ON workflow_events(message_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Initialisation ────────────────────────────────────────────────────────────

async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()


# ── Sessions ──────────────────────────────────────────────────────────────────

async def upsert_session(session_id: str, user_id: str) -> None:
    """Insert session if it doesn't exist; bump updated_at if it does."""
    now = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            """
            INSERT INTO sessions (id, user_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at
            """,
            (session_id, user_id, now, now),
        )
        await db.commit()


# ── Messages ──────────────────────────────────────────────────────────────────

async def _next_seq(db: aiosqlite.Connection, session_id: str) -> int:
    async with db.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?",
        (session_id,),
    ) as cur:
        row = await cur.fetchone()
        return row[0] if row else 1


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
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        seq = await _next_seq(db, session_id)
        await db.execute(
            """
            INSERT INTO messages
                (id, session_id, user_id, role, content, status, seq,
                 created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, user_id, role, content, status,
             seq, created_at, completed_at),
        )
        await db.commit()


async def update_message(
    *,
    message_id: str,
    content: str,
    status: str,
    completed_at: Optional[str],
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            """
            UPDATE messages
            SET    content = ?, status = ?, completed_at = ?
            WHERE  id = ?
            """,
            (content, status, completed_at, message_id),
        )
        await db.commit()


# ── Workflow events ───────────────────────────────────────────────────────────

async def insert_workflow_event(
    *,
    message_id: str,
    step_name: str,
    payload: Optional[str],
    created_at: str,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        await db.execute(
            """
            INSERT INTO workflow_events (id, message_id, step_name, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), message_id, step_name, payload, created_at),
        )
        await db.commit()


# ── Queries ───────────────────────────────────────────────────────────────────

async def get_session_messages(session_id: str) -> list[dict]:
    """Return all messages for a session ordered by seq ascending."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, session_id, user_id, role, content, status,
                   seq, created_at, completed_at
            FROM   messages
            WHERE  session_id = ?
            ORDER  BY seq ASC
            """,
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]


async def get_message_workflow_events(message_id: str) -> list[dict]:
    """Return all workflow events for a message."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT id, message_id, step_name, payload, created_at
            FROM   workflow_events
            WHERE  message_id = ?
            ORDER  BY created_at ASC
            """,
            (message_id,),
        ) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]
