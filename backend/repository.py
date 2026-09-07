"""
backend/repository.py

Storage abstraction for Shield Assist.  Two layers:

  CaseRepository — a Protocol (structural typing) defining every data
      access method the pipeline needs.  Any code that touches storage
      imports and depends on this Protocol, never on SQLite directly.

  SQLiteRepository — the only file in the entire codebase allowed to
      contain raw SQL for case/job data.  Implements CaseRepository.
      One connection per method call (thread-safe); no shared state
      between threads.

Why a Protocol instead of a base class:
  - Structural typing: any class that satisfies the method signatures
    qualifies, without inheritance.  This makes the Postgres swap
    (PRODUCT_SPEC.md P2) a drop-in replacement, not a refactor.
  - Testability: tests can provide a fake repository without importing
    or inheriting from this module.
  - The Protocol lives in this same file for discoverability -- callers
    do ``from backend.repository import CaseRepository`` and get both
    the type and (if they need it) the concrete implementation.

Design notes:
  - Every method opens its own connection via get_connection().  This is
    deliberate for thread safety with SQLite's single-writer model: no
    connection is shared across threads, no lock contention, no WAL
    checkpoint stalls.
  - The repository owns ONLY data access.  It has no knowledge of
    scoring, the LLM layer, or HTTP concerns.  Those live in their
    own modules and receive/return plain dicts from here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Protocol, runtime_checkable

from backend.db import get_connection, transaction


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CaseRepository Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class CaseRepository(Protocol):
    """Structural contract for all data access.

    Every method returns plain dicts (not Row objects) so callers are
    decoupled from SQLite's Row type.  The Protocol enforces the shape;
    the SQLite implementation provides the SQL.
    """

    # -- Disputes -----------------------------------------------------------

    def get(self, dispute_id: str) -> Optional[dict]: ...
    def list_by_priority(self, limit: int = 50) -> list[dict]: ...
    def save_dispute(self, row: dict) -> None: ...
    def update_status(self, dispute_id: str, status: str) -> None: ...

    # -- Documents + facts --------------------------------------------------

    def get_documents(self, dispute_id: str) -> list[dict]: ...
    def get_facts(self, dispute_id: str) -> list[dict]: ...
    def get_facts_by_document(self, dispute_id: str) -> dict[str, dict[str, str]]: ...
    def save_document(self, doc: dict) -> None: ...
    def save_fact(self, fact: dict) -> None: ...
    def find_cached_extraction(self, content_hash: str, dispute_id: str) -> Optional[dict]: ...
    def copy_facts(self, source_doc_id: str, target_doc_id: str, dispute_id: str) -> None: ...
    def replace_evidence(self, dispute_id: str, evidence_slot: str, new_doc_id: str) -> list[str]: ...

    # -- Scores + decisions -------------------------------------------------

    def upsert_scores(self, dispute_id: str, scores: dict) -> None: ...
    def get_scores(self, dispute_id: str) -> Optional[dict]: ...
    def get_latest_decision(self, dispute_id: str) -> Optional[dict]: ...
    def insert_decision(self, decision: dict) -> None: ...

    # -- Drafts -------------------------------------------------------------

    def insert_draft(self, draft: dict) -> None: ...
    def get_latest_draft(self, dispute_id: str) -> Optional[dict]: ...

    # -- Copilot cache ------------------------------------------------------

    def get_copilot_cache(self, dispute_id: str, ability: str, state_hash: str) -> Optional[dict]: ...
    def set_copilot_cache(self, dispute_id: str, ability: str, state_hash: str, response_text: str) -> None: ...

    # -- Audit --------------------------------------------------------------

    def write_audit(self, entry: dict) -> None: ...
    def get_audit_trail(self, dispute_id: str) -> list[dict]: ...
    def has_audit_event(self, dispute_id: str, event_type: str) -> bool: ...

    # -- Jobs ---------------------------------------------------------------

    def enqueue_job(self, job_type: str, dispute_id: str, payload: dict) -> int: ...
    def claim_next_job(self, job_type: str | None = None) -> Optional[dict]: ...
    def mark_job_done(self, job_id: int) -> None: ...
    def mark_job_failed(self, job_id: int, error: str) -> None: ...
    def get_jobs_for_dispute(self, dispute_id: str) -> list[dict]: ...
    def get_stale_jobs(self, stale_seconds: int = 120) -> list[dict]: ...

    # -- Webhook events (idempotency tracking) -----------------------------

    def save_webhook_event(self, event: dict) -> None: ...
    def get_webhook_event(self, event_id: str) -> Optional[dict]: ...
    def update_webhook_event_status(self, event_id: str, status: str, dispute_id: str | None = None) -> None: ...

    # -- Metrics ------------------------------------------------------------

    def count_by_status(self) -> dict[str, int]: ...
    def gate_counts(self) -> dict[str, int]: ...
    def avg_scores(self) -> dict[str, float]: ...
    def total_amount_by_action(self) -> dict[str, int]: ...


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------

class SQLiteRepository:
    """Concrete CaseRepository backed by SQLite.

    Thread safety: each method opens its own connection via
    get_connection(), which uses WAL mode + busy_timeout=30s.  No
    connection is held across methods or threads.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self._db_path)

    def connection(self) -> sqlite3.Connection:
        """Provide a raw connection for legacy code that needs one
        (e.g. load_dispute_for_scoring in dispute_normalizer.py).

        Callers must close the connection themselves.  This is an
        explicit, documented exception to the "no raw SQL outside
        repository" rule.
        """
        return get_connection(self._db_path)

    # -- Disputes -----------------------------------------------------------

    def get(self, dispute_id: str) -> Optional[dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM disputes WHERE dispute_id = ?", (dispute_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_by_priority(self, limit: int = 50) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT d.*, s.win_probability, s.completeness, s.quality,
                          s.consistency,
                          dec.action AS gate_action, dec.passed AS gate_passed,
                          dec.priority_score
                   FROM disputes d
                   LEFT JOIN scores s ON d.dispute_id = s.dispute_id
                   LEFT JOIN (
                     SELECT dispute_id, action, passed, priority_score,
                            ROW_NUMBER() OVER (
                              PARTITION BY dispute_id ORDER BY decision_id DESC
                            ) AS rn
                     FROM decisions
                   ) dec ON d.dispute_id = dec.dispute_id AND dec.rn = 1
                   ORDER BY COALESCE(dec.priority_score, 0) DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def save_dispute(self, row: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO disputes
                   (dispute_id, payment_id, reason_code, network, amount_paise,
                    currency, respond_by, dispute_created_at, order_id,
                    status, source, raw_webhook_payload,
                    ingested_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?, ?)""",
                (
                    row["dispute_id"], row.get("payment_id"), row["reason_code"],
                    row["network"], row["amount_paise"],
                    row.get("currency", "INR"), row.get("respond_by"),
                    row.get("dispute_created_at"), row.get("order_id"),
                    row.get("status", "received"), row["source"],
                    row.get("raw_webhook_payload"),
                    row["ingested_at"], row["updated_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def idempotent_insert_dispute(
        self,
        row: dict,
        job_type: str | None,
        job_payload: dict | None = None,
    ) -> tuple[bool, int | None]:
        """Atomically check idempotency, insert dispute, and enqueue job.

        Returns (was_already_present, job_id).
        If the dispute was already present: (True, None).
        If newly inserted: (False, job_id).

        This is the ONLY way to ingest a dispute from a webhook — it
        prevents the race condition where two concurrent webhook deliveries
        both pass the idempotency check and both enqueue a job.

        The entire operation runs in a single BEGIN IMMEDIATE transaction:
          1. SELECT to check if dispute_id exists
          2. INSERT if not
          3. INSERT job
          4. COMMIT

        The BEGIN IMMEDIATE acquires the write lock immediately, so a
        concurrent thread will block at its own BEGIN IMMEDIATE until
        this transaction commits, then its SELECT will see the row.
        """
        from backend.db import transaction as db_transaction

        conn = self._conn()
        try:
            with db_transaction(conn):
                existing = conn.execute(
                    "SELECT 1 FROM disputes WHERE dispute_id = ?",
                    (row["dispute_id"],),
                ).fetchone()

                if existing:
                    return True, None

                conn.execute(
                    """INSERT INTO disputes
                       (dispute_id, payment_id, reason_code, network, amount_paise,
                        currency, respond_by, dispute_created_at, order_id,
                        status, source, raw_webhook_payload,
                        ingested_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                               ?, ?, ?, ?, ?)""",
                    (
                        row["dispute_id"], row.get("payment_id"), row["reason_code"],
                        row["network"], row["amount_paise"],
                        row.get("currency", "INR"), row.get("respond_by"),
                        row.get("dispute_created_at"), row.get("order_id"),
                        row.get("status", "received"), row["source"],
                        row.get("raw_webhook_payload"),
                        row["ingested_at"], row["updated_at"],
                    ),
                )

                job_id = None
                if job_type is not None:
                    cursor = conn.execute(
                        """INSERT INTO jobs
                           (job_type, dispute_id, payload, status, created_at, updated_at)
                           VALUES (?, ?, ?, 'pending', ?, ?)""",
                        (
                            job_type, row["dispute_id"],
                            json.dumps(job_payload or {}),
                            row["ingested_at"], row["ingested_at"],
                        ),
                    )
                    job_id = cursor.lastrowid

            return False, job_id
        finally:
            conn.close()

    def update_status(self, dispute_id: str, status: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE disputes SET status = ?, updated_at = ? WHERE dispute_id = ?",
                (status, _now_iso(), dispute_id),
            )
            conn.commit()
        finally:
            conn.close()

    # -- Documents + facts --------------------------------------------------

    def get_documents(self, dispute_id: str) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM documents WHERE dispute_id = ? ORDER BY uploaded_at",
                (dispute_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_facts(self, dispute_id: str) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM extracted_facts WHERE dispute_id = ? ORDER BY fact_id",
                (dispute_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_facts_by_document(self, dispute_id: str) -> dict[str, dict[str, str]]:
        """Group facts by document_id -> {fact_type: fact_value}."""
        facts = self.get_facts(dispute_id)
        result: dict[str, dict[str, str]] = {}
        for f in facts:
            doc_id = f["document_id"]
            if doc_id not in result:
                result[doc_id] = {}
            result[doc_id][f["fact_type"]] = f["fact_value"]
        return result

    def save_document(self, doc: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO documents
                   (document_id, dispute_id, evidence_slot, local_path,
                    content_hash, mime_type, size_bytes, quality,
                    razorpay_doc_id, uploaded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc["document_id"], doc["dispute_id"], doc["evidence_slot"],
                    doc.get("local_path"), doc.get("content_hash"),
                    doc.get("mime_type"), doc.get("size_bytes"),
                    doc.get("quality", "unknown"),
                    doc.get("razorpay_doc_id"),
                    doc["uploaded_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def update_document_razorpay_id(self, document_id: str, razorpay_doc_id: str) -> None:
        """Set the Razorpay document ID after a successful upload.

        Called by the Documents API integration when a file is
        successfully uploaded to Razorpay's ecosystem.
        """
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE documents SET razorpay_doc_id = ? WHERE document_id = ?",
                (razorpay_doc_id, document_id),
            )
            conn.commit()
        finally:
            conn.close()

    def save_fact(self, fact: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO extracted_facts
                   (document_id, dispute_id, fact_type, fact_value, extracted_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    fact["document_id"], fact["dispute_id"],
                    fact["fact_type"], str(fact["fact_value"]),
                    fact["extracted_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def find_cached_extraction(self, content_hash: str, dispute_id: str, exclude_doc_id: str | None = None) -> Optional[dict]:
        """Find an existing document with the same SHA-256 that already
        has extracted facts.

        First checks within the same dispute, then falls back to any
        dispute (facts are universal — the same file contains the same
        facts regardless of which dispute it belongs to).

        Args:
            content_hash: SHA-256 hex digest of the uploaded file.
            dispute_id: The current dispute to check first.
            exclude_doc_id: Optional document ID to exclude from results
                           (the newly-saved document that hasn't been
                           processed yet).

        Returns the document row dict if found, None otherwise.
        """
        if not content_hash:
            return None
        conn = self._conn()
        try:
            # Same-dispute cache: faster, avoids cross-dispute lookups
            params: list = [content_hash, dispute_id]
            exclude_clause = ""
            if exclude_doc_id:
                exclude_clause = "AND d.document_id != ?"
                params.append(exclude_doc_id)
            row = conn.execute(
                f"""SELECT d.document_id, d.content_hash
                   FROM documents d
                   WHERE d.content_hash = ?
                     AND d.dispute_id = ?
                     {exclude_clause}
                     AND EXISTS (
                         SELECT 1 FROM extracted_facts f
                         WHERE f.document_id = d.document_id
                     )
                   LIMIT 1""",
                params,
            ).fetchone()
            if row:
                return dict(row)
            # Cross-dispute cache: facts are universal
            params2: list = [content_hash]
            exclude_clause2 = ""
            if exclude_doc_id:
                exclude_clause2 = "AND d.document_id != ?"
                params2.append(exclude_doc_id)
            row = conn.execute(
                f"""SELECT d.document_id, d.content_hash
                   FROM documents d
                   WHERE d.content_hash = ?
                     {exclude_clause2}
                     AND EXISTS (
                         SELECT 1 FROM extracted_facts f
                         WHERE f.document_id = d.document_id
                     )
                   LIMIT 1""",
                params2,
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def copy_facts(self, source_doc_id: str, target_doc_id: str, dispute_id: str) -> int:
        """Copy all extracted facts from source_doc_id to target_doc_id.

        Used when a duplicate file is uploaded and we skip Gemini OCR.
        The fact_type/fact_value are copied as-is; only document_id
        and extracted_at (set to now) change.

        NOTE: We read facts from the source document WITHOUT filtering
        by dispute_id, because the cached source document may belong to
        a different dispute (cross-dispute cache hit). Facts are
        universal — the same file contains the same facts regardless of
        which dispute it was uploaded to.

        Returns the number of facts copied.
        """
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        conn = self._conn()
        try:
            facts = conn.execute(
                """SELECT fact_type, fact_value FROM extracted_facts
                   WHERE document_id = ?""",
                (source_doc_id,),
            ).fetchall()
            for f in facts:
                conn.execute(
                    """INSERT INTO extracted_facts
                       (document_id, dispute_id, fact_type, fact_value, extracted_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (target_doc_id, dispute_id, f["fact_type"], f["fact_value"], now_iso),
                )
            conn.commit()
            return len(facts)
        finally:
            conn.close()

    def replace_evidence(self, dispute_id: str, evidence_slot: str, new_doc_id: str) -> list[str]:
        """Replace evidence for a specific slot in a dispute.

        Deletes all extracted_facts for documents in the given slot,
        so that the new document's facts become the sole source.
        The old document rows are preserved for audit trail but their
        facts are removed to prevent stale contradictions.

        Returns a list of old document_id values whose facts were removed.
        """
        conn = self._conn()
        try:
            # Find old documents in this slot
            old_docs = conn.execute(
                """SELECT document_id FROM documents
                 WHERE dispute_id = ? AND evidence_slot = ?
                 AND document_id != ?""",
                (dispute_id, evidence_slot, new_doc_id),
            ).fetchall()
            old_doc_ids = [r["document_id"] for r in old_docs]

            # Delete their facts (prevents stale contradictions)
            for doc_id in old_doc_ids:
                conn.execute(
                    "DELETE FROM extracted_facts WHERE document_id = ?",
                    (doc_id,),
                )

            conn.commit()
            return old_doc_ids
        finally:
            conn.close()

    # -- Scores + decisions -------------------------------------------------

    def upsert_scores(self, dispute_id: str, scores: dict) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM scores WHERE dispute_id = ?", (dispute_id,))
            conn.execute(
                """INSERT INTO scores
                   (dispute_id, win_probability, completeness, quality,
                    consistency, missing_required_slots, contradiction_flags,
                    computed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    dispute_id,
                    scores["win_probability"],
                    scores["completeness"],
                    scores["quality"],
                    scores["consistency"],
                    json.dumps(scores.get("missing_required_slots", [])),
                    json.dumps(scores.get("contradiction_flags", [])),
                    scores.get("computed_at", _now_iso()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_scores(self, dispute_id: str) -> Optional[dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM scores WHERE dispute_id = ?", (dispute_id,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            # Deserialize JSON fields
            for key in ("missing_required_slots", "contradiction_flags"):
                if d.get(key):
                    d[key] = json.loads(d[key])
            return d
        finally:
            conn.close()

    def get_latest_decision(self, dispute_id: str) -> Optional[dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT * FROM decisions WHERE dispute_id = ?
                   ORDER BY decision_id DESC LIMIT 1""",
                (dispute_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("failing_conditions"):
                d["failing_conditions"] = json.loads(d["failing_conditions"])
            d["passed"] = d["passed"] == "true"
            return d
        finally:
            conn.close()

    def insert_decision(self, decision: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO decisions
                   (dispute_id, action, passed, failing_conditions,
                    priority_score, human_override, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    decision["dispute_id"],
                    decision["action"],
                    "true" if decision["passed"] else "false",
                    json.dumps(decision.get("failing_conditions", [])),
                    decision.get("priority_score"),
                    decision.get("human_override"),
                    decision.get("decided_at", _now_iso()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # -- Drafts -------------------------------------------------------------

    def insert_draft(self, draft: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO drafts
                   (dispute_id, summary_text, citations, approved, generated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    draft["dispute_id"],
                    draft["summary_text"],
                    json.dumps(draft["citations"]),
                    "true" if draft.get("approved") else "false",
                    draft.get("generated_at", _now_iso()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def update_draft_summary(self, dispute_id: str, summary_text: str) -> None:
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT draft_id FROM drafts WHERE dispute_id = ?
                   ORDER BY draft_id DESC LIMIT 1""",
                (dispute_id,),
            ).fetchone()
            if row:
                conn.execute(
                    """UPDATE drafts SET summary_text = ? WHERE draft_id = ?""",
                    (summary_text, row["draft_id"]),
                )
            else:
                conn.execute(
                    """INSERT INTO drafts
                       (dispute_id, summary_text, citations, approved, generated_at)
                       VALUES (?, ?, '[]', 'false', ?)""",
                    (dispute_id, summary_text, _now_iso()),
                )
            conn.commit()
        finally:
            conn.close()

    def get_latest_draft(self, dispute_id: str) -> Optional[dict]:
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT * FROM drafts WHERE dispute_id = ?
                   ORDER BY draft_id DESC LIMIT 1""",
                (dispute_id,),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("citations"):
                d["citations"] = json.loads(d["citations"])
            return d
        finally:
            conn.close()

    # -- Copilot cache ------------------------------------------------------

    def get_copilot_cache(self, dispute_id: str, ability: str, state_hash: str) -> Optional[dict]:
        """Look up a cached copilot response for this dispute + ability + state.

        Returns {'response_text': ..., 'generated_at': ...} if found, else None.
        The caller checks the state_hash to ensure the cached response
        matches the current case state (scores, facts, etc.).
        """
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT response_text, generated_at
                   FROM copilot_cache
                   WHERE dispute_id = ? AND ability = ? AND state_hash = ?
                   LIMIT 1""",
                (dispute_id, ability, state_hash),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def set_copilot_cache(self, dispute_id: str, ability: str, state_hash: str, response_text: str) -> None:
        """Store a copilot response in the cache.

        Uses INSERT OR REPLACE so repeated calls for the same
        (dispute_id, ability, state_hash) overwrite gracefully.
        """
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO copilot_cache
                   (dispute_id, ability, state_hash, response_text, generated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (dispute_id, ability, state_hash, response_text, _now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    # -- Audit --------------------------------------------------------------

    def write_audit(self, entry: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO audit_log
                   (dispute_id, stage, detail, success, error_detail, logged_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    entry.get("dispute_id"),
                    entry["stage"],
                    json.dumps(entry.get("detail"), default=str)
                    if entry.get("detail")
                    else None,
                    "true" if entry.get("success", True) else "false",
                    entry.get("error_detail"),
                    entry.get("logged_at", _now_iso()),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_audit_trail(self, dispute_id: str) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT log_id, stage, detail, success, error_detail, logged_at
                   FROM audit_log
                   WHERE dispute_id = ?
                   ORDER BY log_id ASC""",
                (dispute_id,),
            ).fetchall()
            results = []
            for r in rows:
                d = dict(r)
                if d.get("detail"):
                    d["detail"] = json.loads(d["detail"])
                d["success"] = d["success"] == "true"
                results.append(d)
            return results
        finally:
            conn.close()

    def has_audit_event(self, dispute_id: str, event_type: str) -> bool:
        """Check if a specific audit event exists for a dispute.

        Used by:
          - contest_job.py: checks 'human.approved' before contest submission
          - POST /disputes/{id}/contest route: fail-fast 409 before enqueue
        """
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT 1 FROM audit_log
                   WHERE dispute_id = ? AND stage = ? AND success = 'true'
                   LIMIT 1""",
                (dispute_id, event_type),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    # -- Jobs ---------------------------------------------------------------

    def enqueue_job(self, job_type: str, dispute_id: str, payload: dict) -> int:
        """Insert a pending job and return its ID.

        The job queue's worker will claim it via claim_next_job().
        """
        conn = self._conn()
        try:
            now = _now_iso()
            cursor = conn.execute(
                """INSERT INTO jobs
                   (job_type, dispute_id, payload, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (job_type, dispute_id, json.dumps(payload), now, now),
            )
            conn.commit()
            return cursor.lastrowid  # type: ignore[return-value]
        finally:
            conn.close()

    def claim_next_job(self, job_type: str | None = None) -> Optional[dict]:
        """Atomically claim the next pending job.

        Uses RETURNING if SQLite >= 3.35, otherwise falls back to
        SELECT + UPDATE (still race-safe: the UPDATE's WHERE status='pending'
        ensures only one worker wins).

        Args:
            job_type: If provided, only claim jobs of this type.
                      If None, claim any pending job.
        """
        from backend.db import sqlite_supports_returning

        conn = self._conn()
        try:
            now = _now_iso()

            if sqlite_supports_returning:
                type_clause = "AND job_type = ?" if job_type else ""
                params: tuple = (now,) + ((job_type,) if job_type else ())
                row = conn.execute(
                    f"""UPDATE jobs
                        SET status = 'in_progress', updated_at = ?, attempts = attempts + 1
                        WHERE status = 'pending' {type_clause}
                          AND job_id = (
                            SELECT job_id FROM jobs
                            WHERE status = 'pending' {type_clause}
                            ORDER BY job_id LIMIT 1
                          )
                        RETURNING *""",
                    params,
                ).fetchone()
            else:
                # Fallback: SELECT for a candidate, then UPDATE with status guard
                type_clause = "AND job_type = ?" if job_type else ""
                select_params: tuple = ((job_type,) if job_type else ())
                candidate = conn.execute(
                    f"""SELECT job_id FROM jobs
                        WHERE status = 'pending' {type_clause}
                        ORDER BY job_id LIMIT 1""",
                    select_params,
                ).fetchone()

                if candidate is None:
                    return None

                update_params = (now, candidate["job_id"])
                conn.execute(
                    """UPDATE jobs
                       SET status = 'in_progress', updated_at = ?, attempts = attempts + 1
                       WHERE job_id = ? AND status = 'pending'""",
                    update_params,
                )
                row = conn.execute(
                    "SELECT * FROM jobs WHERE job_id = ?",
                    (candidate["job_id"],),
                ).fetchone()

            conn.commit()
            return dict(row) if row else None
        finally:
            conn.close()

    def mark_job_done(self, job_id: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE jobs SET status = 'done', updated_at = ? WHERE job_id = ?",
                (_now_iso(), job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_job_failed(self, job_id: int, error: str) -> None:
        """Increment attempts and move to dead_letter if at max_attempts."""
        conn = self._conn()
        try:
            now = _now_iso()
            conn.execute(
                """UPDATE jobs
                   SET attempts = attempts + 1,
                       status = CASE
                         WHEN attempts + 1 >= max_attempts THEN 'dead_letter'
                         ELSE 'pending'
                       END,
                       last_error = ?,
                       updated_at = ?
                   WHERE job_id = ?""",
                (error, now, job_id),
            )
            conn.commit()
        finally:
            conn.close()

    def get_jobs_for_dispute(self, dispute_id: str) -> list[dict]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT * FROM jobs WHERE dispute_id = ?
                   ORDER BY job_id ASC""",
                (dispute_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_stale_jobs(self, stale_seconds: int = 120) -> list[dict]:
        """Find jobs stuck in_progress longer than stale_seconds.

        Called on worker startup (PHASE_2_PLAN.md v2, Section 5.10):
        a worker crash leaves jobs at 'in_progress' forever.  This
        method finds them so the worker can requeue or dead-letter.
        """
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT * FROM jobs
                   WHERE status = 'in_progress'
                     AND updated_at < datetime('now', ? || ' seconds')""",
                (-stale_seconds,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # -- Metrics ------------------------------------------------------------

    def count_by_status(self) -> dict[str, int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM disputes GROUP BY status"
            ).fetchall()
            return {r["status"]: r["n"] for r in rows}
        finally:
            conn.close()

    def gate_counts(self) -> dict[str, int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT dec.action, COUNT(*) AS n
                   FROM decisions dec
                   INNER JOIN (
                     SELECT dispute_id, MAX(decision_id) AS max_id
                     FROM decisions GROUP BY dispute_id
                   ) latest ON dec.decision_id = latest.max_id
                   GROUP BY dec.action"""
            ).fetchall()
            return {r["action"]: r["n"] for r in rows}
        finally:
            conn.close()

    def avg_scores(self) -> dict[str, float]:
        conn = self._conn()
        try:
            row = conn.execute(
                """SELECT
                     COALESCE(AVG(completeness), 0) AS avg_completeness,
                     COALESCE(AVG(quality), 0) AS avg_quality,
                     COALESCE(AVG(consistency), 0) AS avg_consistency,
                     COALESCE(AVG(win_probability), 0) AS avg_win_probability
                   FROM scores"""
            ).fetchone()
            return dict(row) if row else {
                "avg_completeness": 0, "avg_quality": 0,
                "avg_consistency": 0, "avg_win_probability": 0,
            }
        finally:
            conn.close()

    def total_amount_by_action(self) -> dict[str, int]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT dec.action, COALESCE(SUM(d.amount_paise), 0) AS total
                   FROM decisions dec
                   INNER JOIN (
                     SELECT dispute_id, MAX(decision_id) AS max_id
                     FROM decisions GROUP BY dispute_id
                   ) latest ON dec.decision_id = latest.max_id
                   JOIN disputes d ON dec.dispute_id = d.dispute_id
                   GROUP BY dec.action"""
            ).fetchall()
            return {r["action"]: r["total"] for r in rows}
        finally:
            conn.close()

    # -- Webhook events (idempotency tracking) -------------------------------

    def save_webhook_event(self, event: dict) -> None:
        """Record an incoming webhook event for idempotency tracking.

        event_id is the primary key — duplicates are silently ignored.
        No secrets are stored.
        """
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO webhook_events
                   (event_id, event_type, received_at, signature_verified,
                    processing_status, dispute_id, raw_payload_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event["event_id"],
                    event["event_type"],
                    event["received_at"],
                    "true" if event.get("signature_verified", False) else "false",
                    event.get("processing_status", "pending"),
                    event.get("dispute_id"),
                    event.get("raw_payload_hash"),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_webhook_event(self, event_id: str) -> Optional[dict]:
        """Check if a webhook event has already been processed."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM webhook_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def update_webhook_event_status(
        self, event_id: str, status: str, dispute_id: str | None = None
    ) -> None:
        """Update the processing status of a webhook event."""
        conn = self._conn()
        try:
            if dispute_id:
                conn.execute(
                    "UPDATE webhook_events SET processing_status = ?, dispute_id = ? WHERE event_id = ?",
                    (status, dispute_id, event_id),
                )
            else:
                conn.execute(
                    "UPDATE webhook_events SET processing_status = ? WHERE event_id = ?",
                    (status, event_id),
                )
            conn.commit()
        finally:
            conn.close()
