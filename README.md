# dummy-agent-chat-service

A local mock of a production-grade, event-driven AI chat backend built with
**FastAPI**, **WebSockets**, **asyncio**, and **SQLite**.

No cloud infrastructure required. Every production component (GCP Pub/Sub,
Redis, PostgreSQL, a real LLM) is replaced with a local equivalent so you can
understand the architecture without any paid account or running Docker.

---

## Table of Contents

1. [What This Project Is](#1-what-this-project-is)
2. [Project Structure](#2-project-structure)
3. [What Is Mocked and Why](#3-what-is-mocked-and-why)
4. [What Replaced by GCP (Production vs Mock)](#4-what-replaced-by-gcp-production-vs-mock)
5. [How the Two Services Communicate](#5-how-the-two-services-communicate)
6. [The Full Message Flow Step by Step](#6-the-full-message-flow-step-by-step)
7. [Database Schema Explained](#7-database-schema-explained)
8. [File-by-File Function Reference](#8-file-by-file-function-reference)
9. [API Reference](#9-api-reference)
10. [How to Run](#10-how-to-run)
11. [Senior Engineer Notes](#11-senior-engineer-notes)

---

## 1. What This Project Is

Imagine you are building a ChatGPT-like product. Behind the scenes you need:

| Concern | Component |
|---|---|
| Accept user messages in real time | **WebSocket** |
| Generate AI responses word-by-word | **Streaming LLM** |
| Reliably hand off events between services | **Message queue / Pub-Sub** |
| Store conversation history | **Database** |
| Read back history | **REST API** |

This project implements all five concerns using **only local Python** — no cloud
account, no Docker, no extra processes. It is designed to teach you the
architecture so that swapping in real infrastructure later is a small, isolated
change.

---

## 2. Project Structure

```
dummy-agent-chat-service/
│
├── shared/
│   ├── __init__.py
│   └── local_queue.py        # The mock message broker (asyncio.Queue)
│
├── agent_service/
│   ├── __init__.py
│   └── main.py               # WebSocket endpoint + event publisher
│
├── chat_service/
│   ├── __init__.py
│   ├── database.py           # SQLite schema + all DB operations
│   ├── consumer.py           # Queue consumer — reads events, writes to DB
│   └── main.py               # REST API + starts the consumer on boot
│
├── run_all.py                # Boots both services in one command
├── test_client.py            # End-to-end smoke test / demo script
└── requirements.txt
```

Each folder is a **Python package** (it has `__init__.py`). This lets you do
`from agent_service.main import app` and `from shared.local_queue import
event_queue` without any path hacks.

---

## 3. What Is Mocked and Why

### Mock 1 — `asyncio.Queue` instead of a real message broker

**What it replaces:** In production you would use
[GCP Pub/Sub](https://cloud.google.com/pubsub) or
[Redis Streams](https://redis.io/docs/data-types/streams/) to pass events
between services. These run as separate network services and guarantee delivery
even if the consumer is temporarily offline.

**What we do instead:** Python's built-in `asyncio.Queue` — a thread-safe,
in-memory FIFO queue. Because both services run in the same Python process and
share the same asyncio event loop, they can both hold a reference to the same
`Queue` object in memory. No network hop required.

**File:** `shared/local_queue.py`

```python
import asyncio
event_queue: asyncio.Queue = asyncio.Queue()
```

One line. That's the entire "message broker".

---

### Mock 2 — SQLite instead of PostgreSQL

**What it replaces:** Production would use
[Cloud SQL (PostgreSQL)](https://cloud.google.com/sql) with UUIDs, JSONB
columns, Row-Level Security, and connection pooling.

**What we do instead:** SQLite — a serverless database stored in a single file
(`chat.db`) on disk. No server process, no port, no password. The Python
library `aiosqlite` gives us an async interface so it plays nicely with FastAPI.

**Adaptations made:**

| PostgreSQL feature | SQLite equivalent used here |
|---|---|
| `UUID PRIMARY KEY DEFAULT gen_random_uuid()` | `TEXT PRIMARY KEY` — Python generates `str(uuid.uuid4())` before INSERT |
| `TIMESTAMPTZ` | `TEXT` storing ISO-8601 string e.g. `2024-01-15T10:30:00+00:00` |
| `JSONB` | `TEXT` storing a JSON-serialised string |
| `ALTER TABLE … ENABLE ROW LEVEL SECURITY` | Not supported — enforced in application code by scoping queries with `WHERE session_id = ?` |
| `current_setting('app.user_id')::uuid` | Not supported — user_id passed as a query parameter |
| `REFERENCES` foreign keys | Supported — enabled with `PRAGMA foreign_keys = ON` |

---

### Mock 3 — Hard-coded word list instead of a real LLM

**What it replaces:** A real system would call an LLM API (e.g. Anthropic
Claude, OpenAI GPT-4) and stream tokens back using server-sent events or
WebSocket frames.

**What we do instead:** A static Python list of 20 words. We loop over them
with `await asyncio.sleep(0.15)` between each word to simulate the latency of
a real model generating tokens.

```python
FAKE_RESPONSE = [
    "Sure!", "Here", "is", "a", "detailed", "streaming", "response",
    "from", "the", "mock", "AI", "assistant.", ...
]
```

---

## 4. What Replaced by GCP (Production vs Mock)

This is the most important table if you plan to take this to production.

```
┌─────────────────────────────┬──────────────────────────────┬─────────────────────────────────┐
│ Concern                     │ Production (GCP)             │ This Mock                       │
├─────────────────────────────┼──────────────────────────────┼─────────────────────────────────┤
│ Message broker              │ GCP Pub/Sub                  │ asyncio.Queue (in-memory)       │
│ Database                    │ Cloud SQL — PostgreSQL       │ SQLite file (chat.db)           │
│ UUID generation             │ gen_random_uuid() in Postgres│ uuid.uuid4() in Python          │
│ Timestamps                  │ TIMESTAMPTZ (native tz)      │ ISO-8601 TEXT string            │
│ JSON storage                │ JSONB (indexed, queryable)   │ TEXT (plain JSON string)        │
│ Row-level security          │ PostgreSQL RLS policies      │ WHERE clause in query           │
│ LLM / AI model              │ Vertex AI / Anthropic API    │ Static word list                │
│ Service isolation           │ Separate containers / K8s    │ Two uvicorn servers, one process│
│ Authentication              │ GCP IAM / JWT                │ Not implemented (no auth)       │
│ Secrets management          │ Secret Manager               │ Hardcoded / not needed          │
│ Observability               │ Cloud Logging / Trace        │ Python logging to stdout        │
│ Connection pooling          │ Cloud SQL Auth Proxy + pgpool│ One aiosqlite connection/call   │
└─────────────────────────────┴──────────────────────────────┴─────────────────────────────────┘
```

### What stays the same when you go to production

- The FastAPI app structure
- The WebSocket endpoint signature
- The event dictionary shape (`type`, `message_id`, `session_id`, etc.)
- The consumer `_dispatch` logic
- The REST endpoint paths

You would only swap:
1. `shared/local_queue.py` → a GCP Pub/Sub publisher/subscriber client
2. `chat_service/database.py` → asyncpg or SQLAlchemy pointing at Cloud SQL
3. `agent_service/main.py` FAKE_RESPONSE loop → an actual LLM streaming call

---

## 5. How the Two Services Communicate

```
┌──────────────────────────────────────────────────────────────────┐
│                        Python Process                            │
│                                                                  │
│   ┌─────────────────┐    put()    ┌────────────────────────┐    │
│   │  agent_service  │ ──────────► │   shared/local_queue   │    │
│   │   (port 8001)   │             │   asyncio.Queue        │    │
│   └─────────────────┘             └──────────┬─────────────┘    │
│          ▲                                   │ get()             │
│          │ WebSocket                         ▼                   │
│        Client                    ┌─────────────────────────┐    │
│                                  │    chat_service         │    │
│                                  │    consumer task        │    │
│                                  │    (port 8002)          │    │
│                                  └──────────┬──────────────┘    │
│                                             │ aiosqlite          │
│                                             ▼                    │
│                                        [ chat.db ]               │
│                                        SQLite file               │
└──────────────────────────────────────────────────────────────────┘
```

**Key insight:** Both services run inside `run_all.py` on the **same asyncio
event loop**. This is why they can share the same `asyncio.Queue` object in
memory. In production, they would be separate Docker containers and use a
network-based broker (Pub/Sub, Redis, Kafka) instead.

---

## 6. The Full Message Flow Step by Step

This is what happens from the moment a user sends "Hello!" to the moment it
appears in the database.

```
Client                  agent_service             shared queue       chat_service consumer
  │                          │                          │                    │
  │── WS connect ──────────► │                          │                    │
  │                          │ accept()                 │                    │
  │── {"content":"Hello!"} ► │                          │                    │
  │                          │                          │                    │
  │                          │── put(user message) ───► │                    │
  │                          │                          │◄── get() ──────────│
  │                          │                          │    upsert_session  │
  │                          │                          │    insert_message  │
  │                          │                          │    (role=user)     │
  │                          │                          │                    │
  │                          │── put(assistant msg) ──► │                    │
  │                          │   status=streaming       │◄── get() ──────────│
  │                          │                          │    insert_message  │
  │                          │                          │    (status=streaming)
  │                          │                          │                    │
  │◄── {"word":"Sure!"} ──── │                          │                    │
  │                          │── put(partial update) ─► │                    │
  │                          │   content="Sure!"        │◄── get() ──────────│
  │                          │   status=partial         │    update_message  │
  │                          │                          │                    │
  │  ... (repeats per word) ...                         │                    │
  │                          │                          │                    │
  │◄── {"event":"done"} ──── │                          │                    │
  │                          │── put(complete update) ► │                    │
  │                          │   status=complete        │◄── get() ──────────│
  │                          │                          │    update_message  │
  │                          │                          │    (status=complete)
  │                          │                          │                    │
  │                          │── put(workflow_event) ─► │                    │
  │                          │   step=response_generated│◄── get() ──────────│
  │                          │                          │    insert_workflow_event
  │                          │                          │                    │
  │── GET /sessions/.../messages ────────────────────────────────────────────► HTTP 200
```

**Why publish partial updates?**
In a real system the LLM generates tokens over several seconds. You want the
database to always hold the latest partial response so that if the user
refreshes the page mid-stream, they see what has been generated so far.

---

## 7. Database Schema Explained

### Table: `sessions`

Represents one conversation thread.

```sql
CREATE TABLE sessions (
    id          TEXT PRIMARY KEY,   -- UUID as text, e.g. "3f8a1c2d-..."
    user_id     TEXT NOT NULL,      -- who owns this session
    created_at  TEXT NOT NULL,      -- ISO-8601 timestamp
    updated_at  TEXT NOT NULL       -- bumped each time session is touched
);
```

**Beginner explanation:** Think of this like a "chat room". Every time a new
conversation starts, a row is added here. The `user_id` tells us which user
this chat belongs to.

---

### Table: `messages`

Every single message in every session — both user messages and AI responses.

```sql
CREATE TABLE messages (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions(id),  -- which conversation
    user_id      TEXT NOT NULL,                          -- who sent / owns it
    role         TEXT NOT NULL                           -- 'user','assistant','system'
                     CHECK (role IN ('user','assistant','system')),
    content      TEXT,                                   -- the actual text
    status       TEXT NOT NULL DEFAULT 'streaming'
                     CHECK (status IN ('streaming','partial','complete','failed')),
    seq          INTEGER NOT NULL,   -- 1, 2, 3 … ordering within the session
    created_at   TEXT NOT NULL,
    completed_at TEXT                -- NULL until the message finishes
);
```

**Beginner explanation:** Every bubble in a chat UI is one row here. The
`status` column tracks whether the AI is still typing (`streaming`), partially
done (`partial`), finished (`complete`), or something went wrong (`failed`).
The `seq` column is a counter per session so messages always come back in the
right order.

**The `role` column:**
- `user` — message typed by the human
- `assistant` — message generated by the AI
- `system` — a behind-the-scenes instruction (e.g. "You are a helpful assistant")

---

### Table: `workflow_events`

Audit trail of processing steps that happened for a message.

```sql
CREATE TABLE workflow_events (
    id          TEXT PRIMARY KEY,
    message_id  TEXT NOT NULL REFERENCES messages(id),
    step_name   TEXT NOT NULL,   -- e.g. "response_generated", "tool_called"
    payload     TEXT,            -- JSON blob with extra details
    created_at  TEXT NOT NULL
);
```

**Beginner explanation:** This is like a log. When the AI finishes generating a
response the system records "response_generated" here, including metadata like
which model was used and how many words were produced. In a real system this
table would track tool calls, RAG retrievals, moderation checks, etc.

---

### Indexes

```sql
CREATE INDEX ON sessions(user_id);             -- fast lookup: all sessions for a user
CREATE INDEX ON messages(session_id, seq);     -- fast lookup: history of a session in order
CREATE INDEX ON messages(user_id);             -- fast lookup: all messages by a user
CREATE INDEX ON workflow_events(message_id);   -- fast lookup: events for a message
```

**Beginner explanation:** An index is like the index at the back of a book. Without it, the
database reads every row to find what you want. With it, it jumps straight to the right
rows. You add indexes on columns you frequently filter or sort by.

---

## 8. File-by-File Function Reference

### `shared/local_queue.py`

**Purpose:** Define the single shared queue that both services use to pass
events to each other.

```python
event_queue: asyncio.Queue = asyncio.Queue()
```

| Symbol | Type | What it does |
|---|---|---|
| `event_queue` | `asyncio.Queue` | A FIFO queue of Python dicts. `put()` adds an item. `get()` removes and returns the next item (waits if empty). `task_done()` signals that the item has been processed. |

**Why a module-level variable?** When Python imports a module the second time,
it returns the same object that was created on the first import. So both
`agent_service` and `chat_service` import `event_queue` and they get the exact
same queue object. This is called the **module singleton pattern**.

---

### `agent_service/main.py`

**Purpose:** Accept WebSocket connections, fake-stream an AI response, and
publish events to the queue.

#### `_now() -> str`
```python
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
```
Returns the current UTC time as an ISO-8601 string like
`"2024-01-15T10:30:00.123456+00:00"`. Always UTC (`timezone.utc`) so that
timestamps from different machines are comparable.

---

#### `GET /health`
```python
@app.get("/health")
async def health():
    return {"status": "ok", "service": "agent_service"}
```
A simple ping endpoint. Load balancers and monitoring tools call this to check
whether the service is alive. Returns HTTP 200 with a JSON body.

---

#### `WS /ws/{session_id}`
```python
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
```
The main endpoint. `{session_id}` is a path parameter — whatever UUID the
client puts in the URL becomes the `session_id` variable.

**Inside the function, step by step:**

| Step | Code | What happens |
|---|---|---|
| 1 | `await websocket.accept()` | Completes the WebSocket handshake. Must be called before sending or receiving. |
| 2 | `data = await websocket.receive_json()` | Waits for the client to send its first JSON message. Blocks until data arrives. |
| 3 | `user_id = data.get("user_id") or str(uuid.uuid4())` | Extract user_id from the message, or generate a random one if not provided. |
| 4 | `await event_queue.put({...})` | Publish the user's message as an event. The consumer will pick this up and save it. |
| 5 | Publish assistant placeholder | Tell the consumer "an assistant message is starting, status=streaming". |
| 6 | `for word in FAKE_RESPONSE:` | Loop over each word in the fake response list. |
| 7 | `await websocket.send_json({"event":"token","word":word,...})` | Send the word to the client over the WebSocket. |
| 8 | `await event_queue.put({...partial update...})` | Tell the consumer the running text so far. |
| 9 | `await asyncio.sleep(0.15)` | Pause 150ms to simulate LLM token generation latency. |
| 10 | `await websocket.send_json({"event":"done",...})` | Notify the client that generation is complete. |
| 11 | Publish complete update | Tell the consumer to mark the message `status=complete`. |
| 12 | Publish `workflow_event` | Record an audit entry: "response_generated", with model name and word count. |

**Error handling:**
If the client disconnects mid-stream, a `WebSocketDisconnect` exception is
raised. The `except` block catches it and publishes a `status=failed` update so
the database reflects the incomplete message correctly.

---

### `chat_service/database.py`

**Purpose:** All SQLite interactions — schema creation and every read/write
operation. No business logic here, only data access.

#### `DB_PATH = "chat.db"`
The file path of the SQLite database. The file is created automatically the
first time `init_db()` runs. Relative to wherever you run `python run_all.py`.

---

#### `_now() -> str`
Same as in agent_service — returns current UTC time as ISO-8601. Duplicated
intentionally so the two services are independent of each other.

---

#### `async def init_db() -> None`
```python
async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(_SCHEMA)
        await db.commit()
```
Runs the full `CREATE TABLE IF NOT EXISTS` script when the service starts.
`IF NOT EXISTS` means it is safe to call on every boot — it won't wipe data.

**Beginner note:** `async with` is Python's way of using a context manager
asynchronously. When the `with` block exits (even due to an error), the
database connection is closed automatically.

---

#### `async def upsert_session(session_id, user_id) -> None`
```python
INSERT INTO sessions ... ON CONFLICT(id) DO UPDATE SET updated_at = ...
```
"Upsert" = "insert or update". If the session already exists (same `id`),
just update `updated_at`. If it doesn't exist, create it. This is safe to call
multiple times for the same session.

**Why call this before inserting a message?** The `messages` table has a
`FOREIGN KEY` pointing to `sessions(id)`. SQLite will reject an insert into
`messages` if the referenced session doesn't exist yet. So we always ensure the
session row exists first.

---

#### `async def _next_seq(db, session_id) -> int`
```python
SELECT COALESCE(MAX(seq), 0) + 1 FROM messages WHERE session_id = ?
```
Finds the highest `seq` value for the session and adds 1. `COALESCE` means "if
MAX returns NULL (no rows yet), use 0 instead". So the first message gets
`seq=1`, the second gets `seq=2`, and so on.

This is called **inside an open connection** (private function, prefixed `_`)
because it needs to share the transaction with the subsequent INSERT to avoid a
race condition.

---

#### `async def insert_message(...) -> None`
Inserts a new row into `messages`. Calls `_next_seq` first to determine the
correct ordering position. All parameters are keyword-only (`*,` in the
signature) to prevent accidentally passing them in the wrong order.

---

#### `async def update_message(...) -> None`
Updates `content`, `status`, and `completed_at` for an existing message. Called
on every `partial` and `complete` event from the queue. This overwrites the
content each time because each partial event contains the full accumulated text
so far (not just the new word).

---

#### `async def insert_workflow_event(...) -> None`
Inserts one row into `workflow_events`. Generates a fresh UUID for the event's
own `id`. The `payload` is stored as a plain JSON string (TEXT column).

---

#### `async def get_session_messages(session_id) -> list[dict]`
```python
SELECT ... FROM messages WHERE session_id = ? ORDER BY seq ASC
```
Returns all messages for a session as a list of plain Python dicts, ordered by
`seq` ascending (oldest first). This is what the REST endpoint serves.
`aiosqlite.Row` acts like a dictionary so `dict(row)` converts each row.

---

#### `async def get_message_workflow_events(message_id) -> list[dict]`
Returns all workflow_events for one message, ordered by `created_at` ascending.
Useful for debugging the processing pipeline for a specific message.

---

### `chat_service/consumer.py`

**Purpose:** Background async task that continuously drains the queue and
dispatches each event to the correct database function.

#### `async def consume() -> None`
```python
async def consume() -> None:
    while True:
        event = await event_queue.get()
        try:
            await _dispatch(event)
        except Exception as exc:
            logger.error(...)
        finally:
            event_queue.task_done()
```
An infinite loop that:
1. `await event_queue.get()` — suspends (yields control back to the event loop)
   until an event arrives. Does NOT block the whole process.
2. Calls `_dispatch(event)` to handle the event.
3. Always calls `task_done()` — even on error — so the queue doesn't
   accumulate "in-flight" markers indefinitely.
4. Logs errors but continues — one bad event doesn't kill the consumer.

**Beginner note on `await`:** `await` tells Python "this operation might take a
while; while you wait, go do other things". This is what makes async code
efficient — the WebSocket can keep streaming to the client while the consumer
is saving to the database, because they `await` different things and take turns
on the CPU.

---

#### `async def _dispatch(event: dict) -> None`
A router function. Reads `event["type"]` and calls the right database function.

| `event["type"]` | Action |
|---|---|
| `"message"` | `upsert_session` then `insert_message` |
| `"message_update"` | `update_message` |
| `"workflow_event"` | `insert_workflow_event` |
| anything else | Log a warning, do nothing |

---

### `chat_service/main.py`

**Purpose:** Define the FastAPI app for chat_service, start the consumer as a
background task, and expose the REST endpoints.

#### `lifespan(app)` context manager
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_db()
    consumer_task = asyncio.create_task(consume(), name="queue-consumer")
    yield
    consumer_task.cancel()
```
FastAPI calls this when the server starts and when it shuts down.

- **On startup** (code before `yield`): create the database tables and launch
  the consumer as a background asyncio task.
- **On shutdown** (code after `yield`): cancel the consumer task gracefully.

`asyncio.create_task()` schedules `consume()` to run concurrently in the same
event loop. It runs alongside request handling — not in a separate thread, not
in a separate process.

---

#### `GET /health`
Same as agent_service — a liveness probe endpoint.

---

#### `GET /sessions/{session_id}/messages`
```python
@app.get("/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    messages = await db.get_session_messages(session_id)
    if not messages:
        raise HTTPException(status_code=404, ...)
    return {"session_id": session_id, "count": len(messages), "messages": messages}
```
Reads all messages for a session from SQLite and returns them as JSON. Returns
HTTP 404 if the session has no messages yet (e.g. the consumer hasn't caught up,
or the session doesn't exist).

---

#### `GET /sessions/{session_id}/messages/{message_id}/events`
Returns the `workflow_events` rows for one message. Useful for inspecting what
processing steps were recorded for an AI response.

---

### `run_all.py`

**Purpose:** Start both FastAPI servers in a single command using the same
asyncio event loop.

#### `async def main() -> None`
```python
async def main() -> None:
    from agent_service.main import app as agent_app
    from chat_service.main import app as chat_app

    agent_server = uvicorn.Server(uvicorn.Config(app=agent_app, port=8001))
    chat_server  = uvicorn.Server(uvicorn.Config(app=chat_app,  port=8002))

    await asyncio.gather(agent_server.serve(), chat_server.serve())
```

**Why import inside the function?** The imports happen after `asyncio.run(main())`
creates the event loop. This ensures `asyncio.Queue()` in `local_queue.py` is
bound to that specific event loop. If you imported at module level, the Queue
would be created before the event loop exists — which can cause subtle bugs on
some platforms.

`asyncio.gather()` runs both servers concurrently. Neither blocks the other.
They share the event loop so they also share the queue.

---

### `test_client.py`

**Purpose:** Automated end-to-end demo that exercises all moving parts.

#### `async def run() -> None`

| Step | What it does |
|---|---|
| 1 | Generates a random `session_id` and `user_id` with `uuid.uuid4()` |
| 2 | Opens a WebSocket connection to `ws://localhost:8001/ws/{session_id}` |
| 3 | Sends `{"user_id": ..., "content": "Tell me something interesting!"}` |
| 4 | Loops over incoming frames, printing each `token` word |
| 5 | Breaks out of the loop when a `done` frame arrives |
| 6 | Waits 500ms for the consumer to finish writing to SQLite |
| 7 | Calls `GET /sessions/{session_id}/messages` and prints results |
| 8 | Calls `GET .../messages/{message_id}/events` and prints workflow events |

---

## 9. API Reference

### agent_service (port 8001)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `WS` | `/ws/{session_id}` | Main WebSocket. Send `{"user_id":"...","content":"..."}` |

**WebSocket frames sent by server:**

```jsonc
// One per word during streaming
{"event": "token", "word": "Sure!", "message_id": "uuid"}

// Once at the end
{"event": "done", "message_id": "uuid", "full_content": "Sure! Here is ..."}
```

---

### chat_service (port 8002)

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/sessions/{session_id}/messages` | All messages in a session, ordered by seq |
| `GET` | `/sessions/{session_id}/messages/{message_id}/events` | Workflow events for one message |

**Example response for `/sessions/.../messages`:**
```json
{
  "session_id": "3f8a1c2d-...",
  "count": 2,
  "messages": [
    {
      "id": "aaa...",
      "session_id": "3f8a...",
      "user_id": "bbb...",
      "role": "user",
      "content": "Tell me something interesting!",
      "status": "complete",
      "seq": 1,
      "created_at": "2024-01-15T10:30:00+00:00",
      "completed_at": "2024-01-15T10:30:00+00:00"
    },
    {
      "id": "ccc...",
      "role": "assistant",
      "content": "Sure! Here is a detailed streaming response ...",
      "status": "complete",
      "seq": 2,
      ...
    }
  ]
}
```

---

## 10. How to Run

### Install dependencies
```bash
pip install -r requirements.txt
```

### Start both services
```bash
python run_all.py
```

You will see:
```
───────────────────────────────────────────────────────
  agent_service  →  ws://localhost:8001/ws/{session_id}
  chat_service   →  http://localhost:8002/sessions/{id}/messages
───────────────────────────────────────────────────────
```

### Run the end-to-end test (in a second terminal)
```bash
python test_client.py
```

### Browse the auto-generated API docs
```
http://localhost:8001/docs   ← agent_service Swagger UI
http://localhost:8002/docs   ← chat_service Swagger UI
```

### Inspect the database
```bash
sqlite3 chat.db
```
```sql
.headers on
.mode column
SELECT role, status, seq, substr(content,1,50) FROM messages;
```

---

## 11. Senior Engineer Notes

These are things a junior engineer might not think about, but matter when you
take this to production.

### 1. The queue is not durable
`asyncio.Queue` lives in RAM. If the process crashes, all unprocessed events
are lost. In production, use GCP Pub/Sub or Redis Streams — both persist
messages to disk and re-deliver them if the consumer dies.

### 2. The consumer has no retry logic
If `db.insert_message()` fails (e.g. disk full), the event is logged and
dropped. In production, implement exponential backoff with a dead-letter queue
— a separate queue where failed events go after N retries so you can inspect
and replay them.

### 3. The `seq` counter has a race condition
`_next_seq` reads MAX(seq) and adds 1 in two separate operations. If two
messages arrive for the same session simultaneously, both could read the same
MAX and produce duplicate seq values. In production, use a `SEQUENCE` in
PostgreSQL or a `SELECT FOR UPDATE` lock.

### 4. Every DB function opens a new connection
Each `async with aiosqlite.connect(...)` opens and closes a connection.
Fine for SQLite with low concurrency, but for PostgreSQL you would use a
connection pool (e.g. `asyncpg` + `databases` library) to avoid the overhead
of establishing a TCP connection on every query.

### 5. No authentication
Any client can connect with any `session_id` and `user_id`. In production you
would validate a JWT token on the WebSocket handshake and extract `user_id`
from the token's claims — never trust user-supplied IDs.

### 6. Row-Level Security
The PostgreSQL schema had `ENABLE ROW LEVEL SECURITY` with a policy that
checks `user_id = current_setting('app.user_id')`. This means even if there
is a SQL injection bug, a user cannot see another user's data. The SQLite mock
enforces isolation at the application layer (WHERE clause) instead — weaker,
but sufficient for a local prototype.

### 7. Partial updates are chatty
Publishing a `message_update` event for every word means 20 writes to the
queue and then 20 UPDATE statements to SQLite per response. In production you
would either:
- Buffer N tokens before publishing (e.g. every 5 words), or
- Store streaming chunks in Redis and only flush the final text to PostgreSQL.

### 8. Status lifecycle
The `status` column follows this state machine:
```
streaming → partial → complete
                    → failed  (client disconnected mid-stream)
```
Nothing transitions backwards. If you ever see `complete` revert to `partial`,
you have a consumer ordering bug — events arrived out of order.

### 9. Two servers, one process
`run_all.py` is convenient for development but not how you deploy. In
production each service gets its own container, its own CPU, its own memory
limit, and its own deployment cycle. The only change needed is replacing the
shared `asyncio.Queue` with a real broker.

### 10. `asyncio.sleep` is not a real delay
`await asyncio.sleep(0.15)` yields control back to the event loop for 150ms,
letting other coroutines run. It does not block the thread. This is why the
WebSocket can stream tokens while the consumer simultaneously saves to SQLite —
they interleave at every `await` point.
# dummy-agentic-chat-websocket-sqlite-event
