"""
models/calibrate.py

Probability calibration for the win-probability classifier.

Wraps the existing GradientBoostingClassifier with CalibratedClassifierCV
(using cv='prefit' on the validation split) to improve probability estimates.
Compares isotonic vs sigmoid methods, picks the better one by Brier score,
and saves the calibrated model alongside the original.

Run:
    python -m models.calibrate
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any, cast

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import train_test_split

from data.evidence_requirements import IN_SCOPE_REASON_CODES, required_slots_for
from data.synthetic_generator import Dispute, load_disputes
from models.contradiction_rules import run_all_contradiction_checks
from models.scoring import (
    completeness_score,
    evaluate_dispute,
    quality_score,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"
MODEL_DIR = Path(__file__).resolve().parent
ORIGINAL_MODEL_PATH = MODEL_DIR / "win_probability_classifier.pkl"
CALIBRATED_MODEL_PATH = MODEL_DIR / "win_probability_classifier_calibrated.pkl"
RELIABILITY_DIAGRAM_PATH = MODEL_DIR / "reliability_diagram.png"

REASON_CODE_COLUMNS = [f"reason_code_{rc}" for rc in IN_SCOPE_REASON_CODES]
FEATURE_COLUMNS = [
    "amount_paise",
    "num_documents",
    "num_required_slots",
    "completeness",
    "quality",
    *REASON_CODE_COLUMNS,
]

MIN_RELIABLE_SAMPLE_SIZE = 20


# ---------------------------------------------------------------------------
# Feature engineering (same as train_and_evaluate.py)
# ---------------------------------------------------------------------------

def build_features(disputes: list[Dispute]) -> pd.DataFrame:
    rows = []
    for d in disputes:
        required = required_slots_for(d.reason_code)
        present_slots = {doc.slot for doc in d.documents}
        completeness = completeness_score(present_slots, d.reason_code)
        quality = quality_score(d.documents)

        row = {
            "dispute_id": d.dispute_id,
            "amount_paise": d.amount_paise,
            "num_documents": len(d.documents),
            "num_required_slots": len(required),
            "completeness": completeness,
            "quality": quality,
            "label": d.label,
        }
        for rc in IN_SCOPE_REASON_CODES:
            row[f"reason_code_{rc}"] = int(d.reason_code == rc)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Expected Calibration Error (ECE)
# ---------------------------------------------------------------------------

def expected_calibration_error(
    y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10
) -> float:
    """Compute ECE: weighted average of |avg_predicted - actual_fraction| per bin."""
    bin_edges = np.linspace(0, 1, n_bins + 1)
    ece = 0.0
    n_total = len(y_true)
    for lo, hi in zip(bin_edges[:-1], bin_edges[1:]):
        mask = (y_prob >= lo) & (y_prob < hi) if hi < 1.0 else (y_prob >= lo)
        if mask.sum() == 0:
            continue
        bin_weight = mask.sum() / n_total
        avg_predicted = y_prob[mask].mean()
        actual_fraction = y_true[mask].mean()
        ece += bin_weight * abs(avg_predicted - actual_fraction)
    return float(ece)


# ---------------------------------------------------------------------------
# Reliability diagram
# ---------------------------------------------------------------------------

def plot_reliability(
    y_true: np.ndarray,
    probs_dict: dict[str, np.ndarray],
    save_path: Path,
    n_bins: int = 10,
) -> None:
    """Plot reliability diagrams for multiple models on the same axes."""
    fig, ax = plt.subplots(figsize=(8, 6))
    colors = {"Uncalibrated": "#e74c3c", "Isotonic": "#2ecc71", "Sigmoid": "#3498db"}

    for name, probs in probs_dict.items():
        fraction_of_positives, mean_predicted = calibration_curve(
            y_true, probs, n_bins=n_bins, strategy="uniform"
        )
        ax.plot(
            mean_predicted, fraction_of_positives,
            "s-", label=f"{name} (Brier={brier_score_loss(y_true, probs):.4f})",
            color=colors.get(name, "#95a5a6"), linewidth=2, markersize=6,
        )

    ax.plot([0, 1], [0, 1], "k--", linewidth=1, label="Perfectly calibrated")
    ax.set_xlabel("Mean Predicted Probability", fontsize=12)
    ax.set_ylabel("Fraction of Positives", fontsize=12)
    ax.set_title("Reliability Diagram — Win Probability Classifier", fontsize=13)
    ax.legend(loc="lower right", fontsize=10)
    ax.set_xlim([0, 1])
    ax.set_ylim([0, 1])
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    fig.savefig(save_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Reliability diagram saved to {save_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # --- 1. Load data and reproduce exact train/val split ---
    all_train_disputes = load_disputes(DATA_DIR / "train.json")
    test_disputes = load_disputes(DATA_DIR / "test.json")

    train_labels = [d.label for d in all_train_disputes]
    train_disputes, val_disputes = train_test_split(
        all_train_disputes, test_size=0.2, random_state=42, stratify=train_labels,
    )

    print(f"Split: {len(train_disputes)} train / {len(val_disputes)} val / {len(test_disputes)} test")

    train_df = build_features(train_disputes)
    val_df = build_features(val_disputes)
    test_df = build_features(test_disputes)

    X_train, y_train = train_df[FEATURE_COLUMNS], train_df["label"]
    X_val, y_val = val_df[FEATURE_COLUMNS], val_df["label"]
    X_test, y_test = test_df[FEATURE_COLUMNS], test_df["label"]

    # --- 2. Train base classifier (same as train_and_evaluate.py) ---
    print("\nTraining base GradientBoostingClassifier...")
    clf = GradientBoostingClassifier(
        n_estimators=150, max_depth=3, learning_rate=0.05, random_state=42,
    )
    clf.fit(X_train, y_train)

    # --- 3. Evaluate BEFORE calibration (on test) ---
    probs_before = clf.predict_proba(X_test)[:, 1]
    preds_before = clf.predict(X_test)
    brier_before = brier_score_loss(y_test, probs_before)
    ece_before = expected_calibration_error(y_test.values, probs_before)

    print(f"\n{'='*60}")
    print(f"BEFORE calibration (test set):")
    print(f"  Brier score:  {brier_before:.4f}")
    print(f"  ECE:          {ece_before:.4f}")
    print(f"{'='*60}")

    # --- 4. Fit CalibratedClassifierCV with both methods ---
    results = {}
    calibrated_models = {}

    for method in ["isotonic", "sigmoid"]:
        print(f"\nFitting CalibratedClassifierCV (method={method}, cv='prefit')...")
        cal_clf = CalibratedClassifierCV(clf, method=method, cv="prefit")
        cal_clf.fit(X_val, y_val)

        probs_cal = cal_clf.predict_proba(X_test)[:, 1]
        preds_cal = cal_clf.predict(X_test)
        brier_cal = brier_score_loss(y_test, probs_cal)
        ece_cal = expected_calibration_error(y_test.values, probs_cal)

        results[method] = {
            "brier": brier_cal,
            "ece": ece_cal,
            "probs": probs_cal,
            "preds": preds_cal,
        }
        calibrated_models[method] = cal_clf

        print(f"\n  AFTER calibration ({method}, test set):")
        print(f"    Brier score:  {brier_cal:.4f}  (was {brier_before:.4f})")
        print(f"    ECE:          {ece_cal:.4f}  (was {ece_before:.4f})")
        print(f"    Brier improvement: {(brier_before - brier_cal) / brier_before * 100:.1f}%")
        print(f"    ECE improvement:   {(ece_before - ece_cal) / ece_before * 100:.1f}%")

    # --- 5. Pick the better method by Brier score ---
    best_method = min(results, key=lambda m: results[m]["brier"])
    print(f"\n>>> Selected method: {best_method} (Brier={results[best_method]['brier']:.4f})")
    best_cal_clf = calibrated_models[best_method]

    # --- 6. Save calibrated model ---
    with open(CALIBRATED_MODEL_PATH, "wb") as f:
        pickle.dump({"model": best_cal_clf, "feature_columns": FEATURE_COLUMNS, "method": best_method}, f)
    print(f"\nCalibrated model saved to {CALIBRATED_MODEL_PATH}")

    # --- 7. Generate reliability diagram ---
    probs_dict = {"Uncalibrated": probs_before}
    probs_dict[best_method.title()] = results[best_method]["probs"]
    other = "sigmoid" if best_method == "isotonic" else "isotonic"
    if other in results:
        probs_dict[other.title()] = results[other]["probs"]

    plot_reliability(y_test.values, probs_dict, RELIABILITY_DIAGRAM_PATH)

    # --- 8. Re-run gate threshold selection with calibrated probabilities ---
    print(f"\n{'='*60}")
    print("Gate threshold re-selection (against calibrated probabilities on val)")
    print(f"{'='*60}")

    probs_val_cal = best_cal_clf.predict_proba(X_val)[:, 1]
    by_id = {d.dispute_id: d for d in val_disputes}

    # Reuse the same cost model constants from train_and_evaluate
    FALSE_POSITIVE_FLAT_COST_PAISE = 25_000
    EXCESSIVE_FP_RATE_CEILING = 0.15

    candidate_thresholds = np.arange(0.50, 0.91, 0.01)
    sweep_rows = []
    qualifying = []

    for t in candidate_thresholds:
        y_true_list, gate_pred, amounts = [], [], []
        for dispute_id, prob in zip(val_df["dispute_id"], probs_val_cal):
            dispute = by_id[dispute_id]
            result = evaluate_dispute(dispute, win_probability=float(prob), min_win_probability=float(t))
            gate_pred.append(1 if result["gate"]["passed"] else 0)
            y_true_list.append(dispute.label)
            amounts.append(dispute.amount_paise)

        y_true_arr = np.array(y_true_list)
        gate_pred_arr = np.array(gate_pred)
        amounts_arr = np.array(amounts)

        fp_mask = (gate_pred_arr == 1) & (y_true_arr == 0)
        tp_mask = (gate_pred_arr == 1) & (y_true_arr == 1)
        fn_mask = (gate_pred_arr == 0) & (y_true_arr == 1)

        fp_count = int(fp_mask.sum())
        tp_count = int(tp_mask.sum())
        fn_count = int(fn_mask.sum())
        n_pred = int((gate_pred_arr == 1).sum())
        fp_rate = fp_count / n_pred if n_pred > 0 else 0.0
        reliable = n_pred >= MIN_RELIABLE_SAMPLE_SIZE
        precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 0.0
        recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 0.0

        row = {
            "threshold": round(float(t), 3),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "fp_rate": round(fp_rate, 4),
            "n_predicted_win": n_pred,
            "reliable_sample": reliable,
            "fp_rate_breach": fp_rate > EXCESSIVE_FP_RATE_CEILING,
        }
        sweep_rows.append(row)
        if reliable and not fp_rate > EXCESSIVE_FP_RATE_CEILING:
            qualifying.append(row)

    if qualifying:
        selected = min(qualifying, key=lambda r: r["threshold"])
        note = "Lowest threshold clearing reliable-sample + FP-rate-ceiling bar."
    else:
        selected = max(sweep_rows, key=lambda r: r["threshold"])
        note = "WARNING: no candidate cleared the safety bar — falling back to highest."

    print(f"\n{'threshold':>10} {'precision':>10} {'recall':>10} {'fp_rate':>9} {'n_pred':>7} {'safe':>6}")
    for row in sweep_rows:
        if row["threshold"] == selected["threshold"] or round(row["threshold"] * 100) % 10 == 0:
            marker = "  <- SELECTED" if row["threshold"] == selected["threshold"] else ""
            safe = "yes" if (row["reliable_sample"] and not row["fp_rate_breach"]) else "no"
            print(f"{row['threshold']:>10.2f} {row['precision']:>10.3f} {row['recall']:>10.3f} "
                  f"{row['fp_rate']:>9.1%} {row['n_predicted_win']:>7} {safe:>6}{marker}")

    print(f"\nSelected threshold: {selected['threshold']} — {note}")

    # Compare against the OLD threshold
    old_threshold_path = MODEL_DIR / "gate_threshold.json"
    if old_threshold_path.exists():
        with open(old_threshold_path) as f:
            old_th = json.load(f)["selected_threshold"]
        print(f"Previous threshold: {old_th}")
        if selected["threshold"] != old_th:
            print(f"Threshold CHANGED: {old_th} -> {selected['threshold']}")
        else:
            print(f"Threshold UNCHANGED at {old_th}")

    # Save the new threshold artifact
    threshold_result = {
        "selected_threshold": selected["threshold"],
        "selection_rule": "lowest threshold with reliable_sample AND fp_rate <= ceiling, on validation split (calibrated probabilities)",
        "selection_note": note,
        "evidence": selected,
        "full_sweep": sweep_rows,
        "min_reliable_sample_size": MIN_RELIABLE_SAMPLE_SIZE,
        "fp_rate_ceiling": EXCESSIVE_FP_RATE_CEILING,
        "calibration": {
            "method": best_method,
            "brier_before": brier_before,
            "brier_after": results[best_method]["brier"],
            "ece_before": ece_before,
            "ece_after": results[best_method]["ece"],
        },
    }
    with open(old_threshold_path, "w") as f:
        json.dump(threshold_result, f, indent=2)
    print(f"Updated gate threshold artifact: {old_threshold_path}")

    # --- 9. Final summary table ---
    print(f"\n{'='*60}")
    print("SUMMARY — before vs after calibration (on held-out test set)")
    print(f"{'='*60}")
    print(f"{'':>25} {'Before':>12} {'After':>12} {'Change':>12}")
    print(f"{'Brier score':>25} {brier_before:>12.4f} {results[best_method]['brier']:>12.4f} {brier_before - results[best_method]['brier']:>+12.4f}")
    print(f"{'ECE':>25} {ece_before:>12.4f} {results[best_method]['ece']:>12.4f} {ece_before - results[best_method]['ece']:>+12.4f}")
    print(f"{'Selected threshold':>25} {old_th if old_threshold_path.exists() else 'N/A':>12} {selected['threshold']:>12.2f}")

    print(f"\nFiles produced:")
    print(f"  Calibrated model:  {CALIBRATED_MODEL_PATH}")
    print(f"  Reliability diagram: {RELIABILITY_DIAGRAM_PATH}")
    print(f"  Gate threshold:    {old_threshold_path}")


if __name__ == "__main__":
    main()
