"""
backend/razorpay_client.py

Real Razorpay API client for Documents and Contest APIs.

Documents API (POST /v1/documents):
  Uploads a file to Razorpay's ecosystem and returns a doc_* ID.
  Uses multipart/form-data (NOT JSON).  This is the key thing that
  trips people up.

Contest API (PATCH /v1/disputes/:id/contest):
  Submits evidence to contest a dispute.  Requires at least one
  document_id across all evidence slots.  Supports draft/submit actions.

Contract sourced from:
  - https://razorpay.com/docs/api/documents/create/
  - https://razorpay.com/docs/api/disputes/contest/

Both endpoints accept test-mode API keys for sandbox testing.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger("shield_assist.razorpay_client")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

RAZORPAY_API_BASE = "https://api.razorpay.com/v1"


@dataclass(frozen=True)
class RazorpayCredentials:
    """Razorpay API credentials (test or live mode)."""

    key_id: str
    key_secret: str

    @classmethod
    def from_env(cls) -> RazorpayCredentials:
        """Load credentials from environment variables."""
        key_id = os.environ.get("RAZORPAY_KEY_ID", "")
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        if not key_id or not key_secret:
            raise ValueError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set. "
                "Generate test-mode keys at https://dashboard.razorpay.com/app/keys"
            )
        return cls(key_id=key_id, key_secret=key_secret)


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class RazorpayAPIError(Exception):
    """Base exception for Razorpay API errors.

    Attributes:
        status_code: HTTP status code
        error_code: Razorpay error code (e.g. 'BAD_REQUEST_ERROR')
        description: Human-readable error description
        raw_response: Full response dict from Razorpay
    """

    def __init__(
        self,
        status_code: int,
        error_code: str,
        description: str,
        raw_response: dict | None = None,
    ):
        self.status_code = status_code
        self.error_code = error_code
        self.description = description
        self.raw_response = raw_response or {}
        super().__init__(
            f"Razorpay API error {status_code} ({error_code}): {description}"
        )


class DocumentUploadError(RazorpayAPIError):
    """Raised when POST /v1/documents fails.

    Common causes:
      - 400: file missing, purpose missing, wrong mime type, file too large,
             concurrent upload in progress
      - 401: invalid API key (wrong mode or expired)
    """
    pass


class ContestSubmissionError(RazorpayAPIError):
    """Raised when PATCH /v1/disputes/:id/contest fails.

    Common causes:
      - 400: dispute_id does not exist, contest after respond_by elapsed,
             dispute already lost/won
      - 401: invalid API key
    """
    pass


# ---------------------------------------------------------------------------
# Response dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentUploadResult:
    """Successful response from POST /v1/documents."""

    razorpay_doc_id: str
    purpose: str
    name: str
    mime_type: str
    size_bytes: int
    created_at: int  # unix timestamp


@dataclass(frozen=True)
class ContestSubmissionResult:
    """Successful response from PATCH /v1/disputes/:id/contest.

    The response shape is the full dispute entity with updated evidence
    and status fields.
    """

    dispute_id: str
    status: str  # 'open' (draft) or 'under_review' (submit)
    amount: int
    currency: str
    evidence: dict[str, Any]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class RazorpayClient:
    """HTTP client for Razorpay's API.

    Handles authentication (Basic Auth with key_id:key_secret),
    retries for transient errors, and structured error handling
    per the documented error contracts.
    """

    def __init__(
        self,
        credentials: RazorpayCredentials | None = None,
        base_url: str = RAZORPAY_API_BASE,
        max_retries: int = 2,
        backoff_base: float = 1.0,
    ):
        self._creds = credentials or RazorpayCredentials.from_env()
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._session = requests.Session()
        self._session.auth = (self._creds.key_id, self._creds.key_secret)

    # -- Low-level request helper -------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        files: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """Execute an HTTP request with retry on transient errors.

        Raises RazorpayAPIError (or subclass) on non-2xx responses.
        """
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None

        for attempt in range(1 + self._max_retries):
            try:
                resp = self._session.request(
                    method,
                    url,
                    json=json,
                    files=files,
                    timeout=timeout,
                )

                if resp.status_code >= 200 and resp.status_code < 300:
                    return resp.json()

                # Parse Razorpay error response
                try:
                    error_body = resp.json()
                except ValueError:
                    error_body = {"error": {"description": resp.text}}

                error_info = error_body.get("error", {})
                error_code = error_info.get("code", "UNKNOWN_ERROR")
                description = error_info.get("description", str(resp.text))

                # Retry on transient server errors (5xx) but not on client errors (4xx)
                if resp.status_code >= 500 and attempt < self._max_retries:
                    backoff = self._backoff_base * (2 ** attempt)
                    logger.warning(
                        "Razorpay %s %s: %d %s — retrying in %.1fs (attempt %d/%d)",
                        method, path, resp.status_code, error_code,
                        backoff, attempt + 1, self._max_retries + 1,
                    )
                    time.sleep(backoff)
                    continue

                # Non-retryable error — raise appropriate subclass
                if "document" in path.lower():
                    raise DocumentUploadError(
                        status_code=resp.status_code,
                        error_code=error_code,
                        description=description,
                        raw_response=error_body,
                    )
                raise ContestSubmissionError(
                    status_code=resp.status_code,
                    error_code=error_code,
                    description=description,
                    raw_response=error_body,
                )

            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = exc
                if attempt < self._max_retries:
                    backoff = self._backoff_base * (2 ** attempt)
                    logger.warning(
                        "Razorpay %s %s: network error (%s) — retrying in %.1fs",
                        method, path, exc, backoff,
                    )
                    time.sleep(backoff)
                    continue

        raise last_error  # type: ignore[misc]

    # -- Documents API ------------------------------------------------------

    def upload_document(
        self,
        file_path: str,
        purpose: str = "dispute_evidence",
    ) -> DocumentUploadResult:
        """Upload a file to Razorpay's Documents API.

        POST /v1/documents

        Content-Type: multipart/form-data (NOT JSON!)

        Args:
            file_path: Path to the local file to upload
            purpose: Document purpose. For dispute evidence, always
                     'dispute_evidence'. Other values may be valid for
                     different Razorpay use cases.

        Returns:
            DocumentUploadResult with the razorpay_doc_id and metadata.

        Raises:
            DocumentUploadError: On 400/401 errors from Razorpay.
            FileNotFoundError: If file_path doesn't exist.
            ValueError: If credentials are missing.
        """
        import mimetypes
        import os

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        file_name = os.path.basename(file_path)

        logger.info(
            "Uploading document to Razorpay: %s (%s, purpose=%s)",
            file_name, mime_type, purpose,
        )

        with open(file_path, "rb") as f:
            result = self._request(
                "POST",
                "/documents",
                files={
                    "file": (file_name, f, mime_type),
                    "purpose": (None, purpose),
                },
            )

        doc_result = DocumentUploadResult(
            razorpay_doc_id=result["id"],
            purpose=result["purpose"],
            name=result.get("display_name", result.get("name", "")),
            mime_type=result["mime_type"],
            size_bytes=result.get("size", result.get("size_bytes", 0)),
            created_at=result["created_at"],
        )

        logger.info(
            "Document uploaded: local=%s razorpay=%s (purpose=%s)",
            file_name, doc_result.razorpay_doc_id, doc_result.purpose,
        )
        return doc_result

    # -- Contest API --------------------------------------------------------

    def draft_contest(
        self,
        dispute_id: str,
        *,
        amount: int | None = None,
        summary: str,
        evidence_slots: dict[str, list[str]] | None = None,
        others: list[dict[str, Any]] | None = None,
    ) -> ContestSubmissionResult:
        """Draft a contest (saves evidence without submitting).

        PATCH /v1/disputes/:id/contest with action='draft'

        Args:
            dispute_id: The Razorpay dispute ID (disp_*)
            amount: Amount to contest (paise). None = full dispute amount.
            summary: Textual explanation (max 1000 chars)
            evidence_slots: Dict of slot_name -> [doc_id, ...] mapping.
                           E.g. {"billing_proof": ["doc_abc", "doc_def"]}
            others: Additional evidence items with type + document_ids.

        Returns:
            ContestSubmissionResult with status='open' (drafted but not submitted).
        """
        payload: dict[str, Any] = {
            "action": "draft",
            "summary": summary,
        }
        if amount is not None:
            payload["amount"] = amount
        if evidence_slots:
            payload.update(evidence_slots)
        if others:
            payload["others"] = others

        result = self._request(
            "PATCH",
            f"/disputes/{dispute_id}/contest",
            json=payload,
        )

        return ContestSubmissionResult(
            dispute_id=result["id"],
            status=result["status"],
            amount=result["amount"],
            currency=result["currency"],
            evidence=result.get("evidence", {}),
        )

    def submit_contest(
        self,
        dispute_id: str,
        *,
        evidence_slots: dict[str, list[str]] | None = None,
        others: list[dict[str, Any]] | None = None,
        billing_proof: list[str] | None = None,
    ) -> ContestSubmissionResult:
        """Submit a contest with evidence.

        PATCH /v1/disputes/:id/contest with action='submit'

        The documented contract requires at least one document_id across
        all evidence slots for action='submit'.  Omitting action or using
        action='draft' saves without submitting.

        Args:
            dispute_id: The Razorpay dispute ID (disp_*)
            evidence_slots: Dict of slot_name -> [doc_id, ...] mapping.
            others: Additional evidence items with type + document_ids.
            billing_proof: Convenience alias — list of doc IDs for
                          the billing_proof slot.

        Returns:
            ContestSubmissionResult with status='under_review' (submitted).
        """
        payload: dict[str, Any] = {
            "action": "submit",
        }
        if evidence_slots:
            payload.update(evidence_slots)
        if billing_proof:
            payload["billing_proof"] = billing_proof
        if others:
            payload["others"] = others

        result = self._request(
            "PATCH",
            f"/disputes/{dispute_id}/contest",
            json=payload,
        )

        return ContestSubmissionResult(
            dispute_id=result["id"],
            status=result["status"],
            amount=result["amount"],
            currency=result["currency"],
            evidence=result.get("evidence", {}),
        )

    # -- Dispute queries (for completeness) ---------------------------------

    def fetch_dispute(self, dispute_id: str) -> dict:
        """Fetch dispute details.

        GET /v1/disputes/:id

        Returns the raw dispute entity dict from Razorpay.
        """
        return self._request("GET", f"/disputes/{dispute_id}")

    def list_disputes(self) -> list[dict]:
        """Fetch all disputes.

        GET /v1/disputes

        Returns the raw list of dispute entity dicts from Razorpay.
        Note: In test mode, this returns {"entity": "collection", "has_more": false}
        with no items key — this is expected (no disputes in sandbox).
        """
        result = self._request("GET", "/disputes")
        # Razorpay returns {"entity": "collection", "items": [...]} when disputes exist,
        # or {"entity": "collection", "has_more": false} when empty.
        return result.get("items", [])
