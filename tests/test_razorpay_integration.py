"""
tests/test_razorpay_integration.py

Comprehensive integration test suite for Razorpay Test Mode integration.

Tests cover:
  1. Authentication — valid/invalid credentials
  2. Webhooks — signature verification, idempotency
  3. Payloads — dispute.created, unknown events, malformed payloads
  4. Documents — upload, Razorpay doc_* ID persistence
  5. Evidence — slot mapping, fact citations
  6. Gate — existing gate checks not bypassed
  7. Contest — human approval required, dry-run mode
  8. Simulator — enters same pipeline

Run:
    pytest tests/test_razorpay_integration.py -v
    pytest tests/test_razorpay_integration.py -v -k "webhook"
    pytest tests/test_razorpay_integration.py -v -k "not live"
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv
from starlette.testclient import TestClient

load_dotenv()

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    os.environ["SHIELD_ASSIST_DB_PATH"] = db_path
    os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_webhook_secret_123"

    from backend import db
    db.DB_PATH = db_path
    from backend.db import init_db
    init_db(db_path)

    import backend.jobs  # Register handlers
    from backend.repository import SQLiteRepository
    from backend.job_queue import JobQueue

    repo = SQLiteRepository(db_path)
    job_queue = JobQueue(repo, num_workers=2, poll_interval=0.2)

    from backend import app as app_module
    app_module.repo = repo
    app_module.job_queue = job_queue

    yield {"repo": repo, "job_queue": job_queue, "db_path": db_path, "app": app_module.app}

    job_queue.stop()
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def client(temp_db):
    """FastAPI test client."""
    return TestClient(temp_db["app"])


@pytest.fixture
def webhook_secret():
    """Return the test webhook secret."""
    return "test_webhook_secret_123"


def _sign_webhook(body: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for webhook body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _make_dispute_webhook(
    dispute_id: str = None,
    reason_code: str = "RZP01",
    amount: int = 4200000,
    payment_id: str = None,
) -> dict:
    """Build a simulated Razorpay payment.dispute.created webhook."""
    dispute_id = dispute_id or f"disp_test_{uuid.uuid4().hex[:10]}"
    payment_id = payment_id or f"pay_test_{uuid.uuid4().hex[:10]}"
    return {
        "event": "payment.dispute.created",
        "payload": {
            "dispute": {"entity": {
                "id": dispute_id,
                "payment_id": payment_id,
                "reason_code": reason_code,
                "amount": amount,
                "currency": "INR",
                "respond_by": int(time.time()) + 7 * 86400,
                "created_at": int(time.time()) - 3600,
            }},
            "payment": {"entity": {
                "id": payment_id,
                "order_id": f"ORD_{dispute_id}",
                "amount": amount,
            }},
        },
    }


# ===========================================================================
# 1. AUTHENTICATION TESTS
# ===========================================================================

class TestAuthentication:
    """Test Razorpay API authentication."""

    def test_config_from_env(self):
        """Valid credentials -> config loads successfully."""
        from backend.razorpay_config import RazorpayConfig

        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_abc123"
        os.environ["RAZORPAY_KEY_SECRET"] = "secret_xyz789"

        config = RazorpayConfig.from_env()

        assert config.key_id == "rzp_test_abc123"
        assert config.key_secret == "secret_xyz789"
        assert config.test_mode is True

    def test_config_missing_credentials(self):
        """Missing credentials -> ValueError."""
        from backend.razorpay_config import RazorpayConfig

        os.environ.pop("RAZORPAY_KEY_ID", None)
        os.environ.pop("RAZORPAY_KEY_SECRET", None)

        with pytest.raises(ValueError, match="Missing required"):
            RazorpayConfig.from_env()

    def test_config_optional_returns_none_when_missing(self):
        """Missing credentials -> optional_from_env returns None."""
        from backend.razorpay_config import RazorpayConfig

        os.environ.pop("RAZORPAY_KEY_ID", None)
        os.environ.pop("RAZORPAY_KEY_SECRET", None)

        config = RazorpayConfig.optional_from_env()
        assert config is None

    def test_config_never_exposes_secrets(self):
        """Config repr/str never contains actual secrets."""
        from backend.razorpay_config import RazorpayConfig

        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_abc123def456"
        os.environ["RAZORPAY_KEY_SECRET"] = "super_secret_12345"

        config = RazorpayConfig.from_env()

        assert "super_secret_12345" not in repr(config)
        assert "super_secret_12345" not in str(config)
        assert "super_secret_12345" not in json.dumps(config.to_safe_dict())

    def test_config_safe_dict_has_no_secrets(self):
        """to_safe_dict contains no secret values."""
        from backend.razorpay_config import RazorpayConfig

        os.environ["RAZORPAY_KEY_ID"] = "rzp_test_abc123"
        os.environ["RAZORPAY_KEY_SECRET"] = "secret"

        config = RazorpayConfig.from_env()
        safe = config.to_safe_dict()

        assert "key_secret" not in safe
        assert "webhook_secret" not in safe
        assert safe["configured"] is True
        assert safe["mode"] == "test"


# ===========================================================================
# 2. WEBHOOK TESTS
# ===========================================================================

class TestWebhookSignature:
    """Test webhook signature verification."""

    def test_valid_signature_accepted(self, client, webhook_secret):
        """Valid HMAC-SHA256 signature -> 200."""
        webhook = _make_dispute_webhook("disp_sig_001")
        body = json.dumps(webhook).encode()
        sig = _sign_webhook(body, webhook_secret)

        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "queued"
        assert data["dispute_id"] == "disp_sig_001"

    def test_invalid_signature_rejected(self, client, webhook_secret):
        """Invalid signature -> 401."""
        webhook = _make_dispute_webhook("disp_sig_002")
        body = json.dumps(webhook).encode()

        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": "invalid_sig_123", "Content-Type": "application/json"},
        )
        assert r.status_code == 401

    def test_missing_signature_rejected(self, client, webhook_secret):
        """Missing signature header -> 401."""
        webhook = _make_dispute_webhook("disp_sig_003")
        body = json.dumps(webhook).encode()

        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 401

    def test_same_event_twice_processed_once(self, client, webhook_secret):
        """Same webhook delivered twice -> only one case created."""
        dispute_id = "disp_idem_001"
        webhook = _make_dispute_webhook(dispute_id)
        body = json.dumps(webhook).encode()
        sig = _sign_webhook(body, webhook_secret)

        headers = {"X-Razorpay-Signature": sig, "Content-Type": "application/json"}

        r1 = client.post("/api/webhooks/razorpay", content=body, headers=headers)
        assert r1.json()["status"] == "queued"

        r2 = client.post("/api/webhooks/razorpay", content=body, headers=headers)
        # Either "already_ingested" (dispute-level idempotency) or
        # "already_processed" (event-level idempotency) are valid
        assert r2.json()["status"] in ("already_ingested", "already_processed")


# ===========================================================================
# 3. PAYLOAD TESTS
# ===========================================================================

class TestWebhookPayloads:
    """Test webhook payload handling."""

    def test_dispute_created_creates_case(self, client, webhook_secret):
        """payment.dispute.created -> case created and queued."""
        webhook = _make_dispute_webhook("disp_payload_001")
        body = json.dumps(webhook).encode()
        sig = _sign_webhook(body, webhook_secret)

        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["dispute_id"] == "disp_payload_001"
        assert data["status"] == "queued"

    def test_unknown_event_ignored_safely(self, client, webhook_secret):
        """Unknown event type -> handled gracefully."""
        webhook = {"event": "payment.captured", "payload": {}}
        body = json.dumps(webhook).encode()
        sig = _sign_webhook(body, webhook_secret)

        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
        )
        # Unknown events that can't be normalized -> 400
        assert r.status_code in (200, 400)

    def test_malformed_json_rejected(self, client, webhook_secret):
        """Malformed JSON body -> 400."""
        body = b"not valid json {{{"
        sig = _sign_webhook(body, webhook_secret)

        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_missing_required_fields_rejected(self, client, webhook_secret):
        """Webhook missing required fields -> 400 with field errors."""
        webhook = {"event": "payment.dispute.created", "payload": {"dispute": {"entity": {}}}}
        body = json.dumps(webhook).encode()
        sig = _sign_webhook(body, webhook_secret)

        r = client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
        )
        assert r.status_code == 400


# ===========================================================================
# 4. DOCUMENT TESTS
# ===========================================================================

class TestDocumentUpload:
    """Test document upload and Razorpay doc_* ID persistence."""

    def test_upload_persists_razorpay_doc_id(self, temp_db):
        """Documents with razorpay_doc_id are persisted correctly."""
        repo = temp_db["repo"]
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        # Create a dispute first
        repo.save_dispute({
            "dispute_id": "disp_doc_001",
            "payment_id": "pay_doc_001",
            "reason_code": "RZP01",
            "network": "razorpay",
            "amount_paise": 4200000,
            "currency": "INR",
            "status": "scored",
            "source": "webhook",
            "raw_webhook_payload": "{}",
            "ingested_at": now_iso,
            "updated_at": now_iso,
        })

        # Save a document with razorpay_doc_id
        repo.save_document({
            "document_id": "doc_local_001",
            "dispute_id": "disp_doc_001",
            "evidence_slot": "proof_of_service",
            "local_path": "/tmp/test.pdf",
            "content_hash": "abc123",
            "mime_type": "application/pdf",
            "size_bytes": 1024,
            "quality": "clear",
            "uploaded_at": now_iso,
        })

        # Simulate Razorpay upload success
        repo.update_document_razorpay_id("doc_local_001", "doc_RZP_abc123")

        # Verify it was persisted
        docs = repo.get_documents("disp_doc_001")
        assert len(docs) == 1
        assert docs[0]["razorpay_doc_id"] == "doc_RZP_abc123"

    def test_document_without_razorpay_id_still_works(self, temp_db):
        """Documents without razorpay_doc_id still work."""
        repo = temp_db["repo"]
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        repo.save_dispute({
            "dispute_id": "disp_doc_002",
            "payment_id": "pay_doc_002",
            "reason_code": "RZP01",
            "network": "razorpay",
            "amount_paise": 4200000,
            "currency": "INR",
            "status": "scored",
            "source": "simulator",
            "raw_webhook_payload": "{}",
            "ingested_at": now_iso,
            "updated_at": now_iso,
        })

        repo.save_document({
            "document_id": "doc_local_002",
            "dispute_id": "disp_doc_002",
            "evidence_slot": "billing_proof",
            "local_path": "/tmp/test2.pdf",
            "content_hash": "def456",
            "mime_type": "application/pdf",
            "size_bytes": 2048,
            "quality": "clear",
            "uploaded_at": now_iso,
        })

        docs = repo.get_documents("disp_doc_002")
        assert len(docs) == 1
        assert docs[0].get("razorpay_doc_id") is None


# ===========================================================================
# 5. EVIDENCE SLOT MAPPING TESTS
# ===========================================================================

class TestEvidenceSlotMapping:
    """Test evidence slot canonicalization and mapping."""

    def test_known_slot_passes_through(self):
        """Known slot names pass through unchanged."""
        from backend.dispute_normalizer import normalize_evidence_slot

        assert normalize_evidence_slot("billing_proof") == "billing_proof"
        assert normalize_evidence_slot("proof_of_service") == "proof_of_service"
        assert normalize_evidence_slot("customer_communication") == "customer_communication"
        assert normalize_evidence_slot("term_and_conditions") == "term_and_conditions"

    def test_alias_correction(self):
        """Known aliases are corrected."""
        from backend.dispute_normalizer import normalize_evidence_slot

        assert normalize_evidence_slot("shipping_proof") == "proof_of_service"
        assert normalize_evidence_slot("terms_and_conditions") == "term_and_conditions"

    def test_unknown_slot_becomes_others(self):
        """Unknown slot names become 'others'."""
        from backend.dispute_normalizer import normalize_evidence_slot

        assert normalize_evidence_slot("random_unknown_slot") == "others"
        assert normalize_evidence_slot("") == "others"

    def test_case_insensitive(self):
        """Slot normalization is case-insensitive for underscored names."""
        from backend.dispute_normalizer import normalize_evidence_slot

        assert normalize_evidence_slot("BILLING_PROOF") == "billing_proof"
        assert normalize_evidence_slot("proof_of_service") == "proof_of_service"


# ===========================================================================
# 6. GATE INTEGRITY TESTS
# ===========================================================================

class TestGateIntegrity:
    """Verify Razorpay integration does not bypass the deterministic gate."""

    def test_gate_requires_completeness(self):
        """Low completeness blocks prepare."""
        from models.scoring import apply_gate

        decision = apply_gate(
            win_probability=0.9,
            completeness=50.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots={"billing_proof"},
        )
        assert decision.action != "prepare"
        assert decision.passed is False

    def test_gate_requires_consistency(self):
        """Contradictions block prepare."""
        from models.scoring import apply_gate, GATE_MIN_WIN_PROBABILITY
        from models.contradiction_rules import ContradictionFlag

        flag = ContradictionFlag(
            rule_type="amount_mismatch",
            severity="high",
            documents_involved=["doc_1", "doc_2"],
            detail="Amount mismatch detected",
        )

        decision = apply_gate(
            win_probability=0.9,
            completeness=100.0,
            consistency=65.0,
            contradiction_flags=[flag],
            missing_required_slots=set(),
        )
        assert decision.action != "prepare"
        assert decision.passed is False

    def test_gate_requires_win_probability(self):
        """Low win probability blocks prepare."""
        from models.scoring import apply_gate, GATE_MIN_WIN_PROBABILITY

        decision = apply_gate(
            win_probability=0.3,
            completeness=100.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )
        assert decision.action != "prepare"

    def test_gate_all_conditions_met(self):
        """All conditions met -> prepare."""
        from models.scoring import apply_gate, GATE_MIN_WIN_PROBABILITY

        decision = apply_gate(
            win_probability=GATE_MIN_WIN_PROBABILITY + 0.1,
            completeness=100.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )
        assert decision.action == "prepare"
        assert decision.passed is True


# ===========================================================================
# 7. CONTEST TESTS
# ===========================================================================

class TestContestFlow:
    """Test contest submission requires human approval."""

    def test_contest_without_approval_returns_409(self, client, temp_db):
        """Contest without human.approved -> 409."""
        repo = temp_db["repo"]
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        repo.save_dispute({
            "dispute_id": "disp_contest_001",
            "payment_id": "pay_contest_001",
            "reason_code": "RZP01",
            "network": "razorpay",
            "amount_paise": 4200000,
            "currency": "INR",
            "status": "scored",
            "source": "webhook",
            "raw_webhook_payload": "{}",
            "ingested_at": now_iso,
            "updated_at": now_iso,
        })

        r = client.post("/disputes/disp_contest_001/contest")
        assert r.status_code == 409

    def test_dry_run_mode(self, temp_db):
        """CONTEST_DRY_RUN=true builds but does not submit."""
        os.environ["CONTEST_DRY_RUN"] = "true"
        try:
            from backend.jobs.contest_job import handle_contest_job

            repo = MagicMock()
            repo.get_latest_draft.return_value = {
                "summary_text": "Test draft response",
                "citations": [],
            }
            repo.has_audit_event.return_value = True
            repo.get.return_value = {
                "dispute_id": "disp_dry_001",
                "status": "ready",
            }
            repo.get_documents.return_value = [
                {
                    "document_id": "doc_dry_001",
                    "evidence_slot": "proof_of_service",
                    "razorpay_doc_id": "doc_RZP_dry123",
                    "quality": "clear",
                },
            ]

            job = {"dispute_id": "disp_dry_001", "payload": "{}"}
            handle_contest_job(job, repo)

            # Verify audit was written with dry_run status
            repo.write_audit.assert_called_once()
            audit_call = repo.write_audit.call_args[0][0]
            assert audit_call["detail"]["status"] == "dry_run"
        finally:
            os.environ.pop("CONTEST_DRY_RUN", None)


# ===========================================================================
# 8. SIMULATOR TESTS
# ===========================================================================

class TestDisputeSimulator:
    """Test the Razorpay dispute simulator."""

    def test_simulate_good_scenario(self, client):
        """Good scenario -> dispute created with complete evidence."""
        r = client.post("/api/dev/simulate/razorpay/dispute?scenario=good")
        assert r.status_code == 200
        data = r.json()
        assert data["simulated"] is True
        assert data["source"] == "simulator"
        assert data["scenario"] == "good"
        assert len(data["documents"]) == 3  # RZP01 needs 3 slots

    def test_simulate_bad_scenario(self, client):
        """Bad scenario -> dispute created with contradictions."""
        r = client.post("/api/dev/simulate/razorpay/dispute?scenario=bad")
        assert r.status_code == 200
        data = r.json()
        assert data["simulated"] is True
        assert data["scenario"] == "bad"
        assert len(data["documents"]) == 3

    def test_simulate_incomplete_scenario(self, client):
        """Incomplete scenario -> dispute with missing evidence."""
        r = client.post("/api/dev/simulate/razorpay/dispute?scenario=incomplete")
        assert r.status_code == 200
        data = r.json()
        assert data["simulated"] is True
        assert data["scenario"] == "incomplete"
        assert len(data["documents"]) == 1

    def test_simulate_unknown_scenario_returns_400(self, client):
        """Unknown scenario -> 400."""
        r = client.post("/api/dev/simulate/razorpay/dispute?scenario=unknown")
        assert r.status_code == 400

    def test_simulate_disabled_in_production(self, client):
        """Simulator disabled when ENVIRONMENT=production."""
        old_env = os.environ.get("ENVIRONMENT")
        os.environ["ENVIRONMENT"] = "production"
        try:
            r = client.post("/api/dev/simulate/razorpay/dispute?scenario=good")
            assert r.status_code == 403
        finally:
            if old_env is not None:
                os.environ["ENVIRONMENT"] = old_env
            else:
                os.environ.pop("ENVIRONMENT", None)

    def test_simulated_dispute_enters_same_pipeline(self, temp_db):
        """Simulated dispute goes through the same normalization pipeline."""
        from backend.dispute_normalizer import normalize_webhook_to_dispute

        # Build the same payload the simulator would produce
        webhook_payload = {
            "event": "payment.dispute.created",
            "payload": {
                "dispute": {"entity": {
                    "id": "disp_sim_pipeline_001",
                    "payment_id": "pay_sim_pipeline_001",
                    "reason_code": "RZP01",
                    "amount": 4200000,
                    "currency": "INR",
                    "respond_by": int(time.time()) + 7 * 86400,
                    "created_at": int(time.time()) - 3600,
                }},
                "payment": {"entity": {
                    "id": "pay_sim_pipeline_001",
                    "order_id": "ORD_SIM_001",
                    "amount": 4200000,
                }},
            },
        }

        row = normalize_webhook_to_dispute(webhook_payload)
        assert row.dispute_id == "disp_sim_pipeline_001"
        assert row.reason_code == "RZP01"
        assert row.amount_paise == 4200000


# ===========================================================================
# 9. WEBHOOK EVENTS TABLE TESTS
# ===========================================================================

class TestWebhookEvents:
    """Test webhook event tracking for idempotency."""

    def test_webhook_event_persisted(self, temp_db):
        """Webhook event metadata is persisted."""
        repo = temp_db["repo"]
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        repo.save_webhook_event({
            "event_id": "evt_test_001",
            "event_type": "payment.dispute.created",
            "received_at": now_iso,
            "signature_verified": True,
            "processing_status": "processed",
            "dispute_id": "disp_test_001",
            "raw_payload_hash": "abc123",
        })

        event = repo.get_webhook_event("evt_test_001")
        assert event is not None
        assert event["event_type"] == "payment.dispute.created"
        assert event["processing_status"] == "processed"
        assert event["signature_verified"] == "true"

    def test_duplicate_event_id_ignored(self, temp_db):
        """Duplicate event_id is silently ignored."""
        repo = temp_db["repo"]
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        event = {
            "event_id": "evt_dup_001",
            "event_type": "payment.dispute.created",
            "received_at": now_iso,
            "signature_verified": True,
            "processing_status": "processed",
        }

        repo.save_webhook_event(event)
        repo.save_webhook_event(event)  # Should not raise

        result = repo.get_webhook_event("evt_dup_001")
        assert result is not None

    def test_event_status_update(self, temp_db):
        """Event status can be updated."""
        repo = temp_db["repo"]
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        repo.save_webhook_event({
            "event_id": "evt_upd_001",
            "event_type": "payment.dispute.created",
            "received_at": now_iso,
            "signature_verified": True,
            "processing_status": "processing",
        })

        repo.update_webhook_event_status("evt_upd_001", "processed", "disp_upd_001")

        event = repo.get_webhook_event("evt_upd_001")
        assert event["processing_status"] == "processed"
        assert event["dispute_id"] == "disp_upd_001"


# ===========================================================================
# 10. INTEGRATION STATUS ENDPOINT TESTS
# ===========================================================================

class TestIntegrationEndpoints:
    """Test integration health and status endpoints."""

    def test_health_endpoint_returns_safe_data(self, client):
        """Health endpoint returns no credentials."""
        r = client.get("/api/integrations/razorpay/health")
        assert r.status_code == 200
        data = r.json()
        assert "provider" in data
        assert data["provider"] == "razorpay"
        # Ensure no secrets in response
        response_str = json.dumps(data)
        assert "key_secret" not in response_str
        assert "webhook_secret" not in response_str

    def test_status_endpoint_returns_checks(self, client):
        """Status endpoint returns structured checks."""
        r = client.get("/api/integrations/razorpay/status")
        assert r.status_code == 200
        data = r.json()
        assert "checks" in data
        assert len(data["checks"]) >= 5
        assert data["overall"] in ("healthy", "degraded", "unhealthy")

    def test_test_connection_endpoint(self, client):
        """Test connection button works."""
        r = client.post("/api/integrations/razorpay/test")
        assert r.status_code == 200
        data = r.json()
        assert "authenticated" in data


# ===========================================================================
# 11. INCOMPLETE CASE UPLOAD + CACHE-HIT TESTS
# ===========================================================================

class TestIncompleteCaseUpload:
    """Upload → document.process → score.case for incomplete disputes."""

    _PDF = PROJECT_ROOT / "data" / "test_customer_comm.pdf"
    _FACTS = json.dumps({
        "amount_paise": "4200000",
        "event_date": "2026-08-22",
        "customer_name": "Rahul Sharma",
        "order_id": "ORD_SIM_001",
    })

    def _wait_for(self, client, dispute_id: str, predicate, timeout: float = 15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = client.get(f"/disputes/{dispute_id}")
            assert r.status_code == 200
            data = r.json()
            if data.get("scores") and predicate(data):
                return data
            time.sleep(0.15)
        raise TimeoutError(f"Timed out waiting for dispute {dispute_id}")

    def test_upload_missing_evidence_recalculates_scores(self, temp_db):
        """Uploading a required slot increases completeness only after facts exist."""
        with TestClient(temp_db["app"]) as client:
            r = client.post("/api/dev/simulate/razorpay/dispute?scenario=incomplete")
            assert r.status_code == 200
            dispute_id = r.json()["dispute_id"]

            initial = self._wait_for(
                client, dispute_id,
                lambda d: len(d["scores"]["missing_required_slots"]) == 2,
            )
            assert set(initial["scores"]["missing_required_slots"]) == {
                "customer_communication", "term_and_conditions",
            }
            assert initial["scores"]["completeness"] == pytest.approx(33.33, abs=0.1)
            assert initial["gate"]["passed"] is False

            with open(self._PDF, "rb") as f:
                up = client.post(
                    f"/disputes/{dispute_id}/documents",
                    files={"file": ("customer_comm.pdf", f, "application/pdf")},
                    data={
                        "evidence_slot": "customer_communication",
                        "quality": "clear",
                        "facts": self._FACTS,
                    },
                )
            assert up.status_code == 200

            after = self._wait_for(
                client, dispute_id,
                lambda d: d["scores"]["missing_required_slots"] == ["term_and_conditions"],
            )
            assert after["scores"]["completeness"] == pytest.approx(66.67, abs=0.1)
            assert after["gate"]["passed"] is False

    def test_cache_hit_triggers_rescore_without_ocr(self, temp_db):
        """Same-file cache reuses facts and still runs score.case."""
        with TestClient(temp_db["app"]) as client:
            r1 = client.post("/api/dev/simulate/razorpay/dispute?scenario=incomplete")
            dispute_a = r1.json()["dispute_id"]
            self._wait_for(
                client, dispute_a,
                lambda d: len(d["scores"]["missing_required_slots"]) == 2,
            )

            with open(self._PDF, "rb") as f:
                seed = client.post(
                    f"/disputes/{dispute_a}/documents",
                    files={"file": ("seed.pdf", f, "application/pdf")},
                    data={
                        "evidence_slot": "customer_communication",
                        "quality": "clear",
                        "facts": self._FACTS,
                    },
                )
            assert seed.status_code == 200
            self._wait_for(
                client, dispute_a,
                lambda d: d["scores"]["missing_required_slots"] == ["term_and_conditions"],
            )

            # Same-dispute cache hit (identical file bytes, different slot)
            with open(self._PDF, "rb") as f:
                cached = client.post(
                    f"/disputes/{dispute_a}/documents",
                    files={"file": ("seed_copy.pdf", f, "application/pdf")},
                    data={
                        "evidence_slot": "term_and_conditions",
                        "quality": "clear",
                        "facts": "{}",
                    },
                )
            assert cached.status_code == 200
            cached_data = cached.json()
            assert cached_data["ocr_skipped"] is True
            assert cached_data["cached_from"] is not None

            final_a = self._wait_for(
                client, dispute_a,
                lambda d: d["scores"]["missing_required_slots"] == [],
            )
            assert final_a["scores"]["completeness"] == pytest.approx(100.0, abs=0.1)

            # Cross-dispute cache hit
            r2 = client.post("/api/dev/simulate/razorpay/dispute?scenario=incomplete")
            dispute_b = r2.json()["dispute_id"]
            self._wait_for(
                client, dispute_b,
                lambda d: len(d["scores"]["missing_required_slots"]) == 2,
            )

            with open(self._PDF, "rb") as f:
                cross = client.post(
                    f"/disputes/{dispute_b}/documents",
                    files={"file": ("seed.pdf", f, "application/pdf")},
                    data={
                        "evidence_slot": "customer_communication",
                        "quality": "clear",
                        "facts": "{}",
                    },
                )
            assert cross.status_code == 200
            cross_data = cross.json()
            assert cross_data["ocr_skipped"] is True

            after_b = self._wait_for(
                client, dispute_b,
                lambda d: d["scores"]["missing_required_slots"] == ["term_and_conditions"],
            )
            comm_docs = [
                d for d in after_b["documents"]
                if d["evidence_slot"] == "customer_communication"
            ]
            assert len(comm_docs) == 1
            assert comm_docs[0]["facts"]
