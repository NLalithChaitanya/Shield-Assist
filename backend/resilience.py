"""
backend/resilience.py

Circuit breaker + rate limiter for AI provider calls (Gemini and Claude).

Protects the system from:
  1. Cascading failures when a provider is down (circuit breaker)
  2. Burst-induced rate limit hits (token bucket rate limiter)
  3. Transient network blips (exponential backoff retry)

The wrapper is built BEFORE the copilot layer (Section 9 of the plan),
so the copilot is never tested against an unprotected client even in dev.

Design:
  - Two-layer retry: wrapper-level (1 backoff retry for transient blips)
    + job-level (attempts budget for real failures).  Most rate-limit
    hiccups resolve in the wrapper without touching the job's attempt budget.
  - Circuit breaker: 5 consecutive failures -> open -> 30s cooldown ->
    half-open -> test.  Prevents hammering a dead provider.
  - Rate limiter: token bucket per provider, respects Gemini's ~14 RPM
    free tier.

Usage:
    from backend.resilience import wrap_gemini_client

    # Wrap at startup, before any copilot/OCR calls
    gemini_client = wrap_gemini_client(raw_gemini_client)

    # Use normally — resilience is transparent
    response = gemini_client.call("generate_content", ...)
"""

from __future__ import annotations

import json
import logging
import time
import threading
from collections import deque
from typing import Any, Callable

logger = logging.getLogger("shield_assist.resilience")


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """State machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.

    CLOSED: normal operation, counting consecutive failures.
    OPEN: failures exceeded threshold, calls fail fast.
    HALF_OPEN: cooldown elapsed, allowing one test call.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
        name: str = "default",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.name = name

        self._state = self.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == self.OPEN:
                # Check if cooldown has elapsed
                elapsed = time.monotonic() - self._last_failure_time
                if elapsed >= self.cooldown_seconds:
                    self._state = self.HALF_OPEN
                    logger.info(
                        "Circuit breaker '%s': OPEN -> HALF_OPEN (cooldown elapsed)",
                        self.name,
                    )
            return self._state

    def allow_request(self) -> bool:
        """Check if a request should be allowed through."""
        state = self.state
        if state == self.CLOSED:
            return True
        if state == self.HALF_OPEN:
            return True  # Allow one test call
        return False  # OPEN — fail fast

    def record_success(self) -> None:
        """Record a successful call — resets the breaker."""
        with self._lock:
            if self._state == self.HALF_OPEN:
                logger.info(
                    "Circuit breaker '%s': HALF_OPEN -> CLOSED (test call succeeded)",
                    self.name,
                )
            self._state = self.CLOSED
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        """Record a failed call — may trip the breaker."""
        with self._lock:
            self._consecutive_failures += 1
            self._last_failure_time = time.monotonic()

            if self._state == self.HALF_OPEN:
                # Test call failed — back to OPEN
                self._state = self.OPEN
                logger.warning(
                    "Circuit breaker '%s': HALF_OPEN -> OPEN (test call failed)",
                    self.name,
                )
            elif (
                self._state == self.CLOSED
                and self._consecutive_failures >= self.failure_threshold
            ):
                self._state = self.OPEN
                logger.warning(
                    "Circuit breaker '%s': CLOSED -> OPEN (%d consecutive failures)",
                    self.name, self._consecutive_failures,
                )

    def reset(self) -> None:
        """Manually reset the breaker (e.g. for testing)."""
        with self._lock:
            self._state = self.CLOSED
            self._consecutive_failures = 0


# ---------------------------------------------------------------------------
# Rate limiter (token bucket)
# ---------------------------------------------------------------------------

class TokenBucketRateLimiter:
    """Token bucket algorithm for rate limiting.

    Tokens refill at a fixed rate up to the bucket capacity.
    Each request consumes one token.  If the bucket is empty,
    the caller waits until a token is available.
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,  # tokens per second
        name: str = "default",
    ) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.name = name

        self._tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 60.0) -> bool:
        """Wait for a token to become available.

        Args:
            timeout: Maximum seconds to wait.  Returns False if timeout.

        Returns:
            True if a token was acquired, False on timeout.
        """
        deadline = time.monotonic() + timeout

        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True

            # Check deadline
            if time.monotonic() >= deadline:
                logger.warning(
                    "Rate limiter '%s': timed out waiting for token",
                    self.name,
                )
                return False

            # Wait for next token
            wait_time = min(1.0 / self.refill_rate, deadline - time.monotonic())
            if wait_time > 0:
                time.sleep(wait_time)

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill = now


# ---------------------------------------------------------------------------
# Resilient client wrapper
# ---------------------------------------------------------------------------

class ResilientClient:
    """Wraps any AI provider client with circuit breaker + rate limiter + retry.

    Usage:
        client = ResilientClient(
            raw_client=some_api_client,
            breaker=CircuitBreaker(name="claude"),
            rate_limiter=TokenBucketRateLimiter(capacity=10, refill_rate=0.2),
            max_retries=1,
            backoff_base=1.0,
        )
        response = client.call("messages.create", **kwargs)
    """

    def __init__(
        self,
        raw_client: Any,
        breaker: CircuitBreaker,
        rate_limiter: TokenBucketRateLimiter,
        max_retries: int = 1,
        backoff_base: float = 1.0,
    ) -> None:
        self.raw_client = raw_client
        self.breaker = breaker
        self.rate_limiter = rate_limiter
        self.max_retries = max_retries
        self.backoff_base = backoff_base

    def call(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Execute a method on the raw client with resilience.

        Args:
            method_name: Name of the method to call on raw_client.
            *args, **kwargs: Passed to the method.

        Returns:
            The method's return value.

        Raises:
            CircuitBreakerOpenError: If the circuit breaker is open.
            RateLimitExceededError: If rate limiter times out.
            The original exception: After retries exhausted.
        """
        # 1. Circuit breaker check
        if not self.breaker.allow_request():
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.breaker.name}' is OPEN — "
                f"failing fast after {self.breaker.failure_threshold} consecutive failures"
            )

        # 2. Rate limiter (fail fast — don't block the SSE stream)
        if not self.rate_limiter.acquire(timeout=3.0):
            raise RateLimitExceededError(
                f"Rate limiter '{self.rate_limiter.name}': "
                f"could not acquire token within timeout"
            )

        # 3. Retry with backoff
        last_error: Exception | None = None
        for attempt in range(1 + self.max_retries):
            try:
                # Resolve dotted paths: 'models.generate_content' ->
                # getattr(getattr(raw_client, 'models'), 'generate_content')
                parts = method_name.split(".")
                method = self.raw_client
                for part in parts:
                    method = getattr(method, part)
                result = method(*args, **kwargs)
                self.breaker.record_success()
                return result
            except CircuitBreakerOpenError:
                raise  # Don't retry if breaker is open
            except RateLimitExceededError:
                raise  # Don't retry rate limit — the limiter already timed out
            except Exception as exc:
                last_error = exc
                self.breaker.record_failure()

                if attempt < self.max_retries:
                    backoff = self.backoff_base * (2 ** attempt)
                    logger.warning(
                        "ResilientClient '%s': attempt %d failed (%s), "
                        "retrying in %.1fs",
                        self.breaker.name, attempt + 1, exc, backoff,
                    )
                    time.sleep(backoff)
                else:
                    logger.error(
                        "ResilientClient '%s': all %d attempts failed",
                        self.breaker.name, attempt + 1,
                    )

        raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class CircuitBreakerOpenError(Exception):
    """Raised when a request is rejected by an open circuit breaker."""


class RateLimitExceededError(Exception):
    """Raised when the rate limiter times out."""


def classify_gemini_error(exc: Exception) -> dict:
    """Classify a Gemini error into a merchant-friendly error type.

    Returns a dict with:
      - error_type: 'rate_limit' | 'quota_exceeded' | 'circuit_breaker' | 'unknown'
      - recoverable: bool (True if the error will resolve on its own)
      - detail: str (human-readable description for logging)

    How the two failure modes differ:
      - Rate limit (HTTP 429): Token bucket empty or Gemini's per-minute
        limit hit.  Recovers in seconds to minutes as tokens refill.
      - Quota exhausted (HTTP 403 + RESOURCE_EXHAUSTED / 'quota'): Daily
        free-tier quota used up.  Won't recover until quota resets
        (typically midnight Pacific).
    """
    # 1. Our own rate limiter / circuit breaker
    if isinstance(exc, RateLimitExceededError):
        return {
            "error_type": "rate_limit",
            "recoverable": True,
            "detail": "Token bucket empty — rate limiter timed out",
        }
    if isinstance(exc, CircuitBreakerOpenError):
        return {
            "error_type": "circuit_breaker",
            "recoverable": True,
            "detail": str(exc),
        }

    # 2. Gemini SDK errors — inspect HTTP status code
    #    ClientError has .code (HTTP status), .status, .message, .details
    code = getattr(exc, "code", None)
    message = str(getattr(exc, "message", "")) or str(exc)
    details = getattr(exc, "details", None)
    details_str = json.dumps(details) if details else ""
    combined = f"{message} {details_str}".lower()

    if code == 429:
        return {
            "error_type": "rate_limit",
            "recoverable": True,
            "detail": f"Gemini HTTP 429: {message}",
        }

    if code == 503:
        return {
            "error_type": "service_unavailable",
            "recoverable": True,
            "detail": f"Gemini HTTP 503: {message}",
        }

    if code == 403 and ("quota" in combined or "resource_exhausted" in combined):
        return {
            "error_type": "quota_exceeded",
            "recoverable": False,
            "detail": f"Gemini quota exhausted (HTTP 403): {message}",
        }

    # 3.5. Network / read / connect timeouts -- transient by nature.
    #    These used to fall through to 'unknown' (unrecoverable), which is
    #    why timeout storms surfaced raw errors instead of retrying/failing
    #    over to the backup provider.
    if _is_timeout_error(exc):
        return {
            "error_type": "service_unavailable",
            "recoverable": True,
            "detail": f"AI provider request timed out: {exc}",
        }

    # 3. String-matching fallback (covers edge cases where code isn't set)
    lower_exc = str(exc).lower()
    if "429" in lower_exc or "rate limit" in lower_exc or "too many" in lower_exc:
        return {
            "error_type": "rate_limit",
            "recoverable": True,
            "detail": str(exc),
        }
    if "503" in lower_exc or "unavailable" in lower_exc:
        return {
            "error_type": "service_unavailable",
            "recoverable": True,
            "detail": str(exc),
        }
    if "quota" in lower_exc or "resource_exhausted" in lower_exc:
        return {
            "error_type": "quota_exceeded",
            "recoverable": False,
            "detail": str(exc),
        }

    return {
        "error_type": "unknown",
        "recoverable": False,
        "detail": str(exc),
    }


# ---------------------------------------------------------------------------
# Timeout detection
# ---------------------------------------------------------------------------

def _is_timeout_error(exc: Exception) -> bool:
    """True for network/read/connect timeouts and deadline-exceeded errors.

    Covers:
      - built-in TimeoutError (incl. socket.timeout on py3.10+)
      - httpx.TimeoutException subclasses (ReadTimeout, ConnectTimeout, ...)
      - provider SDK messages mentioning 'timed out' / 'deadline exceeded'
    """
    if isinstance(exc, TimeoutError):
        return True
    try:
        import httpx
        if isinstance(exc, httpx.TimeoutException):
            return True
    except ImportError:
        pass
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "timed out",
            "read timeout",
            "connect timeout",
            "deadline exceeded",
        )
    )


# ---------------------------------------------------------------------------
# Factory functions
# ---------------------------------------------------------------------------

def wrap_gemini_client(raw_client: Any) -> ResilientClient:
    """Wrap the Gemini (Google) client with resilience.

    Used for both OCR (document.process) and copilot functions
    (explain/investigate/recommend/draft).

    Defaults based on Gemini's free-tier limits:
      - Circuit breaker: 5 failures -> open -> 30s cooldown
      - Rate limiter: 14 RPM (Gemini free tier, configurable via env)
      - Retry: 1 attempt with exponential backoff
    """
    import os
    rpm = int(os.environ.get("GEMINI_RPM", "14"))

    return ResilientClient(
        raw_client=raw_client,
        breaker=CircuitBreaker(
            failure_threshold=5,
            cooldown_seconds=30.0,
            name="gemini",
        ),
        rate_limiter=TokenBucketRateLimiter(
            capacity=min(rpm, 10),
            refill_rate=rpm / 60.0,
            name="gemini",
        ),
        max_retries=3,
        backoff_base=1.0,
    )


def wrap_groq_client(raw_client: Any) -> ResilientClient:
    """Wrap the Groq client with resilience (backup provider for copilot).

    Groq is the automatic failover provider: when Gemini fails (503,
    timeout, quota, rate limit...), copilot chat + draft requests are
    retried against Groq before the honest "Ally unavailable" fallback.

    Defaults are conservative for Groq's free tier and configurable via
    GROQ_RPM:
      - Circuit breaker: 5 failures -> open -> 30s cooldown
      - Rate limiter: 30 RPM
      - Retry: 2 attempts with exponential backoff (1s, 2s)
    """
    import os
    rpm = int(os.environ.get("GROQ_RPM", "30"))

    return ResilientClient(
        raw_client=raw_client,
        breaker=CircuitBreaker(
            failure_threshold=5,
            cooldown_seconds=30.0,
            name="groq",
        ),
        rate_limiter=TokenBucketRateLimiter(
            capacity=min(rpm, 30),
            refill_rate=rpm / 60.0,
            name="groq",
        ),
        max_retries=2,
        backoff_base=1.0,
    )
