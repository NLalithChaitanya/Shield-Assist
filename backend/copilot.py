"""
backend/copilot.py

LLM copilot layer for Shield Assist -- four capabilities powered by
Google Gemini (free tier):

  explain()         / explain_stream()       -- dispute summary
  investigate()     / investigate_stream()   -- investigation steps
  recommend()       / recommend_stream()     -- strategy recommendation
  draft()                                  -- response with citations (batched only)

Batched functions: return the full response as a string.  Used by the
draft.response job (which needs the complete text for citation integrity
checking).

Streaming functions: yield text chunks as Gemini generates them.  Used
by the SSE copilot routes for real-time display in the frontend.

All functions receive ONLY structured facts -- never raw documents.
This is "Evidence Integrity Mode": the copilot can only reference facts
that have been extracted and stored, not hallucinate from raw images.

AI provider: Google Gemini (free tier, gemini-3.5-flash-lite).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Generator

logger = logging.getLogger("shield_assist.copilot")


# ---------------------------------------------------------------------------
# State hashing (for copilot response caching)
# ---------------------------------------------------------------------------

def compute_state_hash(case_data: dict) -> str:
    """Compute a stable hash of the case inputs that determine the Gemini response.

    Changes to scores, facts, reason_code, amount, or gate conditions
    produce a different hash, automatically invalidating stale cache entries.
    Returns a 16-character hex string (64 bits of entropy — sufficient
    for collision avoidance at hackathon scale).
    """
    import hashlib
    key = {
        "reason_code": case_data.get("reason_code"),
        "amount_paise": case_data.get("amount_paise"),
        "scores": case_data.get("scores"),
        "gate_action": case_data.get("gate", {}).get("action"),
        "failing_conditions": case_data.get("gate", {}).get("failing_conditions", []),
        "facts": case_data.get("facts", []),
    }
    raw = json.dumps(key, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Output types
# ---------------------------------------------------------------------------

@dataclass
class Citation:
    """A single citation linking a claim to a specific fact and document."""
    claim: str
    fact_id: int
    document_id: str


@dataclass
class DraftOutput:
    """Structured output from copilot.draft()."""
    summary_text: str
    citations: list[Citation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary_text": self.summary_text,
            "citations": [asdict(c) for c in self.citations],
        }


# ---------------------------------------------------------------------------
# Gemini API client
# ---------------------------------------------------------------------------

_resilient_client = None
_model_name = None

def _get_gemini_client():
    """Get a resilience-wrapped Gemini client via the google-genai SDK.

    Returns (ResilientClient, model_name).  The ResilientClient wraps
    the raw genai.Client with circuit breaker (5 failures -> open -> 30s)
    + rate limiter (14 RPM) + retry (1 backoff attempt).  This is the
    ONLY place a Gemini client is created.

    On the happy path this is a transparent passthrough -- rate limiter
    always has tokens, circuit breaker is closed, no retries needed.
    """
    global _resilient_client, _model_name

    if _resilient_client is not None:
        return _resilient_client, _model_name

    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("GEMINI_API_KEY", "")
    _model_name = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")

    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY not set.  Get a free key from "
            "https://aistudio.google.com/apikey and add it to .env"
        )

    from google import genai
    from backend.resilience import wrap_gemini_client
    raw_client = genai.Client(api_key=api_key)
    _resilient_client = wrap_gemini_client(raw_client)
    return _resilient_client, _model_name


# ---------------------------------------------------------------------------
# Gemini call helpers
# ---------------------------------------------------------------------------

# Fallback messages for when Gemini is temporarily unavailable.
# These never fabricate evidence -- they tell the user what happened
# and ask them to retry.
_GEMINI_UNAVAILABLE_FALLBACK = (
    "Ally is temporarily unavailable because the AI service is experiencing "
    "high demand.  Your evidence analysis is still available.  "
    "Please retry in a few seconds."
)

# Maximum copilot-level retries for transient 503 errors (on top of
# the resilience wrapper's own retries).  Total Gemini calls per
# request = (1 + resilience_max_retries) * (1 + copilot_retries).
# With defaults: (1+3) * (1+1) = 8 max calls.
_COPILOT_MAX_RETRIES = 1


def _is_transient_503(exc: Exception) -> bool:
    """Check if an exception is a transient Gemini 503 error."""
    from backend.resilience import classify_gemini_error
    info = classify_gemini_error(exc)
    return info["error_type"] == "service_unavailable" and info["recoverable"]


def _call_gemini(system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
    """Call Gemini and return the full response text (batched).

    Goes through ResilientClient.call() -- circuit breaker, rate limiter,
    and retry are all active.  On the happy path this is transparent.

    Raises on failure -- batched callers (draft job) rely on job-level
    retry rather than copilot-level fallback, because returning a
    fallback text as a "real draft" would be wrong.
    """
    client, model_name = _get_gemini_client()
    logger.debug("Gemini batch: model=%s", model_name)

    response = client.call(
        "models.generate_content",
        model=model_name,
        contents=user_prompt,
        config={
            "system_instruction": system_prompt,
            "max_output_tokens": max_tokens,
        },
    )
    return response.text or ""


def _call_gemini_stream(system_prompt: str, user_prompt: str, max_tokens: int = 2048):
    """Stream Gemini response as text chunks.

    Goes through ResilientClient.call() for circuit breaker + rate limiter.
    The generator is fully consumed inside the call so the resilience
    wrapper can record success/failure before yielding chunks to callers.

    If all retries fail with a transient 503, yields a clean fallback
    message instead of raising -- the browser never sees raw errors.
    """
    import time
    client, model_name = _get_gemini_client()
    logger.debug("Gemini stream: model=%s", model_name)

    last_exc = None
    for copilot_attempt in range(1 + _COPILOT_MAX_RETRIES):
        try:
            stream = client.call(
                "models.generate_content_stream",
                model=model_name,
                contents=user_prompt,
                config={
                    "system_instruction": system_prompt,
                    "max_output_tokens": max_tokens,
                },
            )
            for chunk in stream:
                if chunk.text:
                    yield chunk.text
            return  # Success -- done
        except Exception as exc:
            last_exc = exc
            if _is_transient_503(exc) and copilot_attempt < _COPILOT_MAX_RETRIES:
                # Transient 503: retry with backoff
                delay = 2 ** (copilot_attempt + 1)  # 2s, 4s
                logger.warning(
                    "Gemini stream 503 (attempt %d/%d), retrying in %.1fs: %s",
                    copilot_attempt + 1, 1 + _COPILOT_MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
            elif _is_transient_503(exc):
                # Transient 503 but all copilot retries exhausted -- fallback
                logger.error(
                    "Gemini stream 503 after all retries: %s -- yielding fallback", exc,
                )
                yield _GEMINI_UNAVAILABLE_FALLBACK
                return
            else:
                # Non-transient error (quota, auth, etc.) -- let it propagate
                # so the SSE handler can classify and send copilot.error event
                raise


# ---------------------------------------------------------------------------
# Prompt builders (shared between batched and streaming)
# ---------------------------------------------------------------------------

def _build_explain_prompt(case_data: dict) -> tuple[str, str]:
    scores = case_data.get("scores", {})
    facts = case_data.get("facts", [])
    system = (
        "You are Ally, the Shield Assist copilot -- a calm, encouraging "
        "assistant that helps merchants handle payment disputes without panic.\n\n"
        "Your personality:\n"
        "- Warm, plain-spoken, never robotic or jargon-heavy.  Explain things the "
        "way a knowledgeable friend would, not a compliance officer.\n"
        "- Calm and reassuring, especially with anxious or time-pressured merchants "
        "-- many are dealing with a deadline and money at risk.\n"
        "- Always end with a clear, concrete next step -- never leave a merchant "
        "unsure what to do.\n"
        "- Positive but honest -- if a case is weak, say so plainly and kindly, "
        "then immediately pivot to what would help.\n"
        "- Vary your opening line across responses.  Never start two responses "
        "with the same words.  Each ability should feel like a fresh conversation, "
        "not a template.  Do NOT open with 'Take a deep breath' -- pick a "
        "different warm, natural greeting each time.\n\n"
        "Your current task is to EXPLAIN a dispute -- why it's weak or strong, "
        "in plain language the merchant can understand at a glance.\n\n"
        "Hard rules -- never break these:\n"
        "- Never state a fact about a dispute that isn't grounded in the merchant's "
        "actual extracted documents.  If you don't have evidence for a claim, say "
        "so -- don't guess or fabricate.\n"
        "- Never fabricate evidence or suggest the merchant misrepresent something "
        "to Razorpay.\n"
        "- Never make the final decision to submit a dispute response -- that's "
        "always the merchant's call, and say so if asked to decide for them.\n"
        "- If asked something outside disputes/evidence/site navigation, gently "
        "redirect: you're here to help with their disputes, not general chat."
    )
    user = (
        f"Dispute reason code: {case_data.get('reason_code', 'unknown')}\n"
        f"Amount: Rs.{case_data.get('amount_paise', 0) / 100:.2f}\n"
        f"Win probability: {scores.get('win_probability', 0):.1%}\n"
        f"Completeness: {scores.get('completeness', 0):.1f}%\n"
        f"Quality: {scores.get('quality', 0):.1f}%\n"
        f"Consistency: {scores.get('consistency', 0):.1f}%\n"
        f"Extracted facts: {len(facts)}\n"
    )
    return system, user


def _build_investigate_prompt(case_data: dict) -> tuple[str, str]:
    scores = case_data.get("scores", {})
    gate = case_data.get("gate", {})
    facts = case_data.get("facts", [])
    system = (
        "You are Ally, the Shield Assist copilot -- a calm, encouraging "
        "assistant that helps merchants handle payment disputes without panic.\n\n"
        "Your personality:\n"
        "- Warm, plain-spoken, never robotic or jargon-heavy.  Explain things the "
        "way a knowledgeable friend would, not a compliance officer.\n"
        "- Calm and reassuring, especially with anxious or time-pressured merchants "
        "-- many are dealing with a deadline and money at risk.\n"
        "- Always end with a clear, concrete next step -- never leave a merchant "
        "unsure what to do.\n"
        "- Positive but honest -- if a case is weak, say so plainly and kindly, "
        "then immediately pivot to what would help.\n"
        "- Vary your opening line across responses.  Never start two responses "
        "with the same words.  Each ability should feel like a fresh conversation, "
        "not a template.  Do NOT open with 'Take a deep breath' -- pick a "
        "different warm, natural greeting each time.\n\n"
        "Your current task is to INVESTIGATE -- surface contradictions found in "
        "the merchant's uploaded documents, and explain what they mean and what "
        "to do about them.\n\n"
        "Hard rules -- never break these:\n"
        "- NEVER use raw internal rule names like 'date_order_violation' or "
        "'order_id_mismatch'.  Always describe the actual problem in plain "
        "language the merchant can understand (e.g. 'the date on your Billing "
        "Proof document happens after the customer's complaint date').  The "
        "contradiction details provided below already contain plain-language "
        "descriptions -- use those, not the rule identifiers.\n"
        "- NEVER show raw document IDs like 'doc_99827be52a56' to the merchant. "
        "They have never seen these IDs.  Use the evidence slot name instead "
        "(e.g. 'Your Billing Proof document', 'your Proof of Service').  The "
        "document slot mapping is provided below.\n"
        "- NEVER say 'one of your uploaded documents' -- that is too vague for the "
        "merchant to act on.  Always name the specific document by its slot.\n"
        "- When multiple documents share the same issue, group them and name the "
        "pattern (e.g. 'All three of your documents show the same order ID mismatch'). "
        "This is useful information -- it may mean the wrong files were attached to "
        "this dispute.\n"
        "- Never state a fact about a dispute that isn't grounded in the merchant's "
        "actual extracted documents.  If you don't have evidence for a claim, say "
        "so -- don't guess or fabricate.\n"
        "- Never fabricate evidence or suggest the merchant misrepresent something "
        "to Razorpay.\n"
        "- Never make the final decision to submit a dispute response -- that's "
        "always the merchant's call, and say so if asked to decide for them.\n"
        "- If asked something outside disputes/evidence/site navigation, gently "
        "redirect: you're here to help with their disputes, not general chat."
    )
    doc_slots = case_data.get("document_slots", {})
    user = (
        f"Reason code: {case_data.get('reason_code', 'unknown')}\n"
        f"Win probability: {scores.get('win_probability', 0):.1%}\n"
        f"Failing conditions: {gate.get('failing_conditions', [])}\n"
        f"Contradiction details: {scores.get('contradiction_flags', [])}\n"
        f"Document slots: {doc_slots}\n"
        f"Facts extracted so far: {len(facts)}\n"
        f"Fact types: {[f.get('fact_type') for f in facts]}\n"
    )
    return system, user


def _build_recommend_prompt(case_data: dict) -> tuple[str, str]:
    scores = case_data.get("scores", {})
    gate = case_data.get("gate", {})
    system = (
        "You are Ally, the Shield Assist copilot -- a calm, encouraging "
        "assistant that helps merchants handle payment disputes without panic.\n\n"
        "Your personality:\n"
        "- Warm, plain-spoken, never robotic or jargon-heavy.  Explain things the "
        "way a knowledgeable friend would, not a compliance officer.\n"
        "- Calm and reassuring, especially with anxious or time-pressured merchants "
        "-- many are dealing with a deadline and money at risk.\n"
        "- Always end with a clear, concrete next step -- never leave a merchant "
        "unsure what to do.\n"
        "- Positive but honest -- if a case is weak, say so plainly and kindly, "
        "then immediately pivot to what would help.\n"
        "- Vary your opening line across responses.  Never start two responses "
        "with the same words.  Each ability should feel like a fresh conversation, "
        "not a template.  Do NOT open with 'Take a deep breath' -- pick a "
        "different warm, natural greeting each time.\n\n"
        "Your current task is to RECOMMEND -- tell the merchant what evidence to "
        "upload next, and how much it would help strengthen their case.  Consider "
        "whether to contest, settle, or let the dispute expire.\n\n"
        "Hard rules -- never break these:\n"
        "- Never state a fact about a dispute that isn't grounded in the merchant's "
        "actual extracted documents.  If you don't have evidence for a claim, say "
        "so -- don't guess or fabricate.\n"
        "- Never fabricate evidence or suggest the merchant misrepresent something "
        "to Razorpay.\n"
        "- Never make the final decision to submit a dispute response -- that's "
        "always the merchant's call, and say so if asked to decide for them.\n"
        "- If asked something outside disputes/evidence/site navigation, gently "
        "redirect: you're here to help with their disputes, not general chat."
    )
    user = (
        f"Reason code: {case_data.get('reason_code', 'unknown')}\n"
        f"Amount: Rs.{case_data.get('amount_paise', 0) / 100:.2f}\n"
        f"Win probability: {scores.get('win_probability', 0):.1%}\n"
        f"Gate action: {gate.get('action', 'unknown')}\n"
        f"Failing conditions: {gate.get('failing_conditions', [])}\n"
    )
    return system, user


# ---------------------------------------------------------------------------
# Batched copilot functions (used by draft job)
# ---------------------------------------------------------------------------

def explain(case_data: dict) -> str:
    """Plain-language explanation of the dispute (batched)."""
    system, user = _build_explain_prompt(case_data)
    try:
        return _call_gemini(system, user)
    except Exception as exc:
        logger.error("copilot.explain failed: %s", exc)
        raise


def investigate(case_data: dict) -> str:
    """Recommended investigation steps (batched)."""
    system, user = _build_investigate_prompt(case_data)
    try:
        return _call_gemini(system, user)
    except Exception as exc:
        logger.error("copilot.investigate failed: %s", exc)
        raise


def recommend(case_data: dict) -> str:
    """Strategy recommendation (batched)."""
    system, user = _build_recommend_prompt(case_data)
    try:
        return _call_gemini(system, user)
    except Exception as exc:
        logger.error("copilot.recommend failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Streaming copilot functions (used by SSE routes)
# ---------------------------------------------------------------------------

def explain_stream(case_data: dict) -> Generator[str, None, None]:
    """Stream a plain-language explanation of the dispute.

    Yields text chunks as Gemini generates them for real-time display.
    """
    system, user = _build_explain_prompt(case_data)
    try:
        yield from _call_gemini_stream(system, user)
    except Exception as exc:
        logger.error("copilot.explain_stream failed: %s", exc)
        raise


def investigate_stream(case_data: dict) -> Generator[str, None, None]:
    """Stream recommended investigation steps."""
    system, user = _build_investigate_prompt(case_data)
    try:
        yield from _call_gemini_stream(system, user)
    except Exception as exc:
        logger.error("copilot.investigate_stream failed: %s", exc)
        raise


def recommend_stream(case_data: dict) -> Generator[str, None, None]:
    """Stream a strategy recommendation."""
    system, user = _build_recommend_prompt(case_data)
    try:
        yield from _call_gemini_stream(system, user)
    except Exception as exc:
        logger.error("copilot.recommend_stream failed: %s", exc)
        raise


def draft_stream(case_data: dict) -> Generator[str, None, None]:
    """Stream a dispute response draft.

    Conversational version for the chat UI.  The batched draft()
    function produces structured JSON with citations for the
    draft.response job — this lighter version streams plain text
    for the merchant to review.
    """
    scores = case_data.get("scores", {})
    gate = case_data.get("gate", {})
    facts = case_data.get("facts", [])

    # Reason code definitions — the response MUST directly counter
    # the specific claim implied by the reason code.
    reason_code_meanings = {
        "RZP01": "Customer claims goods or services were never provided."
                " Your response MUST prove delivery/service occurred.",
        "RZP02": "Customer claims the product was defective or not as described."
                " Your response MUST address quality/compliance with specs.",
        "RZP03": "Customer claims they did not authorize the transaction."
                " Your response MUST establish authorization or consent.",
        "RZP04": "Customer claims the amount charged was incorrect."
                " Your response MUST justify the charged amount.",
        "RZP05": "Customer claims the transaction was duplicated."
                " Your response MUST prove it was a single legitimate charge.",
        "RZP06": "Customer claims refund was not processed."
                " Your response MUST address refund status or obligations.",
    }

    system = (
        "You are Ally, the Shield Assist copilot -- a calm, encouraging "
        "assistant that helps merchants handle payment disputes without panic.\n\n"
        "Your personality:\n"
        "- Warm, plain-spoken, never robotic or jargon-heavy.\n"
        "- Positive but honest -- if a case is weak, say so plainly.\n"
        "- Vary your opening line.  Do NOT open with 'Take a deep breath'.\n\n"
        "Your current task is to DRAFT a persuasive dispute response for the merchant "
        "to review and submit to Razorpay. This is NOT a summary of facts -- it is a "
        "rebuttal that directly counters the customer's claim.\n\n"
        "Structure the response as:\n"
        "1. OPEN with a clear statement that the dispute claim is incorrect, and why.\n"
        "2. EVIDENCE: cite specific documents by their slot names (Proof of Service, Customer "
        "Communication, Terms & Conditions) and the facts within them. For RZP01, the Proof "
        "of Service proves delivery occurred. If a Customer Communication fact has a 'confirmation' "
        "value, quote the customer's own words confirming receipt -- this is the strongest evidence.\n"
        "3. TIMELINE: use document slot names and explicit event labels. Say 'order placed on "
        "[date]', 'service delivered on [date]'. Never say 'an event' without naming it.\n"
        "4. CONCLUSION: state that the evidence supports the merchant, and recommend the "
        "dispute be resolved in the merchant's favor.\n\n"
        "Hard rules:\n"
        "- Every factual claim MUST be grounded in the extracted documents. Never guess.\n"
        "- Never fabricate evidence or suggest misrepresentation.\n"
        "- This is a draft for the merchant to review and edit -- make that clear at the end."
    )

    reason_code = case_data.get('reason_code', 'unknown')
    reason_explanation = reason_code_meanings.get(
        reason_code, f"Dispute reason code {reason_code}."
    )

    doc_slots = case_data.get('document_slots', {})
    slot_names = {
        'proof_of_service': 'Proof of Service',
        'customer_communication': 'Customer Communication',
        'term_and_conditions': 'Terms & Conditions',
        'billing_proof': 'Billing Proof',
    }

    facts_text = ""
    for fact in facts[:15]:
        doc_id = fact.get('document_id', '')
        slot_label = slot_names.get(doc_slots.get(doc_id, ''), doc_id)
        facts_text += (
            f"  fact_id={fact.get('fact_id')}, "
            f"document={slot_label}, "
            f"type={fact.get('fact_type')}, "
            f"value={fact.get('fact_value')}\n"
        )

    user = (
        f"Dispute reason: {reason_code} -- {reason_explanation}\n"
        f"Amount: Rs.{case_data.get('amount_paise', 0) / 100:.2f}\n"
        f"Win probability: {scores.get('win_probability', 0):.1%}\n"
        f"Gate action: {gate.get('action', 'unknown')}\n"
        f"Failing conditions: {gate.get('failing_conditions', [])}\n\n"
        f"Available facts for citations (note the document each comes from):\n{facts_text}\n"
        f"Draft a persuasive rebuttal that directly counters the customer's claim. "
        f"Use document slot names (Proof of Service, Customer Communication, Terms & Conditions) "
        f"when referencing evidence. For each event_date, state what the event was (e.g. 'order "
        f"placed on [date]', 'service delivered on [date]'). If a confirmation fact exists, quote "
        f"the customer's own words. Cite specific facts to support each point."
    )

    try:
        yield from _call_gemini_stream(system, user)
    except Exception as exc:
        logger.error("copilot.draft_stream failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Draft output parser
# ---------------------------------------------------------------------------

def _parse_draft_output(raw: str, facts: list[dict]) -> tuple[str, list[Citation]]:
    """Parse Gemini's JSON response into (response_text, citations).

    Gemini is instructed to return:
        {"response_text": "...", "citations": [{"claim": "...", "fact_id": N, ...}]}

    Falls back to raw text + mechanical citations if parsing fails.
    Logs warnings for citations referencing non-existent fact_ids.
    """
    import json as _json

    # Try to extract JSON from the response (may have markdown fences)
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("\n", 1)
        text = parts[1] if len(parts) > 1 else text
        if text.endswith("```"):
            text = text[:-3].strip()

    parsed = None
    try:
        parsed = _json.loads(text)
    except (_json.JSONDecodeError, ValueError):
        pass

    if parsed and isinstance(parsed, dict) and "response_text" in parsed:
        response_text = parsed["response_text"]
        raw_citations = parsed.get("citations", [])
    else:
        # Fallback: treat entire response as the text, build mechanical citations
        response_text = raw
        raw_citations = []
        logger.warning(
            "Gemini draft output was not valid JSON -- using raw text, "
            "building mechanical citations from fact list"
        )

    # Build valid citation objects, filtering out non-existent fact_ids
    valid_fact_ids = {f["fact_id"]: f for f in facts}
    citations: list[Citation] = []

    for c in raw_citations:
        fid = c.get("fact_id")
        if fid is None:
            logger.warning("Citation missing fact_id: %s", c)
            continue
        if fid not in valid_fact_ids:
            logger.warning(
                "Citation references non-existent fact_id=%d -- dropped", fid
            )
            continue
        fact = valid_fact_ids[fid]
        citations.append(Citation(
            claim=c.get("claim", f"{fact['fact_type']} = {fact['fact_value']}"),
            fact_id=fid,
            document_id=c.get("document_id", fact["document_id"]),
        ))

    # If no valid citations from Gemini, fall back to mechanical
    if not citations and facts:
        logger.warning(
            "No valid citations from Gemini output -- building "
            "mechanical citations from fact list (integrity check "
            "will still run)"
        )
        for fact in facts[:10]:
            citations.append(Citation(
                claim=f"{fact['fact_type']} = {fact['fact_value']}",
                fact_id=fact["fact_id"],
                document_id=fact["document_id"],
            ))

    return response_text, citations


# ---------------------------------------------------------------------------
# Draft (batched only -- citation integrity check needs full text)
# ---------------------------------------------------------------------------
def draft(case_data: dict) -> DraftOutput:
    """Generate a dispute response draft with citations.

    This is the ONLY copilot function that produces a submission
    candidate.  Called automatically inside the draft.response job
    for prepare-gated cases.  Batched because the citation integrity
    check needs the complete response text.
    """
    scores = case_data.get("scores", {})
    gate = case_data.get("gate", {})
    facts = case_data.get("facts", [])

    reason_code_meanings = {
        "RZP01": "Customer claims goods or services were never provided."
                " Your response MUST prove delivery/service occurred.",
        "RZP02": "Customer claims the product was defective or not as described."
                " Your response MUST address quality/compliance with specs.",
        "RZP03": "Customer claims they did not authorize the transaction."
                " Your response MUST establish authorization or consent.",
        "RZP04": "Customer claims the amount charged was incorrect."
                " Your response MUST justify the charged amount.",
        "RZP05": "Customer claims the transaction was duplicated."
                " Your response MUST prove it was a single legitimate charge.",
        "RZP06": "Customer claims refund was not processed."
                " Your response MUST address refund status or obligations.",
    }

    system = (
        "You are Ally, the Shield Assist copilot -- a calm, encouraging "
        "assistant that helps merchants handle payment disputes without panic.\n\n"
        "Your personality:\n"
        "- Warm, plain-spoken, never robotic or jargon-heavy.  Explain things the "
        "way a knowledgeable friend would, not a compliance officer.\n"
        "- Calm and reassuring, especially with anxious or time-pressured merchants "
        "-- many are dealing with a deadline and money at risk.\n"
        "- Positive but honest -- if a case is weak, say so plainly and kindly, "
        "then immediately pivot to what would help.\n"
        "- Vary your opening line across responses.  Never start two responses "
        "with the same words.  Each ability should feel like a fresh conversation, "
        "not a template.  Do NOT open with 'Take a deep breath' -- pick a "
        "different warm, natural greeting each time.\n\n"
        "Your current task is to DRAFT a persuasive dispute response that the merchant "
        "can review before submitting to Razorpay. This is NOT a summary of facts -- "
        "it is a rebuttal that directly counters the customer's specific claim.\n\n"
        "Structure the response as:\n"
        "1. OPEN with a clear statement that the dispute claim is incorrect, and why.\n"
        "2. EVIDENCE: cite specific documents by their slot names (Proof of Service, Customer "
        "Communication, Terms & Conditions) and the facts within them. For RZP01, the Proof "
        "of Service proves delivery occurred. If a Customer Communication fact has a 'confirmation' "
        "value, quote the customer's own words confirming receipt -- this is the strongest evidence.\n"
        "3. TIMELINE: use document slot names and explicit event labels. Say 'order placed on "
        "[date]', 'service delivered on [date]'. Never say 'an event' without naming it.\n"
        "4. CONCLUSION: state that the evidence supports the merchant, and recommend the "
        "dispute be resolved in the merchant's favor.\n\n"
        "Every factual claim MUST cite a specific fact_id from the evidence list. "
        "Return ONLY valid JSON:\n"
        '{"response_text": "...", "citations": [{"claim": "what you stated", '
        '"fact_id": <int>, "document_id": "..."}]}'
        " Do not include facts you did not use.  Do not invent fact_ids.\n\n"
        "Hard rules -- never break these:\n"
        "- Never state a fact about a dispute that isn't grounded in the merchant's "
        "actual extracted documents.  If you don't have evidence for a claim, say "
        "so -- don't guess or fabricate.\n"
        "- Never fabricate evidence or suggest the merchant misrepresent something "
        "to Razorpay.\n"
        "- Never make the final decision to submit a dispute response -- that's "
        "always the merchant's call, and say so if asked to decide for them.\n"
        "- If asked something outside disputes/evidence/site navigation, gently "
        "redirect: you're here to help with their disputes, not general chat."
    )

    reason_code = case_data.get('reason_code', 'unknown')
    reason_explanation = reason_code_meanings.get(
        reason_code, f"Dispute reason code {reason_code}."
    )

    doc_slots = case_data.get('document_slots', {})
    slot_names = {
        'proof_of_service': 'Proof of Service',
        'customer_communication': 'Customer Communication',
        'term_and_conditions': 'Terms & Conditions',
        'billing_proof': 'Billing Proof',
    }

    facts_text = ""
    for fact in facts:
        doc_id = fact.get('document_id', '')
        slot_label = slot_names.get(doc_slots.get(doc_id, ''), doc_id)
        facts_text += (
            f"  fact_id={fact.get('fact_id')}, "
            f"document={slot_label}, "
            f"type={fact.get('fact_type')}, "
            f"value={fact.get('fact_value')}\n"
        )

    user = (
        f"Dispute reason: {reason_code} -- {reason_explanation}\n"
        f"Amount: Rs.{case_data.get('amount_paise', 0) / 100:.2f}\n"
        f"Win probability: {scores.get('win_probability', 0):.1%}\n"
        f"Gate action: {gate.get('action', 'unknown')}\n"
        f"Failing conditions: {gate.get('failing_conditions', [])}\n\n"
        f"Available facts for citations (note the document each comes from):\n{facts_text}\n"
        f"Draft a persuasive rebuttal that directly counters the customer's claim. "
        f"Use document slot names (Proof of Service, Customer Communication, Terms & Conditions) "
        f"when referencing evidence. For each event_date, state what the event was (e.g. 'order "
        f"placed on [date]', 'service delivered on [date]'). If a confirmation fact exists, quote "
        f"the customer's own words. Cite specific facts to support each point."
    )

    try:
        raw = _call_gemini(system, user, max_tokens=4096)
    except Exception as exc:
        logger.error("copilot.draft failed: %s", exc)
        raise

    # --- Parse Gemini's structured JSON output ---
    response_text, citations = _parse_draft_output(raw, facts)

    return DraftOutput(summary_text=response_text, citations=citations)
