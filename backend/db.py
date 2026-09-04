"""
backend/db.py

SQLite schema and connection layer for Shield Assist Phase 2.

Design notes (see PHASE_2_PLAN.md v2, Section 2):
- Every table keys off `dispute_id` so the audit trail is queryable by
  dispute ID with a single indexed lookup, not a scavenger hunt.
- Money is stored as integer paise, never float, to avoid rounding bugs.
- All timestamps are ISO 8601 text (UTC), consistent with how Razorpay's
  webhook payload represents `respond_by`.
- `audit_log` records failures too (success='false' + error_detail), not
  just successes -- this is what the edge-case handling in the ingest
  route depends on.
- `transaction()` gives callers a single commit/rollback boundary spanning
  multiple tables (disputes/scores/decisions/audit_log), so a crash
  mid-pipeline never leaves the DB half-written for a given dispute
  (Section 4.7 of the plan).
- `jobs` table is the SQLite-backed job queue (v2): typed jobs with
  lifecycle tracking, retry budgets, and dead-letter status -- all
  inspectable via standard SQL.

This module owns *only* schema + connection management. It has no
knowledge of Razorpay payloads, scoring, or the LLM layer -- those live
in their own modules and are handed a connection or a cursor.
"""

from __future__ import annotations

import os
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Overridable via env var so tests / smoke_test_backend.py can point at a
# throwaway file (or ":memory:") instead of the real demo database.
DB_PATH = os.environ.get(
    "SHIELD_ASSIST_DB_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "shield_assist.db"),
)

SCHEMA_VERSION = 2

# Minimum SQLite version required for RETURNING clause in atomic job claims.
# SQLite 3.35.0 (2021-03-12) added RETURNING support.  If the bundled
# version is older, the job queue falls back to a SELECT+UPDATE claim
# pattern that is still race-safe for single-process SQLite.
MIN_SQLITE_RETURNING_VERSION = (3, 35)


def _sqlite_supports_returning() -> bool:
    """Check if the bundled SQLite version supports the RETURNING clause."""
    try:
        parts = sqlite3.sqlite_version.split(".")
        return tuple(int(p) for p in parts[:2]) >= MIN_SQLITE_RETURNING_VERSION
    except (ValueError, AttributeError):
        return False


# Module-level flag, set once at import time.  The job queue reads this
# to decide between the RETURNING-based claim and the fallback.
sqlite_supports_returning: bool = _sqlite_supports_returning()

_SCHEMA = """
PRAGMA foreign_keys = ON;

-- disputes: one row per Razorpay payment.dispute.created webhook.
-- status follows an explicit state machine (v2):
--   received -> queued -> ingested -> scored -> gated -> drafted
--     -> awaiting_review | ready
-- 'received' = webhook accepted, job enqueued, worker hasn't picked it up.
CREATE TABLE IF NOT EXISTS disputes (
    dispute_id           TEXT PRIMARY KEY,
    payment_id           TEXT,
    reason_code          TEXT NOT NULL,
    network              TEXT NOT NULL,
    amount_paise         INTEGER NOT NULL,
    currency             TEXT NOT NULL DEFAULT 'INR',
    respond_by           TEXT,
    dispute_created_at   TEXT,
    order_id             TEXT,
    status               TEXT NOT NULL DEFAULT 'received',
    source               TEXT NOT NULL,
    raw_webhook_payload  TEXT,
    ingested_at          TEXT NOT NULL,
    updated_at           TEXT NOT NULL
);

-- documents: evidence files uploaded per dispute.
-- content_hash enables extraction caching (skip redundant Gemini calls
-- when the same file is uploaded again).
CREATE TABLE IF NOT EXISTS documents (
    document_id          TEXT PRIMARY KEY,
    dispute_id           TEXT NOT NULL,
    evidence_slot        TEXT NOT NULL,
    local_path           TEXT,
    content_hash         TEXT,
    mime_type            TEXT,
    size_bytes           INTEGER,
    quality              TEXT,
    uploaded_at          TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE IF NOT EXISTS extracted_facts (
    fact_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id          TEXT NOT NULL,
    dispute_id           TEXT NOT NULL,
    fact_type            TEXT NOT NULL,
    fact_value           TEXT NOT NULL,
    extracted_at         TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(document_id),
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE IF NOT EXISTS scores (
    dispute_id              TEXT PRIMARY KEY,
    win_probability         REAL NOT NULL,
    completeness            REAL NOT NULL,
    quality                 REAL NOT NULL,
    consistency             REAL NOT NULL,
    missing_required_slots  TEXT,
    contradiction_flags     TEXT,
    computed_at             TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id           TEXT NOT NULL,
    action               TEXT NOT NULL,
    passed               TEXT NOT NULL,
    failing_conditions   TEXT,
    priority_score       REAL,
    human_override       TEXT,
    decided_at           TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id           TEXT NOT NULL,
    summary_text         TEXT NOT NULL,
    citations            TEXT NOT NULL,
    approved             INTEGER DEFAULT 0,
    generated_at         TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id               INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id           TEXT NOT NULL,
    stage                TEXT NOT NULL,
    detail               TEXT,
    success              TEXT NOT NULL,
    error_detail         TEXT,
    logged_at            TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_dispute_id
    ON documents(dispute_id);
CREATE INDEX IF NOT EXISTS idx_extracted_facts_dispute_id
    ON extracted_facts(dispute_id);
CREATE INDEX IF NOT EXISTS idx_decisions_dispute_id
    ON decisions(dispute_id);
CREATE INDEX IF NOT EXISTS idx_drafts_dispute_id
    ON drafts(dispute_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_dispute_id
    ON audit_log(dispute_id);
CREATE INDEX IF NOT EXISTS idx_disputes_status
    ON disputes(status);

-- jobs: SQLite-backed job queue (v2).
-- Typed jobs with lifecycle tracking, retry budgets, and dead-letter
-- status -- all inspectable via standard SQL:
--   SELECT * FROM jobs WHERE status = 'pending' ORDER BY job_id;
--   SELECT * FROM jobs WHERE status = 'dead_letter';
--
-- DLQ is a status value on the same table, not a separate table,
-- because a hackathon-scale DLQ just needs to be inspectable, not a
-- separate infrastructure component.
CREATE TABLE IF NOT EXISTS jobs (
    job_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type            TEXT NOT NULL,
    dispute_id          TEXT NOT NULL,
    payload             TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    max_attempts        INTEGER NOT NULL DEFAULT 3,
    last_error          TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_dispute_id
    ON jobs(dispute_id);

-- copilot_cache: cache Gemini copilot responses keyed by dispute +
-- ability + input state hash.  Avoids redundant Gemini API calls
-- when the same case is opened repeatedly with no changes.
-- Invalidation is automatic: any change to scores/documents/facts
-- produces a different state_hash, so old entries are simply orphaned.
CREATE TABLE IF NOT EXISTS copilot_cache (
    dispute_id    TEXT NOT NULL,
    ability       TEXT NOT NULL,
    state_hash    TEXT NOT NULL,
    response_text TEXT NOT NULL,
    generated_at  TEXT NOT NULL,
    PRIMARY KEY (dispute_id, ability, state_hash),
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE INDEX IF NOT EXISTS idx_copilot_cache_dispute_id
    ON copilot_cache(dispute_id);

-- webhook_events: idempotency log for incoming Razorpay webhooks.
-- Prevents duplicate case creation from retried webhook deliveries.
-- Stores safe metadata only — never stores secrets.
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id           TEXT PRIMARY KEY,
    event_type         TEXT NOT NULL,
    received_at        TEXT NOT NULL,
    signature_verified TEXT NOT NULL DEFAULT 'false',
    processing_status  TEXT NOT NULL DEFAULT 'pending',
    dispute_id         TEXT,
    raw_payload_hash   TEXT
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_type
    ON webhook_events(event_type);
CREATE INDEX IF NOT EXISTS idx_webhook_events_dispute_id
    ON webhook_events(dispute_id);
"""


def get_connection(db_path: str | None = None) -> sqlite3.Connection:
    """
    Open a new connection with sane defaults for a single-writer,
    demo-scale SQLite deployment.

    - row_factory=sqlite3.Row so callers can access columns by name.
    - foreign_keys=ON must be set per-connection in SQLite (it does not
      persist in the file), so it's re-issued here as well as in the
      schema script.
    - WAL journal mode allows concurrent readers while one writer holds
      the write lock, which matters for the polling read routes running
      alongside an in-flight ingest.
    """
    path = db_path or DB_PATH
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path, timeout=30.0, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Create all tables/indexes if they don't already exist. Idempotent.

    Also logs the SQLite version and RETURNING support status, which
    determines the job-queue's atomic claim strategy.
    """
    conn = get_connection(db_path)
    try:
        conn.executescript(_SCHEMA)

        # --- Schema migrations for existing databases ---
        # drafts.approved column was added post-Phase 2; existing DBs lack it.
        try:
            conn.execute("SELECT approved FROM drafts LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE drafts ADD COLUMN approved INTEGER DEFAULT 0")
            conn.commit()

        # razorpay_doc_id: real Razorpay document ID from POST /v1/documents.
        # NULL until the Documents API integration uploads the file.
        try:
            conn.execute("SELECT razorpay_doc_id FROM documents LIMIT 1")
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE documents ADD COLUMN razorpay_doc_id TEXT")
            conn.commit()
    finally:
        conn.close()

    # Log once at startup so the operator knows which claim path is active.
    import logging
    logger = logging.getLogger("shield_assist.db")
    logger.info(
        "SQLite %s (RETURNING %s)",
        sqlite3.sqlite_version,
        "supported" if sqlite_supports_returning else "not supported, using fallback claim",
    )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    Single commit/rollback boundary for a multi-table write.

    Usage:
        conn = get_connection()
        with transaction(conn):
            conn.execute("INSERT INTO disputes ...")
            conn.execute("INSERT INTO scores ...")
            conn.execute("INSERT INTO audit_log ...")
        # all three commit together, or none do

    Because `get_connection()` sets isolation_level=None (autocommit),
    we manage the BEGIN/COMMIT/ROLLBACK explicitly here rather than
    relying on sqlite3's implicit transaction handling, which is easy
    to get wrong across multiple execute() calls.
    """
    conn.execute("BEGIN IMMEDIATE;")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.execute("COMMIT;")


if __name__ == "__main__":
    # Convenience: `python -m backend.db` initializes the DB in place.
    import sys
    logging.basicConfig(level=logging.INFO)
    init_db()
    print(f"Shield Assist DB initialized at: {DB_PATH}")
    print(f"SQLite version: {sqlite3.sqlite_version}")
    print(f"RETURNING support: {sqlite_supports_returning}")
    print(f"Schema version: {SCHEMA_VERSION}")