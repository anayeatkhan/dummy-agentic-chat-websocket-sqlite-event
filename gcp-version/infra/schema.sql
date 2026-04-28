-- ─────────────────────────────────────────────────────────────────────────────
-- Cloud SQL (PostgreSQL 15) schema
-- Run once against your Cloud SQL instance:
--
--   psql "$DATABASE_URL" -f infra/schema.sql
--
-- or via Cloud SQL Studio in the GCP Console.
-- ─────────────────────────────────────────────────────────────────────────────

-- pgcrypto provides gen_random_uuid() used by DEFAULT clauses.
-- Already available on Cloud SQL PostgreSQL without extra install.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";


-- ─── sessions ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sessions (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID        NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);

ALTER TABLE sessions ENABLE ROW LEVEL SECURITY;

-- Drop before recreate so this script is re-runnable
DROP POLICY IF EXISTS user_isolation ON sessions;
CREATE POLICY user_isolation ON sessions
  USING (user_id = current_setting('app.user_id')::uuid);


-- ─── messages ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS messages (
  id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id   UUID        NOT NULL REFERENCES sessions(id),
  user_id      UUID        NOT NULL,
  role         TEXT        NOT NULL CHECK (role IN ('user','assistant','system')),
  content      TEXT,
  status       TEXT        NOT NULL DEFAULT 'streaming'
                               CHECK (status IN ('streaming','partial','complete','failed')),
  seq          BIGINT      NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

-- Composite index for history reads (session ordered by position)
CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);
-- Index for user-isolation checks
CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages(user_id);

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS user_isolation ON messages;
CREATE POLICY user_isolation ON messages
  USING (user_id = current_setting('app.user_id')::uuid);


-- ─── workflow_events ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS workflow_events (
  id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id  UUID        NOT NULL REFERENCES messages(id),
  step_name   TEXT        NOT NULL,
  payload     JSONB,                         -- native JSONB: indexed, queryable
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_workflow_events_message_id ON workflow_events(message_id);


-- ─── Helper: updated_at trigger for sessions ─────────────────────────────────

CREATE OR REPLACE FUNCTION touch_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sessions_touch_updated_at ON sessions;
CREATE TRIGGER sessions_touch_updated_at
  BEFORE UPDATE ON sessions
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
