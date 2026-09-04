"""
backend/dispute_normalizer.py

Two functions that form the seam between "whatever shape a webhook /
simulated payload arrives in" and "what Phase 2 needs at different
points in the dispute lifecycle":

  normalize_webhook_to_dispute(webhook_payload)
      Raw Razorpay `payment.dispute.created` webhook -> a
      NormalizedDisputeRow containing exactly the columns the
      `disputes` table needs.  Always produces zero documents and
      an unresolved customer_name -- this is the NORMAL shape of
      this event, not an edge case: Razorpay's real dispute.entity
      carries no evidence yet (evidence.* is null for every slot at
      creation time) and no customer-name field at all (verified
      against the sourced dispute/payment entity shapes).  Evidence
      and identity both arrive later, independently of the webhook,
      through the document-upload route.

  load_dispute_for_scoring(dispute_id, conn)
      DB rows (disputes + documents + extracted_facts) -> a
      backend.domain.Dispute ready for evaluate_dispute().  Called on
      EVERY ingest and EVERY re-score (PHASE_2_PLAN.md 4.6) -- not a
      one-time transform of the webhook, since documents accumulate
      over the dispute's lifetime independently of when it was first
      ingested.

Sourced webhook shape (payment.dispute.created), verified against
Razorpay's own docs:

    payload.dispute.entity: id, payment_id, amount, currency,
        amount_deducted, reason_code, respond_by, status, phase,
        created_at, evidence
    payload.payment.entity: id, amount, currency, status, order_id,
        method, amount_refunded, refund_status, captured, email,
        contact, created_at
    payload.dispute.entity.evidence: dict keyed by slot name
        (shipping_proof, billing_proof, ..., term_and_conditions,
        others), each null until the merchant submits, then a list of
        document_id strings (or, for 'others', a list of
        {type, document_ids} objects).

No customer-name field exists anywhere in this payload.  order_id
lives on payload.payment.entity, not payload.dispute.entity.

The evidence-slot alias map (normalize_evidence_slot) is kept here
as a utility for the document-upload route, which must canonicalize
slot names before storing them in the `documents` table.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.domain import Dispute, Document

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# In-scope reason codes, per PRODUCT_SPEC.md Section "Scope: reason codes
# covered".  Anything outside this set is handled explicitly (4.2) rather
# than left to raise deep inside evaluate_dispute().
IN_SCOPE_REASON_CODES = frozenset({
    "RZP01", "RZP04", "RZP05", "RZP06", "UPI1064", "UPI1062",
})

# Evidence-slot canonicalization, per PHASE_0_SUMMARY.md corrections.
# The real Razorpay field is singular "term_and_conditions", not
# "terms_and_conditions".  "shipping_proof" maps to "proof_of_service"
# because Razorpay's actual language is "proof of service/product
# delivery" -- a distinct field from shipping.
_EVIDENCE_SLOT_ALIASES: dict[str, str] = {
    "terms_and_conditions": "term_and_conditions",
    "shipping_proof": "proof_of_service",
}

_KNOWN_EVIDENCE_SLOTS: frozenset[str] = frozenset({
    "shipping_proof", "billing_proof", "cancellation_proof",
    "customer_communication", "proof_of_service", "explanation_letter",
    "refund_confirmation", "access_activity_log",
    "refund_cancellation_policy", "term_and_conditions", "others",
})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class NormalizationError(Exception):
    """Missing or malformed required field(s).

    Raised by normalize_webhook_to_dispute() when the payload cannot be
    parsed into a NormalizedDisputeRow.  Callers (the ingest route) catch
    this specifically and return 400, never a generic 500 -- see
    PHASE_2_PLAN.md Section 4.2.

    Attributes:
        field_errors: list of human-readable descriptions of each
            missing/invalid field, so the 400 response can list every
            problem in one shot instead of failing on the first one.
    """

    def __init__(self, message: str, field_errors: list[str]):
        super().__init__(message)
        self.field_errors = field_errors


class OutOfScopeReasonCode(Exception):
    """Well-formed payload whose reason_code is not one of the 6 in-scope codes.

    Handled distinctly from NormalizationError: the dispute IS stored
    (status='out_of_scope'), not rejected outright -- this is what
    PHASE_2_PLAN.md Section 4.2 prescribes.
    """

    def __init__(self, reason_code: str):
        super().__init__(f"Reason code '{reason_code}' is out of scope")
        self.reason_code = reason_code


# ---------------------------------------------------------------------------
# NormalizedDisputeRow
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NormalizedDisputeRow:
    """Exactly the columns the `disputes` table needs, no more.

    Kept distinct from backend.domain.Dispute (the scoring-time object)
    so it's obvious at a glance which fields a fresh webhook CAN and
    CANNOT supply.  A webhook never provides:
      - documents (arrive via upload)
      - customer_name (resolved from documents)
      - order_id  (lives on payment.entity, stored separately)
      - dispute_date as a date string (it arrives as created_at, a
        unix timestamp, and is stored as-is for the DB)
    """

    dispute_id: str
    payment_id: str
    reason_code: str
    network: str  # "razorpay" | "upi"
    amount_paise: int
    currency: str
    respond_by: str | None  # ISO datetime; None if webhook omitted it
    created_at: str | None  # ISO datetime from dispute.entity.created_at; None if missing


# ---------------------------------------------------------------------------
# Evidence-slot normalization (utility for document-upload route)
# ---------------------------------------------------------------------------

def normalize_evidence_slot(raw_slot: str) -> str:
    """Canonicalize an evidence-slot label from any source.

    Handles the two known alias corrections from Phase 0 and falls
    through to "others" for unrecognized slots -- an unrecognized-but-
    real Razorpay document type shouldn't crash ingestion, it should
    just not count toward any reason code's required list.

    This is called by the document-upload route before storing a
    document row, ensuring the `documents.evidence_slot` column always
    contains a canonical value that evidence_requirements.py can match
    against.
    """
    slot = raw_slot.strip().lower()
    slot = _EVIDENCE_SLOT_ALIASES.get(slot, slot)
    return slot if slot in _KNOWN_EVIDENCE_SLOTS else "others"


# ---------------------------------------------------------------------------
# Webhook normalization
# ---------------------------------------------------------------------------

def _network_for_reason_code(reason_code: str) -> str:
    """Derive the network label from the reason code prefix."""
    return "upi" if reason_code.upper().startswith("UPI") else "razorpay"


def _unix_to_iso(ts: Any, field_name: str, field_errors: list[str]) -> str | None:
    """Convert a unix timestamp (int/float seconds) to an ISO 8601 string.

    Razorpay sends timestamps as unix seconds (int).  Synthetic/manual
    payloads may send ISO strings directly.  Accept both; reject anything
    else loudly rather than silently defaulting.

    Returns None if the input is None (the field is optional) or
    unparseable (in which case an error is appended to field_errors).
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(
                timespec="seconds"
            )
        except (ValueError, OSError, OverflowError):
            field_errors.append(f"{field_name} (invalid unix timestamp: {ts})")
            return None
    if isinstance(ts, str):
        try:
            # Accept trailing 'Z' as UTC.
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return ts
        except ValueError:
            field_errors.append(f"{field_name} (unparseable ISO timestamp: {ts!r})")
            return None
    field_errors.append(
        f"{field_name} (expected unix timestamp or ISO string, got {type(ts).__name__})"
    )
    return None


def normalize_webhook_to_dispute(webhook_payload: dict) -> NormalizedDisputeRow:
    """Convert a raw `payment.dispute.created` webhook to a DB-ready row.

    Handles two payload shapes:
      1. Full Razorpay webhook: {"payload": {"dispute": {"entity": {...}},
         "payment": {"entity": {...}}}}
      2. Flat entity (synthetic/manual test payloads): {"id": "...",
         "reason_code": "...", ...}

    Expected field paths (verified against Razorpay's sourced dispute
    webhook schema from Phase 0):

      payload.dispute.entity.id              -> dispute_id
      payload.dispute.entity.payment_id      -> payment_id
      payload.dispute.entity.reason_code     -> reason_code
      payload.dispute.entity.amount          -> amount_paise (integer, paise)
      payload.dispute.entity.currency        -> currency
      payload.dispute.entity.respond_by      -> respond_by (unix timestamp)
      payload.dispute.entity.created_at      -> created_at (unix timestamp)
      payload.payment.entity.order_id        -> (NOT stored here; see resolve_order_id)

    Raises:
        NormalizationError    -- required fields missing/malformed.
        OutOfScopeReasonCode  -- well-formed but reason_code not in scope.
    """
    # Try full webhook shape first, then fall back to a flat entity.
    dispute_entity = (
        webhook_payload.get("payload", {}).get("dispute", {}).get("entity")
    )
    if not isinstance(dispute_entity, dict):
        # Flat shape: the webhook payload IS the dispute entity.
        dispute_entity = (
            webhook_payload if "reason_code" in webhook_payload else None
        )

    if dispute_entity is None:
        raise NormalizationError(
            "Payload missing 'payload.dispute.entity' (or a flat dispute entity) block.",
            ["payload.dispute.entity"],
        )

    # Collect ALL field errors before failing, so the 400 response
    # lists every problem in one shot (PHASE_2_PLAN.md Section 4.2).
    field_errors: list[str] = []

    dispute_id = dispute_entity.get("id") or dispute_entity.get("dispute_id")
    if not dispute_id:
        field_errors.append("id (dispute_id)")

    reason_code_raw = dispute_entity.get("reason_code")
    if not reason_code_raw:
        field_errors.append("reason_code")

    amount = dispute_entity.get("amount")
    amount_paise: int | None = None
    if amount is None:
        field_errors.append("amount")
    elif not isinstance(amount, int) or amount < 0:
        field_errors.append("amount (must be a non-negative integer, paise)")
    else:
        amount_paise = amount

    payment_id = dispute_entity.get("payment_id")
    if not payment_id:
        field_errors.append("payment_id")

    # respond_by is structurally optional (Section 4.4 concerns an
    # already-ELAPSED respond_by, not a missing one), but if present
    # it must parse.
    respond_by_iso = _unix_to_iso(
        dispute_entity.get("respond_by"),
        "respond_by",
        field_errors,
    )

    # created_at is the dispute-creation timestamp from Razorpay.
    # Required for scoring (check_date_order compares document event
    # dates against this), but if the webhook omits it we fall back
    # to ingested_at at the DB layer rather than rejecting the payload.
    created_at_iso = _unix_to_iso(
        dispute_entity.get("created_at"),
        "created_at",
        field_errors,
    )

    # If any REQUIRED field is missing/invalid, fail now with a 400.
    # created_at warnings are non-fatal -- the ingest route handles
    # the fallback to ingested_at.
    required_errors = [e for e in field_errors if not e.startswith("created_at")]
    if required_errors:
        raise NormalizationError(
            f"Dispute webhook missing/invalid required field(s): "
            f"{', '.join(required_errors)}",
            required_errors,
        )

    assert amount_paise is not None

    reason_code = str(reason_code_raw).upper().replace(" ", "")
    if reason_code not in IN_SCOPE_REASON_CODES:
        raise OutOfScopeReasonCode(reason_code)

    return NormalizedDisputeRow(
        dispute_id=str(dispute_id),
        payment_id=str(payment_id),
        reason_code=reason_code,
        network=_network_for_reason_code(reason_code),
        amount_paise=amount_paise,
        currency=dispute_entity.get("currency", "INR"),
        respond_by=respond_by_iso,
        created_at=created_at_iso,
    )


# ---------------------------------------------------------------------------
# order_id resolution (called by ingest route, result stored in DB)
# ---------------------------------------------------------------------------

def resolve_order_id(webhook_payload: dict) -> str | None:
    """Extract order_id from the webhook's payment entity.

    order_id lives on payload.payment.entity, not payload.dispute.entity
    (verified: Razorpay's dispute.entity has no order_id field at all).
    The `disputes` table stores it as an `order_id` column so it's
    available for every re-score without re-fetching the original webhook.

    Returns None if payment.entity is missing or lacks order_id -- the
    ingest route should still proceed (order_id is not required for
    dispute ingestion), but check_order_id_match will skip until the
    value is available.
    """
    payment_entity = (
        webhook_payload.get("payload", {}).get("payment", {}).get("entity")
    )
    if not isinstance(payment_entity, dict):
        return None
    order_id = payment_entity.get("order_id")
    return str(order_id) if order_id else None


# ---------------------------------------------------------------------------
# Customer-name anchor resolution
# ---------------------------------------------------------------------------

def resolve_customer_name_anchor(documents: list[Document]) -> str | None:
    """Resolve the customer-name anchor from uploaded documents.

    No document carries an authoritative pre-verified name -- the first
    uploaded document that HAS a customer_name fact becomes the anchor
    every subsequent document is checked against.  Returns None if no
    document has a customer_name fact yet (including the zero-document
    case), which check_name_match treats as "not yet resolvable" (skip,
    no flags) rather than a forced mismatch.

    This is called on EVERY scoring pass (load_dispute_for_scoring),
    not just once, because a new document with a name field can arrive
    at any time and activate the name-match check for the first time.
    """
    for doc in documents:
        name = doc.fields.get("customer_name")
        if name:
            return str(name)
    return None


# ---------------------------------------------------------------------------
# DB -> Dispute reconstruction
# ---------------------------------------------------------------------------

def load_dispute_for_scoring(dispute_id: str, conn: sqlite3.Connection) -> Dispute:
    """Reconstruct a Dispute from DB rows as they stand RIGHT NOW.

    Assembles from three tables:
      disputes       -> dispute_id, reason_code, amount_paise, respond_by,
                        order_id, dispute_created_at (or ingested_at fallback)
      documents      -> doc_id, slot, quality
      extracted_facts -> grouped by document_id -> {fact_type: fact_value}

    Must be called fresh on every (re-)scoring pass -- documents can
    arrive after the initial ingest (PHASE_2_PLAN.md 4.6), so this is
    not a one-time cache.

    customer_name is resolved from the assembled documents via
    resolve_customer_name_anchor(), not stored on the disputes table --
    it's evidence-derived, not webhook-derived.

    Raises:
        KeyError  -- if dispute_id doesn't exist in the disputes table.
        sqlite3.Error-derived exceptions for DB failures.
    """
    dispute_row = conn.execute(
        "SELECT * FROM disputes WHERE dispute_id = ?", (dispute_id,)
    ).fetchone()
    if dispute_row is None:
        raise KeyError(f"No dispute found with dispute_id={dispute_id!r}")

    doc_rows = conn.execute(
        "SELECT * FROM documents WHERE dispute_id = ?", (dispute_id,)
    ).fetchall()

    fact_rows = conn.execute(
        "SELECT * FROM extracted_facts WHERE dispute_id = ?", (dispute_id,)
    ).fetchall()

    # Group extracted facts by document_id -> {fact_type: fact_value}.
    # fact_type values align with what scoring / contradiction_rules
    # read off Document.fields: "amount_paise", "event_date",
    # "customer_name", "order_id".
    facts_by_doc: dict[str, dict[str, Any]] = {}
    for f in fact_rows:
        facts_by_doc.setdefault(f["document_id"], {})[f["fact_type"]] = f["fact_value"]

    documents = [
        Document(
            doc_id=row["document_id"],
            slot=row["evidence_slot"],
            quality=row["quality"] or "unknown",
            fields=facts_by_doc.get(row["document_id"], {}),
        )
        for row in doc_rows
    ]

    # dispute_date: prefer dispute_created_at (from dispute.entity.created_at
    # in the webhook), fall back to ingested_at[:10] for disputes ingested
    # before the column was added.  Extract just the YYYY-MM-DD date portion
    # since check_date_order() compares against date objects, not datetimes.
    raw_date = (
        dispute_row["dispute_created_at"]
        or (dispute_row["ingested_at"][:10] if dispute_row["ingested_at"] else "")
    )
    dispute_date = raw_date[:10] if raw_date else ""

    # order_id: stored on the disputes table, populated at ingest time
    # from resolve_order_id().  None if the column doesn't exist yet
    # (pre-migration rows) or if the webhook lacked payment.entity.
    order_id: str | None = None
    row_keys = dispute_row.keys()
    if "order_id" in row_keys:
        order_id = dispute_row["order_id"]

    customer_name = resolve_customer_name_anchor(documents)

    return Dispute(
        dispute_id=dispute_row["dispute_id"],
        reason_code=dispute_row["reason_code"],
        amount_paise=dispute_row["amount_paise"],
        dispute_date=dispute_date,
        respond_by=dispute_row["respond_by"],
        order_id=order_id,
        customer_name=customer_name,
        documents=documents,
    )
