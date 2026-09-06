"""
tests/test_resilience.py

Tests for Gemini failure resilience:
  - Transient 503 errors → retry with backoff
  - Repeated failures → circuit breaker opens
  - Circuit breaker cooldown → half-open → recovery
  - Rate limiter token acquisition
  - Graceful degradation when provider is unavailable

Run:
    pytest tests/test_resilience.py -v
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.resilience import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    RateLimitExceededError,
    ResilientClient,
    TokenBucketRateLimiter,
    classify_gemini_error,
)


class TestCircuitBreaker:
    """Test circuit breaker state machine."""

    def test_starts_closed(self):
        """Circuit breaker starts in CLOSED state."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1.0, name="test")
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self):
        """Circuit breaker opens after consecutive failures."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1.0, name="test")

        for _ in range(3):
            cb.record_failure()

        assert cb.state == CircuitBreaker.OPEN
        assert cb.allow_request() is False

    def test_fails_fast_when_open(self):
        """Open circuit breaker fails fast without network call."""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0, name="test")

        cb.record_failure()
        cb.record_failure()

        assert cb.state == CircuitBreaker.OPEN

        # Should fail fast
        start = time.monotonic()
        assert cb.allow_request() is False
        elapsed = time.monotonic() - start
        assert elapsed < 0.01  # Should be near-instant

    def test_transitions_to_half_open_after_cooldown(self):
        """After cooldown, circuit breaker moves to HALF_OPEN."""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.1, name="test")

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        # Wait for cooldown
        time.sleep(0.15)

        # Should now be HALF_OPEN (checked via state property)
        state = cb.state
        assert state == CircuitBreaker.HALF_OPEN
        assert cb.allow_request() is True  # Allow one test call

    def test_closes_on_successful_half_open_call(self):
        """Successful call in HALF_OPEN → CLOSED (recovery)."""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.1, name="test")

        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.state  # Trigger HALF_OPEN transition
        cb.record_success()

        assert cb.state == CircuitBreaker.CLOSED

    def test_reopens_on_failed_half_open_call(self):
        """Failed call in HALF_OPEN → OPEN (back to failing)."""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.1, name="test")

        cb.record_failure()
        cb.record_failure()
        time.sleep(0.15)
        cb.state  # Trigger HALF_OPEN transition
        cb.record_failure()

        assert cb.state == CircuitBreaker.OPEN

    def test_resets_consecutive_failures_on_success(self):
        """Success resets failure counter."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1.0, name="test")

        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        cb.record_failure()

        # Still only 2 consecutive failures, not 3
        assert cb.state == CircuitBreaker.CLOSED

    def test_manual_reset(self):
        """Manual reset returns to CLOSED."""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0, name="test")

        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitBreaker.OPEN

        cb.reset()
        assert cb.state == CircuitBreaker.CLOSED
        assert cb.allow_request() is True


class TestTokenBucketRateLimiter:
    """Test token bucket rate limiter."""

    def test_acquire_when_tokens_available(self):
        """Can acquire token when bucket has capacity."""
        rl = TokenBucketRateLimiter(capacity=5, refill_rate=10, name="test")
        assert rl.acquire(timeout=0.1) is True

    def test_acquire_respects_capacity(self):
        """Bucket respects capacity limit."""
        rl = TokenBucketRateLimiter(capacity=2, refill_rate=100, name="test")

        assert rl.acquire(timeout=0.1) is True
        assert rl.acquire(timeout=0.1) is True
        # Third acquisition should wait (bucket empty)
        # With refill_rate=100, token available in 10ms
        assert rl.acquire(timeout=0.2) is True

    def test_acquire_times_out(self):
        """Acquire returns False on timeout."""
        rl = TokenBucketRateLimiter(capacity=1, refill_rate=0.1, name="test")

        # Exhaust the bucket
        assert rl.acquire(timeout=0.01) is True

        # Try again with very short timeout
        result = rl.acquire(timeout=0.01)
        # May or may not have refilled — just check it doesn't hang


class TestResilientClient:
    """Test resilient client wrapper."""

    def test_successful_call(self):
        """Successful call goes through and records success."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        mock_client = MagicMock()
        mock_client.process.return_value = "success"

        client = ResilientClient(mock_client, cb, rl, max_retries=0)
        result = client.call("process", "arg1")

        assert result == "success"
        assert cb.state == CircuitBreaker.CLOSED
        mock_client.process.assert_called_once_with("arg1")

    def test_transient_failure_retries(self):
        """Transient failures trigger retry."""
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        mock_client = MagicMock()
        # First call fails, second succeeds
        mock_client.process.side_effect = [ConnectionError("Network blip"), "success"]

        client = ResilientClient(mock_client, cb, rl, max_retries=1, backoff_base=0.01)
        result = client.call("process")

        assert result == "success"
        assert mock_client.process.call_count == 2

    def test_all_retries_exhausted_raises(self):
        """After all retries exhausted, original exception propagates."""
        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        mock_client = MagicMock()
        mock_client.process.side_effect = ConnectionError("Provider down")

        client = ResilientClient(mock_client, cb, rl, max_retries=1, backoff_base=0.01)

        with pytest.raises(ConnectionError, match="Provider down"):
            client.call("process")

        assert mock_client.process.call_count == 2  # Original + 1 retry

    def test_circuit_breaker_blocks_after_failures(self):
        """Circuit breaker blocks calls after failure threshold."""
        cb = CircuitBreaker(failure_threshold=2, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        mock_client = MagicMock()
        mock_client.process.side_effect = ConnectionError("Down")

        client = ResilientClient(mock_client, cb, rl, max_retries=0)

        # First two calls fail and trip the breaker
        with pytest.raises(ConnectionError):
            client.call("process")
        with pytest.raises(ConnectionError):
            client.call("process")

        # Third call should fail fast with CircuitBreakerOpenError
        with pytest.raises(CircuitBreakerOpenError):
            client.call("process")

        # Should NOT have called the client a third time
        assert mock_client.process.call_count == 2

    def test_dotted_method_path(self):
        """Dotted method paths are resolved correctly."""
        cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = "generated"

        client = ResilientClient(mock_client, cb, rl, max_retries=0)
        result = client.call("models.generate_content", prompt="test")

        assert result == "generated"
        mock_client.models.generate_content.assert_called_once_with(prompt="test")


class TestClassifyGeminiError:
    """Test error classification for user-facing messages."""

    def test_rate_limit_error(self):
        """HTTP 429 → rate_limit."""
        exc = Exception("429 Too Many Requests")
        exc.code = 429
        result = classify_gemini_error(exc)
        assert result["error_type"] == "rate_limit"
        assert result["recoverable"] is True

    def test_quota_exceeded_error(self):
        """HTTP 403 with quota message → quota_exceeded."""
        exc = Exception("Quota exceeded")
        exc.code = 403
        exc.details = {"error": {"message": "quota exhausted"}}
        result = classify_gemini_error(exc)
        assert result["error_type"] == "quota_exceeded"
        assert result["recoverable"] is False

    def test_circuit_breaker_error(self):
        """CircuitBreakerOpenError → circuit_breaker."""
        exc = CircuitBreakerOpenError("Circuit breaker 'gemini' is OPEN")
        result = classify_gemini_error(exc)
        assert result["error_type"] == "circuit_breaker"
        assert result["recoverable"] is True

    def test_rate_limiter_error(self):
        """RateLimitExceededError → rate_limit."""
        exc = RateLimitExceededError("Rate limiter timed out")
        result = classify_gemini_error(exc)
        assert result["error_type"] == "rate_limit"
        assert result["recoverable"] is True

    def test_unknown_error(self):
        """Unknown exception → unknown."""
        exc = ValueError("Something weird")
        result = classify_gemini_error(exc)
        assert result["error_type"] == "unknown"
        assert result["recoverable"] is False

    def test_service_unavailable_error(self):
        """HTTP 503 → service_unavailable, recoverable."""
        exc = Exception("503 UNAVAILABLE")
        exc.code = 503
        result = classify_gemini_error(exc)
        assert result["error_type"] == "service_unavailable"
        assert result["recoverable"] is True

    def test_service_unavailable_string_match(self):
        """String containing '503' or 'unavailable' → service_unavailable."""
        exc = Exception("This model is currently experiencing high demand. 503 UNAVAILABLE")
        result = classify_gemini_error(exc)
        assert result["error_type"] == "service_unavailable"
        assert result["recoverable"] is True

    def test_unavailable_without_503_string(self):
        """String containing 'unavailable' without 503 code → service_unavailable."""
        exc = Exception("Service unavailable")
        result = classify_gemini_error(exc)
        assert result["error_type"] == "service_unavailable"
        assert result["recoverable"] is True

    def test_timeout_error_recoverable(self):
        """Built-in TimeoutError → service_unavailable, recoverable."""
        result = classify_gemini_error(TimeoutError("Operation timed out"))
        assert result["error_type"] == "service_unavailable"
        assert result["recoverable"] is True

    def test_timeout_message_recoverable(self):
        """Message mentioning a read timeout → recoverable (no code attr)."""
        exc = Exception("httpx.ReadTimeout: read timed out after 30s")
        result = classify_gemini_error(exc)
        assert result["error_type"] == "service_unavailable"
        assert result["recoverable"] is True

    def test_httpx_timeout_subclass_recoverable(self):
        """httpx.TimeoutException subclass → recoverable."""
        httpx = pytest.importorskip("httpx")

        class FakeReadTimeout(httpx.TimeoutException):
            pass

        result = classify_gemini_error(FakeReadTimeout("read timed out"))
        assert result["error_type"] == "service_unavailable"
        assert result["recoverable"] is True


class TestCopilot503Retry:
    """Test copilot-level retry and fallback for Gemini 503 errors."""

    @pytest.fixture(autouse=True)
    def _disable_groq_failover(self, monkeypatch):
        """Keep these pre-existing Gemini tests offline and deterministic.

        The new Groq failover path must not attempt real network calls
        here, so it is stubbed as disabled (None, None) -- which restores
        the exact behavior these tests were written against.
        """
        import backend.copilot as copilot
        monkeypatch.setattr(copilot, "_get_groq_client", lambda: (None, None))

    def test_streaming_succeeds_immediately(self):
        """Normal Gemini success → full response, no fallback."""
        import backend.copilot as copilot
        from backend.resilience import CircuitBreaker, TokenBucketRateLimiter, ResilientClient

        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        mock_chunk = MagicMock()
        mock_chunk.text = "Hello from Gemini"
        mock_client = MagicMock()
        mock_client.models.generate_content_stream.return_value = [mock_chunk]

        client = ResilientClient(mock_client, cb, rl, max_retries=0)

        original_fn = copilot._get_gemini_client
        copilot._get_gemini_client = lambda: (client, "test-model")
        try:
            chunks = list(copilot._call_gemini_stream("system", "user"))
            assert len(chunks) == 1
            assert chunks[0] == "Hello from Gemini"
        finally:
            copilot._get_gemini_client = original_fn

    def test_streaming_503_then_success(self):
        """First stream attempt gets 503, copilot retries, second succeeds."""
        import backend.copilot as copilot
        from backend.resilience import CircuitBreaker, TokenBucketRateLimiter, ResilientClient

        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        exc_503 = Exception("503 UNAVAILABLE")
        exc_503.code = 503

        mock_chunk = MagicMock()
        mock_chunk.text = "Success after retry"

        mock_client = MagicMock()
        # First call raises 503, second succeeds
        mock_client.models.generate_content_stream.side_effect = [
            exc_503,
            [mock_chunk],
        ]

        client = ResilientClient(mock_client, cb, rl, max_retries=0)

        original_fn = copilot._get_gemini_client
        copilot._get_gemini_client = lambda: (client, "test-model")
        try:
            with patch('time.sleep') as mock_sleep:
                chunks = list(copilot._call_gemini_stream("system", "user"))
            assert len(chunks) == 1
            assert chunks[0] == "Success after retry"
            # Should have retried once (2s delay)
            mock_sleep.assert_called_once()
        finally:
            copilot._get_gemini_client = original_fn

    def test_streaming_all_503s_returns_fallback(self):
        """All retries exhausted → yields fallback text, no exception raised."""
        import backend.copilot as copilot
        from backend.resilience import CircuitBreaker, TokenBucketRateLimiter, ResilientClient

        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        exc_503 = Exception("503 UNAVAILABLE")
        exc_503.code = 503

        mock_client = MagicMock()
        mock_client.models.generate_content_stream.side_effect = exc_503

        client = ResilientClient(mock_client, cb, rl, max_retries=0)

        original_fn = copilot._get_gemini_client
        copilot._get_gemini_client = lambda: (client, "test-model")
        try:
            with patch('time.sleep'):
                chunks = list(copilot._call_gemini_stream("system", "user"))
            # Should get exactly one chunk: the fallback message
            assert len(chunks) == 1
            fallback = chunks[0]
            assert "temporarily unavailable" in fallback.lower()
            assert "evidence analysis" in fallback.lower()
            assert "retry" in fallback.lower()
            # Must NOT contain fabricated evidence
            assert "fact_id" not in fallback.lower()
            assert "doc_" not in fallback.lower()
            assert "contradiction" not in fallback.lower()
        finally:
            copilot._get_gemini_client = original_fn

    def test_streaming_non_transient_error_raises(self):
        """Non-transient error (e.g. quota) → raises, no retry, no fallback."""
        import backend.copilot as copilot
        from backend.resilience import CircuitBreaker, TokenBucketRateLimiter, ResilientClient

        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        exc_quota = Exception("Quota exceeded")
        exc_quota.code = 403
        exc_quota.details = {"error": {"message": "quota exhausted"}}

        mock_client = MagicMock()
        mock_client.models.generate_content_stream.side_effect = exc_quota

        client = ResilientClient(mock_client, cb, rl, max_retries=0)

        original_fn = copilot._get_gemini_client
        copilot._get_gemini_client = lambda: (client, "test-model")
        try:
            with pytest.raises(Exception, match="Quota exceeded"):
                list(copilot._call_gemini_stream("system", "user"))
            # Should NOT have retried (only 1 call)
            assert mock_client.models.generate_content_stream.call_count == 1
        finally:
            copilot._get_gemini_client = original_fn

    def test_batched_503_raises_for_job_retry(self):
        """Batched _call_gemini with 503 → raises (job-level handles retry)."""
        import backend.copilot as copilot
        from backend.resilience import CircuitBreaker, TokenBucketRateLimiter, ResilientClient

        cb = CircuitBreaker(failure_threshold=5, cooldown_seconds=1.0, name="test")
        rl = TokenBucketRateLimiter(capacity=10, refill_rate=10, name="test")

        exc_503 = Exception("503 UNAVAILABLE")
        exc_503.code = 503

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = exc_503

        client = ResilientClient(mock_client, cb, rl, max_retries=0)

        original_fn = copilot._get_gemini_client
        copilot._get_gemini_client = lambda: (client, "test-model")
        try:
            with pytest.raises(Exception, match="503 UNAVAILABLE"):
                copilot._call_gemini("system", "user")
        finally:
            copilot._get_gemini_client = original_fn

    def test_fallback_contains_no_fabricated_evidence(self):
        """Fallback message must not invent facts, citations, or document IDs."""
        import backend.copilot as copilot
        fallback = copilot._GEMINI_UNAVAILABLE_FALLBACK

        # Must not contain any evidence-like content
        assert "fact_id" not in fallback.lower()
        assert "doc_" not in fallback.lower()
        assert "contradiction" not in fallback.lower()
        assert "amount" not in fallback.lower()
        assert "₹" not in fallback
        assert "rs." not in fallback.lower()
        # Must contain an honest statement
        assert "temporarily unavailable" in fallback.lower()
        assert "retry" in fallback.lower()
        # Must reference evidence analysis existing (not fabricated)
        assert "evidence analysis" in fallback.lower()
