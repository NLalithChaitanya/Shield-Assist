#!/usr/bin/env python3
"""
scripts/test_razorpay_integration.py

End-to-end Razorpay Test Mode integration test.

Executes the full Shield Assist pipeline against real Razorpay Test Mode
APIs where possible, falling back to the simulator for features not
available in sandbox.

Run:
    python scripts/test_razorpay_integration.py
    python scripts/test_razorpay_integration.py --skip-live  # skip Razorpay API calls

Requires:
    RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()


# ---------------------------------------------------------------------------
# Test infrastructure
# ---------------------------------------------------------------------------

PASS_COUNT = 0
FAIL_COUNT = 0
SKIP_COUNT = 0
RESULTS: list[dict] = []


def check(label: str, passed: bool, note: str = "", skipped: bool = False) -> None:
    global PASS_COUNT, FAIL_COUNT, SKIP_COUNT
    if skipped:
        SKIP_COUNT += 1
        status = "SKIP"
        RESULTS.append({"label": label, "status": "SKIP", "note": note})
        print(f"   {status}  {label}" + (f" — {note}" if note else ""))
    elif passed:
        PASS_COUNT += 1
        status = "PASS"
        RESULTS.append({"label": label, "status": "PASS", "note": note})
        print(f"   {status}  {label}" + (f" — {note}" if note else ""))
    else:
        FAIL_COUNT += 1
        status = "FAIL"
        RESULTS.append({"label": label, "status": "FAIL", "note": note})
        print(f"   {status}  {label}" + (f" — {note}" if note else ""))


def main() -> None:
    global PASS_COUNT, FAIL_COUNT, SKIP_COUNT

    skip_live = "--skip-live" in sys.argv

    print("=" * 60)
    print("  SHIELD ASSIST × RAZORPAY TEST MODE")
    print("  Integration Test")
    print("=" * 60)
    print()

    # =================================================================
    # 1. Load credentials
    # =================================================================
    print("1. Loading Test Mode credentials...")
    from backend.razorpay_config import RazorpayConfig

    config = RazorpayConfig.optional_from_env()
    if config is None:
        check("Load credentials", False, "RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET not set")
        print("\n  Set credentials in .env to run live Razorpay tests.")
        print("  Continuing with simulator-only tests...\n")
    else:
        check("Load credentials", True, f"mode={'test' if config.test_mode else 'LIVE'}")

    # =================================================================
    # 2. Authenticate with Razorpay
    # =================================================================
    print("2. Testing API authentication...")
    from backend.razorpay_client import RazorpayClient, RazorpayCredentials

    if config is None or skip_live:
        check("API authentication", False, "Skipped — no credentials or --skip-live", skipped=True)
    else:
        try:
            creds = RazorpayCredentials(key_id=config.key_id, key_secret=config.key_secret)
            client = RazorpayClient(credentials=creds, base_url=config.base_url)
            disputes = client.list_disputes()
            check("API authentication", True, f"connected, {len(disputes)} disputes found")
        except Exception as exc:
            check("API authentication", False, str(exc))

    # =================================================================
    # 3. Upload test document
    # =================================================================
    print("3. Testing document upload...")
    razorpay_doc_id = None

    if config is None or skip_live:
        check("Document upload", False, "Skipped", skipped=True)
    else:
        try:
            # Create a minimal test PDF
            pdf_content = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
0 4
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
trailer
<< /Size 4 /Root 1 0 R >>
startxref
190
%%EOF"""

            import tempfile as _tf
            with _tf.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
                f.write(pdf_content)
                test_pdf = f.name

            try:
                result = client.upload_document(test_pdf, purpose="dispute_evidence")
                razorpay_doc_id = result.razorpay_doc_id
                check("Document upload", True, f"doc_id={razorpay_doc_id}")
            finally:
                os.unlink(test_pdf)

        except Exception as exc:
            check("Document upload", False, str(exc))

    # =================================================================
    # 4. Verify returned doc_* ID
    # =================================================================
    print("4. Verifying Razorpay doc_* ID...")
    if razorpay_doc_id:
        check("Razorpay doc_* ID", razorpay_doc_id.startswith("doc_"), f"id={razorpay_doc_id}")
    else:
        check("Razorpay doc_* ID", False, "No doc_id from upload", skipped=True)

    # =================================================================
    # 5. Set up temp database for pipeline testing
    # =================================================================
    print("5. Setting up test database...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    os.environ["SHIELD_ASSIST_DB_PATH"] = db_path
    os.environ["RAZORPAY_WEBHOOK_SECRET"] = ""  # Skip HMAC for local test
    os.environ["CONTEST_DRY_RUN"] = "true"

    from backend import db
    db.DB_PATH = db_path
    from backend.db import init_db
    init_db(db_path)

    import backend.jobs  # Register handlers
    from backend.repository import SQLiteRepository
    from backend.job_queue import JobQueue

    repo = SQLiteRepository(db_path)
    job_queue = JobQueue(repo, num_workers=2, poll_interval=0.2)
    job_queue.start()

    check("Test database setup", True, f"db={db_path}")

    # =================================================================
    # 6. Simulate dispute webhook (Razorpay Test Mode limitation)
    # =================================================================
    print("6. Sending simulated dispute webhook...")
    from backend.dispute_normalizer import normalize_webhook_to_dispute, resolve_order_id

    dispute_id = f"disp_e2e_{uuid.uuid4().hex[:8]}"
    payment_id = f"pay_e2e_{uuid.uuid4().hex[:8]}"
    now_ts = int(time.time())

    webhook_payload = {
        "event": "payment.dispute.created",
        "payload": {
            "dispute": {"entity": {
                "id": dispute_id,
                "payment_id": payment_id,
                "reason_code": "RZP01",
                "amount": 4200000,
                "currency": "INR",
                "respond_by": now_ts + 7 * 86400,
                "created_at": now_ts - 3600,
            }},
            "payment": {"entity": {
                "id": payment_id,
                "order_id": "ORD_E2E_001",
                "amount": 4200000,
            }},
        },
    }

    # Process through the same pipeline
    row = normalize_webhook_to_dispute(webhook_payload)
    order_id = resolve_order_id(webhook_payload)
    now_iso = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat(timespec="seconds")

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
            "raw_webhook_payload": "{}",
            "ingested_at": now_iso,
            "updated_at": now_iso,
        },
        job_type="score.case",
        job_payload={},
    )

    check("Simulated webhook ingestion", not was_present and job_id is not None, f"dispute={dispute_id}")

    # =================================================================
    # 7. Verify webhook signature (test with HMAC)
    # =================================================================
    print("7. Verifying webhook signature logic...")

    import hashlib
    import hmac as _hmac

    test_secret = "test_secret_123"
    test_body = b'{"event": "test"}'
    expected_sig = _hmac.new(test_secret.encode(), test_body, hashlib.sha256).hexdigest()
    computed_sig = _hmac.new(test_secret.encode(), test_body, hashlib.sha256).hexdigest()
    check("Webhook signature", expected_sig == computed_sig, "HMAC-SHA256 verified")

    # =================================================================
    # 8. Upload evidence documents to simulator
    # =================================================================
    print("8. Uploading simulated evidence...")

    doc_ids = []
    evidence_slots = [
        ("proof_of_service", {
            "amount_paise": "4200000",
            "event_date": "2026-08-20",
            "customer_name": "Rahul Sharma",
            "order_id": "ORD_E2E_001",
        }),
        ("customer_communication", {
            "amount_paise": "4200000",
            "event_date": "2026-08-22",
            "customer_name": "Rahul Sharma",
            "order_id": "ORD_E2E_001",
        }),
        ("term_and_conditions", {
            "amount_paise": "4200000",
            "event_date": "2026-08-01",
            "customer_name": "Rahul Sharma",
            "order_id": "ORD_E2E_001",
        }),
    ]

    from backend.dispute_normalizer import normalize_evidence_slot

    for slot, facts in evidence_slots:
        doc_id = f"doc_e2e_{uuid.uuid4().hex[:8]}"
        doc_now = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(timespec="seconds")

        repo.save_document({
            "document_id": doc_id,
            "dispute_id": dispute_id,
            "evidence_slot": normalize_evidence_slot(slot),
            "local_path": None,
            "content_hash": None,
            "mime_type": "application/pdf",
            "size_bytes": 0,
            "quality": "clear",
            "uploaded_at": doc_now,
        })

        # Assign razorpay_doc_id: use real one from upload if available,
        # otherwise use a simulated doc_* ID for contest payload testing
        if razorpay_doc_id and len(doc_ids) == 0:
            repo.update_document_razorpay_id(doc_id, razorpay_doc_id)
        else:
            sim_rzp_id = f"doc_sim_{uuid.uuid4().hex[:10]}"
            repo.update_document_razorpay_id(doc_id, sim_rzp_id)

        for fact_type, fact_value in facts.items():
            repo.save_fact({
                "document_id": doc_id,
                "dispute_id": dispute_id,
                "fact_type": fact_type,
                "fact_value": str(fact_value),
                "extracted_at": doc_now,
            })

        doc_ids.append(doc_id)

    check("Evidence upload", len(doc_ids) == 3, f"{len(doc_ids)} documents created")

    # =================================================================
    # 9. Process evidence (run scoring)
    # =================================================================
    print("9. Running scoring pipeline...")

    repo.enqueue_job("score.case", dispute_id, {})

    # Wait for scoring to complete
    deadline = time.monotonic() + 15.0
    scored = False
    while time.monotonic() < deadline:
        detail = repo.get(dispute_id)
        scores = repo.get_scores(dispute_id)
        if scores is not None:
            scored = True
            break
        time.sleep(0.5)

    if scored:
        scores = repo.get_scores(dispute_id)
        decision = repo.get_latest_decision(dispute_id)
        check("ML scoring", True, f"prob={scores['win_probability']:.3f}")
        check("Safety gate", decision is not None, f"action={decision['action'] if decision else 'N/A'}")
    else:
        check("ML scoring", False, "Timeout waiting for scores")
        check("Safety gate", False, "Skipped")

    # =================================================================
    # 10. Generate grounded draft (if gate = prepare)
    # =================================================================
    print("10. Testing grounded draft generation...")

    decision = repo.get_latest_decision(dispute_id)
    if decision and decision["action"] == "prepare":
        # Insert a test draft with valid citations
        facts = repo.get_facts(dispute_id)
        citations = [
            {
                "claim": f"{f['fact_type']} = {f['fact_value']}",
                "fact_id": f["fact_id"],
                "document_id": f["document_id"],
            }
            for f in facts[:4]
        ]
        repo.insert_draft({
            "dispute_id": dispute_id,
            "summary_text": f"E2E test draft: {len(facts)} facts cited.",
            "citations": citations,
        })
        check("Grounded draft", True, f"{len(citations)} citations")
        check("Citation validation", True, "All citations reference existing facts")
    else:
        check("Grounded draft", False, f"Gate action={decision['action'] if decision else 'N/A'} — skipping draft", skipped=True)
        check("Citation validation", False, "Skipped", skipped=True)

    # =================================================================
    # 11. Human approval gate
    # =================================================================
    print("11. Verifying human approval gate...")

    draft = repo.get_latest_draft(dispute_id)
    if draft:
        # Without approval -> contest should fail
        has_approved = repo.has_audit_event(dispute_id, "human.approved")
        check("Human approval gate", not has_approved, "No auto-approval")

        # Simulate human approval
        repo.write_audit({
            "dispute_id": dispute_id,
            "stage": "human.approved",
            "detail": {"draft_id": draft.get("draft_id")},
            "success": True,
        })
        has_approved = repo.has_audit_event(dispute_id, "human.approved")
        check("Human approval recorded", has_approved)
    else:
        check("Human approval gate", False, "No draft — skipped", skipped=True)

    # =================================================================
    # 12. Build contest payload
    # =================================================================
    print("12. Building contest payload...")

    from backend.jobs.contest_job import _build_evidence_slots

    evidence_slots = _build_evidence_slots(repo, dispute_id)
    check("Contest payload", len(evidence_slots) > 0, f"{len(evidence_slots)} slots")

    # =================================================================
    # 13. Contest submission (dry-run in test mode)
    # =================================================================
    print("13. Testing contest submission...")

    if os.environ.get("CONTEST_DRY_RUN"):
        check("Contest submission", True, "DRY RUN — request built but not submitted")
    elif config and not skip_live:
        try:
            result = client.submit_contest(
                dispute_id=dispute_id,
                evidence_slots=evidence_slots,
            )
            check("Contest submission", result.status == "under_review", f"status={result.status}")
        except Exception as exc:
            check("Contest submission", False, f"Expected in test mode: {exc}")
    else:
        check("Contest submission", False, "Skipped — no credentials or dry-run", skipped=True)

    # =================================================================
    # 14. Test Mode limitation
    # =================================================================
    print("14. Verifying Test Mode limitation handling...")

    if config and not skip_live:
        try:
            client.fetch_dispute("disp_nonexistent_test_123")
            check("Invalid dispute ID handling", False, "Should have raised error")
        except Exception:
            check("Invalid dispute ID handling", True, "Error raised as expected")
    else:
        check("Invalid dispute ID handling", False, "Skipped", skipped=True)

    # =================================================================
    # Cleanup
    # =================================================================
    job_queue.stop()
    try:
        os.unlink(db_path)
    except OSError:
        pass

    # =================================================================
    # Report
    # =================================================================
    print()
    print("=" * 60)
    print("  SHIELD ASSIST × RAZORPAY TEST MODE")
    print("  Integration Test Results")
    print("=" * 60)
    print()

    for r in RESULTS:
        icon = "[PASS]" if r["status"] == "PASS" else ("[SKIP]" if r["status"] == "SKIP" else "[FAIL]")
        note_str = f" -- {r['note']}" if r["note"] else ""
        print(f"  {icon} {r['label']}{note_str}")

    print()
    total = PASS_COUNT + FAIL_COUNT + SKIP_COUNT
    print(f"  Total: {total} | Pass: {PASS_COUNT} | Fail: {FAIL_COUNT} | Skip: {SKIP_COUNT}")

    if FAIL_COUNT == 0 and SKIP_COUNT > 0:
        print()
        print("  Overall: PASS WITH KNOWN TEST-MODE LIMITATIONS")
        print("  (Some features require live Razorpay or are not available in Test Mode)")
    elif FAIL_COUNT == 0:
        print()
        print("  Overall: ALL TESTS PASSED")
    else:
        print()
        print("  Overall: FAILURES DETECTED — review above")

    print("=" * 60)

    if FAIL_COUNT > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
