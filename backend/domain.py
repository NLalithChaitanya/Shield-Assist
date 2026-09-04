"""
backend/domain.py

Phase 2's own Dispute/Document dataclasses -- structurally compatible
with what models.scoring.evaluate_dispute() and
models.contradiction_rules.run_all_contradiction_checks() actually
read (duck-typed access only: .reason_code, .documents, .dispute_date,
.customer_name, .order_id, .slot, .fields, .quality), but NOT the same
class as data/synthetic_generator.py's Dispute/Document.

Why a separate class instead of reusing Phase 1's:
  - Phase 1's Dispute.customer_name is a required `str`.  Real Razorpay
    webhooks never carry a customer name (verified against the sourced
    dispute/payment entity shapes -- see dispute_normalizer.py docstring),
    so Phase 2 needs Optional[str] there.  Changing Phase 1's dataclass
    in place would touch data/synthetic_generator.py, which
    models/train_and_evaluate.py's already-run training depends on --
    not something to risk from Phase 2 without re-running training.
  - Phase 2's Dispute is assembled from THREE DB tables (disputes,
    documents, extracted_facts), not deserialized from one JSON blob
    like the synthetic generator's.  Giving it its own construction
    path (load_dispute_for_scoring, in dispute_normalizer.py) keeps
    that assembly logic in one obvious place.

Usage:
    from backend.domain import Dispute, Document

    doc = Document(
        doc_id="doc_abc123",
        slot="proof_of_service",
        quality="clear",
        fields={"amount_paise": 38500, "event_date": "2026-08-20",
                "customer_name": "Rahul Sharma", "order_id": "ORD123456"},
    )
    dispute = Dispute(
        dispute_id="disp_xyz",
        reason_code="RZP01",
        amount_paise=3850000,
        dispute_date="2026-08-25",
        respond_by="2026-09-01T00:00:00+00:00",
        order_id="ORD123456",
        customer_name="Rahul Sharma",
        documents=[doc],
    )

    # Passes to evaluate_dispute() via duck typing -- no isinstance check:
    result = evaluate_dispute(dispute, win_probability=0.82)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    """A single evidence document attached to a dispute.

    Structurally identical to data.synthetic_generator.Document's
    interface (doc_id, slot, quality, fields) so both satisfy the
    _DocumentLike protocol in models.contradiction_rules without
    an explicit inheritance relationship.

    `fields` is a dict of extracted facts keyed by fact type
    ("amount_paise", "event_date", "customer_name", "order_id").
    Populated by the document intelligence layer (Phase 2) from
    extracted_facts rows, or directly from the synthetic generator
    in Phase 1 test scenarios.
    """

    doc_id: str
    slot: str  # evidence slot value (e.g. "proof_of_service", "billing_proof")
    quality: str  # "clear" | "degraded" | "unknown"
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class Dispute:
    """A dispute reconstructed from DB rows for scoring.

    This is the object evaluate_dispute() receives.  It carries exactly
    the attributes evaluate_dispute() and run_all_contradiction_checks()
    access via duck typing:

      dispute.reason_code           -> str
      dispute.documents             -> list[Document]  (with .slot, .quality, .fields)
      dispute.dispute_date          -> str  (ISO date, from dispute.entity.created_at)
      dispute.customer_name         -> str | None  (resolved from uploaded documents)
      dispute.order_id              -> str | None  (from payment.entity.order_id)
      dispute.amount_paise          -> int
      dispute.respond_by            -> str | None  (ISO datetime)
      dispute.dispute_id            -> str

    customer_name and order_id are Optional because:
      - Razorpay's webhook payload carries no customer name at all
        (verified against the sourced dispute/payment entity shapes).
      - order_id lives on payload.payment.entity, not dispute.entity,
        and could be missing if the payment entity is malformed.

    When either is None, the corresponding contradiction check
    (check_name_match / check_order_id_match) skips entirely --
    "not yet possible," not "passed."  See
    models/contradiction_rules.py for the implementation.
    """

    dispute_id: str
    reason_code: str
    amount_paise: int
    dispute_date: str  # ISO date (YYYY-MM-DD) from dispute.entity.created_at
    respond_by: str | None  # ISO datetime; None if webhook omitted it
    order_id: str | None  # from payment.entity.order_id; None if unresolvable
    customer_name: str | None  # resolved from uploaded documents; None if no anchor yet
    documents: list[Document] = field(default_factory=list)
