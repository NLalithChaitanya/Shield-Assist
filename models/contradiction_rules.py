"""
models/contradiction_rules.py

Rule-based contradiction detection across a dispute's documents.

Deliberately rule-based, not learned: for a hackathon "grounded, not
fabricated" story, a contradiction flag needs to be explainable in one
sentence to a judge ("invoice says ₹4,500, refund confirmation says
₹9,800 -- amounts don't match"), not a model's internal weight.

Each rule returns zero or more ContradictionFlag objects. Flags are
structured (not bare booleans) because this same object is reused by:
  - consistency scoring (models/scoring.py)
  - the `investigate()` LLM copilot function (Phase 2)
  - the UI's contradiction-warning panel (Phase 3)
Building the rich structure once here avoids retrofitting it later.

The four rule_type strings below intentionally match the injection
types in data/synthetic_generator.py -- this is what makes it possible
to measure the detector's own precision/recall against injected
ground truth (see models/train_and_evaluate.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

CURRENCY_MINOR_UNIT_TOLERANCE_PAISE = 100  # ~1 rupee rounding tolerance


@dataclass
class ContradictionFlag:
    rule_type: str  # "amount_mismatch" | "date_order_violation" | "name_mismatch" | "order_id_mismatch"
    severity: str  # "high" | "medium"
    documents_involved: list[str]
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_type": self.rule_type,
            "severity": self.severity,
            "documents_involved": self.documents_involved,
            "detail": self.detail,
        }


# A minimal structural protocol -- these rules only need doc_id, slot,
# and a `fields` dict (extracted facts), so they work equally against
# synthetic Document objects and real extracted-fact records in Phase 2.
class _DocumentLike:
    doc_id: str
    slot: str
    fields: dict[str, Any]


def check_amount_match(
    documents: Iterable[_DocumentLike],
    tolerance_paise: int = CURRENCY_MINOR_UNIT_TOLERANCE_PAISE,
    expected_amount_paise: int | None = None,
) -> list[ContradictionFlag]:
    """Flag documents whose amount disagrees beyond rounding tolerance.

    If expected_amount_paise is provided (the dispute's own amount),
    each document is also compared against it.  A document that shows
    ₹45,430 for a ₹38,500 dispute is flagged even when every other
    document agrees on the wrong amount.
    """
    amounts = [(d.doc_id, int(d.fields.get("amount_paise"))) for d in documents if d.fields.get("amount_paise") is not None]
    flags: list[ContradictionFlag] = []

    # --- Compare documents against each other (existing behaviour) ---
    if len(amounts) >= 2:
        base_doc_id, base_amount = amounts[0]
        for doc_id, amount in amounts[1:]:
            if amount is None or base_amount is None:
                continue
            if abs(amount - base_amount) > tolerance_paise:
                flags.append(
                    ContradictionFlag(
                        rule_type="amount_mismatch",
                        severity="high",
                        documents_involved=[base_doc_id, doc_id],
                        detail=(
                            f"Amount mismatch: {base_doc_id} shows "
                            f"₹{base_amount / 100:.2f}, {doc_id} shows "
                            f"₹{amount / 100:.2f}."
                        ),
                    )
                )

    # --- Compare each document against the dispute amount ---
    if expected_amount_paise is not None:
        for doc_id, amount in amounts:
            if amount is None:
                continue
            if abs(amount - expected_amount_paise) > tolerance_paise:
                flags.append(
                    ContradictionFlag(
                        rule_type="amount_mismatch",
                        severity="high",
                        documents_involved=[doc_id],
                        detail=(
                            f"{doc_id} shows ₹{amount / 100:.2f}, "
                            f"but the dispute amount is ₹{expected_amount_paise / 100:.2f}."
                        ),
                    )
                )

    return flags


def check_date_order(
    documents: Iterable[_DocumentLike],
    dispute_date_iso: str,
) -> list[ContradictionFlag]:
    """Flag any document whose event_date is on/after the dispute date.

    A service/delivery event cannot legitimately occur after the
    customer already disputed the charge.
    """
    if not dispute_date_iso:
        return []
    try:
        dispute_dt = date.fromisoformat(dispute_date_iso)
    except (ValueError, TypeError):
        return []
    flags: list[ContradictionFlag] = []
    for d in documents:
        event_date_raw = d.fields.get("event_date")
        if not event_date_raw:
            continue
        try:
            event_dt = date.fromisoformat(event_date_raw)
        except (ValueError, TypeError):
            continue
        try:
            if event_dt >= dispute_dt:
                flags.append(
                    ContradictionFlag(
                        rule_type="date_order_violation",
                        severity="high",
                        documents_involved=[d.doc_id],
                        detail=(
                            f"{d.doc_id} shows an event date of {event_date_raw}, "
                            f"which is on or after the dispute date {dispute_date_iso}."
                        ),
                    )
                )
        except TypeError:
            continue
    return flags


def check_name_match(
    documents: Iterable[_DocumentLike],
    expected_customer_name: str | None,
) -> list[ContradictionFlag]:
    """Flag documents whose customer name doesn't match the dispute's customer.

    expected_customer_name is None when no identity anchor exists yet
    (e.g. a freshly-ingested dispute with zero documents -- Razorpay's
    payment.dispute.created webhook carries no customer name field at
    all, verified against the real dispute/payment entity shapes).
    In that state this check does not run and returns no flags -- it
    is explicitly "not yet possible," not "passed."
    """
    if expected_customer_name is None:
        return []
    flags: list[ContradictionFlag] = []
    for d in documents:
        name = d.fields.get("customer_name")
        if name and name != expected_customer_name:
            flags.append(
                ContradictionFlag(
                    rule_type="name_mismatch",
                    severity="medium",
                    documents_involved=[d.doc_id],
                    detail=(
                        f"{d.doc_id} shows customer name '{name}', which does "
                        f"not match the dispute's customer '{expected_customer_name}'."
                    ),
                )
            )
    return flags


def check_order_id_match(
    documents: Iterable[_DocumentLike],
    expected_order_id: str | None,
) -> list[ContradictionFlag]:
    """Flag documents whose order/transaction ID doesn't match the dispute's order.

    expected_order_id is None when the order ID cannot be resolved from
    the webhook payload (e.g. payment.entity missing or lacking order_id).
    In that state this check does not run -- same reasoning as
    check_name_match: "not yet possible," not "passed."
    """
    if expected_order_id is None:
        return []
    flags: list[ContradictionFlag] = []
    for d in documents:
        order_id = d.fields.get("order_id")
        if order_id and order_id != expected_order_id:
            flags.append(
                ContradictionFlag(
                    rule_type="order_id_mismatch",
                    severity="medium",
                    documents_involved=[d.doc_id],
                    detail=(
                        f"{d.doc_id} references order ID '{order_id}', which "
                        f"does not match the dispute's order ID '{expected_order_id}'."
                    ),
                )
            )
    return flags


def run_all_contradiction_checks(
    documents: list[_DocumentLike],
    *,
    dispute_date_iso: str,
    expected_customer_name: str | None,
    expected_order_id: str | None,
    expected_amount_paise: int | None = None,
) -> list[ContradictionFlag]:
    """Run every contradiction rule and return the combined, deduplicated flag list.

    Both expected_customer_name and expected_order_id may be None when
    the corresponding anchor hasn't been resolved yet (no uploaded
    document with a name field, or order_id missing from the webhook).
    The respective checks return [] immediately in that state -- the
    caller's audit log should note which checks were skipped and why.
    """
    flags: list[ContradictionFlag] = []
    flags.extend(check_amount_match(documents, expected_amount_paise=expected_amount_paise))
    flags.extend(check_date_order(documents, dispute_date_iso))
    flags.extend(check_name_match(documents, expected_customer_name))
    flags.extend(check_order_id_match(documents, expected_order_id))
    return flags