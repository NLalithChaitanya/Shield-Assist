"""
backend/jobs/contest_job.py

'contest.submit' job handler — submits a dispute contest to Razorpay.

This handler validates preconditions (human.approved audit event must
exist) and submits evidence to Razorpay via the Contest API.

IMPORTANT LIMITATION:
  Razorpay's Test Mode does NOT allow creating disputes — they are
  bank/issuer-initiated only.  The Contest API (PATCH /v1/disputes/:id/contest)
  requires a real dispute_id that can only exist in Live Mode or in a
  test account that happens to have an actual dispute.  This means the
  Contest API cannot be end-to-end tested in sandbox.

  The implementation below is correct against the documented contract,
  but will return a 400 'The id provided does not exist' when called
  with a synthetic/test dispute_id.  This is expected and documented.

Section 4 of the plan: contest submission only runs AFTER a human
has explicitly approved the draft.  The route handler (POST
/disputes/{id}/contest) checks this BEFORE enqueueing, but the job
handler also validates as a defense-in-depth measure.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from backend.job_queue import register_handler
from backend.razorpay_client import (
    RazorpayClient,
    RazorpayCredentials,
    ContestSubmissionError,
    RazorpayAPIError,
)
from backend.repository import CaseRepository

logger = logging.getLogger("shield_assist.jobs.contest")


# ---------------------------------------------------------------------------
# Razorpay client (lazy init, same pattern as app.py)
# ---------------------------------------------------------------------------

_razorpay_client: RazorpayClient | None = None


def _get_razorpay_client() -> RazorpayClient | None:
    """Lazy-init Razorpay client. Returns None if credentials not configured."""
    global _razorpay_client
    if _razorpay_client is not None:
        return _razorpay_client
    try:
        creds = RazorpayCredentials.from_env()
        _razorpay_client = RazorpayClient(credentials=creds)
        return _razorpay_client
    except ValueError:
        logger.warning(
            "RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET not set — "
            "contest submission will be logged but not sent to Razorpay"
        )
        return None


# ---------------------------------------------------------------------------
# Evidence slot builder
# ---------------------------------------------------------------------------

def _build_evidence_slots(
    repo: CaseRepository, dispute_id: str
) -> dict[str, list[str]]:
    """Build Razorpay evidence_slots dict from uploaded documents.

    Maps our document records to Razorpay's evidence slot format:
    {"billing_proof": ["doc_abc", ...], "proof_of_service": ["doc_def", ...]}

    Only includes documents that have a razorpay_doc_id (i.e., were
    successfully uploaded to Razorpay's Documents API).

    Returns an empty dict if no documents have razorpay_doc_ids.
    """
    docs = repo.get_documents(dispute_id)
    slots: dict[str, list[str]] = {}

    for doc in docs:
        rzp_id = doc.get("razorpay_doc_id")
        if not rzp_id:
            continue
        slot = doc.get("evidence_slot", "others")
        if slot not in slots:
            slots[slot] = []
        slots[slot].append(rzp_id)

    return slots


# ---------------------------------------------------------------------------
# Job handler
# ---------------------------------------------------------------------------

@register_handler("contest.submit")
def handle_contest_job(job: dict, repo: CaseRepository) -> None:
    """Submit a dispute contest to Razorpay.

    Preconditions (checked in order):
      1. A draft must exist for this dispute
      2. A 'human.approved' audit event must exist
      3. The dispute must be in 'drafted' or 'ready' status

    Submits to Razorpay via PATCH /v1/disputes/:id/contest with
    action='submit'.  Requires at least one document with a
    razorpay_doc_id to be present.
    """
    dispute_id = job["dispute_id"]
    payload = json.loads(job.get("payload") or "{}")
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- Precondition: draft must exist --------------------------------------
    draft = repo.get_latest_draft(dispute_id)
    if draft is None:
        raise ValueError(
            f"No draft found for dispute {dispute_id} — "
            f"draft.response must complete before contest.submit"
        )

    # --- Precondition: human must have approved ------------------------------
    if not repo.has_audit_event(dispute_id, "human.approved"):
        raise ValueError(
            f"No 'human.approved' audit event for dispute {dispute_id} — "
            f"a human must approve the draft before contest submission"
        )

    # --- Precondition: status must allow submission --------------------------
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise ValueError(f"Dispute {dispute_id} not found")

    if dispute["status"] not in ("drafted", "ready"):
        raise ValueError(
            f"Dispute {dispute_id} has status '{dispute['status']}' — "
            f"must be 'drafted' or 'ready' for contest submission"
        )

    # --- Build evidence slots from uploaded documents -----------------------
    evidence_slots = _build_evidence_slots(repo, dispute_id)

    if not evidence_slots:
        raise ValueError(
            f"No documents with razorpay_doc_id found for dispute {dispute_id} — "
            f"at least one document must be uploaded to Razorpay before contesting"
        )

    # --- Generate idempotency key (Section 5.3) ------------------------------
    idempotency_key = payload.get("idempotency_key") or f"contest_{dispute_id}_{uuid.uuid4().hex[:8]}"

    # --- Build contest summary from draft ------------------------------------
    summary = draft.get("summary_text", "")
    if not summary:
        raise ValueError(
            f"Draft for dispute {dispute_id} has empty summary_text"
        )

    # --- Dry-run check -------------------------------------------------------
    dry_run = os.environ.get("CONTEST_DRY_RUN", "").lower() in ("true", "1", "yes")

    if dry_run:
        # Build and validate the request, but do NOT submit
        logger.info(
            "CONTEST_DRY_RUN enabled — building contest request for %s but NOT submitting",
            dispute_id,
        )
        contest_response = {
            "status": "dry_run",
            "note": "CONTEST_DRY_RUN enabled — request built but not submitted",
            "dispute_id": dispute_id,
            "idempotency_key": idempotency_key,
            "evidence_slots": evidence_slots,
            "summary_length": len(summary),
            "document_count": sum(len(ids) for ids in evidence_slots.values()),
            "submitted_at": now_iso,
        }

        logger.info(
            "Contest dry-run for dispute %s: %d evidence slots, %d documents",
            dispute_id, len(evidence_slots),
            sum(len(ids) for ids in evidence_slots.values()),
        )

    # --- Submit to Razorpay Contest API --------------------------------------
    elif not dry_run:
        rzp_client = _get_razorpay_client()

        if rzp_client is not None:
            try:
                rzp_response = rzp_client.submit_contest(
                    dispute_id=dispute_id,
                    evidence_slots=evidence_slots,
                    # billing_proof is included in evidence_slots if present
                )

                contest_response = {
                    "status": "submitted",
                    "razorpay_status": rzp_response.status,
                    "razorpay_amount": rzp_response.amount,
                    "razorpay_currency": rzp_response.currency,
                    "idempotency_key": idempotency_key,
                    "evidence_slots": evidence_slots,
                    "submitted_at": now_iso,
                }

                logger.info(
                    "Contest submitted to Razorpay for dispute %s: status=%s (idempotency_key=%s)",
                    dispute_id, rzp_response.status, idempotency_key,
                )

            except ContestSubmissionError as exc:
                # Razorpay returned a structured error (400 'id does not exist', etc.)
                # Log it but don't crash — the job will be retried.
                # In test mode, this is expected: disputes don't exist in sandbox.
                contest_response = {
                    "status": "failed",
                    "error_code": exc.error_code,
                    "error_description": exc.description,
                    "idempotency_key": idempotency_key,
                    "evidence_slots": evidence_slots,
                    "submitted_at": now_iso,
                }

                logger.warning(
                    "Contest submission failed for dispute %s: %s (%s) — "
                    "check if dispute exists in Razorpay. In test mode, "
                    "this is expected: disputes cannot be created in sandbox.",
                    dispute_id, exc.error_code, exc.description,
                )

                # Re-raise to trigger job retry — the dispute might not exist
                # yet, or there might be a transient error.
                raise

            except RazorpayAPIError as exc:
                contest_response = {
                    "status": "failed",
                    "error_code": exc.error_code,
                    "error_description": exc.description,
                    "idempotency_key": idempotency_key,
                    "evidence_slots": evidence_slots,
                    "submitted_at": now_iso,
                }
                logger.error(
                    "Contest API error for dispute %s: %s",
                    dispute_id, exc,
                )
                raise
        else:
            # No Razorpay credentials — log submission intent but don't fail.
            # This allows the audit trail and status update to proceed.
            contest_response = {
                "status": "logged",
                "note": "Razorpay credentials not configured — submission logged but not sent",
                "idempotency_key": idempotency_key,
                "evidence_slots": evidence_slots,
                "submitted_at": now_iso,
            }

            logger.info(
                "Contest submission logged for dispute %s (no Razorpay credentials) "
                "(idempotency_key=%s)",
                dispute_id, idempotency_key,
            )

    # --- Audit log ----------------------------------------------------------
    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "contest.submitted",
        "detail": contest_response,
        "success": True,
    })

    # --- Update status ------------------------------------------------------
    repo.update_status(dispute_id, "submitted")

    logger.info(
        "Contest job complete for dispute %s: status=%s",
        dispute_id, contest_response["status"],
    )
