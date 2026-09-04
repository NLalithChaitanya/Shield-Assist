"""
tests/test_safety_gate.py

Adversarial safety test set proving ML probability alone cannot override
safety conditions. These tests verify the deterministic safety gate
behaves correctly under adversarial inputs.

Test cases:
  CASE A: High prob + complete + consistent + no contradictions → PREPARE
  CASE B: High prob + incomplete + no contradictions → BLOCK
  CASE C: High prob + complete + contradictions → BLOCK
  CASE D: Medium prob + complete + consistent + no contradictions → REVIEW
  CASE E: Low prob + complete + no contradictions → REVIEW/BLOCK

These tests exist to prove that ML probability alone cannot override
safety conditions. Do NOT modify the gate rules to make tests pass.

Run:
    pytest tests/test_safety_gate.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import tempfile

from backend.domain import Dispute, Document
from models.scoring import (
    apply_gate,
    completeness_score,
    consistency_score,
    evaluate_dispute,
    quality_score,
    GATE_MIN_WIN_PROBABILITY,
    GATE_MIN_COMPLETENESS,
    GATE_MIN_CONSISTENCY,
)
from models.contradiction_rules import ContradictionFlag, run_all_contradiction_checks


# ─── Fixture for integration tests ──────────────────────────────────

@pytest.fixture
def temp_db():
    """Create a temporary database for integration tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    os.environ["SHIELD_ASSIST_DB_PATH"] = db_path
    os.environ["RAZORPAY_WEBHOOK_SECRET"] = "test_secret_123"

    from backend import db
    db.DB_PATH = db_path
    from backend.db import init_db
    init_db(db_path)

    import backend.jobs  # Register handlers
    from backend.repository import SQLiteRepository
    from backend.job_queue import JobQueue

    repo = SQLiteRepository(db_path)
    job_queue = JobQueue(repo, num_workers=0, poll_interval=1.0)

    from backend import app as app_module
    app_module.repo = repo
    app_module.job_queue = job_queue

    yield {"repo": repo, "db_path": db_path, "app": app_module.app}

    job_queue.stop()
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ─── Helper: build a dispute with controlled properties ─────────────

def _build_dispute(
    *,
    reason_code: str = "RZP01",
    amount_paise: int = 4200000,
    slots: list[str] | None = None,
    quality: str = "clear",
    contradictions: list[tuple[str, str]] | None = None,
    customer_name: str = "Rahul Sharma",
    order_id: str = "ORD123456",
    dispute_date: str = "2026-08-25",
) -> Dispute:
    """Build a dispute with controlled properties for testing.

    Args:
        slots: Evidence slots to include. None = all required slots.
        quality: Document quality for all documents.
        contradictions: List of (field, value) pairs that will create
                       contradictions across documents.
    """
    if slots is None:
        from data.evidence_requirements import required_slots_for
        slots = [s.value for s in required_slots_for(reason_code)]

    documents = []
    for i, slot in enumerate(slots):
        fields = {
            "amount_paise": amount_paise,
            "event_date": "2026-08-20",
            "customer_name": customer_name,
            "order_id": order_id,
        }
        # Apply contradictions if specified
        if contradictions and i == len(slots) - 1:
            for field, value in contradictions:
                fields[field] = value

        documents.append(Document(
            doc_id=f"doc_adv_{i:03d}",
            slot=slot,
            quality=quality,
            fields=fields,
        ))

    return Dispute(
        dispute_id=f"disp_adv_{uuid.uuid4().hex[:8]}",
        reason_code=reason_code,
        amount_paise=amount_paise,
        dispute_date=dispute_date,
        respond_by="2026-09-01T00:00:00+00:00",
        order_id=order_id,
        customer_name=customer_name,
        documents=documents,
    )


# ─── CASE A: High prob + complete + consistent + no contradictions ──

class TestCaseA:
    """CASE A: Everything looks good → PREPARE."""

    def test_gate_passes_with_high_probability_and_complete_evidence(self):
        """High ML probability + complete evidence + no contradictions → PREPARE."""
        dispute = _build_dispute()
        required = {s.value for s in __import__("data.evidence_requirements", fromlist=["required_slots_for"]).required_slots_for("RZP01")}

        result = evaluate_dispute(dispute, win_probability=0.85)

        # Completeness should be 100%
        assert result["completeness"] == 100.0, f"Expected completeness=100, got {result['completeness']}"

        # No contradictions
        assert result["contradiction_flags"] == [], f"Expected no contradictions, got {result['contradiction_flags']}"

        # Quality should be 100% (all clear)
        assert result["quality"] == 100.0, f"Expected quality=100, got {result['quality']}"

        # Consistency should be 100% (no flags)
        assert result["consistency"] == 100.0, f"Expected consistency=100, got {result['consistency']}"

        # Gate should be PREPARE
        assert result["gate"]["passed"] is True
        assert result["gate"]["action"] == "prepare"
        assert result["gate"]["failing_conditions"] == []

    def test_direct_gate_passes_with_high_probability(self):
        """Direct apply_gate call with high probability → PREPARE."""
        decision = apply_gate(
            win_probability=0.85,
            completeness=100.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )

        assert decision.passed is True
        assert decision.action == "prepare"
        assert decision.failing_conditions == []


# ─── CASE B: High prob + incomplete + no contradictions ────────────

class TestCaseB:
    """CASE B: Incomplete evidence blocks PREPARE even with high probability."""

    def test_gate_blocks_with_incomplete_evidence(self):
        """High probability but incomplete evidence → BLOCK (not prepare)."""
        # Only 1 of 3 required slots
        dispute = _build_dispute(slots=["proof_of_service"])

        result = evaluate_dispute(dispute, win_probability=0.85)

        # Completeness should be low (~33.3%)
        assert result["completeness"] < GATE_MIN_COMPLETENESS, (
            f"Completeness {result['completeness']} should be below {GATE_MIN_COMPLETENESS}"
        )

        # Gate should NOT be PREPARE
        assert result["gate"]["passed"] is False
        assert result["gate"]["action"] != "prepare"

        # Failing conditions should mention completeness
        failing = " ".join(result["gate"]["failing_conditions"])
        assert "completeness" in failing.lower() or "missing" in failing.lower(), (
            f"Failing conditions should mention completeness/missing: {result['gate']['failing_conditions']}"
        )

    def test_direct_gate_blocks_with_low_completeness(self):
        """Direct apply_gate: high prob but low completeness → BLOCK."""
        decision = apply_gate(
            win_probability=0.85,
            completeness=33.33,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots={"customer_communication", "term_and_conditions"},
        )

        assert decision.passed is False
        assert decision.action != "prepare"
        assert any("completeness" in c.lower() for c in decision.failing_conditions)


# ─── CASE C: High prob + complete + contradictions ─────────────────

class TestCaseC:
    """CASE C: Contradictions block PREPARE even with high probability."""

    def test_gate_blocks_with_contradictions(self):
        """High probability + complete evidence but contradictions → BLOCK."""
        # Complete evidence but with amount mismatch
        dispute = _build_dispute(
            contradictions=[("amount_paise", 999999)],  # Different amount
        )

        result = evaluate_dispute(dispute, win_probability=0.85)

        # Should have contradictions
        assert len(result["contradiction_flags"]) > 0, "Expected contradiction flags"

        # Consistency should be penalized
        assert result["consistency"] < 100.0, f"Expected consistency < 100, got {result['consistency']}"

        # Gate should NOT be PREPARE
        assert result["gate"]["passed"] is False
        assert result["gate"]["action"] != "prepare"

        # Failing conditions should mention contradictions
        failing = " ".join(result["gate"]["failing_conditions"])
        assert "contradiction" in failing.lower() or "mismatch" in failing.lower(), (
            f"Failing conditions should mention contradiction: {result['gate']['failing_conditions']}"
        )

    def test_direct_gate_blocks_with_contradiction_flags(self):
        """Direct apply_gate: high prob but contradictions → BLOCK."""
        flags = [
            ContradictionFlag(
                rule_type="amount_mismatch",
                severity="high",
                documents_involved=["doc_001", "doc_002"],
                detail="Amount mismatch detected",
            )
        ]

        decision = apply_gate(
            win_probability=0.85,
            completeness=100.0,
            consistency=65.0,  # Penalized by high severity flag
            contradiction_flags=flags,
            missing_required_slots=set(),
        )

        assert decision.passed is False
        assert decision.action != "prepare"
        assert any("contradiction" in c.lower() or "unresolved" in c.lower()
                    for c in decision.failing_conditions)

    def test_amount_mismatch_detected(self):
        """Amount mismatch between documents triggers contradiction flag."""
        dispute = _build_dispute(
            contradictions=[("amount_paise", 5000000)],  # 50L vs 42L
        )

        result = evaluate_dispute(dispute, win_probability=0.85)

        # Should detect amount mismatch
        amount_flags = [f for f in result["contradiction_flags"] if f["rule_type"] == "amount_mismatch"]
        assert len(amount_flags) > 0, "Expected amount_mismatch contradiction flag"

    def test_name_mismatch_detected(self):
        """Customer name mismatch between documents triggers flag."""
        dispute = _build_dispute(
            contradictions=[("customer_name", "Different Person")],
        )

        result = evaluate_dispute(dispute, win_probability=0.85)

        name_flags = [f for f in result["contradiction_flags"] if f["rule_type"] == "name_mismatch"]
        assert len(name_flags) > 0, "Expected name_mismatch contradiction flag"


# ─── CASE D: Medium prob + complete + consistent ───────────────────

class TestCaseD:
    """CASE D: Probability below threshold with complete evidence → not PREPARE.

    With the current data-derived threshold (0.5), a probability of 0.45
    fails the probability check.  Since 0.45 < 0.5, the action is
    'low_priority' (below the review cutoff).  The key assertion is:
    NOT PREPARE, regardless of which non-prepare action is chosen.
    """

    def test_below_threshold_probability_blocks_prepare(self):
        """Probability below threshold + complete evidence → NOT PREPARE."""
        dispute = _build_dispute()

        result = evaluate_dispute(dispute, win_probability=0.45)

        # Completeness should be 100%
        assert result["completeness"] == 100.0

        # No contradictions
        assert result["contradiction_flags"] == []

        # But probability is below threshold → NOT PREPARE
        assert result["gate"]["passed"] is False
        assert result["gate"]["action"] != "prepare"

    def test_below_threshold_probability_directly(self):
        """Direct apply_gate: below-threshold probability → not PREPARE."""
        decision = apply_gate(
            win_probability=0.45,
            completeness=100.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )

        assert decision.passed is False
        assert decision.action != "prepare"
        assert any("win_probability" in c.lower() for c in decision.failing_conditions)


# ─── CASE E: Low prob + complete + no contradictions ───────────────

class TestCaseE:
    """CASE E: Low probability → REVIEW or LOW_PRIORITY."""

    def test_low_probability_gets_low_priority(self):
        """Low probability + complete evidence → low_priority."""
        dispute = _build_dispute()

        result = evaluate_dispute(dispute, win_probability=0.3)

        # Gate should not be PREPARE
        assert result["gate"]["passed"] is False
        # Low probability → low_priority
        assert result["gate"]["action"] == "low_priority"

    def test_low_probability_directly(self):
        """Direct apply_gate: low probability → low_priority."""
        decision = apply_gate(
            win_probability=0.3,
            completeness=100.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )

        assert decision.passed is False
        assert decision.action == "low_priority"


# ─── Additional adversarial tests ──────────────────────────────────

class TestAdversarialEdgeCases:
    """Edge cases that try to trick the gate."""

    def test_boundary_probability_just_below_threshold(self):
        """Probability just below threshold → not PREPARE."""
        decision = apply_gate(
            win_probability=GATE_MIN_WIN_PROBABILITY - 0.01,
            completeness=100.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )

        assert decision.passed is False

    def test_boundary_probability_at_threshold(self):
        """Probability exactly at threshold → PREPARE (if other conditions met)."""
        decision = apply_gate(
            win_probability=GATE_MIN_WIN_PROBABILITY,
            completeness=100.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )

        # At threshold, should be considered passing
        assert decision.passed is True
        assert decision.action == "prepare"

    def test_boundary_completeness_just_below(self):
        """Completeness just below minimum → not PREPARE."""
        decision = apply_gate(
            win_probability=0.9,
            completeness=GATE_MIN_COMPLETENESS - 0.1,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots=set(),
        )

        assert decision.passed is False

    def test_boundary_consistency_just_below(self):
        """Consistency just below minimum → not PREPARE."""
        decision = apply_gate(
            win_probability=0.9,
            completeness=100.0,
            consistency=GATE_MIN_CONSISTENCY - 0.1,
            contradiction_flags=[],
            missing_required_slots=set(),
        )

        assert decision.passed is False

    def test_multiple_failing_conditions(self):
        """Multiple conditions failing simultaneously."""
        decision = apply_gate(
            win_probability=0.3,
            completeness=50.0,
            consistency=60.0,
            contradiction_flags=[
                ContradictionFlag(
                    rule_type="amount_mismatch",
                    severity="high",
                    documents_involved=["doc_001", "doc_002"],
                    detail="Amount mismatch",
                )
            ],
            missing_required_slots={"billing_proof"},
        )

        assert decision.passed is False
        assert len(decision.failing_conditions) >= 3  # prob + completeness + contradictions + missing

    def test_ai_cannot_approve_dispute(self):
        """Verify AI cannot bypass the gate — the gate is deterministic."""
        # Even with 99.9% probability, if completeness is low, gate blocks
        decision = apply_gate(
            win_probability=0.999,
            completeness=50.0,
            consistency=100.0,
            contradiction_flags=[],
            missing_required_slots={"billing_proof", "customer_communication"},
        )

        assert decision.passed is False
        assert decision.action != "prepare"
        # The gate MUST block regardless of probability
        assert any("completeness" in c.lower() or "missing" in c.lower()
                    for c in decision.failing_conditions)


# ─── Evidence integrity tests ───────────────────────────────────────

class TestEvidenceIntegrity:
    """Verify citation integrity enforcement in draft job."""

    def test_verify_citations_rejects_nonexistent_fact(self):
        """Citations referencing nonexistent facts are rejected."""
        from backend.jobs.draft_job import _verify_citations

        facts_by_id = {
            1: {"fact_id": 1, "document_id": "doc_001", "fact_type": "amount_paise", "fact_value": "4200000"},
            2: {"fact_id": 2, "document_id": "doc_001", "fact_type": "event_date", "fact_value": "2026-08-20"},
        }

        # Citation referencing fact_id=999 (doesn't exist)
        citations = [
            {"claim": "Amount is 42L", "fact_id": 1, "document_id": "doc_001"},  # Valid
            {"claim": "Date is Aug 20", "fact_id": 999, "document_id": "doc_001"},  # Invalid
        ]

        invalid = _verify_citations(citations, facts_by_id)

        assert len(invalid) == 1
        assert invalid[0]["fact_id"] == 999
        assert "not found" in invalid[0]["reason"]

    def test_verify_citations_rejects_document_id_mismatch(self):
        """Citations with wrong document_id are rejected."""
        from backend.jobs.draft_job import _verify_citations

        facts_by_id = {
            1: {"fact_id": 1, "document_id": "doc_001", "fact_type": "amount_paise", "fact_value": "4200000"},
        }

        citations = [
            {"claim": "Amount", "fact_id": 1, "document_id": "doc_wrong"},  # Wrong doc_id
        ]

        invalid = _verify_citations(citations, facts_by_id)

        assert len(invalid) == 1
        assert "mismatch" in invalid[0]["reason"].lower()

    def test_verify_citations_accepts_valid_citations(self):
        """All valid citations pass verification."""
        from backend.jobs.draft_job import _verify_citations

        facts_by_id = {
            1: {"fact_id": 1, "document_id": "doc_001", "fact_type": "amount_paise", "fact_value": "4200000"},
            2: {"fact_id": 2, "document_id": "doc_001", "fact_type": "event_date", "fact_value": "2026-08-20"},
        }

        citations = [
            {"claim": "Amount", "fact_id": 1, "document_id": "doc_001"},
            {"claim": "Date", "fact_id": 2, "document_id": "doc_001"},
        ]

        invalid = _verify_citations(citations, facts_by_id)
        assert len(invalid) == 0


# ─── Human approval boundary tests ──────────────────────────────────

class TestHumanApprovalBoundary:
    """Verify human approval is mandatory — AI cannot self-approve."""

    def test_contest_requires_human_approval(self, temp_db):
        """Contest submission blocked without human.approved audit event."""
        from starlette.testclient import TestClient

        repo = temp_db["repo"]
        client = TestClient(temp_db["app"])
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        # Create dispute
        repo.save_dispute({
            "dispute_id": "disp_human_test_001",
            "payment_id": "pay_human_001",
            "reason_code": "RZP01",
            "network": "razorpay",
            "amount_paise": 4200000,
            "currency": "INR",
            "status": "ready",
            "source": "simulator",
            "raw_webhook_payload": "{}",
            "ingested_at": now_iso,
            "updated_at": now_iso,
        })

        # Try contest without approval → must be 409
        r = client.post("/disputes/disp_human_test_001/contest")
        assert r.status_code == 409, f"Expected 409, got {r.status_code}"

    def test_approve_requires_draft(self, temp_db):
        """Approval blocked without a draft."""
        from starlette.testclient import TestClient

        repo = temp_db["repo"]
        client = TestClient(temp_db["app"])
        now_iso = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        # Create dispute without draft
        repo.save_dispute({
            "dispute_id": "disp_nodraft_001",
            "payment_id": "pay_nodraft_001",
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

        # Try approve without draft → must be 400
        r = client.post("/disputes/disp_nodraft_001/approve")
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
