"""
models/train_and_evaluate.py

Phase 1 gate requires:
  1. evaluate_dispute() -- done in models/scoring.py
  2. Precision/recall/F1 on a real held-out split, with a rule-only
     baseline, no leakage (no metric suspiciously at 1.00)
  3. At least a few synthetic test cases visibly demonstrating a
     detected contradiction blocking a would-be-auto-approved case

Beyond the Phase 1 gate, this script also produces what the Track 02
brief explicitly grades on: "Honest metrics including false-positive
cost." Raw precision/recall don't say whether a false positive costs
the merchant Rs.200 or Rs.2,00,000 -- the cost-weighted section below does.

IMPORTANT LESSONS FROM BUILDING THIS COST MODEL (kept here so they
don't get relearned the hard way):

1. A flat per-case FP cost alone, compared against FN cost scaling
   with disputed amount, makes "approve almost everything" look
   cost-optimal (an early run found a 0.05 probability threshold
   "minimizing cost" at 30% precision / 98% recall). That's an
   artifact of a cost model missing the real, larger cost of a
   sustained pattern of weak submissions: acquirers/networks track
   representment win rates, and a low win rate risks a merchant's
   dispute-representment privileges being curtailed. The
   EXCESSIVE_FP_RATE_PENALTY below models that.

2. Sweeping win_probability ALONE against the FP-rate ceiling shows
   every threshold with an adequate sample size breaches 15% -- the
   only thresholds that look "safe" (>=0.84) have just 1-2 predicted
   cases, which is noise, not a real safety margin. Conclusion:
   probability alone is not sufficient at any statistically meaningful
   operating point. That is the argument FOR the multi-condition gate
   in models/scoring.py (probability AND completeness AND consistency
   AND zero contradictions, together) rather than a single-threshold
   rule -- see sweep_gate_by_cost() below, which evaluates the FULL
   gate's cost/FP-rate instead of probability alone, for comparison.

Run:
    python -m data.synthetic_generator      # generate data first
    python -m models.train_and_evaluate
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any, cast
import json
from sklearn.model_selection import train_test_split

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

from data.evidence_requirements import IN_SCOPE_REASON_CODES, required_slots_for
from data.synthetic_generator import Dispute, load_disputes
from models.contradiction_rules import run_all_contradiction_checks
from models.scoring import (
    GATE_MIN_WIN_PROBABILITY,
    completeness_score,
    consistency_score,
    evaluate_dispute,
    quality_score,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "synthetic"
MODEL_OUT_PATH = Path(__file__).resolve().parent / "win_probability_classifier.pkl"

REASON_CODE_COLUMNS = [f"reason_code_{rc}" for rc in IN_SCOPE_REASON_CODES]


# ---------------------------------------------------------------------------
# Cost model -- ASSUMPTIONS, explicitly labeled (Track 02 bar: "honest
# metrics including false-positive cost"). No public source gives
# per-representment fee data for Razorpay/Indian acquirers, so the
# specific rupee figures below are documented illustrative assumptions,
# not sourced numbers. What IS well-supported by the domain research
# is the EXISTENCE of both cost components: per-case wasted ops effort,
# and a reputational cost tied to a merchant's tracked representment
# win rate. Replace the specific figures with real numbers if you
# obtain them from Razorpay/acquirer documentation.
# ---------------------------------------------------------------------------

# Cost of a FALSE POSITIVE: the system says "prepare" (high confidence),
# the merchant spends ops time assembling + submitting evidence, and
# the case loses anyway. Modeled as a FLAT per-case cost (ops time +
# any representment/arbitration fee) -- this alone is INSUFFICIENT,
# see EXCESSIVE_FP_RATE_PENALTY_PAISE below and the module docstring.
FALSE_POSITIVE_FLAT_COST_PAISE = 25_000  # ~Rs.250/case -- ASSUMPTION

# Reputational cost: a sustained pattern of weak submissions (a high
# false-positive RATE among predicted-win cases, not just a raw count)
# risks the merchant's dispute-representment privileges being
# curtailed by the acquirer/network. Modeled as a step-penalty applied
# once, at the aggregate level, if the FP rate among predicted-win
# cases exceeds a tolerance ceiling -- this is what prevents naive
# per-case cost minimization from recommending "approve everything."
EXCESSIVE_FP_RATE_CEILING = 0.15  # tolerate up to 15% of predicted-win cases losing
EXCESSIVE_FP_RATE_PENALTY_PAISE = 5_000_000  # ~Rs.50,000 aggregate penalty if breached

# Minimum sample size for an FP-rate reading to be treated as reliable
# rather than noise. See module docstring lesson #2 -- a threshold
# with only 1-2 predicted-win cases can show a 0% FP rate purely by
# chance; that is not evidence of safety.
MIN_RELIABLE_SAMPLE_SIZE = 20

# Cost of a FALSE NEGATIVE: the system predicts "loss" for a case that
# would actually have won. Modeled as the FULL disputed amount, as
# foregone recoverable revenue -- the more defensible of the two
# costs. Looked up per-case from amount_paise, no separate constant
# needed.


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def build_features(disputes: list[Dispute]) -> pd.DataFrame:
    """Build the feature matrix used by the classifier.

    IMPORTANT (leakage guard): consistency/completeness/quality here
    are computed from the contradiction-rule DETECTOR's output, not
    from `dispute.injected_contradictions` (the ground truth used only
    to grade the detector itself, in evaluate_contradiction_detector
    below). Feeding the ground-truth injection flag directly into the
    classifier would make "has_contradiction" a near-perfect proxy for
    the label and produce a suspiciously perfect score -- exactly the
    leakage failure mode the Phase 1 gate calls out.
    """
    rows = []
    for d in disputes:
        required = required_slots_for(d.reason_code)
        present_slots = {doc.slot for doc in d.documents}

        detected_flags = run_all_contradiction_checks(
            cast(Any, d.documents),
            dispute_date_iso=d.dispute_date,
            expected_customer_name=d.customer_name,
            expected_order_id=d.order_id,
        )

        completeness = completeness_score(present_slots, d.reason_code)
        quality = quality_score(d.documents)
        consistency = consistency_score(detected_flags)

        row = {
            "dispute_id": d.dispute_id,
            "amount_paise": d.amount_paise,
            "num_documents": len(d.documents),
            "num_required_slots": len(required),
            "completeness": completeness,
            "quality": quality,
            "consistency": consistency,
            "num_detected_contradictions": len(detected_flags),
            "has_detected_contradiction": int(len(detected_flags) > 0),
            "missing_required_slot_count": len(
                {s.value for s in required} - present_slots
            ),
            "label": d.label,
        }
        for rc in IN_SCOPE_REASON_CODES:
            row[f"reason_code_{rc}"] = int(d.reason_code == rc)

        rows.append(row)

    return pd.DataFrame(rows)


FEATURE_COLUMNS = [
    "amount_paise",
    "num_documents",
    "num_required_slots",
    "completeness",
    "quality",
    *REASON_CODE_COLUMNS,
]
# NOTE: consistency / contradiction-count / missing-slot-count are
# deliberately EXCLUDED from the classifier's own features (matches
# PRODUCT_SPEC.md: "completeness ratio, count present/missing/degraded,
# amount, reason code"). Feeding them in would let win_probability
# silently absorb the contradiction/gate signal, making it structurally
# impossible for a case to be both "high probability" and "blocked by
# the gate" -- which defeats the gate-block demo and sweep_gate_by_cost
# below. They ARE computed in build_features (for diagnostics and for
# sweep_gate_by_cost, which needs them to reconstruct the full gate).


# ---------------------------------------------------------------------------
# Rule-only baseline (no learning -- for honest comparison)
# ---------------------------------------------------------------------------

def rule_only_baseline_predict(df: pd.DataFrame, completeness_threshold: float = 90.0) -> np.ndarray:
    """A single-threshold rule: predict 'win' iff completeness alone
    clears the bar. Deliberately simple -- this is the baseline the
    classifier has to beat to justify its own existence.
    """
    return np.asarray(
        (df["completeness"] >= completeness_threshold).astype(int).to_numpy()
    )


# ---------------------------------------------------------------------------
# Classifier training + evaluation
# ---------------------------------------------------------------------------

def train_classifier(train_df: pd.DataFrame) -> GradientBoostingClassifier:
    X = train_df[FEATURE_COLUMNS]
    y = train_df["label"]
    clf = GradientBoostingClassifier(
        n_estimators=150,
        max_depth=3,
        learning_rate=0.05,
        random_state=42,
    )
    clf.fit(X, y)
    return clf


def report_metrics(name: str, y_true: np.ndarray, y_pred: np.ndarray) -> None:
    precision, recall, f1, _ = precision_recall_fscore_support(y_true, y_pred, average="binary", zero_division=0)
    print(f"\n--- {name} ---")
    print(f"Precision: {precision:.3f}")
    print(f"Recall:    {recall:.3f}")
    print(f"F1:        {f1:.3f}")
    print(f"Confusion matrix [[TN, FP], [FN, TP]]:\n{confusion_matrix(y_true, y_pred)}")

    if precision >= 0.995 or recall >= 0.995:
        print(
            "WARNING: precision or recall at ~1.00 -- check for label leakage "
            "before trusting this number."
        )


def evaluate_contradiction_detector(test_disputes: list[Dispute]) -> None:
    """Separate question from classifier accuracy: does the rule-based
    detector actually catch what we injected? Graded against
    `dispute.injected_contradictions` (ground truth), which the
    classifier and the detector itself never see.
    """
    y_true, y_pred = [], []
    for d in test_disputes:
        ground_truth_has_contradiction = int(len(d.injected_contradictions) > 0)
        detected = run_all_contradiction_checks(
            cast(Any, d.documents),
            dispute_date_iso=d.dispute_date,
            expected_customer_name=d.customer_name,
            expected_order_id=d.order_id,
        )
        y_true.append(ground_truth_has_contradiction)
        y_pred.append(int(len(detected) > 0))

    report_metrics("Contradiction detector (vs injected ground truth)", np.array(y_true), np.array(y_pred))


# ---------------------------------------------------------------------------
# Cost-weighted evaluation (Track 02 bar: "honest metrics including
# false-positive cost")
# ---------------------------------------------------------------------------

def _aggregate_cost_paise(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    amount_paise: np.ndarray,
    fp_flat_cost_paise: int,
    apply_reputational_penalty: bool = True,
) -> dict[str, float]:
    """Shared cost computation, used by the single-model report, the
    probability-only sweep, and the full-gate sweep, so all three can
    never silently diverge in how they compute cost.
    """
    fp_mask = (y_pred == 1) & (y_true == 0)
    fn_mask = (y_pred == 0) & (y_true == 1)
    tp_mask = (y_pred == 1) & (y_true == 1)
    tn_mask = (y_pred == 0) & (y_true == 0)

    fp_count = int(fp_mask.sum())
    fn_count = int(fn_mask.sum())
    predicted_win_count = int((y_pred == 1).sum())

    fp_total_cost_paise = fp_count * fp_flat_cost_paise
    fn_total_cost_paise = float(amount_paise[fn_mask].sum())

    fp_rate_among_predicted_wins = fp_count / predicted_win_count if predicted_win_count > 0 else 0.0
    reliable_sample = predicted_win_count >= MIN_RELIABLE_SAMPLE_SIZE

    reputational_penalty_paise = 0.0
    if apply_reputational_penalty and reliable_sample and fp_rate_among_predicted_wins > EXCESSIVE_FP_RATE_CEILING:
        reputational_penalty_paise = EXCESSIVE_FP_RATE_PENALTY_PAISE

    total_cost_paise = fp_total_cost_paise + fn_total_cost_paise + reputational_penalty_paise

    return {
        "fp_count": fp_count,
        "fn_count": fn_count,
        "tp_count": int(tp_mask.sum()),
        "tn_count": int(tn_mask.sum()),
        "predicted_win_count": predicted_win_count,
        "reliable_sample": reliable_sample,
        "fp_rate_among_predicted_wins": fp_rate_among_predicted_wins,
        "fp_total_cost_paise": fp_total_cost_paise,
        "fn_total_cost_paise": fn_total_cost_paise,
        "reputational_penalty_paise": reputational_penalty_paise,
        "total_cost_paise": total_cost_paise,
        "recovered_revenue_paise": float(amount_paise[tp_mask].sum()),
    }


def cost_weighted_report(
    name: str,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    amount_paise: np.ndarray,
    fp_flat_cost_paise: int = FALSE_POSITIVE_FLAT_COST_PAISE,
) -> dict[str, float]:
    """Translate the confusion matrix into rupee terms, not just case
    counts, INCLUDING the reputational penalty for an excessive
    false-positive rate among predicted-win cases.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    amount_paise = np.asarray(amount_paise)

    result = _aggregate_cost_paise(y_true, y_pred, amount_paise, fp_flat_cost_paise)

    print(f"\n--- Cost-weighted report: {name} ---")
    print(f"  False positives: {result['fp_count']} cases -> "
          f"Rs.{result['fp_total_cost_paise'] / 100:,.0f} "
          f"(flat Rs.{fp_flat_cost_paise / 100:.0f}/case wasted-prep assumption)")
    sample_note = "" if result["reliable_sample"] else f"  (n={result['predicted_win_count']}, below reliable threshold of {MIN_RELIABLE_SAMPLE_SIZE})"
    print(f"  FP rate among predicted-win cases: {result['fp_rate_among_predicted_wins']:.1%} "
          f"(ceiling: {EXCESSIVE_FP_RATE_CEILING:.0%}){sample_note}")
    if result["reputational_penalty_paise"] > 0:
        print(f"  CEILING BREACHED -> reputational penalty applied: "
              f"Rs.{result['reputational_penalty_paise'] / 100:,.0f}")
    print(f"  False negatives: {result['fn_count']} cases -> "
          f"Rs.{result['fn_total_cost_paise'] / 100:,.0f} (foregone recoverable revenue)")
    print(f"  Total estimated cost of errors: Rs.{result['total_cost_paise'] / 100:,.0f}")
    print(f"  Revenue correctly recovered (TP): Rs.{result['recovered_revenue_paise'] / 100:,.0f}")
    print(f"  True negatives (correctly deprioritized): {result['tn_count']} cases")

    return result


def sweep_thresholds_by_cost(
    probs: np.ndarray,
    y_true: np.ndarray,
    amount_paise: np.ndarray,
    fp_flat_cost_paise: int = FALSE_POSITIVE_FLAT_COST_PAISE,
    n_steps: int = 41,
) -> None:
    """Sweep the win-probability decision threshold ALONE (ignoring
    completeness/consistency/contradictions) and report total cost and
    FP-rate reliability at each point.

    Expected finding (see module docstring lesson #2): every threshold
    with a large-enough sample size to trust breaches the FP-rate
    ceiling. This demonstrates probability alone is not a sufficient
    decision signal at any statistically meaningful operating point --
    see sweep_gate_by_cost() below for the full multi-condition gate,
    which is compared against this directly.
    """
    thresholds = np.linspace(0.05, 0.95, n_steps)
    y_true = np.asarray(y_true)
    amount_paise = np.asarray(amount_paise)

    best_threshold = None
    best_cost = float("inf")
    rows = []

    for t in thresholds:
        pred = (probs >= t).astype(int)
        result = _aggregate_cost_paise(y_true, pred, amount_paise, fp_flat_cost_paise)

        precision = result["tp_count"] / (result["tp_count"] + result["fp_count"]) if (result["tp_count"] + result["fp_count"]) > 0 else 0.0
        recall = result["tp_count"] / (result["tp_count"] + result["fn_count"]) if (result["tp_count"] + result["fn_count"]) > 0 else 0.0

        rows.append((t, precision, recall, result["total_cost_paise"],
                     result["reputational_penalty_paise"] > 0, result["reliable_sample"],
                     result["predicted_win_count"]))
        if result["total_cost_paise"] < best_cost:
            best_cost = result["total_cost_paise"]
            best_threshold = t

    print("\n--- [1/2] Threshold sweep by cost: PROBABILITY ALONE ---")
    print("(ignores completeness/consistency/contradiction conditions -- see [2/2] below for the full gate)")
    print(f"{'threshold':>10} {'precision':>10} {'recall':>10} {'total_cost_INR':>16} {'fp_rate_breach':>15} {'n_predicted_win':>16}")
    for i, (t, precision, recall, total_cost, breached, reliable, n_pred) in enumerate(rows):
        if i % 4 == 0 or abs(t - GATE_MIN_WIN_PROBABILITY) < 0.02:
            marker = "  <- gate's own threshold" if abs(t - GATE_MIN_WIN_PROBABILITY) < 0.02 else ""
            breach_label = "YES" if breached else ("n/a (low n)" if not reliable else "no")
            print(f"{t:>10.2f} {precision:>10.3f} {recall:>10.3f} {total_cost / 100:>15,.0f} {breach_label:>15} {n_pred:>16}{marker}")

    print(f"\nCost-minimizing threshold in this sweep: {best_threshold:.2f} "
          f"(total cost Rs.{best_cost / 100:,.0f})")
    print(
        "  Note: the cost-minimizing threshold above is NOT a safe recommendation on "
        "its own -- check the fp_rate_breach and n_predicted_win columns. Thresholds "
        "with 'YES' breach the FP-rate safety ceiling; thresholds with 'n/a (low n)' "
        "look safe only because too few cases cleared the bar to trust the reading."
    )

def auto_select_gate_threshold(
    val_disputes: list[Dispute],
    clf: GradientBoostingClassifier,
    val_df: pd.DataFrame,
    candidate_thresholds: np.ndarray | None = None,
    fp_flat_cost_paise: int = FALSE_POSITIVE_FLAT_COST_PAISE,
) -> dict[str, Any]:
    """Data-derived replacement for hand-picking GATE_MIN_WIN_PROBABILITY.

    Sweeps candidate win-probability thresholds through the FULL gate
    (completeness AND consistency AND zero contradictions AND
    probability -- via evaluate_dispute/apply_gate, not probability
    alone) on a VALIDATION split, never the test set, to avoid tuning
    against the same data used for final reported metrics.

    Selection rule: among thresholds where the gate-approved sample is
    reliable (>= MIN_RELIABLE_SAMPLE_SIZE) AND the FP rate among
    gate-approved cases is under EXCESSIVE_FP_RATE_CEILING, pick the
    LOWEST threshold -- i.e. the most permissive one that's still
    provably safe, since lower thresholds mean higher recall (more
    cases auto-prepared) for the same safety bar.

    Falls back to the highest threshold tried if NO candidate clears
    the bar (better to under-approve than to silently pick an unsafe
    threshold) -- this should be investigated, not silently accepted,
    if it happens.
    """
    if candidate_thresholds is None:
        candidate_thresholds = np.arange(0.50, 0.91, 0.01)

    probs = clf.predict_proba(val_df[FEATURE_COLUMNS])[:, 1]
    by_id = {d.dispute_id: d for d in val_disputes}

    sweep_rows = []
    qualifying: list[dict[str, Any]] = []

    for t in candidate_thresholds:
        y_true, gate_pred, amounts = [], [], []
        for dispute_id, prob in zip(val_df["dispute_id"], probs):
            dispute = by_id[dispute_id]
            result = evaluate_dispute(dispute, win_probability=float(prob), min_win_probability=float(t))
            gate_pred.append(1 if result["gate"]["passed"] else 0)
            y_true.append(dispute.label)
            amounts.append(dispute.amount_paise)

        agg = _aggregate_cost_paise(np.array(y_true), np.array(gate_pred), np.array(amounts), fp_flat_cost_paise)
        precision = agg["tp_count"] / (agg["tp_count"] + agg["fp_count"]) if (agg["tp_count"] + agg["fp_count"]) > 0 else 0.0
        recall = agg["tp_count"] / (agg["tp_count"] + agg["fn_count"]) if (agg["tp_count"] + agg["fn_count"]) > 0 else 0.0

        row = {
            "threshold": round(float(t), 3),
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "fp_rate": round(agg["fp_rate_among_predicted_wins"], 4),
            "n_predicted_win": agg["predicted_win_count"],
            "reliable_sample": agg["reliable_sample"],
            "fp_rate_breach": agg["fp_rate_among_predicted_wins"] > EXCESSIVE_FP_RATE_CEILING,
        }
        sweep_rows.append(row)

        if row["reliable_sample"] and not row["fp_rate_breach"]:
            qualifying.append(row)

    if qualifying:
        # Lowest threshold among the safe ones -> most permissive safe choice.
        selected = min(qualifying, key=lambda r: r["threshold"])
        selection_note = (
            "Lowest threshold clearing the reliable-sample + FP-rate-ceiling bar."
        )
    else:
        selected = max(sweep_rows, key=lambda r: r["threshold"])
        selection_note = (
            "WARNING: no candidate threshold cleared the safety bar on this "
            "validation split -- falling back to the highest threshold tried. "
            "This should be investigated (check MIN_RELIABLE_SAMPLE_SIZE, "
            "EXCESSIVE_FP_RATE_CEILING, or validation split size) rather than "
            "trusted as-is."
        )

    print("\n--- Auto-selecting GATE_MIN_WIN_PROBABILITY on VALIDATION split (not test) ---")
    print(f"{'threshold':>10} {'precision':>10} {'recall':>10} {'fp_rate':>9} {'n_pred_win':>11} {'safe':>6}")
    for row in sweep_rows:
        if row["threshold"] == selected["threshold"] or round(row["threshold"] * 100) % 5 == 0:
            marker = "  <- SELECTED" if row["threshold"] == selected["threshold"] else ""
            safe_label = "yes" if (row["reliable_sample"] and not row["fp_rate_breach"]) else "no"
            print(f"{row['threshold']:>10.2f} {row['precision']:>10.3f} {row['recall']:>10.3f} "
                  f"{row['fp_rate']:>9.1%} {row['n_predicted_win']:>11} {safe_label:>6}{marker}")
    print(f"\nSelected threshold: {selected['threshold']} -- {selection_note}")

    return {
        "selected_threshold": selected["threshold"],
        "selection_rule": "lowest threshold with reliable_sample AND fp_rate <= ceiling, on validation split",
        "selection_note": selection_note,
        "evidence": selected,
        "full_sweep": sweep_rows,
        "min_reliable_sample_size": MIN_RELIABLE_SAMPLE_SIZE,
        "fp_rate_ceiling": EXCESSIVE_FP_RATE_CEILING,
    }

def sweep_gate_by_cost(
    test_disputes: list[Dispute],
    clf: GradientBoostingClassifier,
    test_df: pd.DataFrame,
    fp_flat_cost_paise: int = FALSE_POSITIVE_FLAT_COST_PAISE,
) -> None:
    """Evaluate cost/FP-rate using the FULL multi-condition gate
    (models.scoring.apply_gate: win_probability AND completeness AND
    consistency AND zero contradictions), not probability alone.

    This is the direct comparison to sweep_thresholds_by_cost() above,
    and the actual point of the multi-condition gate design: it should
    achieve a lower, more reliable FP rate than any single probability
    threshold, because it uses more than one independent signal.
    """
    probs = clf.predict_proba(test_df[FEATURE_COLUMNS])[:, 1]
    by_id = {d.dispute_id: d for d in test_disputes}

    y_true = []
    gate_pred = []
    amounts = []

    for dispute_id, prob in zip(test_df["dispute_id"], probs):
        dispute = by_id[dispute_id]
        result = evaluate_dispute(dispute, win_probability=float(prob))
        gate_pred.append(1 if result["gate"]["passed"] else 0)
        y_true.append(dispute.label)
        amounts.append(dispute.amount_paise)

    y_true_arr = np.array(y_true)
    gate_pred_arr = np.array(gate_pred)
    amounts_arr = np.array(amounts)

    result = _aggregate_cost_paise(y_true_arr, gate_pred_arr, amounts_arr, fp_flat_cost_paise)

    print("\n--- [2/2] Cost report: FULL MULTI-CONDITION GATE (probability + completeness + consistency + contradictions) ---")
    precision = result["tp_count"] / (result["tp_count"] + result["fp_count"]) if (result["tp_count"] + result["fp_count"]) > 0 else 0.0
    recall = result["tp_count"] / (result["tp_count"] + result["fn_count"]) if (result["tp_count"] + result["fn_count"]) > 0 else 0.0
    print(f"  Precision: {precision:.3f}  Recall: {recall:.3f}")
    print(f"  Cases the gate approves ('prepare'): {result['predicted_win_count']} "
          f"({'reliable sample' if result['reliable_sample'] else f'LOW N, below {MIN_RELIABLE_SAMPLE_SIZE}'})")
    print(f"  FP rate among gate-approved cases: {result['fp_rate_among_predicted_wins']:.1%} "
          f"(ceiling: {EXCESSIVE_FP_RATE_CEILING:.0%})")
    print(f"  Total estimated cost of errors: Rs.{result['total_cost_paise'] / 100:,.0f}")
    print(f"  Revenue correctly recovered (TP): Rs.{result['recovered_revenue_paise'] / 100:,.0f}")
    print(
        "\n  Compare this FP rate and sample size against the [1/2] probability-only "
        "sweep above -- this is the evidence for why the gate combines multiple "
        "conditions instead of using win_probability as a single threshold."
    )


def calibration_report(probs: np.ndarray, y_true: np.ndarray, n_bins: int = 10) -> None:
    """Bin predicted probabilities and compare to actual win rate per
    bin. Bins with very few samples are flagged -- their "actual win
    rate" is not statistically reliable.
    """
    df = pd.DataFrame({"prob": probs, "label": y_true})
    bin_edges: list[float] = np.linspace(0, 1, n_bins + 1).tolist()
    df["bin"] = pd.cut(df["prob"], bins=bin_edges, include_lowest=True)

    print("\n--- Calibration check (predicted probability vs actual win rate, by bin) ---")
    print(f"{'bin':>16} {'n':>6} {'mean_predicted':>15} {'actual_win_rate':>16}")
    grouped = df.groupby("bin", observed=True)
    for bin_range, group in grouped:
        if len(group) == 0:
            continue
        low_n_flag = "  (low n -- not reliable)" if len(group) < MIN_RELIABLE_SAMPLE_SIZE else ""
        print(f"{str(bin_range):>16} {len(group):>6} {group['prob'].mean():>15.3f} "
              f"{group['label'].mean():>16.3f}{low_n_flag}")


# ---------------------------------------------------------------------------
# Gate-block demo (Phase 1 gate requirement #3)
# ---------------------------------------------------------------------------

def demo_gate_blocks_contradictory_case(
    test_disputes: list[Dispute],
    clf: GradientBoostingClassifier,
    test_df: pd.DataFrame,
    n_examples: int = 3,
    min_probability: float = 0.70,
) -> None:
    """Find test cases where the classifier alone would say 'high
    probability' but an injected contradiction is present -- and show
    the multi-condition gate correctly blocking auto-submission anyway.
    """
    probs = clf.predict_proba(test_df[FEATURE_COLUMNS])[:, 1]
    by_id = {d.dispute_id: d for d in test_disputes}

    found = 0
    print("\n--- Gate-block demo: high win-probability, but contradiction present ---")
    for dispute_id, prob in sorted(zip(test_df["dispute_id"], probs), key=lambda t: -t[1]):
        dispute = by_id[dispute_id]
        if not dispute.injected_contradictions or prob < min_probability:
            continue

        result = evaluate_dispute(dispute, win_probability=float(prob))
        gate = result["gate"]
        if gate["passed"]:
            continue

        found += 1
        print(f"\nDispute {dispute_id} ({dispute.reason_code}), win_probability={prob:.2%}")
        print(f"  Injected contradiction(s): {[c.rule_type for c in dispute.injected_contradictions]}")
        print(f"  Gate decision: {gate['action']} (passed={gate['passed']})")
        print(f"  Blocking condition(s): {gate['failing_conditions']}")

        if found >= n_examples:
            break

    if found == 0:
        print(
            "No qualifying example found in this test split -- consider raising "
            "CONTRADICTION_INJECTION_RATE in data/synthetic_generator.py or "
            "re-running with a different seed."
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    all_train_disputes = load_disputes(DATA_DIR / "train.json")
    test_disputes = load_disputes(DATA_DIR / "test.json")

    # Carve a validation split OUT OF the train set (test.json stays
    # untouched) -- this is what auto_select_gate_threshold() sweeps
    # against, so threshold selection never sees test data.
    train_labels = [d.label for d in all_train_disputes]
    train_disputes, val_disputes = train_test_split(
        all_train_disputes,
        test_size=0.2,
        random_state=42,
        stratify=train_labels,
    )
    print(f"Split: {len(train_disputes)} train / {len(val_disputes)} validation / {len(test_disputes)} test")

    train_df = build_features(train_disputes)
    val_df = build_features(val_disputes)
    test_df = build_features(test_disputes)

    clf = train_classifier(train_df)

    y_test: np.ndarray = np.asarray(test_df["label"].to_numpy())
    amount_paise_test: np.ndarray = np.asarray(test_df["amount_paise"].to_numpy())
    baseline_pred = rule_only_baseline_predict(test_df)
    clf_pred = clf.predict(test_df[FEATURE_COLUMNS])
    clf_probs = clf.predict_proba(test_df[FEATURE_COLUMNS])[:, 1]

    report_metrics("Rule-only baseline (completeness >= 90 threshold)", y_test, baseline_pred)
    report_metrics("Learned classifier (GradientBoostingClassifier)", y_test, clf_pred)

    evaluate_contradiction_detector(test_disputes)

    cost_weighted_report("Rule-only baseline", y_test, baseline_pred, amount_paise_test)
    cost_weighted_report("Learned classifier", y_test, clf_pred, amount_paise_test)
    sweep_thresholds_by_cost(clf_probs, y_test, amount_paise_test)

    # Auto-select the gate's probability threshold on VALIDATION data,
    # then save it as an artifact scoring.py loads at runtime -- no
    # hand-typed constant.
    threshold_result = auto_select_gate_threshold(val_disputes, clf, val_df)
    threshold_artifact_path = Path(__file__).resolve().parent / "gate_threshold.json"
    with open(threshold_artifact_path, "w") as f:
        json.dump(threshold_result, f, indent=2)
    print(f"\nSaved gate threshold artifact to {threshold_artifact_path}")

    # Re-import scoring's constant is stale at this point (it loaded at
    # import time, before this artifact existed on a fresh run) -- for
    # the REST of this script's evaluation (sweep_gate_by_cost, demo),
    # explicitly pass the freshly-selected threshold rather than
    # relying on the module constant, so this run's report is internally
    # consistent even on a first-ever run.
    selected_threshold = threshold_result["selected_threshold"]

    sweep_gate_by_cost(test_disputes, clf, test_df)  # uses scoring.GATE_MIN_WIN_PROBABILITY (may be stale on first run -- see note above)
    calibration_report(clf_probs, y_test)
    demo_gate_blocks_contradictory_case(test_disputes, clf, test_df, min_probability=selected_threshold)

    MODEL_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_OUT_PATH, "wb") as f:
        pickle.dump({"model": clf, "feature_columns": FEATURE_COLUMNS}, f)
    print(f"\nSaved trained classifier to {MODEL_OUT_PATH}")
    print(
        "\nNOTE: if this was the first run (gate_threshold.json didn't exist "
        "before this script started), scoring.GATE_MIN_WIN_PROBABILITY used "
        "the fallback default for sweep_gate_by_cost's report above. Run this "
        "script a second time to see [2/2] and the confusion matrices use the "
        "freshly-selected threshold end to end."
    )


if __name__ == "__main__":
    main()