"""
tests/test_groq_fallback.py

Tests for the automatic Groq failover added to the copilot layer:

  Gemini (primary) --ANY failure--> Groq (backup) --also fails--> honest
  "Ally temporarily unavailable" fallback (streams) / raise (batched,
  where the draft job's grounded template fallback takes over).

Scope under test: copilot chat + draft.  Gemini OCR (document.process)
does NOT route through Groq and is untouched.

All tests are fully mocked and offline -- no real network calls.

Run:
    pytest tests/test_groq_fallback.py -v
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import backend.copilot as copilot


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

def _provider_error(code: int, message: str) -> Exception:
    """Build a provider exception that classify_gemini_error understands."""
    exc = Exception(message)
    exc.code = code
    if code == 403:
        exc.details = {"error": {"message": "quota exhausted"}}
    return exc


def _make_gemini_batch_response(text: str):
    """Simulate a batched google-genai response (has .text)."""
    resp = MagicMock()
    resp.text = text
    return resp


def _make_gemini_stream_chunks(text: str):
    """Simulate an iterable of google-genai stream chunks (each has .text)."""
    chunk = MagicMock()
    chunk.text = text
    return [chunk]


def _make_groq_batch_response(content: str):
    """Simulate a batched chat.completions.create response object."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_groq_stream_chunks(text: str):
    """Simulate Groq stream chunks; rejoining the deltas reproduces `text`."""
    words = text.split(" ")
    chunks = []
    for i, word in enumerate(words):
        delta = MagicMock()
        delta.content = word + (" " if i < len(words) - 1 else "")
        choice = MagicMock()
        choice.delta = delta
        chunk = MagicMock()
        chunk.choices = [choice]
        chunks.append(chunk)
    return chunks


def _patch_clients(monkeypatch, gemini_client, groq_client):
    """Point both client factories at mocks.

    groq_client=None simulates 'Groq not configured' (returns None, None).
    """
    monkeypatch.setattr(
        copilot, "_get_gemini_client", lambda: (gemini_client, "gemini-test-model")
    )
    if groq_client is None:
        monkeypatch.setattr(copilot, "_get_groq_client", lambda: (None, None))
    else:
        monkeypatch.setattr(
            copilot, "_get_groq_client", lambda: (groq_client, "groq-test-model")
        )


def _simple_case_data() -> dict:
    return {
        "reason_code": "RZP01",
        "amount_paise": 4200000,
        "scores": {"win_probability": 0.8, "completeness": 100.0},
        "gate": {"action": "prepare", "passed": True, "failing_conditions": []},
        "facts": [
            {
                "fact_id": 1,
                "document_id": "doc_a",
                "fact_type": "order_id",
                "fact_value": "ORD_001",
            },
            {
                "fact_id": 2,
                "document_id": "doc_a",
                "fact_type": "event_date",
                "fact_value": "2026-08-22",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Batched failover (used by copilot.explain/investigate/recommend/draft)
# ---------------------------------------------------------------------------

class TestBatchedFailover:

    def test_gemini_503_groq_succeeds(self, monkeypatch):
        """Gemini 503 -> Groq answers the batch request."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        groq = MagicMock()
        groq.call.return_value = _make_groq_batch_response("Answer from Groq")
        _patch_clients(monkeypatch, gemini, groq)

        result = copilot._call_gemini("system", "user")

        assert result == "Answer from Groq"
        gemini.call.assert_called_once()
        # Groq received the same prompts as chat messages
        args = groq.call.call_args
        assert args[1]["messages"][0]["role"] == "system"
        assert args[1]["messages"][0]["content"] == "system"
        assert args[1]["messages"][1]["role"] == "user"
        assert args[1]["messages"][1]["content"] == "user"

    def test_gemini_succeeds_groq_not_called(self, monkeypatch):
        """Happy path: Gemini answers, Groq is never consulted."""
        gemini = MagicMock()
        gemini.call.return_value = _make_gemini_batch_response("Answer from Gemini")
        groq = MagicMock()
        _patch_clients(monkeypatch, gemini, groq)

        result = copilot._call_gemini("system", "user")

        assert result == "Answer from Gemini"
        groq.call.assert_not_called()

    def test_gemini_503_groq_disabled_raises_original(self, monkeypatch):
        """No Groq configured -> original Gemini error propagates (old behavior)."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        _patch_clients(monkeypatch, gemini, None)

        with pytest.raises(Exception, match="503 UNAVAILABLE"):
            copilot._call_gemini("system", "user")

    def test_both_providers_fail_raises(self, monkeypatch):
        """Gemini AND Groq fail -> raises (draft job template fallback applies)."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        groq = MagicMock()
        groq.call.side_effect = _provider_error(429, "Groq rate limited")
        _patch_clients(monkeypatch, gemini, groq)

        with pytest.raises(Exception, match="Groq rate limited"):
            copilot._call_gemini("system", "user")

    def test_quota_fails_over_to_groq(self, monkeypatch):
        """Gemini quota exhausted (403) -> Groq still answers the batch."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(
            403, "RESOURCE_EXHAUSTED: daily quota"
        )
        groq = MagicMock()
        groq.call.return_value = _make_groq_batch_response("Groq answer despite Gemini quota")
        _patch_clients(monkeypatch, gemini, groq)

        result = copilot._call_gemini("system", "user")
        assert result == "Groq answer despite Gemini quota"


# ---------------------------------------------------------------------------
# Streaming failover (SSE chat)
# ---------------------------------------------------------------------------

class TestStreamingFailover:

    def test_gemini_503_groq_succeeds(self, monkeypatch):
        """Gemini repeatedly 503s -> Groq streams the full answer."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        groq = MagicMock()
        groq.call.return_value = _make_groq_stream_chunks("Hello from Groq fallback")
        _patch_clients(monkeypatch, gemini, groq)

        with patch("time.sleep"):
            chunks = list(copilot._call_gemini_stream("system", "user"))

        text = "".join(chunks)
        assert "Hello from Groq fallback" in text
        # No honest-fallback text when Groq actually answered
        assert "temporarily unavailable" not in text
        # Gemini got its own retry attempts before failover
        assert gemini.call.call_count == 1 + copilot._COPILOT_MAX_RETRIES

    def test_gemini_succeeds_groq_not_called(self, monkeypatch):
        """Happy path stream: Gemini streams, Groq never consulted."""
        gemini = MagicMock()
        gemini.call.return_value = _make_gemini_stream_chunks("Streamed from Gemini")
        groq = MagicMock()
        _patch_clients(monkeypatch, gemini, groq)

        chunks = list(copilot._call_gemini_stream("system", "user"))

        assert "".join(chunks) == "Streamed from Gemini"
        groq.call.assert_not_called()

    def test_gemini_fails_groq_disabled_yields_fallback(self, monkeypatch):
        """No Groq configured -> honest fallback message (pre-existing behavior)."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        _patch_clients(monkeypatch, gemini, None)

        with patch("time.sleep"):
            chunks = list(copilot._call_gemini_stream("system", "user"))

        assert len(chunks) == 1
        assert chunks[0] == copilot._GEMINI_UNAVAILABLE_FALLBACK

    def test_both_fail_yields_single_honest_fallback(self, monkeypatch):
        """Gemini AND Groq down -> one fallback chunk, no raw errors, no invention."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        groq = MagicMock()
        groq.call.side_effect = _provider_error(503, "Groq 503 too")
        _patch_clients(monkeypatch, gemini, groq)

        with patch("time.sleep"):
            chunks = list(copilot._call_gemini_stream("system", "user"))

        assert len(chunks) == 1
        fallback = chunks[0]
        assert fallback == copilot._GEMINI_UNAVAILABLE_FALLBACK
        # Honest fallback must not fabricate any evidence
        assert "fact_id" not in fallback.lower()
        assert "doc_" not in fallback.lower()
        assert "contradiction" not in fallback.lower()
        assert "amount" not in fallback.lower()

    def test_quota_fails_over_to_groq(self, monkeypatch):
        """Gemini quota (non-transient) with Groq configured -> Groq streams."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(
            403, "RESOURCE_EXHAUSTED: daily quota"
        )
        groq = MagicMock()
        groq.call.return_value = _make_groq_stream_chunks("Groq streams despite Gemini quota")
        _patch_clients(monkeypatch, gemini, groq)

        chunks = list(copilot._call_gemini_stream("system", "user"))

        assert "".join(chunks) == "Groq streams despite Gemini quota"

    def test_quota_no_groq_raises(self, monkeypatch):
        """Gemini quota + no Groq -> raises so SSE sends the specific error."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(
            403, "RESOURCE_EXHAUSTED: daily quota"
        )
        _patch_clients(monkeypatch, gemini, None)

        with pytest.raises(Exception, match="quota"):
            list(copilot._call_gemini_stream("system", "user"))


# ---------------------------------------------------------------------------
# Grounded draft end-to-end (citations parsed from Groq, integrity kept)
# ---------------------------------------------------------------------------

class TestDraftWithGroq:

    def test_draft_json_citations_valid_and_invalid_dropped(self, monkeypatch):
        """Groq draft: valid citations kept, nonexistent fact_id dropped."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        groq = MagicMock()
        groq_text = json.dumps({
            "response_text": (
                "We delivered service for order ORD_001 on 2026-08-22 "
                "as documented in our proof of service."
            ),
            "citations": [
                {"claim": "order id is ORD_001", "fact_id": 1, "document_id": "doc_a"},
                {"claim": "delivery on 2026-08-22", "fact_id": 2, "document_id": "doc_a"},
                {"claim": "ghost fact", "fact_id": 999, "document_id": "doc_ghost"},
            ],
        })
        groq.call.return_value = _make_groq_batch_response(groq_text)
        _patch_clients(monkeypatch, gemini, groq)

        out = copilot.draft(_simple_case_data())

        # Ghost citation (fact 999) must be dropped by the parser
        assert len(out.citations) == 2
        cited_ids = {c.fact_id for c in out.citations}
        assert cited_ids == {1, 2}
        assert all(c.document_id == "doc_a" for c in out.citations)
        assert "proof of service" in out.summary_text.lower()

    def test_draft_all_invalid_falls_back_to_mechanical_grounded_citations(self, monkeypatch):
        """If Groq cites only nonexistent facts, parser builds grounded citations from real facts."""
        gemini = MagicMock()
        gemini.call.side_effect = _provider_error(503, "503 UNAVAILABLE")
        groq = MagicMock()
        groq_text = json.dumps({
            "response_text": "Response with no usable citations.",
            "citations": [
                {"claim": "made up", "fact_id": 999, "document_id": "doc_x"},
                {"claim": "also made up", "fact_id": 888, "document_id": "doc_y"},
            ],
        })
        groq.call.return_value = _make_groq_batch_response(groq_text)
        _patch_clients(monkeypatch, gemini, groq)

        out = copilot.draft(_simple_case_data())

        # Mechanical fallback citations must reference real facts only
        valid_ids = {f["fact_id"] for f in _simple_case_data()["facts"]}
        assert out.citations, "expected mechanical citations as integrity fallback"
        assert {c.fact_id for c in out.citations} <= valid_ids
        assert all(c.document_id == "doc_a" for c in out.citations)
