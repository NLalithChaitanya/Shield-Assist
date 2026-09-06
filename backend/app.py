"""
backend/app.py

Phase 2 FastAPI application — thin route dispatchers that enqueue
jobs and return immediately.  The actual pipeline work (scoring,
drafting) runs in worker threads via the job queue.

Architecture (v2):
  Routes are HTTP-layer concerns only: signature verification,
  idempotency checks, input validation, and job enqueueing.  They
  return 200/202 in milliseconds with no blocking work.

  The job queue (backend/job_queue.py) handles the real pipeline:
  score.case -> draft.response (for prepare-gated cases),
  document.process -> score.case (on upload).

Run:
    cd "Shield Assist Razorpay"
    uvicorn backend.app:app --reload --port 8000

Smoke test:
    python scripts/smoke_test_backend.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from pydantic import BaseModel

from sse_starlette.sse import ServerSentEvent
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import StreamingResponse  # kept for SSE fallback

from backend.db import init_db
from backend.dispute_normalizer import (
    NormalizationError,
    OutOfScopeReasonCode,
    normalize_evidence_slot,
    normalize_webhook_to_dispute,
    resolve_order_id,
)
from backend.job_queue import JobQueue
from backend.razorpay_client import (
    RazorpayClient,
    RazorpayCredentials,
    DocumentUploadError,
)
from backend.razorpay_config import RazorpayConfig
from backend.quality_detection import assess_document_quality
from backend.repository import SQLiteRepository

# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shield_assist.app")

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "data" / "uploads"

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "application/pdf"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB, per Razorpay Documents API

# --- Razorpay configuration (typed, validated) ---
_razorpay_config: RazorpayConfig | None = None


def _get_razorpay_config() -> RazorpayConfig | None:
    """Lazy-init Razorpay config. Returns None if credentials not configured."""
    global _razorpay_config
    if _razorpay_config is not None:
        return _razorpay_config
    _razorpay_config = RazorpayConfig.optional_from_env()
    return _razorpay_config


# --- Razorpay API client (lazy init — skips if credentials not set) ---
_razorpay_client: RazorpayClient | None = None


def _get_razorpay_client() -> RazorpayClient | None:
    """Lazy-init Razorpay client. Returns None if credentials not configured."""
    global _razorpay_client
    if _razorpay_client is not None:
        return _razorpay_client
    config = _get_razorpay_config()
    if config is None:
        return None
    try:
        creds = RazorpayCredentials(key_id=config.key_id, key_secret=config.key_secret)
        _razorpay_client = RazorpayClient(credentials=creds, base_url=config.base_url)
        logger.info(
            "Razorpay API client initialized (mode=%s)",
            "test" if config.test_mode else "LIVE",
        )
        return _razorpay_client
    except Exception as exc:
        logger.warning("Razorpay client init failed: %s", exc)
        return None

# ---------------------------------------------------------------------------
# Repository + job queue (shared across the application)
# ---------------------------------------------------------------------------

repo = SQLiteRepository()


def _on_job_complete(job: dict) -> None:
    """Broadcast SSE event when a job completes successfully.

    Called by the worker thread after mark_job_done.  Maps job_type
    to the appropriate SSE event type so the frontend knows what
    changed.
    """
    job_type = job.get("job_type", "unknown")
    dispute_id = job.get("dispute_id", "")

    event_map = {
        "score.case": "scores.computed",
        "document.process": "document.extracted",
        "draft.response": "draft.ready",
        "contest.submit": "contest.submitted",
    }
    event_type = event_map.get(job_type, f"job.{job_type}.done")

    logger.info(
        "Job complete: %s dispute=%s -> broadcasting SSE '%s' (%d connections)",
        job_type, dispute_id, event_type, len(_sse_connections),
    )
    _broadcast_sse(event_type, {
        "dispute_id": dispute_id,
        "job_type": job_type,
        "job_id": job.get("job_id"),
    })


job_queue = JobQueue(repo, num_workers=2, poll_interval=1.0, on_complete=_on_job_complete)

# ---------------------------------------------------------------------------
# SSE event bus (simple in-process broadcast)
# ---------------------------------------------------------------------------

_sse_connections: list[Any] = []


def _broadcast_sse(event_type: str, data: dict) -> None:
    """Send an SSE event to all connected clients."""
    event = ServerSentEvent(event=event_type, data=json.dumps(data, default=str))
    stale = []
    for q in _sse_connections:
        try:
            q.put_nowait(event)
        except Exception:
            stale.append(q)
    for q in stale:
        _sse_connections.remove(q)


# ---------------------------------------------------------------------------
# Lifespan (startup + shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage startup and shutdown lifecycle."""
    # --- Startup ---
    init_db()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Import job handlers to trigger registration
    import backend.jobs  # noqa: F401

    job_queue.start()
    logger.info("Shield Assist started — workers running")

    yield

    # --- Shutdown ---
    job_queue.stop()
    logger.info("Shield Assist stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Shield Assist",
    description="AI Dispute Resolution Copilot for Razorpay Merchants",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)# ---------------------------------------------------------------------------
# Webhook signature verification (reused from Phase 0)
# ---------------------------------------------------------------------------
def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """HMAC-SHA256 over the RAW body, per Razorpay's docs."""
    config = _get_razorpay_config()
    webhook_secret = config.webhook_secret if config else ""
    if not webhook_secret:
        logger.warning(
            "RAZORPAY_WEBHOOK_SECRET not set — skipping signature "
            "verification. OK for local smoke testing only."
        )
        return True
    if not signature:
        return False
    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Document storage helper
# ---------------------------------------------------------------------------

_ALLOWED_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "application/pdf": ".pdf",
}


async def _store_upload(
    file: UploadFile, dispute_id: str, document_id: str
) -> tuple[str, int, str, str]:
    """Write an uploaded file to local disk.

    Returns (local_path, size_bytes, mime_type, content_hash).
    content_hash is the SHA-256 hex digest of the file bytes, used
    to skip redundant Gemini OCR when the same file is uploaded again.
    """
    import hashlib

    mime = file.content_type or "application/octet-stream"
    if mime not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{mime}'. Allowed: {sorted(ALLOWED_MIME_TYPES)}",
        )

    ext = _ALLOWED_EXTENSIONS.get(mime, ".bin")
    dest_dir = UPLOAD_DIR / dispute_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{document_id}{ext}"

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(contents)} bytes). Maximum: {MAX_UPLOAD_BYTES} bytes.",
        )

    content_hash = hashlib.sha256(contents).hexdigest()
    dest_path.write_bytes(contents)
    return str(dest_path), len(contents), mime, content_hash


# ===================================================================
# POST /api/webhooks/razorpay  —  webhook receiver (replaces /ingest)
# ===================================================================

@app.post("/api/webhooks/razorpay")
async def webhook_razorpay(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
):
    """Receive a Razorpay `payment.dispute.created` webhook.

    This is a thin dispatcher:
      1. Verify HMAC signature
      2. Parse + normalize the payload
      3. Check idempotency (dispute_id already exists?)
      4. Save dispute row
      5. Enqueue score.case job
      6. Return 200 immediately (no blocking work)

    The actual scoring pipeline runs in a worker thread via the job queue.
    """
    raw_body = await request.body()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # --- Generate event ID for idempotency tracking ---
    payload_hash = hashlib.sha256(raw_body).hexdigest()[:16]
    event_id = f"evt_{payload_hash}_{int(datetime.now(timezone.utc).timestamp())}"

    # --- Signature verification ---
    signature_valid = _verify_signature(raw_body, x_razorpay_signature)
    if not signature_valid:
        logger.error("Webhook signature verification failed — rejecting.")
        # Record the failed attempt
        repo.save_webhook_event({
            "event_id": event_id,
            "event_type": "unknown",
            "received_at": now_iso,
            "signature_verified": False,
            "processing_status": "rejected_signature",
        })
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    # --- Parse JSON ---
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.exception("Received non-JSON webhook body")
        repo.save_webhook_event({
            "event_id": event_id,
            "event_type": "unknown",
            "received_at": now_iso,
            "signature_verified": True,
            "processing_status": "rejected_malformed",
        })
        raise HTTPException(status_code=400, detail="malformed JSON body")

    event = payload.get("event", "unknown")
    logger.info("Webhook received: event=%s", event)

    # --- Track webhook event for idempotency ---
    # Check if we already processed this event (by payload hash)
    existing_event = repo.get_webhook_event(event_id)
    if existing_event and existing_event["processing_status"] == "processed":
        logger.info("Webhook event %s already processed — skipping", event_id)
        return {"status": "already_processed", "event_id": event_id}

    repo.save_webhook_event({
        "event_id": event_id,
        "event_type": event,
        "received_at": now_iso,
        "signature_verified": True,
        "processing_status": "processing",
    })

    # --- Normalize ---
    try:
        row = normalize_webhook_to_dispute(payload)
    except NormalizationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": str(exc), "field_errors": exc.field_errors},
        )
    except OutOfScopeReasonCode as exc:
        # Store as out_of_scope, return 200 (not 400)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        network = "upi" if exc.reason_code.startswith("UPI") else "razorpay"
        entity = (
            payload.get("payload", {}).get("dispute", {}).get("entity") or payload
        )
        dispute_id_oos = (
            entity.get("id") or entity.get("dispute_id")
            or f"dsp_{uuid.uuid4().hex[:10]}"
        )

        repo.save_dispute({
            "dispute_id": dispute_id_oos,
            "payment_id": None,
            "reason_code": exc.reason_code,
            "network": network,
            "amount_paise": 0,
            "currency": "INR",
            "status": "out_of_scope",
            "source": "webhook",
            "raw_webhook_payload": raw_body.decode("utf-8", errors="replace"),
            "ingested_at": now_iso,
            "updated_at": now_iso,
        })

        repo.write_audit({
            "dispute_id": dispute_id_oos,
            "stage": "ingest",
            "detail": {"reason_code": exc.reason_code, "status": "out_of_scope"},
            "success": True,
        })

        return {
            "dispute_id": dispute_id_oos,
            "status": "out_of_scope",
            "reason_code": exc.reason_code,
            "message": f"Reason code {exc.reason_code} is not in the current 6-code scope.",
        }

    # --- Atomic idempotent insert + enqueue (5.3) ---
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    order_id = resolve_order_id(payload)

    was_present, job_id = repo.idempotent_insert_dispute(
        row={
            "dispute_id": row.dispute_id,
            "payment_id": row.payment_id,
            "reason_code": row.reason_code,
            "network": row.network,
            "amount_paise": row.amount_paise,
            "currency": row.currency,
            "respond_by": row.respond_by,
            "dispute_created_at": row.created_at,
            "order_id": order_id,
            "status": "queued",
            "source": "webhook",
            "raw_webhook_payload": raw_body.decode("utf-8", errors="replace"),
            "ingested_at": now_iso,
            "updated_at": now_iso,
        },
        job_type="score.case",
        job_payload={},
    )

    if was_present:
        repo.update_webhook_event_status(event_id, "duplicate")
        repo.write_audit({
            "dispute_id": row.dispute_id,
            "stage": "ingest",
            "detail": {"idempotent": True},
            "success": True,
        })
        return {
            "dispute_id": row.dispute_id,
            "status": "already_ingested",
            "message": "Duplicate webhook delivery -- skipping.",
        }

    # --- Audit: ingest ---
    repo.write_audit({
        "dispute_id": row.dispute_id,
        "stage": "ingest",
        "detail": {
            "reason_code": row.reason_code,
            "amount_paise": row.amount_paise,
            "network": row.network,
        },
        "success": True,
    })

    repo.write_audit({
        "dispute_id": row.dispute_id,
        "stage": "queue",
        "detail": {"job_id": job_id, "job_type": "score.case"},
        "success": True,
    })

    repo.update_webhook_event_status(event_id, "processed", dispute_id=row.dispute_id)

    # --- Broadcast SSE ---
    _broadcast_sse("dispute.received", {
        "dispute_id": row.dispute_id,
        "reason_code": row.reason_code,
        "amount_paise": row.amount_paise,
    })

    logger.info(
        "Webhook processed: dispute=%s job=%s (returning 200 immediately)",
        row.dispute_id, job_id,
    )

    return {
        "dispute_id": row.dispute_id,
        "status": "queued",
        "job_id": job_id,
        "message": "Dispute received and queued for scoring.",
    }


# ===================================================================
# POST /disputes/{dispute_id}/documents  —  upload evidence
# ===================================================================

@app.post("/disputes/{dispute_id}/documents")
async def upload_document(
    dispute_id: str,
    file: UploadFile = File(...),
    evidence_slot: str = Form(...),
    quality: str = Form("unknown"),
    facts: str = Form("{}"),
):
    """Upload an evidence document and enqueue processing.

    Returns 202 (accepted) — the actual processing happens in a
    worker thread via document.process -> score.case job chain
    (Section 5.6).
    """
    # --- Validate dispute exists ---
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    # --- Parse facts JSON ---
    try:
        facts_dict = json.loads(facts) if facts else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'facts' field.")

    # --- Generate document ID and store file ---
    document_id = f"doc_{uuid.uuid4().hex[:12]}"
    local_path, size_bytes, mime_type, content_hash = await _store_upload(file, dispute_id, document_id)

    # --- Normalize evidence slot ---
    canonical_slot = normalize_evidence_slot(evidence_slot)

    # --- Compute document quality from actual file content ---
    # Laplacian variance: low variance = blurry/low-quality scan.
    # The form field 'quality' is kept as an optional merchant override:
    # if the merchant explicitly sets a quality value, it takes precedence
    # over the computed signal (they may know their scan is fine despite
    # low variance, e.g. a clean white document with sparse text).
    computed_quality, variance = assess_document_quality(local_path, mime_type)
    if quality in ("clear", "degraded"):
        # Merchant explicitly set quality — use their value (override)
        effective_quality = quality
        logger.info(
            "Document quality: merchant override '%s' (computed='%s', variance=%.1f)",
            quality, computed_quality, variance,
        )
    else:
        # Form field is 'unknown' or empty — use computed value
        effective_quality = computed_quality
        logger.info(
            "Document quality: computed='%s' (variance=%.1f, file=%s)",
            effective_quality, variance, Path(local_path).name,
        )

    # --- Upload to Razorpay Documents API (best-effort, non-blocking) ---
    razorpay_doc_id = None
    rzp_client = _get_razorpay_client()
    if rzp_client is not None:
        try:
            rzp_result = rzp_client.upload_document(local_path, purpose="dispute_evidence")
            razorpay_doc_id = rzp_result.razorpay_doc_id
            logger.info(
                "Razorpay document uploaded: local=%s razorpay=%s",
                document_id, razorpay_doc_id,
            )
        except (DocumentUploadError, FileNotFoundError) as exc:
            # Non-fatal: local file is saved, Razorpay upload can be retried later.
            # DocumentUploadError from Razorpay: file too large, wrong type, etc.
            # FileNotFoundError: local file path issue (shouldn't happen here)
            logger.warning(
                "Razorpay Documents API upload failed for %s: %s — "
                "file saved locally, razorpay_doc_id will be NULL",
                document_id, exc,
            )
        except Exception as exc:
            # Catch-all for network errors, timeouts, etc.
            logger.warning(
                "Razorpay Documents API unexpected error for %s: %s — "
                "file saved locally, razorpay_doc_id will be NULL",
                document_id, exc,
            )

    # --- Save document row ---
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    repo.save_document({
        "document_id": document_id,
        "dispute_id": dispute_id,
        "evidence_slot": canonical_slot,
        "local_path": local_path,
        "content_hash": content_hash,
        "mime_type": mime_type,
        "size_bytes": size_bytes,
        "quality": effective_quality,
        "uploaded_at": now_iso,
    })

    # --- Extraction cache check ---
    # If an identical file (same SHA-256) was already extracted for this
    # dispute, copy the cached facts instead of re-calling Gemini OCR.
    ocr_skipped = False
    cached = repo.find_cached_extraction(content_hash, dispute_id)
    if cached:
        repo.copy_facts(cached["document_id"], document_id, dispute_id)
        ocr_skipped = True
        job_id = None
    else:
        # --- Enqueue document.process job (Gemini OCR) ---
        job_id = repo.enqueue_job("document.process", dispute_id, {
            "document_id": document_id,
            "facts": facts_dict,
        })

    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "upload",
        "detail": {
            "document_id": document_id,
            "evidence_slot": canonical_slot,
            "original_slot": evidence_slot,
            "quality": effective_quality,
            "fact_count": len(facts_dict),
            "content_hash": content_hash,
            "ocr_skipped": ocr_skipped,
            "cached_from": cached["document_id"] if cached else None,
        },
        "success": True,
    })

    # --- Broadcast SSE ---
    _broadcast_sse("document.uploaded", {
        "dispute_id": dispute_id,
        "document_id": document_id,
        "evidence_slot": canonical_slot,
        "ocr_skipped": ocr_skipped,
        "razorpay_doc_id": razorpay_doc_id,
    })

    return {
        "dispute_id": dispute_id,
        "document_id": document_id,
        "evidence_slot": canonical_slot,
        "razorpay_doc_id": razorpay_doc_id,
        "job_id": job_id,
        "status": "processing",
        "ocr_skipped": ocr_skipped,
        "cached_from": cached["document_id"] if cached else None,
        "message": "Document uploaded and queued for processing." if not ocr_skipped else "Document uploaded; facts reused from cached extraction.",
    }


# ===================================================================
# POST /disputes/{dispute_id}/approve  —  human approval
# ===================================================================

@app.post("/disputes/{dispute_id}/approve")
def approve_draft(dispute_id: str):
    """Human approves a drafted response.

    Preconditions:
      1. Dispute exists
      2. A draft exists for this dispute
      3. No prior human.approved event (idempotent — re-approve is a no-op)

    Side effects:
      - Writes 'human.approved' audit event
      - Updates dispute status to 'ready'

    This MUST happen before POST /contest (Section 5.5).
    """
    # --- Validate dispute exists ---
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    # --- Validate draft exists ---
    draft = repo.get_latest_draft(dispute_id)
    if draft is None:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "No draft exists for this dispute",
                "dispute_id": dispute_id,
                "hint": "The copilot must draft a response before approval.",
            },
        )

    # --- Idempotent: skip if already approved ---
    if repo.has_audit_event(dispute_id, "human.approved"):
        return {
            "dispute_id": dispute_id,
            "status": "already_approved",
            "message": "This dispute was already approved.",
        }

    # --- Write audit event + update status ---
    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "human.approved",
        "detail": {
            "draft_id": draft.get("draft_id"),
            "citations_count": len(draft.get("citations", [])),
        },
        "success": True,
    })

    repo.update_status(dispute_id, "ready")

    _broadcast_sse("human.approved", {
        "dispute_id": dispute_id,
    })

    logger.info("Human approved draft for dispute %s", dispute_id)

    return {
        "dispute_id": dispute_id,
        "status": "approved",
        "message": "Draft approved. Ready for Razorpay submission.",
    }


# ===================================================================
# PUT /disputes/{dispute_id}/draft  —  edit draft response
# ===================================================================

class UpdateDraftRequest(BaseModel):
    summary_text: str


@app.put("/disputes/{dispute_id}/draft")
@app.post("/disputes/{dispute_id}/draft")
def update_draft(dispute_id: str, payload: UpdateDraftRequest):
    """Edit the draft response text for a dispute."""
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    summary_text = payload.summary_text.strip()
    if not summary_text:
        raise HTTPException(
            status_code=400,
            detail="Draft summary text cannot be empty.",
        )

    repo.update_draft_summary(dispute_id, summary_text)

    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "draft.edited",
        "detail": {"length": len(summary_text)},
        "success": True,
    })

    _broadcast_sse("draft.updated", {
        "dispute_id": dispute_id,
    })

    logger.info("Updated draft response for dispute %s", dispute_id)

    latest_draft = repo.get_latest_draft(dispute_id)
    return {
        "dispute_id": dispute_id,
        "status": "updated",
        "draft": latest_draft,
    }


# ===================================================================
# POST /disputes/{dispute_id}/draft/regenerate  —  regenerate draft
# ===================================================================

@app.post("/disputes/{dispute_id}/draft/regenerate")
def regenerate_draft(dispute_id: str):
    """Regenerate a fresh dispute response draft via the copilot."""
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    case_data = _build_case_data(dispute_id)

    from backend.jobs.draft_job import _call_copilot_draft
    draft_output = _call_copilot_draft(case_data)
    citations = draft_output.get("citations", [])

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    repo.insert_draft({
        "dispute_id": dispute_id,
        "summary_text": draft_output["summary_text"],
        "citations": citations,
        "approved": False,
        "generated_at": now_iso,
    })

    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "draft.regenerated",
        "detail": {
            "citation_count": len(citations),
        },
        "success": True,
    })

    _broadcast_sse("draft.updated", {
        "dispute_id": dispute_id,
    })

    logger.info("Regenerated draft response for dispute %s", dispute_id)

    latest_draft = repo.get_latest_draft(dispute_id)
    return {
        "dispute_id": dispute_id,
        "status": "regenerated",
        "draft": latest_draft,
    }


# ===================================================================
# POST /disputes/{dispute_id}/contest  —  submit contest
# ===================================================================

@app.post("/disputes/{dispute_id}/contest")
def submit_contest(dispute_id: str):
    """Submit a dispute contest to Razorpay.

    Requires a 'human.approved' audit event to exist first (Section 5.5).
    Returns 202 (accepted) — the actual submission happens in a worker
    thread via the contest.submit job.
    """
    # --- Validate dispute exists ---
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    # --- Check human.approved precondition ---
    if not repo.has_audit_event(dispute_id, "human.approved"):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Contest requires human approval first",
                "dispute_id": dispute_id,
                "required_event": "human.approved",
            },
        )

    # --- Enqueue contest.submit job ---
    job_id = repo.enqueue_job("contest.submit", dispute_id, {})

    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "contest.queued",
        "detail": {"job_id": job_id},
        "success": True,
    })

    return {
        "dispute_id": dispute_id,
        "job_id": job_id,
        "status": "queued",
        "message": "Contest submission queued.",
    }


# ===================================================================
# GET /disputes/{dispute_id}/copilot/{ability}  --  streaming SSE
# ===================================================================

@app.get("/disputes/{dispute_id}/copilot/explain")
def copilot_explain(dispute_id: str, request: Request):
    """Stream a plain-language explanation via SSE."""
    return _stream_copilot(dispute_id, "explain", request)


@app.get("/disputes/{dispute_id}/copilot/investigate")
def copilot_investigate(dispute_id: str, request: Request):
    """Stream investigation steps via SSE."""
    return _stream_copilot(dispute_id, "investigate", request)


@app.get("/disputes/{dispute_id}/copilot/recommend")
def copilot_recommend(dispute_id: str, request: Request):
    """Stream a strategy recommendation via SSE."""
    return _stream_copilot(dispute_id, "recommend", request)


@app.get("/disputes/{dispute_id}/copilot/draft")
def copilot_draft(dispute_id: str, request: Request):
    """Stream a dispute response draft via SSE."""
    return _stream_copilot(dispute_id, "draft", request)


def _build_case_data(dispute_id: str) -> dict:
    """Build the structured case_data dict for copilot functions."""
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    scores = repo.get_scores(dispute_id)
    decision = repo.get_latest_decision(dispute_id)
    facts = repo.get_facts(dispute_id)
    docs = repo.get_documents(dispute_id)

    # Map document_id -> evidence slot name for the copilot to reference
    doc_slots = {d["document_id"]: d["evidence_slot"] for d in docs}

    return {
        "dispute_id": dispute_id,
        "reason_code": dispute["reason_code"],
        "amount_paise": dispute["amount_paise"],
        "network": dispute["network"],
        "scores": {
            "win_probability": scores["win_probability"] if scores else 0,
            "completeness": scores["completeness"] if scores else 0,
            "quality": scores["quality"] if scores else 0,
            "consistency": scores["consistency"] if scores else 0,
            "contradiction_flags": scores.get("contradiction_flags", []) if scores else [],
            "missing_required_slots": scores.get("missing_required_slots", []) if scores else [],
        },
        "gate": {
            "action": decision["action"] if decision else "unknown",
            "passed": decision["passed"] if decision else False,
            "failing_conditions": decision.get("failing_conditions", []) if decision else [],
        },
        "document_slots": doc_slots,
        "facts": [
            {
                "fact_id": f["fact_id"],
                "document_id": f["document_id"],
                "fact_type": f["fact_type"],
                "fact_value": f["fact_value"],
            }
            for f in facts
        ],
    }


_STREAM_FUNCTIONS = {
    "explain": "explain_stream",
    "investigate": "investigate_stream",
    "recommend": "recommend_stream",
    "draft": "draft_stream",
}


def _stream_copilot(dispute_id: str, ability: str, request: Request | None = None):
    """Stream copilot response as SSE events, with state-hash caching.

    Before calling the LLM, checks copilot_cache for a response that
    matches the current case state (scores, facts, gate conditions) AND
    the merchant's question/history (if any).
    Cache hit: replays cached text as SSE token chunks (no LLM call).
    Cache miss: streams from the LLM, accumulates full text, stores in cache.

    Optional query params (chat mode -- used by the frontend CopilotPanel):
      q=...        -- the merchant's literal question (URL-encoded)
      history=...  -- JSON array of {"role": "user"|"assistant", "text": ...}
                      of recent turns, so answers can reference the
                      conversation. Without these, the ability answers
                      its fixed prompt (legacy behavior preserved).

    SSE events sent:
      - copilot.token:  {text: "..."}  -- each text chunk
      - copilot.done:   {ability, dispute_id}  -- stream complete
      - copilot.error:  {error, dispute_id}  -- on failure
    """
    import asyncio
    from backend.copilot import (
        explain_stream, investigate_stream, recommend_stream, draft_stream,
        compute_state_hash, _GEMINI_UNAVAILABLE_FALLBACK,
    )

    stream_fn_name = _STREAM_FUNCTIONS.get(ability)
    if not stream_fn_name:
        raise HTTPException(status_code=400, detail=f"Unknown ability: {ability}")

    case_data = _build_case_data(dispute_id)

    # --- Chat context: the merchant's question + recent conversation ---
    question = ""
    history: list[dict] = []
    if request is not None:
        question = (request.query_params.get("q") or "").strip()[:500]
        raw_history = request.query_params.get("history") or ""
        if raw_history:
            try:
                parsed = json.loads(raw_history)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                for m in parsed[-6:]:
                    if not isinstance(m, dict):
                        continue
                    role = str(m.get("role") or "")
                    text = str(m.get("text") or "")[:500]
                    if role in ("user", "assistant") and text:
                        history.append({"role": role, "text": text})
    case_data["question"] = question
    case_data["history"] = history
    state_hash = compute_state_hash(case_data)

    # --- Cache lookup ---
    cached = repo.get_copilot_cache(dispute_id, ability, state_hash)
    if cached is not None:
        logger.info(
            "copilot.%s cache HIT dispute=%s hash=%s (no Gemini call)",
            ability, dispute_id, state_hash,
        )
        response_text = cached["response_text"]

        async def event_generator_cached():
            # Replay cached text as SSE token chunks (simulates streaming)
            chunk_size = 80  # characters per chunk
            for i in range(0, len(response_text), chunk_size):
                chunk = response_text[i:i + chunk_size]
                yield f"event: copilot.token\ndata: {json.dumps({'text': chunk})}\n\n"

            repo.write_audit({
                "dispute_id": dispute_id,
                "stage": f"copilot.{ability}",
                "detail": {"ability": ability, "cached": True, "state_hash": state_hash},
                "success": True,
            })

            done_data = json.dumps({
                "ability": ability,
                "dispute_id": dispute_id,
            })
            yield f"event: copilot.done\ndata: {done_data}\n\n"

        return StreamingResponse(
            event_generator_cached(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # --- Cache miss: call Gemini and store result ---
    logger.info(
        "copilot.%s cache MISS dispute=%s hash=%s (calling Gemini)",
        ability, dispute_id, state_hash,
    )

    stream_fns = {
        "explain_stream": explain_stream,
        "investigate_stream": investigate_stream,
        "recommend_stream": recommend_stream,
        "draft_stream": draft_stream,
    }
    stream_fn = stream_fns[stream_fn_name]

    async def event_generator():
        full_text = []  # accumulate for cache storage
        try:
            loop = asyncio.get_event_loop()
            gen = stream_fn(case_data)

            while True:
                chunk = await loop.run_in_executor(None, next, gen, None)
                if chunk is None:
                    break
                full_text.append(chunk)
                yield f"event: copilot.token\ndata: {json.dumps({'text': chunk})}\n\n"

            # Store complete response in cache -- EXCEPT the honest
            # "both AI providers unavailable" fallback, which must never
            # be cached as a real answer (otherwise a stale unavailable
            # reply would replay after the provider recovers).
            complete_text = "".join(full_text)
            complete_text_stripped = complete_text.strip()
            is_provider_fallback = (
                complete_text_stripped == _GEMINI_UNAVAILABLE_FALLBACK
            )
            if complete_text_stripped and not is_provider_fallback:
                repo.set_copilot_cache(
                    dispute_id, ability, state_hash, complete_text
                )
                logger.info(
                    "copilot.%s cached dispute=%s hash=%s (%d chars)",
                    ability, dispute_id, state_hash, len(complete_text),
                )

            # Stream complete
            audit_detail = {
                "ability": ability,
                "streamed": True,
                "cached": False,
                "state_hash": state_hash,
            }
            audit_success = True
            if is_provider_fallback:
                audit_detail["provider_unavailable"] = True
                audit_success = False
            repo.write_audit({
                "dispute_id": dispute_id,
                "stage": f"copilot.{ability}",
                "detail": audit_detail,
                "success": audit_success,
                **({
                    "error_detail": (
                        "Both AI providers (Gemini, Groq) unavailable -- "
                        "honest fallback message returned, no fabrication"
                    )
                } if is_provider_fallback else {}),
            })

            done_data = json.dumps({
                "ability": ability,
                "dispute_id": dispute_id,
            })
            yield f"event: copilot.done\ndata: {done_data}\n\n"

        except Exception as exc:
            logger.error("copilot.%s stream failed: %s", ability, exc)

            from backend.resilience import classify_gemini_error
            error_info = classify_gemini_error(exc)

            repo.write_audit({
                "dispute_id": dispute_id,
                "stage": f"copilot.{ability}",
                "detail": {
                    "ability": ability,
                    "streamed": True,
                    "error_type": error_info["error_type"],
                    "recoverable": error_info["recoverable"],
                },
                "success": False,
                "error_detail": error_info["detail"],
            })

            error_data = json.dumps({
                "error": error_info["detail"],
                "error_type": error_info["error_type"],
                "recoverable": error_info["recoverable"],
                "dispute_id": dispute_id,
            })
            yield f"event: copilot.error\ndata: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ===================================================================
# GET /disputes  —  priority-sorted case queue
# ===================================================================

@app.get("/disputes")
def list_disputes(limit: int = 50):
    """Return all disputes sorted by priority (highest first).

    Empty state returns [] cleanly (5.8).
    """
    rows = repo.list_by_priority(limit=limit)

    results = []
    for r in rows:
        past_deadline = _is_past_deadline(r.get("respond_by"))
        results.append({
            "dispute_id": r["dispute_id"],
            "reason_code": r["reason_code"],
            "amount_paise": r["amount_paise"],
            "currency": r["currency"],
            "status": r["status"],
            "respond_by": r["respond_by"],
            "past_deadline": past_deadline,
            "source": r["source"],
            "scores": {
                "win_probability": r.get("win_probability"),
                "completeness": r.get("completeness"),
                "quality": r.get("quality"),
                "consistency": r.get("consistency"),
            } if r.get("win_probability") is not None else None,
            "gate_action": r.get("gate_action"),
            "gate_passed": r["gate_passed"] == "true" if r.get("gate_passed") else None,
            "priority": r.get("priority_score"),
            "ingested_at": r["ingested_at"],
            "updated_at": r["updated_at"],
        })

    return results


def _is_past_deadline(respond_by: str | None) -> bool:
    if not respond_by:
        return False
    try:
        deadline = datetime.fromisoformat(respond_by.replace("Z", "+00:00"))
        return deadline < datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


# ===================================================================
# GET /disputes/{dispute_id}  —  full case detail
# ===================================================================

@app.get("/disputes/{dispute_id}")
def get_dispute(dispute_id: str):
    """Full case: scores, gate decision, documents, draft, job status."""
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    scores = repo.get_scores(dispute_id)
    decision = repo.get_latest_decision(dispute_id)
    docs = repo.get_documents(dispute_id)
    facts_by_doc = repo.get_facts_by_document(dispute_id)
    draft = repo.get_latest_draft(dispute_id)
    jobs = repo.get_jobs_for_dispute(dispute_id)

    documents = []
    for d in docs:
        documents.append({
            "document_id": d["document_id"],
            "evidence_slot": d["evidence_slot"],
            "quality": d["quality"],
            "local_path": d["local_path"],
            "mime_type": d["mime_type"],
            "size_bytes": d["size_bytes"],
            "razorpay_doc_id": d.get("razorpay_doc_id"),
            "facts": facts_by_doc.get(d["document_id"], {}),
            "uploaded_at": d["uploaded_at"],
        })

    gate = None
    if decision:
        gate = {
            "action": decision["action"],
            "passed": decision["passed"],
            "failing_conditions": decision.get("failing_conditions", []),
        }

    draft_response = None
    if draft:
        draft_response = {
            "summary_text": draft["summary_text"],
            "citations": draft.get("citations", []),
            "generated_at": draft["generated_at"],
        }

    past_deadline = _is_past_deadline(dispute.get("respond_by"))

    return {
        "dispute_id": dispute["dispute_id"],
        "payment_id": dispute.get("payment_id"),
        "reason_code": dispute["reason_code"],
        "network": dispute["network"],
        "amount_paise": dispute["amount_paise"],
        "currency": dispute["currency"],
        "respond_by": dispute.get("respond_by"),
        "past_deadline": past_deadline,
        "status": dispute["status"],
        "source": dispute["source"],
        "scores": {
            "win_probability": scores["win_probability"] if scores else None,
            "completeness": scores["completeness"] if scores else None,
            "quality": scores["quality"] if scores else None,
            "consistency": scores["consistency"] if scores else None,
            "missing_required_slots": scores.get("missing_required_slots", []) if scores else [],
            "contradiction_flags": scores.get("contradiction_flags", []) if scores else [],
            "computed_at": scores["computed_at"] if scores else None,
        } if scores else None,
        "gate": gate,
        "priority": decision["priority_score"] if decision else None,
        "documents": documents,
        "draft": draft_response,
        "jobs": [
            {
                "job_id": j["job_id"],
                "job_type": j["job_type"],
                "status": j["status"],
                "attempts": j["attempts"],
                "last_error": j["last_error"],
                "created_at": j["created_at"],
                "updated_at": j["updated_at"],
            }
            for j in jobs
        ],
        "ingested_at": dispute["ingested_at"],
        "updated_at": dispute["updated_at"],
    }


# ===================================================================
# GET /disputes/{dispute_id}/audit  —  audit trail
# ===================================================================

@app.get("/disputes/{dispute_id}/audit")
def get_audit_trail(dispute_id: str):
    """Full audit trail for one dispute, ordered chronologically."""
    dispute = repo.get(dispute_id)
    if dispute is None:
        raise HTTPException(
            status_code=404,
            detail=f"Dispute {dispute_id!r} not found.",
        )

    entries = repo.get_audit_trail(dispute_id)

    return {
        "dispute_id": dispute_id,
        "entries": entries,
    }


# ===================================================================
# GET /metrics  —  aggregate dashboard numbers
# ===================================================================

@app.get("/metrics")
def get_metrics():
    """Aggregate dashboard numbers, explicitly labeled as simulated.

    Returns zeroed values on an empty database (5.8).
    """
    total_by_status = repo.count_by_status()
    total = sum(total_by_status.values())

    if total == 0:
        return {
            "total_disputes": 0,
            "by_status": {},
            "auto_prepared": 0,
            "human_review": 0,
            "low_priority": 0,
            "likely_recoverable_paise": 0,
            "avg_completeness": 0,
            "avg_quality": 0,
            "avg_consistency": 0,
            "avg_win_probability": 0,
            "note": "Simulated on synthetic test batch",
        }

    gate = repo.gate_counts()
    amounts = repo.total_amount_by_action()
    scores = repo.avg_scores()

    return {
        "total_disputes": total,
        "by_status": total_by_status,
        "auto_prepared": gate.get("prepare", 0),
        "human_review": gate.get("review", 0),
        "low_priority": gate.get("low_priority", 0),
        "likely_recoverable_paise": amounts.get("prepare", 0),
        "avg_completeness": round(scores["avg_completeness"], 2),
        "avg_quality": round(scores["avg_quality"], 2),
        "avg_consistency": round(scores["avg_consistency"], 2),
        "avg_win_probability": round(scores["avg_win_probability"], 4),
        "note": "Simulated on synthetic test batch",
    }


# ===================================================================
# GET /events  —  SSE stream
# ===================================================================

@app.get("/events")
async def sse_events(request: Request):
    """Server-sent events stream for live updates.

    Streams job completions, score updates, gate decisions, and
    draft-ready notifications to connected frontends.
    """
    import asyncio
    import queue

    q: queue.Queue = queue.Queue()
    _sse_connections.append(q)

    async def event_generator():
        try:
            # Send an initial comment so the Vite proxy sees data
            # immediately and doesn't buffer the response.
            yield ": ping\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = q.get_nowait()
                    # Format as proper SSE text: "event: X\ndata: Y\n\n"
                    yield f"event: {event.event}\ndata: {event.data}\n\n"
                except queue.Empty:
                    await asyncio.sleep(0.5)
        finally:
            if q in _sse_connections:
                _sse_connections.remove(q)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ===================================================================
# GET /healthz
# ===================================================================

@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "version": "0.3.0",
        "workers_running": job_queue.is_running,
    }


# ===================================================================
# GET /api/integrations/razorpay/health  —  Razorpay API health check
# ===================================================================

@app.get("/api/integrations/razorpay/health")
def razorpay_health():
    """Verify Razorpay API authentication.

    Returns safe metadata only — never exposes credentials.
    Used by the frontend 'Test Connection' button and integration status page.
    """
    config = _get_razorpay_config()
    if config is None:
        return {
            "provider": "razorpay",
            "mode": "unknown",
            "configured": False,
            "authenticated": False,
            "message": "Razorpay credentials not configured. Check RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET.",
        }

    client = _get_razorpay_client()
    if client is None:
        return {
            "provider": "razorpay",
            "mode": "test" if config.test_mode else "live",
            "configured": True,
            "authenticated": False,
            "message": "Razorpay client could not be initialized.",
        }

    # Test authentication by listing disputes (lightweight GET)
    try:
        disputes = client.list_disputes()
        return {
            "provider": "razorpay",
            "mode": "test" if config.test_mode else "live",
            "configured": True,
            "authenticated": True,
            "disputes_found": len(disputes),
            "message": f"Connected to Razorpay {'Test Mode' if config.test_mode else 'Live Mode'}.",
        }
    except Exception as exc:
        logger.warning("Razorpay health check failed: %s", exc)
        return {
            "provider": "razorpay",
            "mode": "test" if config.test_mode else "live",
            "configured": True,
            "authenticated": False,
            "message": f"Razorpay authentication failed. Check Test Mode Key ID and Key Secret.",
        }


# ===================================================================
# GET /api/integrations/razorpay/status  —  full integration status
# ===================================================================

@app.get("/api/integrations/razorpay/status")
def razorpay_status():
    """Full integration status page for Settings -> Razorpay Integration.

    Uses real health checks where possible. Never exposes credentials.
    """
    config = _get_razorpay_config()
    client = _get_razorpay_client()

    checks = []

    # 1. Environment / credentials
    checks.append({
        "name": "Environment",
        "status": "pass" if (config and config.test_mode) else ("warn" if config else "fail"),
        "label": "Test Mode" if (config and config.test_mode) else ("Live Mode" if config else "Not configured"),
    })

    # 2. API credentials
    checks.append({
        "name": "API credentials",
        "status": "pass" if config else "fail",
        "label": "Configured" if config else "Missing — set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET",
    })

    # 3. API authentication
    authenticated = False
    if client:
        try:
            client.list_disputes()
            authenticated = True
        except Exception:
            pass
    checks.append({
        "name": "API authentication",
        "status": "pass" if authenticated else "fail",
        "label": "Connected" if authenticated else "Failed",
    })

    # 4. Webhook endpoint
    webhook_configured = bool(config and config.webhook_secret)
    checks.append({
        "name": "Webhook endpoint",
        "status": "pass",
        "label": "POST /api/webhooks/razorpay",
    })

    # 5. Webhook signature validation
    checks.append({
        "name": "Webhook signature validation",
        "status": "pass" if webhook_configured else "warn",
        "label": "Enabled" if webhook_configured else "Disabled (RAZORPAY_WEBHOOK_SECRET not set)",
    })

    # 6. Documents API
    checks.append({
        "name": "Documents API",
        "status": "pass" if authenticated else "fail",
        "label": "Connected" if authenticated else "Not connected",
    })

    # 7. Document upload (check if any docs have razorpay_doc_id)
    try:
        doc_count = 0
        razorpay_doc_count = 0
        for d in repo.list_by_priority(limit=100):
            docs = repo.get_documents(d["dispute_id"])
            doc_count += len(docs)
            razorpay_doc_count += sum(1 for doc in docs if doc.get("razorpay_doc_id"))
        checks.append({
            "name": "Document upload",
            "status": "pass" if authenticated else "warn",
            "label": f"{razorpay_doc_count} docs with Razorpay IDs ({doc_count} total)" if doc_count > 0 else "No documents uploaded yet",
        })
    except Exception:
        checks.append({
            "name": "Document upload",
            "status": "warn",
            "label": "Unable to check",
        })

    # 8. Dispute webhook handler
    checks.append({
        "name": "Dispute webhook",
        "status": "pass",
        "label": "Handler implemented",
    })

    # 9. Contest API
    checks.append({
        "name": "Contest API",
        "status": "pass" if authenticated else "warn",
        "label": "Client implemented" + (" (dry-run: CONTEST_DRY_RUN=" + os.environ.get("CONTEST_DRY_RUN", "not set") + ")" if os.environ.get("CONTEST_DRY_RUN") else ""),
    })

    # 10. Test dispute creation
    checks.append({
        "name": "Test dispute creation",
        "status": "warn",
        "label": "Not available in Razorpay Test Mode — use /api/dev/simulate/razorpay/dispute",
    })

    overall = "healthy" if all(c["status"] == "pass" for c in checks) else (
        "degraded" if any(c["status"] == "pass" for c in checks) else "unhealthy"
    )

    return {
        "provider": "razorpay",
        "mode": "test" if (config and config.test_mode) else "live",
        "overall": overall,
        "checks": checks,
    }


# ===================================================================
# POST /api/integrations/razorpay/test  —  test connection button
# ===================================================================

@app.post("/api/integrations/razorpay/test")
def test_razorpay_connection():
    """Test Razorpay connection and return result.

    Used by the 'Test Razorpay Connection' button in the UI.
    """
    return razorpay_health()


# ===================================================================
# POST /api/dev/simulate/razorpay/dispute  —  dispute simulator
# ===================================================================

@app.post("/api/dev/simulate/razorpay/dispute")
def simulate_razorpay_dispute(
    scenario: str = "good",
    reason_code: str = "RZP01",
    amount_paise: int = 4200000,
):
    """Generate a Razorpay-shaped dispute payload and process it through
    the SAME pipeline as a real webhook.

    Development/demo only — disabled in production.

    Scenarios:
      - 'good': complete evidence, no contradictions
      - 'bad': complete documents but contradictory facts
      - 'incomplete': only one required evidence category
    """
    # Guard: development only
    env = os.environ.get("ENVIRONMENT", "development")
    if env == "production":
        raise HTTPException(
            status_code=403,
            detail="Dispute simulator is disabled in production",
        )

    import hashlib as _hashlib

    # Generate a realistic Razorpay dispute webhook payload
    dispute_id = f"disp_sim_{uuid.uuid4().hex[:10]}"
    payment_id = f"pay_sim_{uuid.uuid4().hex[:10]}"
    now_ts = int(datetime.now(timezone.utc).timestamp())
    respond_by_ts = now_ts + (7 * 24 * 3600)  # 7 days from now

    # Build evidence documents based on scenario
    facts_sets: dict[str, dict] = {}
    if scenario == "good":
        # Complete, consistent evidence
        facts_sets = {
            "proof_of_service": {
                "amount_paise": str(amount_paise),
                "event_date": "2026-08-20",
                "customer_name": "Rahul Sharma",
                "order_id": "ORD_SIM_001",
            },
            "customer_communication": {
                "amount_paise": str(amount_paise),
                "event_date": "2026-08-22",
                "customer_name": "Rahul Sharma",
                "order_id": "ORD_SIM_001",
            },
            "term_and_conditions": {
                "amount_paise": str(amount_paise),
                "event_date": "2026-08-01",
                "customer_name": "Rahul Sharma",
                "order_id": "ORD_SIM_001",
            },
        }
    elif scenario == "bad":
        # Complete documents but contradictory amounts
        facts_sets = {
            "proof_of_service": {
                "amount_paise": str(amount_paise),
                "event_date": "2026-08-20",
                "customer_name": "Rahul Sharma",
                "order_id": "ORD_SIM_001",
            },
            "customer_communication": {
                "amount_paise": str(amount_paise + 50000),  # Contradictory amount
                "event_date": "2026-08-22",
                "customer_name": "Rahul Sharma",
                "order_id": "ORD_SIM_001",
            },
            "term_and_conditions": {
                "amount_paise": str(amount_paise),
                "event_date": "2026-08-01",
                "customer_name": "Rahul Sharma",
                "order_id": "ORD_SIM_001",
            },
        }
    elif scenario == "incomplete":
        # Only one required evidence category
        facts_sets = {
            "proof_of_service": {
                "amount_paise": str(amount_paise),
                "event_date": "2026-08-20",
                "customer_name": "Rahul Sharma",
                "order_id": "ORD_SIM_001",
            },
        }
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scenario '{scenario}'. Use 'good', 'bad', or 'incomplete'.",
        )

    # Build the Razorpay-shaped webhook payload
    webhook_payload = {
        "event": "payment.dispute.created",
        "payload": {
            "dispute": {
                "entity": {
                    "id": dispute_id,
                    "payment_id": payment_id,
                    "reason_code": reason_code,
                    "amount": amount_paise,
                    "currency": "INR",
                    "respond_by": respond_by_ts,
                    "created_at": now_ts,
                    "status": "open",
                    "phase": "chargeback",
                }
            },
            "payment": {
                "entity": {
                    "id": payment_id,
                    "order_id": "ORD_SIM_001",
                    "amount": amount_paise,
                    "currency": "INR",
                    "status": "captured",
                    "method": "card",
                }
            },
        },
    }

    # Process through the SAME webhook normalization pipeline
    raw_body = json.dumps(webhook_payload).encode("utf-8")
    payload_hash = _hashlib.sha256(raw_body).hexdigest()[:16]

    # Track the simulated webhook event
    event_id = f"evt_sim_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    repo.save_webhook_event({
        "event_id": event_id,
        "event_type": "payment.dispute.created",
        "received_at": now_iso,
        "signature_verified": False,  # simulated, not real webhook
        "processing_status": "processing",
        "dispute_id": dispute_id,
        "raw_payload_hash": payload_hash,
    })

    # Normalize through the same pipeline
    try:
        row = normalize_webhook_to_dispute(webhook_payload)
    except NormalizationError as exc:
        repo.update_webhook_event_status(event_id, "failed")
        raise HTTPException(
            status_code=400,
            detail={"error": str(exc), "field_errors": exc.field_errors},
        )
    except OutOfScopeReasonCode as exc:
        repo.update_webhook_event_status(event_id, "out_of_scope")
        return {
            "dispute_id": dispute_id,
            "status": "out_of_scope",
            "reason_code": exc.reason_code,
            "source": "simulator",
            "simulated": True,
        }

    order_id = resolve_order_id(webhook_payload)

    # Atomic idempotent insert + enqueue (same as webhook handler)
    was_present, job_id = repo.idempotent_insert_dispute(
        row={
            "dispute_id": row.dispute_id,
            "payment_id": row.payment_id,
            "reason_code": row.reason_code,
            "network": row.network,
            "amount_paise": row.amount_paise,
            "currency": row.currency,
            "respond_by": row.respond_by,
            "dispute_created_at": row.created_at,
            "order_id": order_id,
            "status": "queued",
            "source": "simulator",
            "raw_webhook_payload": raw_body.decode("utf-8", errors="replace"),
            "ingested_at": now_iso,
            "updated_at": now_iso,
        },
        job_type="score.case",
        job_payload={},
    )

    if was_present:
        repo.update_webhook_event_status(event_id, "duplicate")
        return {
            "dispute_id": row.dispute_id,
            "status": "already_ingested",
            "source": "simulator",
            "simulated": True,
        }

    repo.write_audit({
        "dispute_id": row.dispute_id,
        "stage": "ingest",
        "detail": {
            "reason_code": row.reason_code,
            "amount_paise": row.amount_paise,
            "source": "simulator",
            "scenario": scenario,
        },
        "success": True,
    })

    repo.write_audit({
        "dispute_id": row.dispute_id,
        "stage": "queue",
        "detail": {"job_id": job_id, "job_type": "score.case"},
        "success": True,
    })

    repo.update_webhook_event_status(event_id, "processed", dispute_id=row.dispute_id)

    _broadcast_sse("dispute.received", {
        "dispute_id": row.dispute_id,
        "reason_code": row.reason_code,
        "amount_paise": row.amount_paise,
        "source": "simulator",
    })

    # Now upload simulated evidence documents
    uploaded_docs = []
    for slot, facts in facts_sets.items():
        doc_id = f"doc_sim_{uuid.uuid4().hex[:10]}"
        now_doc_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

        repo.save_document({
            "document_id": doc_id,
            "dispute_id": row.dispute_id,
            "evidence_slot": normalize_evidence_slot(slot),
            "local_path": None,
            "content_hash": None,
            "mime_type": "application/pdf",
            "size_bytes": 0,
            "quality": "clear",
            "uploaded_at": now_doc_iso,
        })

        # Save extracted facts directly (simulated OCR output)
        for fact_type, fact_value in facts.items():
            repo.save_fact({
                "document_id": doc_id,
                "dispute_id": row.dispute_id,
                "fact_type": fact_type,
                "fact_value": str(fact_value),
                "extracted_at": now_doc_iso,
            })

        uploaded_docs.append({
            "document_id": doc_id,
            "evidence_slot": slot,
        })

    # Enqueue re-score with all simulated documents in place
    if uploaded_docs:
        repo.enqueue_job("score.case", row.dispute_id, {})

    logger.info(
        "Simulated dispute created: dispute=%s scenario=%s docs=%d",
        row.dispute_id, scenario, len(uploaded_docs),
    )

    return {
        "dispute_id": row.dispute_id,
        "payment_id": row.payment_id,
        "reason_code": row.reason_code,
        "amount_paise": row.amount_paise,
        "source": "simulator",
        "scenario": scenario,
        "simulated": True,
        "documents": uploaded_docs,
        "job_id": job_id,
        "status": "queued",
        "message": f"Simulated dispute ({scenario}) created and queued for scoring.",
    }
