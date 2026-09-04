"""
backend/razorpay_config.py

Typed configuration for Razorpay integration. Loads credentials from
environment variables, validates presence, and provides a clean interface
that prevents accidental secret exposure.

Security guarantees:
  - Never exposes key_secret or webhook_secret through __repr__, str, or dict
  - Never logs credential values
  - Provides is_configured() for conditional feature enablement
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("shield_assist.config")


@dataclass(frozen=True)
class RazorpayConfig:
    """Immutable Razorpay configuration loaded from environment variables.

    Attributes:
        key_id: Razorpay API key ID (e.g. 'rzp_test_...').
        key_secret: Razorpay API key secret. NEVER logged or exposed.
        webhook_secret: Razorpay webhook secret for HMAC verification. NEVER logged.
        base_url: Razorpay API base URL (default: https://api.razorpay.com/v1).
        test_mode: True if key_id starts with 'rzp_test_'.
    """

    key_id: str
    key_secret: str  # noqa: S105 — stored but never exposed
    webhook_secret: str  # noqa: S105 — stored but never exposed
    base_url: str
    test_mode: bool

    # -- Redaction helpers ---------------------------------------------------

    def __repr__(self) -> str:
        """Safe repr that never shows secrets."""
        return (
            f"RazorpayConfig(key_id={self._mask_key(self.key_id)}, "
            f"test_mode={self.test_mode}, base_url={self.base_url!r})"
        )

    def __str__(self) -> str:
        """Safe str that never shows secrets."""
        return self.__repr__()

    def to_safe_dict(self) -> dict[str, Any]:
        """Return a safe representation for API responses / status pages.

        Contains NO secrets — only configuration metadata.
        """
        return {
            "provider": "razorpay",
            "mode": "test" if self.test_mode else "live",
            "configured": True,
            "base_url": self.base_url,
            "key_id_prefix": self._mask_key(self.key_id),
        }

    @staticmethod
    def _mask_key(key_id: str) -> str:
        """Mask a key_id for display: show prefix + last 4 chars."""
        if len(key_id) <= 8:
            return "rzp_****"
        return f"{key_id[:8]}...{key_id[-4:]}"

    # -- Factories -----------------------------------------------------------

    @classmethod
    def from_env(cls) -> RazorpayConfig:
        """Load configuration from environment variables.

        Raises:
            ValueError: If required credentials are missing.

        The error message guides the developer to fix the issue without
        revealing actual credential values.
        """
        key_id = os.environ.get("RAZORPAY_KEY_ID", "").strip()
        key_secret = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
        webhook_secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip()
        base_url = (
            os.environ.get("RAZORPAY_BASE_URL", "https://api.razorpay.com/v1").strip()
        )

        missing = []
        if not key_id:
            missing.append("RAZORPAY_KEY_ID")
        if not key_secret:
            missing.append("RAZORPAY_KEY_SECRET")

        if missing:
            raise ValueError(
                f"Missing required Razorpay credentials: {', '.join(missing)}. "
                "Set them in .env or environment. "
                "Generate test-mode keys at https://dashboard.razorpay.com/app/keys"
            )

        test_mode = key_id.startswith("rzp_test_")

        if test_mode:
            logger.info(
                "Razorpay config loaded: mode=test key_id_prefix=%s",
                cls._mask_key(key_id),
            )
        else:
            logger.warning(
                "Razorpay config loaded: mode=LIVE key_id_prefix=%s — "
                "ensure this is intentional",
                cls._mask_key(key_id),
            )

        if not webhook_secret:
            logger.warning(
                "RAZORPAY_WEBHOOK_SECRET not set — webhook signature "
                "verification will be skipped. OK for local development only."
            )

        return cls(
            key_id=key_id,
            key_secret=key_secret,
            webhook_secret=webhook_secret,
            base_url=base_url,
            test_mode=test_mode,
        )

    @classmethod
    def optional_from_env(cls) -> RazorpayConfig | None:
        """Load configuration, returning None if credentials are missing.

        Use this when Razorpay integration is optional (e.g., the app
        can still run without it for local development).
        """
        try:
            return cls.from_env()
        except ValueError:
            logger.info(
                "Razorpay credentials not configured — "
                "Razorpay-specific features will be unavailable"
            )
            return None

    # -- Validation ----------------------------------------------------------

    def validate(self) -> list[str]:
        """Validate the configuration and return any warnings.

        Returns a list of warning messages (empty if all good).
        Does not raise — use for health-check diagnostics.
        """
        warnings = []

        if not self.key_id.startswith(("rzp_test_", "rzp_live_")):
            warnings.append(
                f"Key ID prefix '{self.key_id[:8]}...' does not match "
                "expected Razorpay format (rzp_test_ or rzp_live_)"
            )

        if not self.webhook_secret:
            warnings.append(
                "Webhook secret not configured — signature verification disabled"
            )

        if self.test_mode and "razorpay.com" not in self.base_url:
            warnings.append(
                f"Base URL {self.base_url} may not be correct for Razorpay API"
            )

        return warnings
