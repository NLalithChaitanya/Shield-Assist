"""
data/evidence_requirements.py

Single source of truth for reason-code -> required-evidence mappings.
VERIFIED against https://razorpay.com/docs/payments/disputes/submit-evidence/
(fetched 2026-08-27) -- every mapping below is sourced from Razorpay's
own "Suggested Documents" list per reason code, not inferred.

Every other Phase 1 module (synthetic generator, scoring, classifier
features) imports from here. Nothing downstream should hardcode a slot
name or a reason code's requirements -- if the mapping ever needs to
change, it changes here once.

NOTE on the two corrections made in Phase 0:
  - RZP01 / RZP06 / UPI1064 use PROOF_OF_SERVICE, not SHIPPING_PROOF.
    Razorpay's real language is "proof of service/product delivery" --
    a distinct field from shipping.
  - The T&C slot is TERM_AND_CONDITIONS (singular "term"), not
    "terms_and_conditions".

NOTE on RZP00: it's a real, documented catch-all reason code and is
kept in REASON_CODE_TABLE for completeness/future use, but is
deliberately excluded from IN_SCOPE_REASON_CODES -- PRODUCT_SPEC.md
scopes this build to exactly 6 reason codes, and RZP00 is flagged
there as a P2 "additional reason codes" candidate, not P0/P1 scope.
Promoting it later is a one-line change (see IN_SCOPE_REASON_CODES
below), not a schema change.
"""

from __future__ import annotations

from enum import Enum
from typing import NamedTuple


class EvidenceSlot(str, Enum):
    SHIPPING_PROOF = "shipping_proof"
    BILLING_PROOF = "billing_proof"
    CANCELLATION_PROOF = "cancellation_proof"
    CUSTOMER_COMMUNICATION = "customer_communication"
    PROOF_OF_SERVICE = "proof_of_service"
    EXPLANATION_LETTER = "explanation_letter"
    REFUND_CONFIRMATION = "refund_confirmation"
    ACCESS_ACTIVITY_LOG = "access_activity_log"
    REFUND_CANCELLATION_POLICY = "refund_cancellation_policy"
    TERM_AND_CONDITIONS = "term_and_conditions"  # singular "term" -- verified
    OTHERS = "others"


class ReasonCodeSpec(NamedTuple):
    code: str
    network: str  # "razorpay" | "upi"
    name: str
    required_evidence: tuple[EvidenceSlot, ...]
    source_note: str


# Full table, sourced field-by-field from Razorpay's "Suggested
# Documents" per reason code. Do not add/remove a slot here without
# re-checking the source doc -- this table is what the completeness
# score, the synthetic ground truth, and the classifier all key off of.
REASON_CODE_TABLE: dict[str, ReasonCodeSpec] = {
    "RZP01": ReasonCodeSpec(
        code="RZP01", network="razorpay",
        name="Goods/Services not Provided",
        required_evidence=(
            EvidenceSlot.PROOF_OF_SERVICE,
            EvidenceSlot.CUSTOMER_COMMUNICATION,
            EvidenceSlot.TERM_AND_CONDITIONS,
        ),
        source_note="Razorpay docs: proof of service/product delivery; "
                     "customer interaction; T&Cs showcasing refund & "
                     "fulfillment policies.",
    ),
    "RZP04": ReasonCodeSpec(
        code="RZP04", network="razorpay",
        name="Refund not Processed",
        required_evidence=(
            EvidenceSlot.REFUND_CONFIRMATION,
            EvidenceSlot.BILLING_PROOF,
            EvidenceSlot.CUSTOMER_COMMUNICATION,
            EvidenceSlot.REFUND_CANCELLATION_POLICY,
        ),
        source_note="Razorpay docs: proof of refund generation; bank "
                     "statement showing refund amount; customer comms "
                     "confirming refund; refund policies.",
    ),
    "RZP05": ReasonCodeSpec(
        code="RZP05", network="razorpay",
        name="Account Debited but No Confirmation",
        required_evidence=(
            EvidenceSlot.BILLING_PROOF,
            EvidenceSlot.ACCESS_ACTIVITY_LOG,
            EvidenceSlot.CUSTOMER_COMMUNICATION,
            EvidenceSlot.TERM_AND_CONDITIONS,
        ),
        source_note="Razorpay docs: service/product invoice (if captured); "
                     "internal logs proving no service given (if failed); "
                     "customer comms; T&Cs.",
    ),
    "RZP06": ReasonCodeSpec(
        code="RZP06", network="razorpay",
        name="Business Not Responding",
        required_evidence=(
            EvidenceSlot.PROOF_OF_SERVICE,
            EvidenceSlot.BILLING_PROOF,
            EvidenceSlot.CUSTOMER_COMMUNICATION,
        ),
        source_note="Razorpay docs: proof of service/goods delivery in "
                     "committed timeline; invoicing details; customer "
                     "comms via email.",
    ),
    "UPI1064": ReasonCodeSpec(
        code="UPI1064", network="upi",
        name="Goods/Services Not Received",
        required_evidence=(
            EvidenceSlot.PROOF_OF_SERVICE,
            EvidenceSlot.CUSTOMER_COMMUNICATION,
            EvidenceSlot.TERM_AND_CONDITIONS,
        ),
        source_note="Razorpay docs (UPI network): proof of service/product "
                     "delivery; customer interaction; T&Cs showcasing "
                     "refund & fulfillment policies.",
    ),
    "UPI1062": ReasonCodeSpec(
        code="UPI1062", network="upi",
        name="Goods/Services Not As Described",
        required_evidence=(
            EvidenceSlot.PROOF_OF_SERVICE,
            EvidenceSlot.CUSTOMER_COMMUNICATION,
            EvidenceSlot.REFUND_CANCELLATION_POLICY,
        ),
        source_note="Razorpay docs (UPI network): product description/"
                     "image screenshots + proof of delivery (both map to "
                     "proof_of_service); customer comms showing "
                     "dissatisfaction; return policies.",
    ),
    "RZP00": ReasonCodeSpec(
        code="RZP00", network="razorpay",
        name="Not Available (catch-all)",
        required_evidence=(
            EvidenceSlot.PROOF_OF_SERVICE,
            EvidenceSlot.BILLING_PROOF,
            EvidenceSlot.CUSTOMER_COMMUNICATION,
            EvidenceSlot.REFUND_CONFIRMATION,
        ),
        source_note="Razorpay docs: catch-all reason code not in "
                     "PRODUCT_SPEC.md's original 6 -- included for "
                     "completeness since it's a real, documented code. "
                     "Excluded from IN_SCOPE_REASON_CODES; P2 candidate.",
    ),
}

# The 6 codes PRODUCT_SPEC.md actually scopes this build to. RZP00 is
# deliberately NOT here -- see module docstring. To bring RZP00 into
# scope later (P2), add "RZP00" to this tuple; no other code changes.
IN_SCOPE_REASON_CODES: tuple[str, ...] = (
    "RZP01", "RZP04", "RZP05", "RZP06", "UPI1064", "UPI1062",
)


def get_required_evidence(reason_code: str) -> tuple[EvidenceSlot, ...]:
    """Original accessor name -- returns required slots for ANY code in
    REASON_CODE_TABLE, including RZP00. Kept for whatever Phase 0 code
    already calls this.
    """
    return REASON_CODE_TABLE[reason_code].required_evidence


def required_slots_for(reason_code: str) -> tuple[EvidenceSlot, ...]:
    """Return required evidence slots for an IN-SCOPE reason code only.

    Raises KeyError with a clear message for out-of-scope codes
    (including RZP00) rather than silently returning an empty tuple --
    an empty tuple would make completeness() score 100% for a case we
    were never meant to handle. This is the function
    data/synthetic_generator.py and models/scoring.py import.
    """
    if reason_code not in IN_SCOPE_REASON_CODES:
        raise KeyError(
            f"'{reason_code}' is not one of the {len(IN_SCOPE_REASON_CODES)} "
            f"in-scope reason codes: {IN_SCOPE_REASON_CODES}. "
            "(RZP00 is a real, documented code but is explicitly out of "
            "scope for this build -- see PRODUCT_SPEC.md and "
            "REASON_CODE_TABLE['RZP00'].source_note.)"
        )
    return REASON_CODE_TABLE[reason_code].required_evidence


def in_scope_reason_codes() -> list[str]:
    """Original accessor name -- kept for whatever Phase 0 code already
    calls this. Returns only the 6 in-scope codes (not RZP00), unlike
    the original version which returned all table keys -- see note
    below if that distinction matters to existing callers.
    """
    return list(IN_SCOPE_REASON_CODES)


def is_in_scope(reason_code: str) -> bool:
    return reason_code in IN_SCOPE_REASON_CODES


if __name__ == "__main__":
    print("All codes in REASON_CODE_TABLE (including out-of-scope RZP00):")
    for code, spec in REASON_CODE_TABLE.items():
        marker = "" if code in IN_SCOPE_REASON_CODES else "  [OUT OF SCOPE]"
        print(f"  {code:10s} {spec.name:40s} -> "
              f"{[e.value for e in spec.required_evidence]}{marker}")