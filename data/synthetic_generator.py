"""
data/synthetic_generator.py

Generates synthetic disputes with attached documents, and TWO separate
ground-truth signals:

  1. `label` (win / loss) -- used to train and evaluate the
     win-probability classifier. Derived from a rule-based
     completeness/quality/consistency threshold, plus an 8% outcome
     noise flip (modeling real-world bank discretion). This is
     deliberately NOT a trivial function of any single feature, to
     avoid label leakage.

  2. `injected_contradictions` -- used ONLY to evaluate the
     contradiction-detection rule layer's own precision/recall
     (models/contradiction_rules.py). This is ground truth about what
     we *deliberately broke*, kept separate from whatever the rule
     layer *detects* -- conflating the two would make the detector's
     eval circular.

Run directly to regenerate the dataset:
    python -m data.synthetic_generator
"""

from __future__ import annotations

import json
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
N_TOTAL = 3000  # 2,100 train / 900 test at 70/30, matching PRODUCT_SPEC.md
TRAIN_FRACTION = 0.70

DEGRADED_DOC_RATE = 0.12          # documents that are illegible / low quality
CONTRADICTION_INJECTION_RATE = 0.18   # disputes that get one injected contradiction
OUTCOME_NOISE_FLIP_RATE = 0.08    # label flips to model bank discretion

# Completeness "scenario" mix: how many of the required slots are present.
# (full, missing_one, missing_several) -- weights, not exact counts.
COMPLETENESS_SCENARIO_WEIGHTS = {"full": 0.45, "missing_one": 0.35, "missing_several": 0.20}

_NAMES = [
    "Aarav Mehta", "Priya Nair", "Rohit Sharma", "Ananya Iyer", "Vikram Rao",
    "Sneha Kapoor", "Karthik Subramanian", "Divya Menon", "Arjun Reddy",
    "Meera Pillai", "Sameer Khan", "Lakshmi Venkat", "Nikhil Bose", "Ishita Ghosh",
]

CURRENCY_MINOR_UNIT_TOLERANCE_PAISE = 100  # 1 rupee -- rounding tolerance, not a bug


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Document:
    doc_id: str
    slot: str  # EvidenceSlot value
    quality: str  # "clear" | "degraded"
    fields: dict[str, Any]  # amount, event_date, customer_name, order_id (subset, per slot type)


@dataclass
class InjectedContradiction:
    rule_type: str  # matches the rule id contradiction_rules.py should catch
    doc_id_affected: str
    field: str
    original_value: Any
    mutated_value: Any
    severity: str  # "high" | "medium"


@dataclass
class Dispute:
    dispute_id: str
    reason_code: str
    amount_paise: int
    dispute_date: str  # ISO date
    respond_by: str  # ISO date
    customer_name: str
    order_id: str
    documents: list[Document] = field(default_factory=list)
    label: int = 0  # 1 = would win, 0 = would lose
    injected_contradictions: list[InjectedContradiction] = field(default_factory=list)

    def to_json_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


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

        # event_date semantics differ slightly by slot, but for a
        # consistent (non-contradictory) case they all anchor to a
        # delivery/service date that precedes the dispute date.
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

    def _compute_rule_based_label(
        self,
        documents: list[Document],
        required: tuple[EvidenceSlot, ...],
        has_contradiction: bool,
    ) -> int:
        present_slots = {d.slot for d in documents}
        completeness_ratio = len(present_slots & {s.value for s in required}) / len(required)
        degraded_ratio = sum(1 for d in documents if d.quality == "degraded") / max(len(documents), 1)

        # Rule-based ground truth: strong evidence wins, weak evidence
        # loses, contradictions are heavily penalized. This threshold
        # is intentionally coarser than the multi-condition gate used
        # at inference time -- the gate is stricter than "would win".
        strong = completeness_ratio >= 0.9 and degraded_ratio <= 0.15 and not has_contradiction
        label = 1 if strong else 0

        if self.rng.random() < OUTCOME_NOISE_FLIP_RATE:
            label = 1 - label

        return label

    def _maybe_inject_contradiction(self, dispute: Dispute) -> None:
        if self.rng.random() >= CONTRADICTION_INJECTION_RATE or not dispute.documents:
            return
        if len(dispute.documents) < 2:
            return  # need at least 2 docs for a cross-document contradiction to be meaningful

        rule_type = self.rng.choice(["amount_mismatch", "date_order_violation", "name_mismatch", "order_id_mismatch"])
        target_doc = self.rng.choice(dispute.documents)

        if rule_type == "amount_mismatch":
            original = target_doc.fields["amount_paise"]
            # 25% of injected amount mismatches are deliberately kept
            # near the detector's tolerance boundary (150-400 paise) --
            # a genuine test of whether the rule's tolerance is well
            # calibrated, not just "is a huge discrepancy visible".
            # The rest are clearly-not-rounding amounts.
            if self.rng.random() < 0.25:
                delta = self.rng.choice([-1, 1]) * self.rng.randint(150, 400)
            else:
                delta = self.rng.choice([-1, 1]) * self.rng.randint(5000, 50000)
            mutated = max(0, original + delta)
            target_doc.fields["amount_paise"] = mutated
            severity = "high"

        elif rule_type == "date_order_violation":
            original = target_doc.fields["event_date"]
            dispute_dt = date.fromisoformat(dispute.dispute_date)
            # service/delivery date now AFTER the dispute date -- impossible in a valid case
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

        amount_paise = self.rng.randint(50_000, 15_000_000)  # ~₹500 to ~₹1.5L
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

        dispute.label = self._compute_rule_based_label(
            documents, required, has_contradiction=bool(dispute.injected_contradictions)
        )

        return dispute

    def generate_many(self, n: int) -> list[Dispute]:
        return [self.generate_one() for _ in range(n)]


# ---------------------------------------------------------------------------
# Split + persistence
# ---------------------------------------------------------------------------

def train_test_split_disputes(
    disputes: list[Dispute], train_fraction: float = TRAIN_FRACTION, seed: int = RANDOM_SEED
) -> tuple[list[Dispute], list[Dispute]]:
    """Stratified split on `label` so class balance is preserved in both splits.

    Enforced in code (not just "should be 70/30") -- this function is
    imported directly by models/train_and_evaluate.py, so there is a
    single, auditable split point rather than two scripts each doing
    their own shuffling.
    """
    rng = random.Random(seed)
    by_label: dict[int, list[Dispute]] = {0: [], 1: []}
    for d in disputes:
        by_label[d.label].append(d)

    train: list[Dispute] = []
    test: list[Dispute] = []
    for label, group in by_label.items():
        shuffled = group[:]
        rng.shuffle(shuffled)
        cut = int(len(shuffled) * train_fraction)
        train.extend(shuffled[:cut])
        test.extend(shuffled[cut:])

    rng.shuffle(train)
    rng.shuffle(test)
    return train, test


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
        out.append(Dispute(**d))
    return out


def main() -> None:
    gen = SyntheticDisputeGenerator(seed=RANDOM_SEED)
    disputes = gen.generate_many(N_TOTAL)
    train, test = train_test_split_disputes(disputes)

    assert len(train) + len(test) == len(disputes)
    print(f"Generated {len(disputes)} disputes -> {len(train)} train / {len(test)} test")

    label_rate = sum(d.label for d in disputes) / len(disputes)
    contradiction_rate = sum(1 for d in disputes if d.injected_contradictions) / len(disputes)
    print(f"Win-label rate: {label_rate:.3f} | Injected-contradiction rate: {contradiction_rate:.3f}")

    out_dir = Path(__file__).resolve().parent / "synthetic"
    save_disputes(train, out_dir / "train.json")
    save_disputes(test, out_dir / "test.json")
    print(f"Saved to {out_dir}/train.json and {out_dir}/test.json")


if __name__ == "__main__":
    main()