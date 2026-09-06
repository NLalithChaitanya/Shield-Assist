"""
backend/copilot.py

LLM copilot layer for Shield Assist -- four capabilities powered by
Google Gemini (free tier) with automatic Groq failover:

  explain()         / explain_stream()       -- dispute summary
  investigate()     / investigate_stream()   -- investigation steps
  recommend()       / recommend_stream()     -- strategy recommendation
  draft()                                  -- response with citations (batched only)

Batched functions: return the full response as a string.  Used by the
draft.response job (which needs the complete text for citation integrity
checking).

Streaming functions: yield text chunks as the LLM generates them.  Used
by the SSE copilot routes for real-time display in the frontend.

Provider layout (all failover is inside _call_gemini/_call_gemini_stream):
  1. Gemini (primary) -- resilience wrapper retries + copilot 503 retries.
  2. On ANY Gemini failure: Groq (backup, GROQ_API_KEY/GROQ_MODEL).
  3. Both down: honest "Ally temporarily unavailable" fallback text for
     streams (never a raw error, never fabricated evidence); a raised
     error for batched calls so the draft job's grounded template
     fallback applies.
  Gemini OCR for document extraction (document.process) does NOT route
  through Groq -- it stays Gemini-only.

All functions receive ONLY structured facts -- never raw documents.
This is "Evidence Integrity Mode": the copilot can only reference facts
that have been extracted and stored, not hallucinate from raw images.

AI providers: Google Gemini (primary) + Groq (automatic failover).
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
        # Chat context: a different question asked against the same case
        # state must produce a different response, not a cached replay.
        "question": case_data.get("question", ""),
        "history": case_data.get("history", []),
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
# AI provider clients (Gemini primary + Groq automatic failover)
# ---------------------------------------------------------------------------

_resilient_client = None
_model_name = None
_groq_client = None
_groq_model_name = None


def _get_gemini_client():
    """Get a resilience-wrapped Gemini client via the google-genai SDK.

    Returns (ResilientClient, model_name).  The ResilientClient wraps
    the raw genai.Client with circuit breaker (5 failures -> open -> 30s)
    + rate limiter (14 RPM) + retry with backoff.  This is the ONLY
    place a Gemini client is created.

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


def _get_groq_client():
    """Get a resilience-wrapped Groq client (automatic failover provider).

    Returns (ResilientClient | None, model_name | None).  Returns
    (None, None) when GROQ_API_KEY is not set (or the groq package is
    not installed) so the system degrades gracefully to Gemini-only
    behavior -- the original code path is preserved exactly.

    Groq is the BACKUP provider only: copilot chat + draft requests fail
    over here when Gemini errors (503 / timeout / quota / rate limit).
    Gemini OCR for document extraction is NOT routed here.
    """
    global _groq_client, _groq_model_name

    if _groq_client is not None:
        return _groq_client, _groq_model_name

    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    _groq_model_name = (
        os.environ.get("GROQ_MODEL", "").strip() or "llama-3.3-70b-versatile"
    )

    if not api_key:
        logger.debug("GROQ_API_KEY not set -- Groq failover disabled")
        return None, None

    try:
        from groq import Groq  # noqa: F401
    except ImportError:
        logger.error(
            "GROQ_API_KEY is set but the 'groq' package is not installed. "
            "Run: pip install groq -- Groq failover disabled"
        )
        return None, None

    from backend.resilience import wrap_groq_client
    # max_retries=0: the ResilientClient wrapper owns retry/backoff, so the
    # raw SDK must not double-retry underneath it.
    raw_client = Groq(api_key=api_key, timeout=60.0, max_retries=0)
    _groq_client = wrap_groq_client(raw_client)
    logger.info("Groq failover client initialized (model=%s)", _groq_model_name)
    return _groq_client, _groq_model_name


# ---------------------------------------------------------------------------
# AI call helpers (Gemini primary, Groq failover, honest fallback)
# ---------------------------------------------------------------------------

# Fallback message used when BOTH providers are unavailable.  It never
# fabricates evidence -- it tells the user what happened and asks them
# to retry.  app.py refuses to cache this text as a real answer.
_GEMINI_UNAVAILABLE_FALLBACK = (
    "Ally is temporarily unavailable because the AI service is experiencing "
    "high demand.  Your evidence analysis is still available.  "
    "Please retry in a few seconds."
)

# Maximum copilot-level retries for transient 503 errors on the PRIMARY
# provider (on top of the resilience wrapper's own retries).  After
# these are exhausted the request fails over to Groq.
_COPILOT_MAX_RETRIES = 1


class _GroqNotConfiguredError(RuntimeError):
    """Raised when Groq failover is attempted but GROQ_API_KEY is unset."""


def _is_transient_503(exc: Exception) -> bool:
    """Check if an exception is a transient 503 / service-unavailable error."""
    from backend.resilience import classify_gemini_error
    info = classify_gemini_error(exc)
    return info["error_type"] == "service_unavailable" and info["recoverable"]


def _is_recoverable_failure(exc: Exception) -> bool:
    """True when an AI-provider error will resolve on its own.

    Covers service unavailable (503), timeouts, rate limits (429), and
    circuit-breaker trips -- but NOT quota exhaustion / auth errors.
    Used to decide between yielding the honest fallback message vs
    re-raising so the SSE layer reports the specific error type.
    """
    from backend.resilience import classify_gemini_error
    return classify_gemini_error(exc)["recoverable"]


def _call_gemini_primary_batch(
    system_prompt: str, user_prompt: str, max_tokens: int = 2048
) -> str:
    """Call Gemini and return the full response text (batched, NO failover).

    Goes through ResilientClient.call() -- circuit breaker, rate limiter,
    and retry are all active.  Raises on failure; the caller (_call_gemini)
    decides whether to fail over to Groq.
    """
    client, model_name = _get_gemini_client()
    logger.debug("Gemini batch (primary): model=%s", model_name)

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


def _call_gemini_primary_stream(
    system_prompt: str, user_prompt: str, max_tokens: int = 2048
):
    """Stream from Gemini (primary, NO failover).

    Retries transient 503s with backoff (_COPILOT_MAX_RETRIES).  Any
    final failure -- transient-exhausted or not -- raises so the caller
    (_call_gemini_stream) can fail over to Groq or yield the honest
    fallback message.
    """
    import time
    client, model_name = _get_gemini_client()
    logger.debug("Gemini stream (primary): model=%s", model_name)

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
            if _is_transient_503(exc) and copilot_attempt < _COPILOT_MAX_RETRIES:
                # Transient 503: retry with backoff
                delay = 2 ** (copilot_attempt + 1)  # 2s, 4s
                logger.warning(
                    "Gemini stream 503 (attempt %d/%d), retrying in %.1fs: %s",
                    copilot_attempt + 1, 1 + _COPILOT_MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
            else:
                # Exhausted transient retries OR non-transient error
                # (quota, auth, timeout, unknown) -- let the failover
                # wrapper decide what to do next.
                raise


# ---------------------------------------------------------------------------
# Groq call helpers (failover provider -- OpenAI-compatible chat API)
# ---------------------------------------------------------------------------

def _groq_messages(system_prompt: str, user_prompt: str) -> list[dict]:
    """Translate copilot prompts into Groq's chat message format.

    The system/user prompt text is IDENTICAL to what Gemini receives,
    so grounding behavior and citation rules do not change per provider.
    """
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _call_groq_batch(
    system_prompt: str, user_prompt: str, max_tokens: int = 2048
) -> str:
    """Call Groq and return the full response text (batched).

    Runs through ResilientClient (circuit breaker + rate limiter +
    retry).  Raises on failure -- callers fall through to the honest
    unavailable message / job-level template.
    """
    client, model_name = _get_groq_client()
    if client is None:
        raise _GroqNotConfiguredError(
            "GROQ_API_KEY not set -- cannot use Groq failover"
        )
    logger.debug("Groq batch (failover): model=%s", model_name)

    response = client.call(
        "chat.completions.create",
        model=model_name,
        messages=_groq_messages(system_prompt, user_prompt),
        max_tokens=max_tokens,
        temperature=0.7,
    )
    choices = getattr(response, "choices", None) or []
    if not choices:
        return ""
    return getattr(getattr(choices[0], "message", None), "content", None) or ""


def _call_groq_stream(
    system_prompt: str, user_prompt: str, max_tokens: int = 2048
):
    """Stream from Groq as text chunks (failover provider)."""
    client, model_name = _get_groq_client()
    if client is None:
        raise _GroqNotConfiguredError(
            "GROQ_API_KEY not set -- cannot use Groq failover"
        )
    logger.debug("Groq stream (failover): model=%s", model_name)

    stream = client.call(
        "chat.completions.create",
        model=model_name,
        messages=_groq_messages(system_prompt, user_prompt),
        max_tokens=max_tokens,
        temperature=0.7,
        stream=True,
    )
    for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None)
        if content:
            yield content


# ---------------------------------------------------------------------------
# Conversation context (question + recent turns) for chat answers
# ---------------------------------------------------------------------------
# Root cause of "Ally feels static": the merchant's actual question and
# earlier turns were never sent to the model -- each ability answered a
# fixed pre-written prompt.  These helpers attach the live question and
# the recent conversation to the user prompt so answers are tailored to
# what was just asked, while keeping every claim grounded in the
# extracted facts that are already in the prompt.

_MAX_CONTEXT_MESSAGES = 6
_MAX_CONTEXT_CHARS = 500


def _conversation_block(case_data: dict) -> str:
    """Build a 'merchant asked + recent conversation' block, or '' if none."""
    question = str(case_data.get("question") or "").strip()[:_MAX_CONTEXT_CHARS]
    history = case_data.get("history") or []
    if not question and not history:
        return ""

    parts: list[str] = []
    if history:
        lines = []
        for m in history[-_MAX_CONTEXT_MESSAGES:]:
            role = m.get("role") if isinstance(m, dict) else ""
            text = str(m.get("text") or "")[: _MAX_CONTEXT_CHARS]
            if role in ("user", "assistant") and text:
                who = "Merchant" if role == "user" else "Ally"
                lines.append(f"{who}: {text}")
        if lines:
            parts.append(
                "Recent conversation (oldest first):\n" + "\n".join(lines)
            )

    if question:
        parts.append(f"The merchant's latest question is:\n{question}")

    if question:
        parts.append(
            "Answer THAT question directly. It may go beyond the task described "
            "above -- that is fine. Hard rule: every factual claim must stay "
            "grounded in the extracted facts and case data provided; never "
            "invent facts, amounts, dates, document IDs, or citations. If the "
            "question asks something the case data cannot answer, say so plainly."
        )
    else:
        parts.append(
            "Use the conversation above for context about this case while "
            "carrying out your task. Hard rule: every factual claim must stay "
            "grounded in the extracted facts and case data provided; never "
            "invent facts, amounts, dates, document IDs, or citations."
        )
    return "\n\n---\n\n" + "\n\n".join(parts)


def _append_conversation(user_prompt: str, case_data: dict | None) -> str:
    """Append the question/history block to a user prompt when present."""
    if not case_data:
        return user_prompt
    block = _conversation_block(case_data)
    if not block:
        return user_prompt
    return user_prompt.rstrip() + block


# ---------------------------------------------------------------------------
# Failover wrappers (public call surface -- Gemini first, Groq second)
# ---------------------------------------------------------------------------

def _call_gemini(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
    case_data: dict | None = None,
) -> str:
    """Call the LLM and return the full response text (batched, failover).

    Order:
      1. Gemini (primary) with resilience retries.
      2. On ANY Gemini failure: Groq failover.
      3. If Groq is not configured: re-raise the original Gemini error
         (preserves pre-failover behavior for batched callers).
      4. If both providers fail: raise the Groq error -- the draft
         job applies its grounded template fallback from there, and
         returning fabricated/fallback text as a "real draft" would be
         wrong.

    When case_data contains the merchant's live 'question' / 'history',
    that context is appended to the user prompt so the answer addresses
    what was actually asked (chat mode).
    """
    user_prompt = _append_conversation(user_prompt, case_data)
    try:
        return _call_gemini_primary_batch(system_prompt, user_prompt, max_tokens)
    except Exception as exc:
        client, _model = _get_groq_client()
        if client is None:
            logger.error(
                "Gemini batch failed (%s) and Groq failover is not "
                "configured -- raising", exc,
            )
            raise
        logger.warning(
            "Gemini batch failed (%s) -- failing over to Groq", exc,
        )
        try:
            return _call_groq_batch(system_prompt, user_prompt, max_tokens)
        except Exception as groq_exc:
            logger.error(
                "Gemini AND Groq both failed. Gemini: %s -- Groq: %s",
                exc, groq_exc,
            )
            raise groq_exc from exc


def _call_gemini_stream(
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2048,
    case_data: dict | None = None,
):
    """Stream a copilot response as text chunks (failover).

    Order:
      1. Gemini (primary) -- resilience retries + _COPILOT_MAX_RETRIES.
      2. If Gemini fails BEFORE any text was streamed: retry on Groq.
      3. If both fail: yield the honest _GEMINI_UNAVAILABLE_FALLBACK
         message (never a raw error, never fabricated evidence).
      4. If Gemini dies MID-STREAM after partial text: re-raise so the
         SSE layer emits an error event (no concatenated two-provider
         reply).

    When case_data contains the merchant's live 'question' / 'history',
    that context is appended to the user prompt (chat mode).
    """
    user_prompt = _append_conversation(user_prompt, case_data)
    yielded_any = False
    try:
        for chunk in _call_gemini_primary_stream(
            system_prompt, user_prompt, max_tokens
        ):
            yielded_any = True
            yield chunk
        return  # Gemini succeeded
    except Exception as primary_exc:
        if yielded_any:
            # Gemini streamed partial text then failed mid-stream -- do NOT
            # concatenate a second provider's reply onto the partial one.
            logger.error(
                "Gemini stream interrupted after partial text: %s", primary_exc,
            )
            raise

        recoverable = _is_recoverable_failure(primary_exc)
        client, _model = _get_groq_client()
        if client is None:
            # No backup provider configured.
            if recoverable:
                # Transient (503 / timeout / rate limit / circuit breaker):
                # yield the honest fallback -- the browser never sees a raw
                # provider error (pre-existing behavior, preserved).
                logger.error(
                    "Gemini unavailable (%s) and Groq failover is not "
                    "configured -- yielding honest fallback", primary_exc,
                )
                yield _GEMINI_UNAVAILABLE_FALLBACK
                return
            # Non-transient (quota, auth): re-raise so the SSE layer can
            # classify and send the ability-specific copilot.error event.
            logger.error(
                "Gemini non-transient failure (%s) and Groq failover is not "
                "configured -- raising", primary_exc,
            )
            raise

        logger.warning(
            "Gemini stream failed (%s) -- failing over to Groq", primary_exc,
        )
        try:
            for chunk in _call_groq_stream(system_prompt, user_prompt, max_tokens):
                yield chunk
        except Exception as groq_exc:
            logger.error(
                "Gemini AND Groq both failed. Gemini: %s -- Groq: %s",
                primary_exc, groq_exc,
            )
            if recoverable:
                yield _GEMINI_UNAVAILABLE_FALLBACK
            else:
                # Original error was non-transient (e.g. quota) -- surface
                # it so the frontend shows the specific, honest state.
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
        return _call_gemini(system, user, case_data=case_data)
    except Exception as exc:
        logger.error("copilot.explain failed: %s", exc)
        raise


def investigate(case_data: dict) -> str:
    """Recommended investigation steps (batched)."""
    system, user = _build_investigate_prompt(case_data)
    try:
        return _call_gemini(system, user, case_data=case_data)
    except Exception as exc:
        logger.error("copilot.investigate failed: %s", exc)
        raise


def recommend(case_data: dict) -> str:
    """Strategy recommendation (batched)."""
    system, user = _build_recommend_prompt(case_data)
    try:
        return _call_gemini(system, user, case_data=case_data)
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
        yield from _call_gemini_stream(system, user, case_data=case_data)
    except Exception as exc:
        logger.error("copilot.explain_stream failed: %s", exc)
        raise


def investigate_stream(case_data: dict) -> Generator[str, None, None]:
    """Stream recommended investigation steps."""
    system, user = _build_investigate_prompt(case_data)
    try:
        yield from _call_gemini_stream(system, user, case_data=case_data)
    except Exception as exc:
        logger.error("copilot.investigate_stream failed: %s", exc)
        raise


def recommend_stream(case_data: dict) -> Generator[str, None, None]:
    """Stream a strategy recommendation."""
    system, user = _build_recommend_prompt(case_data)
    try:
        yield from _call_gemini_stream(system, user, case_data=case_data)
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
        yield from _call_gemini_stream(system, user, case_data=case_data)
    except Exception as exc:
        logger.error("copilot.draft_stream failed: %s", exc)
        raise


# ---------------------------------------------------------------------------
# Draft output parser
# ---------------------------------------------------------------------------

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
    import re

    text = raw.strip()

    # Try to extract JSON from markdown fences or regex match for outer {...}
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1).strip()
    elif text.startswith("```"):
        parts = text.split("\n", 1)
        text = parts[1] if len(parts) > 1 else text
        if text.endswith("```"):
            text = text[:-3].strip()

    parsed = None
    try:
        parsed = _json.loads(text)
    except (_json.JSONDecodeError, ValueError):
        json_obj_match = re.search(r"(\{.*\})", text, re.DOTALL)
        if json_obj_match:
            try:
                parsed = _json.loads(json_obj_match.group(1))
            except (_json.JSONDecodeError, ValueError):
                pass

    response_text = ""
    raw_citations = []

    if isinstance(parsed, dict):
        response_text = parsed.get("response_text") or parsed.get("summary_text") or parsed.get("text") or ""
        raw_citations = parsed.get("citations", [])

    # If response_text is empty or still contains an unparsed raw JSON string wrapper
    if not response_text:
        response_text = raw

    # Extra safety check: if response_text is a JSON string containing "response_text":
    if response_text.strip().startswith("{") and "response_text" in response_text:
        try:
            inner_parsed = _json.loads(response_text)
            if isinstance(inner_parsed, dict) and "response_text" in inner_parsed:
                response_text = inner_parsed["response_text"]
                if not raw_citations and "citations" in inner_parsed:
                    raw_citations = inner_parsed.get("citations", [])
        except Exception:
            resp_match = re.search(r'"response_text"\s*:\s*"((?:[^"\\]|\\.)*)"', response_text)
            if resp_match:
                response_text = resp_match.group(1).encode('utf-8').decode('unicode_escape')

    if not response_text:
        response_text = raw

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
        raw = _call_gemini(system, user, max_tokens=4096, case_data=case_data)
    except Exception as exc:
        logger.error("copilot.draft failed: %s", exc)
        raise

    # --- Parse Gemini's structured JSON output ---
    response_text, citations = _parse_draft_output(raw, facts)

    return DraftOutput(summary_text=response_text, citations=citations)
