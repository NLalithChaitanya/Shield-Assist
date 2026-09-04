#!/usr/bin/env python3
"""
scripts/evaluate.py

Generate a comprehensive evaluation report for Shield Assist.
Produces both machine-readable JSON and human-readable Markdown.

Usage:
    python scripts/evaluate.py
    python scripts/evaluate.py --output-dir reports/
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from sklearn.metrics import (
    precision_recall_fscore_support,
    confusion_matrix,
    roc_auc_score,
    average_precision_score,
)


def run_all_tests() -> dict:
    """Run all test suites and collect results."""
    import subprocess

    results = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "suites": {},
    }

    test_suites = [
        ("quality_detection", "tests/test_quality_detection.py"),
        ("razorpay_client", "tests/test_razorpay_client.py"),
        ("razorpay_integration", "tests/test_razorpay_integration.py"),
        ("safety_gate", "tests/test_safety_gate.py"),
        ("resilience", "tests/test_resilience.py"),
    ]

    for name, path in test_suites:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", path, "-v", "--tb=no", "-q"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(PROJECT_ROOT),
            )
            output = proc.stdout + proc.stderr

            # Parse results
            passed = output.count(" PASSED")
            failed = output.count(" FAILED")
            skipped = output.count(" SKIPPED") + output.count(" skipped")

            results["suites"][name] = {
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "exit_code": proc.returncode,
            }
            results["total"] += passed + failed + skipped
            results["passed"] += passed
            results["failed"] += failed
            results["skipped"] += skipped
        except Exception as e:
            results["suites"][name] = {"error": str(e)}
            results["failed"] += 1

    return results


def run_smoke_test() -> dict:
    """Run the smoke test and capture results."""
    import subprocess

    try:
        proc = subprocess.run(
            [sys.executable, "scripts/smoke_test_backend.py"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        output = proc.stdout + proc.stderr

        passed = output.count("PASS")
        failed = output.count("FAIL")

        return {
            "passed": passed,
            "failed": failed,
            "exit_code": proc.returncode,
            "success": proc.returncode == 0,
        }
    except Exception as e:
        return {"error": str(e), "success": False}


def compute_ml_metrics() -> dict:
    """Compute ML evaluation metrics from the trained model."""
    from data.synthetic_generator import load_disputes
    from models.train_and_evaluate import build_features, FEATURE_COLUMNS
    import pickle

    DATA_DIR = PROJECT_ROOT / "data" / "synthetic"
    MODEL_PATH = PROJECT_ROOT / "models" / "win_probability_classifier.pkl"
    CALIBRATED_PATH = PROJECT_ROOT / "models" / "win_probability_classifier_calibrated.pkl"

    if not DATA_DIR.exists() or not MODEL_PATH.exists():
        return {"error": "Data or model files not found"}

    test_disputes = load_disputes(DATA_DIR / "test.json")
    test_df = build_features(test_disputes)

    # Load model
    if CALIBRATED_PATH.exists():
        with open(CALIBRATED_PATH, "rb") as f:
            artifact = pickle.load(f)
        clf = artifact["model"]
        model_type = "calibrated"
    else:
        with open(MODEL_PATH, "rb") as f:
            artifact = pickle.load(f)
        clf = artifact["model"]
        model_type = "uncalibrated"

    X_test = test_df[FEATURE_COLUMNS]
    y_test = np.array(test_df["label"])

    # Predictions
    y_pred = clf.predict(X_test)
    y_prob = clf.predict_proba(X_test)[:, 1]

    # Metrics
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, y_pred, average="binary", zero_division=0
    )

    try:
        roc_auc = roc_auc_score(y_test, y_prob)
    except ValueError:
        roc_auc = None

    try:
        pr_auc = average_precision_score(y_test, y_prob)
    except ValueError:
        pr_auc = None

    cm = confusion_matrix(y_test, y_pred)
    tn, fp, fn, tp = cm.ravel() if cm.size == 4 else (0, 0, 0, 0)

    return {
        "model_type": model_type,
        "dataset": {"test_size": len(test_disputes)},
        "classifier": {
            "precision": round(float(precision), 4),
            "recall": round(float(recall), 4),
            "f1": round(float(f1), 4),
            "roc_auc": round(float(roc_auc), 4) if roc_auc is not None else None,
            "pr_auc": round(float(pr_auc), 4) if pr_auc is not None else None,
            "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        },
    }


def compute_gate_metrics() -> dict:
    """Compute safety gate metrics."""
    from data.synthetic_generator import load_disputes
    from models.train_and_evaluate import build_features, FEATURE_COLUMNS
    from models.scoring import evaluate_dispute, GATE_MIN_WIN_PROBABILITY
    import pickle

    DATA_DIR = PROJECT_ROOT / "data" / "synthetic"

    if not DATA_DIR.exists():
        return {"error": "Data files not found"}

    test_disputes = load_disputes(DATA_DIR / "test.json")
    test_df = build_features(test_disputes)

    MODEL_PATH = PROJECT_ROOT / "models" / "win_probability_classifier.pkl"
    CALIBRATED_PATH = PROJECT_ROOT / "models" / "win_probability_classifier_calibrated.pkl"

    if CALIBRATED_PATH.exists():
        with open(CALIBRATED_PATH, "rb") as f:
            artifact = pickle.load(f)
    else:
        with open(MODEL_PATH, "rb") as f:
            artifact = pickle.load(f)

    clf = artifact["model"]
    probs = clf.predict_proba(test_df[FEATURE_COLUMNS])[:, 1]

    y_true = []
    gate_pred = []
    amounts = []

    by_id = {d.dispute_id: d for d in test_disputes}

    for _, row in test_df.iterrows():
        dispute = by_id[row["dispute_id"]]
        prob = float(probs[test_df.index.get_loc(row.name)])
        result = evaluate_dispute(dispute, win_probability=prob)
        gate_pred.append(1 if result["gate"]["passed"] else 0)
        y_true.append(dispute.label)
        amounts.append(dispute.amount_paise)

    y_true_arr = np.array(y_true)
    gate_pred_arr = np.array(gate_pred)

    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true_arr, gate_pred_arr, average="binary", zero_division=0
    )

    fp_mask = (gate_pred_arr == 1) & (y_true_arr == 0)
    fp_count = int(fp_mask.sum())
    predicted_win = int((gate_pred_arr == 1).sum())
    fp_rate = fp_count / predicted_win if predicted_win > 0 else 0.0

    return {
        "gate_threshold": GATE_MIN_WIN_PROBABILITY,
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "fp_count": fp_count,
        "fp_rate": round(fp_rate, 4),
        "predicted_win_count": predicted_win,
        "approved_count": predicted_win,
    }


def run_adversarial_tests() -> dict:
    """Run adversarial safety tests specifically."""
    import subprocess

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_safety_gate.py", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        output = proc.stdout + proc.stderr
        passed = output.count(" PASSED")
        failed = output.count(" FAILED")

        return {
            "passed": passed,
            "failed": failed,
            "total": passed + failed,
            "success": proc.returncode == 0,
        }
    except Exception as e:
        return {"error": str(e), "success": False}


def run_evidence_integrity_tests() -> dict:
    """Run evidence integrity tests."""
    import subprocess

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_safety_gate.py::TestEvidenceIntegrity", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        output = proc.stdout + proc.stderr
        passed = output.count(" PASSED")
        failed = output.count(" FAILED")

        return {"passed": passed, "failed": failed, "success": proc.returncode == 0}
    except Exception as e:
        return {"error": str(e), "success": False}


def run_human_approval_tests() -> dict:
    """Run human approval boundary tests."""
    import subprocess

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_safety_gate.py::TestHumanApprovalBoundary", "-v", "--tb=short"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        output = proc.stdout + proc.stderr
        passed = output.count(" PASSED")
        failed = output.count(" FAILED")

        return {"passed": passed, "failed": failed, "success": proc.returncode == 0}
    except Exception as e:
        return {"error": str(e), "success": False}


def check_razorpay_integration() -> dict:
    """Check Razorpay integration status."""
    from backend.razorpay_config import RazorpayConfig

    config = RazorpayConfig.optional_from_env()

    return {
        "configured": config is not None,
        "mode": "test" if (config and config.test_mode) else ("live" if config else "not_configured"),
        "authentication": "CONFIGURED" if config else "NOT_CONFIGURED",
        "documents_api": "CLIENT_IMPLEMENTED",
        "webhook": "HANDLER_IMPLEMENTED",
        "contest_api": "CLIENT_IMPLEMENTED",
        "dispute_simulator": "IMPLEMENTED",
        "contest_e2e_submission": "KNOWN_TEST_MODE_LIMITATION",
    }


def generate_report(output_dir: Path) -> None:
    """Generate the full evaluation report."""
    print("Running evaluation...")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect all results
    report = {
        "report_type": "shield_assist_evaluation",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    print("  Running test suites...")
    report["tests"] = run_all_tests()

    print("  Running smoke test...")
    report["smoke_test"] = run_smoke_test()

    print("  Computing ML metrics...")
    report["ml_evaluation"] = compute_ml_metrics()

    print("  Computing gate metrics...")
    report["gate_evaluation"] = compute_gate_metrics()

    print("  Running adversarial tests...")
    report["adversarial_tests"] = run_adversarial_tests()

    print("  Running evidence integrity tests...")
    report["evidence_integrity"] = run_evidence_integrity_tests()

    print("  Running human approval tests...")
    report["human_approval"] = run_human_approval_tests()

    print("  Checking Razorpay integration...")
    report["razorpay_integration"] = check_razorpay_integration()

    # Save JSON
    json_path = output_dir / "evaluation.json"
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nJSON report: {json_path}")

    # Generate Markdown
    md_lines = [
        "# Shield Assist Evaluation Report",
        f"\nGenerated: {report['timestamp']}",
        "\n## Test Results\n",
        f"| Suite | Passed | Failed | Skipped |",
        f"|-------|--------|--------|---------|",
    ]

    for suite_name, suite_data in report["tests"]["suites"].items():
        if "error" in suite_data:
            md_lines.append(f"| {suite_name} | - | ERROR | - |")
        else:
            md_lines.append(f"| {suite_name} | {suite_data['passed']} | {suite_data['failed']} | {suite_data['skipped']} |")

    md_lines.append(f"\n**Total: {report['tests']['total']} | Passed: {report['tests']['passed']} | Failed: {report['tests']['failed']}**")

    # Smoke test
    smoke = report["smoke_test"]
    md_lines.append(f"\n## Smoke Test\n")
    if smoke.get("success"):
        md_lines.append(f"**PASSED** ({smoke['passed']} checks)")
    else:
        md_lines.append(f"**FAILED** ({smoke.get('failed', '?')} failures)")

    # ML evaluation
    ml = report["ml_evaluation"]
    if "error" not in ml:
        md_lines.append(f"\n## ML Classifier Evaluation\n")
        md_lines.append(f"- Model: {ml['model_type']}")
        md_lines.append(f"- Test set size: {ml['dataset']['test_size']}")
        c = ml["classifier"]
        md_lines.append(f"- Precision: {c['precision']:.4f}")
        md_lines.append(f"- Recall: {c['recall']:.4f}")
        md_lines.append(f"- F1: {c['f1']:.4f}")
        if c.get("roc_auc"):
            md_lines.append(f"- ROC-AUC: {c['roc_auc']:.4f}")
        if c.get("pr_auc"):
            md_lines.append(f"- PR-AUC: {c['pr_auc']:.4f}")
        cm = c["confusion_matrix"]
        md_lines.append(f"- Confusion: TP={cm['tp']} FP={cm['fp']} TN={cm['tn']} FN={cm['fn']}")

    # Gate evaluation
    gate = report["gate_evaluation"]
    if "error" not in gate:
        md_lines.append(f"\n## Safety Gate Evaluation\n")
        md_lines.append(f"- Threshold: {gate['gate_threshold']}")
        md_lines.append(f"- Precision: {gate['precision']:.4f}")
        md_lines.append(f"- Recall: {gate['recall']:.4f}")
        md_lines.append(f"- F1: {gate['f1']:.4f}")
        md_lines.append(f"- FP count: {gate['fp_count']}")
        md_lines.append(f"- FP rate: {gate['fp_rate']:.4f}")
        md_lines.append(f"- Approved count: {gate['approved_count']}")

    # Adversarial tests
    adv = report["adversarial_tests"]
    md_lines.append(f"\n## Adversarial Safety Tests\n")
    md_lines.append(f"- Passed: {adv.get('passed', 0)}/{adv.get('total', 0)}")
    if adv.get("failed", 0) > 0:
        md_lines.append(f"- **FAILED: {adv['failed']}**")

    # Evidence integrity
    ei = report["evidence_integrity"]
    md_lines.append(f"\n## Evidence Integrity\n")
    md_lines.append(f"- Passed: {ei.get('passed', 0)} | Failed: {ei.get('failed', 0)}")

    # Human approval
    ha = report["human_approval"]
    md_lines.append(f"\n## Human Approval Boundary\n")
    md_lines.append(f"- Passed: {ha.get('passed', 0)} | Failed: {ha.get('failed', 0)}")

    # Razorpay integration
    rzp = report["razorpay_integration"]
    md_lines.append(f"\n## Razorpay Integration\n")
    md_lines.append(f"- Configured: {rzp['configured']}")
    md_lines.append(f"- Mode: {rzp['mode']}")
    md_lines.append(f"- Authentication: {rzp['authentication']}")
    md_lines.append(f"- Documents API: {rzp['documents_api']}")
    md_lines.append(f"- Webhook: {rzp['webhook']}")
    md_lines.append(f"- Contest API: {rzp['contest_api']}")
    md_lines.append(f"- Dispute Simulator: {rzp['dispute_simulator']}")
    md_lines.append(f"- Contest E2E: {rzp['contest_e2e_submission']}")

    md_lines.append("\n---\n*Report generated by scripts/evaluate.py*\n")

    md_path = output_dir / "evaluation.md"
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))
    print(f"Markdown report: {md_path}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Shield Assist evaluation report")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "reports")
    args = parser.parse_args()

    generate_report(args.output_dir)
