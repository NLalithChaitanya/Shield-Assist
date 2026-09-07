"""
backend/job_queue.py

SQLite-backed, in-process job queue for Shield Assist.

Zero external dependencies (no Redis/Celery) per PRODUCT_SPEC.md's
stated rationale: hackathon-pragmatic, production-swappable later.

Architecture:
  1. Repository enqueues jobs (enqueue_job) -- called from route handlers.
  2. Worker threads continuously claim_next_job() and dispatch to handlers.
  3. On success: mark_job_done() + audit log + optionally enqueue follow-up.
  4. On failure: mark_job_failed() increments attempts; at max_attempts
     the job moves to dead_letter status (the DLQ).
  5. On startup: stale in_progress jobs (crash recovery) are requeued or
     dead-lettered (Section 5.10).

The job queue owns ONLY the claim-dispatch-complete lifecycle.  It has no
knowledge of what each job handler actually does -- that's the job
handlers' responsibility (backend/jobs/).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

from backend.repository import CaseRepository

logger = logging.getLogger("shield_assist.jobs")

# ---------------------------------------------------------------------------
# Job handler registry
# ---------------------------------------------------------------------------

# Maps job_type string -> handler callable.
# Handlers receive (job_row: dict, repo: CaseRepository) and are responsible
# for their own error handling.  The queue framework catches unhandled
# exceptions and marks the job failed.
_HANDLERS: dict[str, Callable[[dict, CaseRepository], None]] = {}


def register_handler(job_type: str) -> Callable:
    """Decorator to register a job handler for a given job_type.

    Usage:
        @register_handler("score.case")
        def handle_score(job: dict, repo: CaseRepository) -> None:
            ...
    """
    def decorator(fn: Callable[[dict, CaseRepository], None]) -> Callable:
        if job_type in _HANDLERS:
            raise ValueError(f"Handler already registered for job_type={job_type!r}")
        _HANDLERS[job_type] = fn
        logger.debug("Registered handler for job_type=%r", job_type)
        return fn
    return decorator


def get_handler(job_type: str) -> Optional[Callable[[dict, CaseRepository], None]]:
    """Look up a registered handler by job_type.  Returns None if unregistered."""
    return _HANDLERS.get(job_type)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------

class _Worker(threading.Thread):
    """Background thread that claims and processes jobs.

    Lifecycle:
      1. On start: recover stale in_progress jobs (Section 5.10).
      2. Loop: claim_next_job() -> dispatch -> complete/fail.
      3. On shutdown signal: finish current job, then exit.
    """

    def __init__(
        self,
        worker_id: int,
        repo: CaseRepository,
        shutdown_event: threading.Event,
        poll_interval: float = 1.0,
        on_complete: Callable[[dict], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name=f"shield-worker-{worker_id}")
        self.worker_id = worker_id
        self.repo = repo
        self.shutdown_event = shutdown_event
        self.poll_interval = poll_interval
        self._on_complete = on_complete
        self._current_job_id: int | None = None

    def run(self) -> None:
        logger.info("Worker %d starting", self.worker_id)
        # --- DIAGNOSTIC: log handler state at worker startup ---
        import sys as _sys
        _jq = _sys.modules.get("backend.job_queue")
        logger.info(
            "DIAGNOSTIC worker-%d init: job_queue_module=%s id=%d file=%s | "
            "_HANDLERS_id=%d keys=%s",
            self.worker_id,
            getattr(_jq, "__name__", "?"), id(_jq) if _jq else 0,
            getattr(_jq, "__file__", "?"),
            id(_HANDLERS), list(_HANDLERS.keys()),
        )
        self._recover_stale_jobs()

        while not self.shutdown_event.is_set():
            job = self.repo.claim_next_job()
            if job is None:
                # No pending jobs -- sleep briefly before polling again.
                self.shutdown_event.wait(self.poll_interval)
                continue

            self._current_job_id = job["job_id"]
            try:
                self._process(job)
            finally:
                self._current_job_id = None

        logger.info("Worker %d shutting down", self.worker_id)

    def _process(self, job: dict) -> None:
        job_id = job["job_id"]
        job_type = job["job_type"]
        dispute_id = job["dispute_id"]

        # --- DIAGNOSTIC: log handler registry state before dispatch ---
        import sys
        jq_mod = sys.modules.get("backend.job_queue")
        logger.info(
            "DIAGNOSTIC dispatch: job_id=%d type=%s | "
            "job_queue_module=%s id=%d file=%s | "
            "_HANDLERS_id=%d keys=%s | "
            "score.case_present=%s score.case_handler=%s",
            job_id, job_type,
            getattr(jq_mod, "__name__", "?"),
            id(jq_mod) if jq_mod else 0,
            getattr(jq_mod, "__file__", "?"),
            id(_HANDLERS), list(_HANDLERS.keys()),
            "score.case" in _HANDLERS,
            getattr(_HANDLERS.get("score.case"), "__module__", "MISSING"),
        )

        handler = get_handler(job_type)
        if handler is None:
            error = f"No handler registered for job_type={job_type!r}"
            logger.error(
                "Job %d (%s): %s | registered=%s",
                job_id, job_type, error, list(_HANDLERS.keys()),
            )
            self.repo.mark_job_failed(job_id, error)
            self.repo.write_audit({
                "dispute_id": dispute_id,
                "stage": f"job.{job_type}",
                "detail": {"job_id": job_id, "error": error},
                "success": False,
                "error_detail": error,
            })
            return

        logger.info(
            "Job %d (%s) dispute=%s attempt=%d/%d",
            job_id, job_type, dispute_id,
            job.get("attempts", 1), job.get("max_attempts", 3),
        )

        try:
            handler(job, self.repo)
            self.repo.mark_job_done(job_id)
            logger.info("Job %d (%s) completed successfully", job_id, job_type)
            if self._on_complete:
                try:
                    self._on_complete(job)
                except Exception:
                    logger.exception("on_complete callback failed for job %d", job_id)
        except Exception as exc:
            error_detail = f"{type(exc).__name__}: {exc}"
            logger.exception("Job %d (%s) failed", job_id, job_type)
            self.repo.mark_job_failed(job_id, error_detail)
            self.repo.write_audit({
                "dispute_id": dispute_id,
                "stage": f"job.{job_type}",
                "detail": {"job_id": job_id},
                "success": False,
                "error_detail": error_detail,
            })

    def _recover_stale_jobs(self) -> None:
        """Recover jobs stuck in_progress (Section 5.10).

        On worker startup, scan for jobs that have been in_progress
        longer than 2 minutes (likely from a crashed worker).  Requeue
        pending ones, dead-letter ones at max_attempts.
        """
        stale_jobs = self.repo.get_stale_jobs(stale_seconds=120)
        for job in stale_jobs:
            job_id = job["job_id"]
            attempts = job.get("attempts", 0)
            max_attempts = job.get("max_attempts", 3)

            logger.warning(
                "Recovering stale job %d (type=%s, dispute=%s, attempts=%d/%d)",
                job_id, job["job_type"], job["dispute_id"],
                attempts, max_attempts,
            )

            if attempts >= max_attempts:
                self.repo.mark_job_failed(job_id, "Stale job: exceeded max attempts")
                self.repo.write_audit({
                    "dispute_id": job["dispute_id"],
                    "stage": f"job.{job['job_type']}",
                    "detail": {"job_id": job_id, "recovery": "dead_lettered"},
                    "success": False,
                    "error_detail": "Job was stale and at max attempts; dead-lettered.",
                })
            else:
                # Reset to pending so it gets retried
                self.repo.mark_job_failed(job_id, "Stale job: requeued by worker startup")
                self.repo.write_audit({
                    "dispute_id": job["dispute_id"],
                    "stage": f"job.{job['job_type']}",
                    "detail": {"job_id": job_id, "recovery": "requeued"},
                    "success": True,
                })


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class JobQueue:
    """Manages worker threads and provides the public start/stop interface.

    Usage:
        queue = JobQueue(repo)
        queue.start()   # spawns workers
        ...             # app runs
        queue.stop()    # graceful shutdown
    """

    def __init__(
        self,
        repo: CaseRepository,
        num_workers: int = 2,
        poll_interval: float = 1.0,
        on_complete: Callable[[dict], None] | None = None,
    ) -> None:
        self.repo = repo
        self.num_workers = num_workers
        self.poll_interval = poll_interval
        self._on_complete = on_complete
        self._shutdown_event = threading.Event()
        self._workers: list[_Worker] = []

    def start(self) -> None:
        """Spawn worker threads.  Idempotent -- safe to call multiple times."""
        if self._workers:
            logger.warning("JobQueue already started; ignoring duplicate start()")
            return

        self._shutdown_event.clear()
        for i in range(self.num_workers):
            worker = _Worker(
                worker_id=i,
                repo=self.repo,
                shutdown_event=self._shutdown_event,
                poll_interval=self.poll_interval,
                on_complete=self._on_complete,
            )
            worker.start()
            self._workers.append(worker)

        logger.info("JobQueue started with %d workers", self.num_workers)

    def stop(self) -> None:
        """Signal workers to finish current jobs and exit.

        Waits up to 30 seconds for workers to drain.  If a worker is
        stuck on a long job, it will be abandoned (daemon thread) when
        the process exits.
        """
        if not self._workers:
            return

        logger.info("JobQueue stopping — signaling %d workers", len(self._workers))
        self._shutdown_event.set()

        for w in self._workers:
            w.join(timeout=30)
            if w.is_alive():
                logger.warning(
                    "Worker %s did not exit within 30s (likely stuck on a long job)",
                    w.name,
                )

        self._workers.clear()
        logger.info("JobQueue stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._workers) and any(w.is_alive() for w in self._workers)
