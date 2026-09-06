"""
tests/test_copilot_context.py

Regression tests for question-aware copilot chat:

  - The merchant's actual question + recent turns are appended to the
    LLM user prompt (so answers stop being fixed ability scripts).
  - The response cache key includes the question/history, so two
    different questions on the same case produce different responses
    instead of replaying one another's text.
  - Grounding instruction is always present; without question/history
    the prompt is unchanged (legacy fixed-ability behavior preserved).

Pure functions only -- no network, no LLM calls.

Run:
    pytest tests/test_copilot_context.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.copilot import (
    _append_conversation,
    _conversation_block,
    compute_state_hash,
)


def _case(**overrides) -> dict:
    case = {
        "reason_code": "RZP01",
        "amount_paise": 4200000,
        "scores": {"win_probability": 0.8, "completeness": 100.0},
        "gate": {"action": "prepare", "passed": True, "failing_conditions": []},
        "facts": [
            {"fact_id": 1, "document_id": "doc_a", "fact_type": "order_id", "fact_value": "ORD_001"},
        ],
    }
    case.update(overrides)
    return case


class TestConversationBlock:

    def test_block_contains_question_and_grounding_rule(self):
        block = _conversation_block(_case(question="which evidence is wrong"))
        assert "which evidence is wrong" in block
        assert "Answer THAT question directly" in block
        assert "never" in block and "invent" in block

    def test_block_contains_history_roles(self):
        history = [
            {"role": "user", "text": "what's wrong"},
            {"role": "assistant", "text": "there is a mismatch"},
        ]
        block = _conversation_block(_case(question="which evidence is wrong", history=history))
        assert "what's wrong" in block
        assert "there is a mismatch" in block
        assert "Merchant:" in block and "Ally:" in block

    def test_block_empty_without_question_or_history(self):
        assert _conversation_block(_case()) == ""

    def test_append_unchanged_without_context(self):
        user = "Some prompt."
        assert _append_conversation(user, _case()) == user

    def test_append_adds_question(self):
        user = "Some prompt."
        augmented = _append_conversation(user, _case(question="what's wrong"))
        assert augmented.startswith(user.rstrip())
        assert "what's wrong" in augmented


class TestStateHashIncludesQuestion:

    def test_different_question_different_hash(self):
        base = _case()
        h1 = compute_state_hash(_case(question="what's wrong"))
        h2 = compute_state_hash(_case(question="which evidence is wrong"))
        # Same facts/gate, different question => different cache key
        assert h1 != h2
        # Sanity: identical inputs still hash identically
        assert h1 == compute_state_hash(_case(question="what's wrong"))

    def test_history_changes_hash(self):
        h1 = compute_state_hash(_case(question="q", history=[]))
        h2 = compute_state_hash(_case(question="q", history=[{"role": "user", "text": "hi"}]))
        assert h1 != h2

    def test_no_question_keeps_stable_hash(self):
        assert compute_state_hash(_case()) == compute_state_hash(_case())
