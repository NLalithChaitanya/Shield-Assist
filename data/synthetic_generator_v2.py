"""
data/synthetic_generator_v2.py

Strengthened synthetic dispute generator that uses a LATENT STOCHASTIC
PROCESS for ground truth labels, rather than directly deriving them from
the same evidence rules used as model features.

This eliminates the circularity problem where "the model learns the
labeling rules" instead of learning genuine dispute outcomes.

Latent score model:
    latent_score =
        base_evidence_quality        (from completeness, quality, consistency)
        + reason_code_difficulty     (some codes are harder to win)
        + contradiction_penalty      (contradictions hurt, but not deterministically)
        + case_characteristics       (amount, document count, slot coverage)
        + stochastic_variation       (random noise modeling real-world uncertainty)

    P(win) = sigmoid(latent_score)

    outcome ~ Bernoulli(P(win))

Key differences from v1:
  - Ground truth is NOT a deterministic function of features
  - Same evidence quality can produce different outcomes (stochastic)
  - Reason codes have varying difficulty (latent variable)
  - Contradictions reduce P(win) but don't guarantee loss
  - Amount has a non-trivial effect (larger disputes may be harder to win)

Run directly to regenerate:
    python -m data.synthetic_generator_v2
"""

from __future__ import annotations

import json
import math
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from data.evidence_requirements import (
    IN_SCOPE_REASON_CODES,
    EvidenceSlot,
    required_slots_for,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

RANDOM_SEED = 42
N_TOTAL = 3000
TRAIN_FRACTION = 0.70
VAL_FRACTION = 0.14  # 14% of total for validation (20% of train-sized portion)

DEGRADED_DOC_RATE = 0.12
CONTRADICTION_INJECTION_RATE = 0.18

COMPLETENESS_SCENARIO_WEIGHTS = {"full": 0.45, "missing_one": 0.35, "missing_several": 0.20}

_NAMES = [
    "Aarav Mehta", "Priya Nair", "Rohit Sharma", "Ananya Iyer", "Vikram Rao",
    "Sneha Kapoor", "Karthik Subramanian", "Divya Menon", "Arjun Reddy",
    "Meera Pillai", "Sameer Khan", "Lakshmi Venkat", "Nikhil Bose", "Ishita Ghosh",
]

# Latent model parameters (conceptual, not tuned to real data)
# These control the SHAPE of the outcome distribution, not its calibration.
REASON_CODE_DIFFICULTY = {
    "RZP01": 0.3,   # Relatively easier (goods not provided — clear documentation)
    "RZP04": -0.2,  # Harder (refund not processed — needs proof of refund attempt)
    "RZP05": -0.4,  # Hardest (account debited — bank-side, hard to dispute)
    "RZP06": 0.1,   # Moderate (business not responding — needs response proof)
    "UPI1064": 0.2,  # Similar to RZP01
    "UPI1062": 0.0,  # Neutral (goods not as described — subjective)
}

# Amount effect: larger disputes may be harder (more scrutiny)
# Modeled as a slight negative pressure for very large amounts
AMOUNT_DIFFICULTY_SCALE = -0.3  # max negative effect at 15L


# ---------------------------------------------------------------------------
# Data model (same as v1 for compatibility)
# ---------------------------------------------------------------------------

@dataclass
class Document:
    doc_id: str
    slot: str
    quality: str
    fields: dict[str, Any]


@dataclass
class InjectedContradiction:
    rule_type: str
    doc_id_affected: str
    field: str
    original_value: Any
    mutated_value: Any
    severity: str


@dataclass
class Dispute:
    dispute_id: str
    reason_code: str
    amount_paise: int
    dispute_date: str
    respond_by: str
    customer_name: str
    order_id: str
    documents: list[Document] = field(default_factory=list)
    label: int = 0
    injected_contradictions: list[InjectedContradiction] = field(default_factory=list)
    latent_score: float = 0.0  # NEW: the latent score before sigmoid
    win_probability: float = 0.0  # NEW: P(win) from sigmoid

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Latent stochastic model
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid function."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    else:
        ex = math.exp(x)
        return ex / (1.0 + ex)


def _compute_latent_score(
    *,
    completeness_ratio: float,
    quality_ratio: float,
    consistency_ratio: float,
    has_contradiction: bool,
    contradiction_severity: str | None,
    reason_code: str,
    amount_paise: int,
    num_documents: int,
    rng: random.Random,
) -> tuple[float, float]:
    """Compute latent score and P(win) for a dispute.

    Returns (latent_score, win_probability).

    The latent score is a linear combination of factors, then passed
    through sigmoid to get P(win).  Stochastic variation is added
    BEFORE the sigmoid, ensuring the same evidence can produce
    different outcomes.
    """
    # 1. Base evidence quality (0-1 scale)
    evidence_quality = (
        0.4 * completeness_ratio +
        0.2 * quality_ratio +
        0.4 * consistency_ratio
    )

    # 2. Reason code difficulty (latent variable)
    rc_difficulty = REASON_CODE_DIFFICULTY.get(reason_code, 0.0)

    # 3. Contradiction penalty (stochastic — not deterministic)
    contradiction_penalty = 0.0
    if has_contradiction:
        if contradiction_severity == "high":
            # High severity: large penalty, but variable
            contradiction_penalty = -1.5 + rng.gauss(0, 0.3)
        else:
            # Medium severity: moderate penalty
            contradiction_penalty = -0.8 + rng.gauss(0, 0.2)

    # 4. Amount effect (larger = slightly harder)
    amount_normalized = min(1.0, amount_paise / 15_000_000)
    amount_effect = AMOUNT_DIFFICULTY_SCALE * amount_normalized

    # 5. Document count effect (more docs = slightly better, diminishing returns)
    doc_effect = 0.1 * math.log1p(num_documents) / math.log1p(5)

    # 6. Stochastic variation (the key innovation)
    # This ensures the same evidence can produce different outcomes
    noise = rng.gauss(0, 0.4)

    # Combine into latent score
    latent_score = (
        3.0 * evidence_quality +  # evidence quality is primary driver
        rc_difficulty +
        contradiction_penalty +
        amount_effect +
        doc_effect +
        noise -
        1.5  # intercept: shifts baseline P(win) to ~50% for average cases
    )

    win_probability = _sigmoid(latent_score)

    return latent_score, win_probability


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

class SyntheticDisputeGenerator:
    def __init__(self, seed: int = RANDOM_SEED) -> None:
        self.rng = random.Random(seed)

    def _pick_completeness_scenario(self) -> str:
        scenarios, weights = zip(*COMPLETENESS_SCENARIO_WEIGHTS.items())
        return self.rng.choices(scenarios, weights=weights, k=1)[0]

    def _present_slots(self, required: tuple[EvidenceSlot, ...], scenario: str) -> list[EvidenceSlot]:
        required_list = list(required)
        if scenario == "full":
            return required_list
        if scenario == "missing_one" and len(required_list) > 1:
            drop = self.rng.choice(required_list)
            return [s for s in required_list if s != drop]
        if scenario == "missing_several":
            keep_n = max(1, len(required_list) - self.rng.randint(2, max(2, len(required_list) - 1)))
            return self.rng.sample(required_list, k=min(keep_n, len(required_list)))
        return required_list

    def _make_document(
        self,
        slot: EvidenceSlot,
        *,
        amount_paise: int,
        dispute_dt: date,
        customer_name: str,
        order_id: str,
    ) -> Document:
        quality = "degraded" if self.rng.random() < DEGRADED_DOC_RATE else "clear"
        service_date = dispute_dt - timedelta(days=self.rng.randint(2, 30))

        fields: dict[str, Any] = {
            "amount_paise": amount_paise,
            "event_date": service_date.isoformat(),
            "customer_name": customer_name,
            "order_id": order_id,
        }

        return Document(
            doc_id=f"doc_{uuid.uuid4().hex[:10]}",
            slot=slot.value,
            quality=quality,
            fields=fields,
        )

    def _maybe_inject_contradiction(self, dispute: Dispute) -> None:
        if self.rng.random() >= CONTRADICTION_INJECTION_RATE or not dispute.documents:
            return
        if len(dispute.documents) < 2:
            return

        rule_type = self.rng.choice(["amount_mismatch", "date_order_violation", "name_mismatch", "order_id_mismatch"])
        target_doc = self.rng.choice(dispute.documents)

        if rule_type == "amount_mismatch":
            original = target_doc.fields["amount_paise"]
            if self.rng.random() < 0.25:
                delta = self.rng.choice([-1, 1]) * self.rng.randint(150, 400)
            else:
                delta = self.rng.choice([-1, 1]) * self.rng.randint(5000, 50000)
            mutated = max(0, original + delta)
            target_doc.fields["amount_paise"] = mutated
            severity = "high"

        elif rule_type == "date_order_violation":
            dispute_dt = date.fromisoformat(dispute.dispute_date)
            mutated_dt = dispute_dt + timedelta(days=self.rng.randint(1, 10))
            mutated = mutated_dt.isoformat()
            target_doc.fields["event_date"] = mutated
            severity = "high"

        elif rule_type == "name_mismatch":
            original = target_doc.fields["customer_name"]
            other_names = [n for n in _NAMES if n != original]
            mutated = self.rng.choice(other_names)
            target_doc.fields["customer_name"] = mutated
            severity = "medium"

        else:  # order_id_mismatch
            original = target_doc.fields["order_id"]
            mutated = f"ORD{self.rng.randint(100000, 999999)}"
            target_doc.fields["order_id"] = mutated
            severity = "medium"

        dispute.injected_contradictions.append(
            InjectedContradiction(
                rule_type=rule_type,
                doc_id_affected=target_doc.doc_id,
                field={
                    "amount_mismatch": "amount_paise",
                    "date_order_violation": "event_date",
                    "name_mismatch": "customer_name",
                    "order_id_mismatch": "order_id",
                }[rule_type],
                original_value=original,
                mutated_value=mutated,
                severity=severity,
            )
        )

    def generate_one(self) -> Dispute:
        reason_code = self.rng.choice(IN_SCOPE_REASON_CODES)
        required = required_slots_for(reason_code)

        amount_paise = self.rng.randint(50_000, 15_000_000)
        dispute_dt = date(2025, 1, 1) + timedelta(days=self.rng.randint(0, 700))
        respond_by = dispute_dt + timedelta(days=self.rng.randint(3, 21))
        customer_name = self.rng.choice(_NAMES)
        order_id = f"ORD{self.rng.randint(100000, 999999)}"

        scenario = self._pick_completeness_scenario()
        present_slots = self._present_slots(required, scenario)

        documents = [
            self._make_document(
                slot,
                amount_paise=amount_paise,
                dispute_dt=dispute_dt,
                customer_name=customer_name,
                order_id=order_id,
            )
            for slot in present_slots
        ]

        dispute = Dispute(
            dispute_id=f"dsp_{uuid.uuid4().hex[:10]}",
            reason_code=reason_code,
            amount_paise=amount_paise,
            dispute_date=dispute_dt.isoformat(),
            respond_by=respond_by.isoformat(),
            customer_name=customer_name,
            order_id=order_id,
            documents=documents,
        )

        self._maybe_inject_contradiction(dispute)

        # Compute latent features
        present_slot_set = {d.slot for d in documents}
        completeness_ratio = len(present_slot_set & {s.value for s in required}) / len(required)
        quality_ratio = 1.0 - sum(1 for d in documents if d.quality == "degraded") / max(len(documents), 1)

        # Compute consistency from injected contradictions
        from models.contradiction_rules import run_all_contradiction_checks
        from backend.domain import Document as DomainDocument

        # Convert to domain documents for consistency check
        domain_docs = [
            DomainDocument(doc_id=d.doc_id, slot=d.slot, quality=d.quality, fields=d.fields)
            for d in documents
        ]
        flags = run_all_contradiction_checks(
            domain_docs,
            dispute_date_iso=dispute.dispute_date,
            expected_customer_name=dispute.customer_name,
            expected_order_id=dispute.order_id,
        )
        consistency_ratio = max(0.0, 1.0 - sum(
            {"high": 0.35, "medium": 0.15}.get(f.severity, 0.1) for f in flags
        ))

        has_contradiction = bool(dispute.injected_contradictions)
        contradiction_severity = (
            dispute.injected_contradictions[0].severity if has_contradiction else None
        )

        # Compute latent score and P(win)
        latent_score, win_probability = _compute_latent_score(
            completeness_ratio=completeness_ratio,
            quality_ratio=quality_ratio,
            consistency_ratio=consistency_ratio,
            has_contradiction=has_contradiction,
            contradiction_severity=contradiction_severity,
            reason_code=reason_code,
            amount_paise=amount_paise,
            num_documents=len(documents),
            rng=self.rng,
        )

        dispute.latent_score = latent_score
        dispute.win_probability = win_probability

        # Sample outcome from Bernoulli distribution
        dispute.label = 1 if self.rng.random() < win_probability else 0

        return dispute

    def generate_many(self, n: int) -> list[Dispute]:
        return [self.generate_one() for _ in range(n)]


# ---------------------------------------------------------------------------
# Split + persistence (3-way: train / val / test)
# ---------------------------------------------------------------------------

def train_val_test_split_disputes(
    disputes: list[Dispute],
    train_fraction: float = TRAIN_FRACTION,
    val_fraction: float = VAL_FRACTION,
    seed: int = RANDOM_SEED,
) -> tuple[list[Dispute], list[Dispute], list[Dispute]]:
    """Stratified 3-way split on `label`.

    Returns (train, val, test).
    - train: used for classifier training
    - val: used for threshold selection and calibration
    - test: held out for final evaluation ONLY
    """
    rng = random.Random(seed)
    by_label: dict[int, list[Dispute]] = {0: [], 1: []}
    for d in disputes:
        by_label[d.label].append(d)

    train: list[Dispute] = []
    val: list[Dispute] = []
    test: list[Dispute] = []

    for label, group in by_label.items():
        shuffled = group[:]
        rng.shuffle(shuffled)
        n = len(shuffled)
        n_test = int(n * (1 - train_fraction - val_fraction))
        n_val = int(n * val_fraction)
        n_train = n - n_test - n_val
        train.extend(shuffled[:n_train])
        val.extend(shuffled[n_train:n_train + n_val])
        test.extend(shuffled[n_train + n_val:])

    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)
    return train, val, test


def save_disputes(disputes: list[Dispute], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([d.to_json_dict() for d in disputes], f, indent=2)


def load_disputes(path: Path) -> list[Dispute]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for d in raw:
        docs = [Document(**doc) for doc in d["documents"]]
        contras = [InjectedContradiction(**c) for c in d["injected_contradictions"]]
        d = {**d, "documents": docs, "injected_contradictions": contras}
        # Handle new fields gracefully (backward compat with v1 data)
        d.pop("latent_score", None)
        d.pop("win_probability", None)
        out.append(Dispute(**d))
    return out


def main() -> None:
    gen = SyntheticDisputeGenerator(seed=RANDOM_SEED)
    disputes = gen.generate_many(N_TOTAL)
    train, val, test = train_val_test_split_disputes(disputes)

    assert len(train) + len(val) + test.__len__() == len(disputes)
    print(f"Generated {len(disputes)} disputes -> {len(train)} train / {len(val)} val / {len(test)} test")

    label_rate = sum(d.label for d in disputes) / len(disputes)
    contradiction_rate = sum(1 for d in disputes if d.injected_contradictions) / len(disputes)
    avg_prob = sum(d.win_probability for d in disputes) / len(disputes)
    print(f"Win-label rate: {label_rate:.3f} | Injected-contradiction rate: {contradiction_rate:.3f}")
    print(f"Average P(win): {avg_prob:.3f}")

    out_dir = Path(__file__).resolve().parent / "synthetic"
    save_disputes(train, out_dir / "train.json")
    save_disputes(val, out_dir / "val.json")
    save_disputes(test, out_dir / "test.json")
    print(f"Saved to {out_dir}/train.json, {out_dir}/val.json, {out_dir}/test.json")


if __name__ == "__main__":
    main()
