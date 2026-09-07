"""
backend/jobs/__init__.py

Job handler registry.  Importing this module registers all four job
types with the job queue framework (backend/job_queue.py).

Job types:
  - score.case        — run Phase 1 scoring pipeline (evaluate_dispute)
  - document.process  — Gemini OCR + fact extraction + re-score
  - draft.response    — LLM copilot draft + citation integrity check
  - contest.submit    — Razorpay Contest API (stubbed for Phase 5)
"""

from __future__ import annotations

# Import handlers to trigger registration via @register_handler decorators.
# Each module's top-level @register_handler calls run at import time,
# populating job_queue._HANDLERS before any worker starts.
from backend.jobs import score_job  # noqa: F401
from backend.jobs import document_job  # noqa: F401
from backend.jobs import draft_job  # noqa: F401
from backend.jobs import contest_job  # noqa: F401

__all__ = ["score_job", "document_job", "draft_job", "contest_job"]
