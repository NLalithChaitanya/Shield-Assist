"""
backend/jobs/score_job.py

'score.case' job handler — the heart of the async pipeline.

Extracts the Phase 1 scoring logic that was previously inline in the
ingest route (backend/app.py v1) and runs it inside a worker thread.
The scoring pipeline itself is unchanged:

  1. load_dispute_for_scoring() -> domain.Dispute
  2. _predict_win_probability() -> float (via trained classifier)
  3. evaluate_dispute() -> scores dict + gate decision
  4. _compute_priority() -> float
  5. Persist scores + decision + audit via repository

Zero-document cases score completeness=0 and gate to 'review' or
'low_priority' — this is a valid, common state (Section 5.1).
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from backend.dispute_normalizer import (
    IN_SCOPE_REASON_CODES,
    load_dispute_for_scoring,
)
from backend.domain import Dispute
from backend.job_queue import register_handler
from backend.repository import CaseRepository
from data.evidence_requirements import required_slots_for
from models.scoring import (
    completeness_score,
    evaluate_dispute,
    quality_score,
)

logger = logging.getLogger("shield_assist.jobs.score")

# ---------------------------------------------------------------------------
# Classifier (loaded once, shared across all job invocations)
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODEL_PATH = BASE_DIR / "models" / "win_probability_classifier.pkl"
CALIBRATED_MODEL_PATH = BASE_DIR / "models" / "win_probability_classifier_calibrated.pkl"

_clf: Any = None
_clf_features: list[str] | None = None
_clf_loaded = False


def _ensure_classifier_loaded() -> None:
    """Lazily load the trained classifier on first use.

    Prefers the calibrated model (isotonic regression wrapper) if
    available, falling back to the uncalibrated base classifier.
    Both expose the same predict_proba() interface.

    On failure, logs the error and leaves _clf=None so the next call
    retries (instead of permanently disabling the classifier).
    """
    global _clf, _clf_features, _clf_loaded
    if _clf_loaded:
        return

    # Prefer calibrated model (better probability estimates)
    if CALIBRATED_MODEL_PATH.exists():
        try:
            with open(CALIBRATED_MODEL_PATH, "rb") as f:
                artifact = pickle.load(f)
            _clf = artifact["model"]
            _clf_features = artifact["feature_columns"]
            method = artifact.get("method", "unknown")
            logger.info(
                "Loaded CALIBRATED classifier (%s, %d features) from %s",
                method,
                len(_clf_features) if _clf_features else 0,
                CALIBRATED_MODEL_PATH,
            )
            _clf_loaded = True
            return
        except Exception as exc:
            logger.error(
                "Failed to load CALIBRATED classifier from %s: %s. "
                "Ensure scikit-learn==1.7.1 is installed (match training environment). "
                "Falling back to uncalibrated model.",
                CALIBRATED_MODEL_PATH, exc,
            )

    # Fall back to uncalibrated model
    if not MODEL_PATH.exists():
        logger.warning(
            "Classifier not found at %s — defaulting win_probability=0.5. "
            "Run `python -m models.train_and_evaluate` to train.",
            MODEL_PATH,
        )
        _clf_loaded = True
        return

    try:
        with open(MODEL_PATH, "rb") as f:
            artifact = pickle.load(f)
        _clf = artifact["model"]
        _clf_features = artifact["feature_columns"]
        logger.info(
            "Loaded UNCALIBRATED classifier (%d features) from %s",
            len(_clf_features) if _clf_features else 0,
            MODEL_PATH,
        )
        _clf_loaded = True
    except Exception as exc:
        logger.error(
            "Failed to load UNCALIBRATED classifier from %s: %s. "
            "Ensure scikit-learn==1.7.1 is installed (match training environment).",
            MODEL_PATH, exc,
        )


REASON_CODE_COLUMNS = [f"reason_code_{rc}" for rc in IN_SCOPE_REASON_CODES]
FEATURE_COLUMNS = [
    "amount_paise",
    "num_documents",
    "num_required_slots",
    "completeness",
    "quality",
    *REASON_CODE_COLUMNS,
]


def _predict_win_probability(dispute: Dispute) -> float:
    """Predict win probability using the trained classifier.

    Builds the same feature vector the classifier was trained on,
    minus the label and deliberately-excluded consistency/contradiction
    columns.  Falls back to 0.5 when no classifier is available.
    """
    _ensure_classifier_loaded()

    required = required_slots_for(dispute.reason_code)
    present_slots = {doc.slot for doc in dispute.documents}

    row: dict[str, Any] = {
        "amount_paise": dispute.amount_paise,
        "num_documents": len(dispute.documents),
        "num_required_slots": len(required),
        "completeness": completeness_score(present_slots, dispute.reason_code),
        "quality": quality_score(dispute.documents),
    }
    for rc in IN_SCOPE_REASON_CODES:
        row[f"reason_code_{rc}"] = int(dispute.reason_code == rc)

    if _clf is None or _clf_features is None:
        logger.warning(
            "Classifier not loaded — returning default win_probability=0.5 "
            "(dispute=%s, reason=%s)",
            dispute.dispute_id, dispute.reason_code,
        )
        return 0.5

    X = pd.DataFrame([row])[_clf_features]
    proba = float(_clf.predict_proba(X)[0, 1])
    logger.info(
        "Win probability for %s: prob=%.4f (completeness=%.1f, quality=%.1f)",
        dispute.dispute_id, proba,
        row["completeness"], row["quality"],
    )
    return proba


# ---------------------------------------------------------------------------
# Priority scoring
# ---------------------------------------------------------------------------

def _compute_priority(
    amount_paise: int,
    win_probability: float,
    completeness: float,
    respond_by: str | None,
) -> float:
    """Priority = amount * probability * urgency * readiness (0-100 scale).

    urgency:
      - 0.0 if deadline has passed (past_deadline, needs human triage)
      - linear 0->1 over the 168-hour (1-week) window before deadline
      - 0.5 if no deadline supplied

    readiness:
      - completeness / 100

    amount normalized to [0, 1] against 15 lakh paise ceiling.
    """
    amount_norm = min(1.0, amount_paise / 15_000_000)
    readiness = completeness / 100.0

    if respond_by:
        try:
            deadline_str = str(respond_by).replace("Z", "+00:00")
            deadline = datetime.fromisoformat(deadline_str)
            now = datetime.now(timezone.utc)
            # Ensure both operands are datetime objects before subtraction
            if not isinstance(deadline, datetime):
                raise TypeError(f"fromisoformat returned {type(deadline).__name__}, expected datetime")
            hours_remaining = (deadline - now).total_seconds() / 3600
            urgency = 0.0 if hours_remaining <= 0 else min(1.0, hours_remaining / 168)
        except (ValueError, TypeError, AttributeError) as exc:
            logger.debug("_compute_priority: respond_by parsing failed (%s), defaulting urgency=0.5", exc)
            urgency = 0.5
    else:
        urgency = 0.5

    return round(amount_norm * win_probability * urgency * readiness * 100, 2)


# ---------------------------------------------------------------------------
# Job handler
# ---------------------------------------------------------------------------

@register_handler("score.case")
def handle_score_job(job: dict, repo: CaseRepository) -> None:
    """Run the full scoring pipeline for a dispute.

    This is the same logic that was inline in the v1 ingest route,
    now running inside a worker thread.  The scoring functions
    (evaluate_dispute, contradiction rules) are pure Phase 1 code
    and are called unchanged.

    Args:
        job: The job row from the jobs table.  job["payload"] may
             contain overrides (not used in the base case).
        repo: Repository for all data access.
    """
    dispute_id = job["dispute_id"]
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    # 1. Load dispute from DB (documents may have accumulated since ingest).
    #    load_dispute_for_scoring needs a raw sqlite3.Connection (legacy
    #    Phase 1 code).  We use repo.connection() which provides a
    #    correctly-pathed connection for this repo instance.
    conn = repo.connection()
    try:
        dispute = load_dispute_for_scoring(dispute_id, conn)
    finally:
        conn.close()

    # 2. Predict win probability
    win_prob = _predict_win_probability(dispute)

    # 3. Evaluate: completeness, quality, consistency, gate
    result = evaluate_dispute(dispute, win_probability=win_prob)

    # 4. Compute priority
    priority = _compute_priority(
        dispute.amount_paise, result["win_probability"],
        result["completeness"], dispute.respond_by,
    )

    # 5. Persist scores (upsert)
    scores_data = {
        "win_probability": result["win_probability"],
        "completeness": result["completeness"],
        "quality": result["quality"],
        "consistency": result["consistency"],
        "missing_required_slots": result["missing_required_slots"],
        "contradiction_flags": result["contradiction_flags"],
        "computed_at": now_iso,
    }
    repo.upsert_scores(dispute_id, scores_data)

    # 6. Persist gate decision
    gate = result["gate"]
    repo.insert_decision({
        "dispute_id": dispute_id,
        "action": gate["action"],
        "passed": gate["passed"],
        "failing_conditions": gate["failing_conditions"],
        "priority_score": priority,
        "decided_at": now_iso,
    })

    # 7. Audit log
    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "score",
        "detail": {
            "win_probability": result["win_probability"],
            "completeness": result["completeness"],
            "quality": result["quality"],
            "consistency": result["consistency"],
            "gate_action": gate["action"],
            "gate_passed": gate["passed"],
            "failing_conditions": gate["failing_conditions"],
            "contradiction_flags": result["contradiction_flags"],
            "priority": priority,
        },
        "success": True,
    })

    # 8. Update dispute status
    repo.update_status(dispute_id, "scored")

    # 9. If gate = 'prepare', auto-enqueue a draft.response job
    #    (Phase 2 plan §5.5: prepare-gated cases get drafted automatically)
    if gate["action"] == "prepare":
        job_id = repo.enqueue_job("draft.response", dispute_id, {})
        repo.write_audit(
            {
                "dispute_id": dispute_id,
                "stage": "queue",
                "detail": {"job_id": job_id, "job_type": "draft.response"},
                "success": True,
            }
        )
        logger.info(
            "Gate=prepare for %s — enqueued draft.response (job %d)",
            dispute_id, job_id,
        )

    logger.info(
        "Scored dispute %s: prob=%.3f comp=%.1f gate=%s priority=%.2f",
        dispute_id, result["win_probability"], result["completeness"],
        gate["action"], priority,
    )
