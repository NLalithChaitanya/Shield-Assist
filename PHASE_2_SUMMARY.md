# Phase 2 — Backend, Async Infrastructure & Agent Orchestration — Complete

## Status: ✅ COMPLETE — All exit gate criteria met

---

## What was built

Phase 2 transformed the Phase 1 scoring pipeline into a production-shaped async backend with an AI copilot layer. The webhook route returns in milliseconds; all real work runs in background worker threads via a SQLite-backed job queue.

### Architecture

```
Webhook (POST /api/webhooks/razorpay)
  → HMAC verify + idempotent insert + enqueue
  → returns 200 immediately

Job Queue (worker threads)
  → score.case:      evaluate_dispute() → persist scores/decisions
  → document.process: Gemini OCR → extract facts → enqueue score.case
  → draft.response:   copilot.draft() → citation integrity check → persist
  → contest.submit:   Razorpay Contest API (stubbed for Phase 5)

Read Routes (GET /disputes, /disputes/{id}, /metrics)
  → query repository → return JSON

Copilot On-Demand (GET /disputes/{id}/copilot/explain|investigate|recommend)
  → call Gemini → return result + audit log

SSE (GET /events)
  → live event stream for frontend
```

---

## Files created (10 new)

| File | Purpose | Lines |
|---|---|---|
| `backend/repository.py` | `CaseRepository` Protocol + `SQLiteRepository` — all SQL lives here only | ~550 |
| `backend/job_queue.py` | SQLite-backed workers, atomic claims, DLQ, stale-job recovery | ~200 |
| `backend/jobs/__init__.py` | Handler registry — imports trigger `@register_handler` | ~20 |
| `backend/jobs/score_job.py` | `score.case` handler — Phase 1 scoring in worker thread | ~180 |
| `backend/jobs/document_job.py` | `document.process` handler — Gemini OCR + fact extraction + re-score chain | ~150 |
| `backend/jobs/draft_job.py` | `draft.response` handler — copilot draft + citation integrity check | ~180 |
| `backend/jobs/contest_job.py` | `contest.submit` handler — stubbed for Phase 5 | ~90 |
| `backend/resilience.py` | Circuit breaker + rate limiter for Gemini | ~280 |
| `backend/copilot.py` | `explain/investigate/recommend/draft` via Gemini API | ~305 |
| `scripts/smoke_test_backend.py` | 11-step smoke test (39 assertions) | ~415 |

## Files modified (4)

| File | Changes |
|---|---|
| `backend/db.py` | Added `jobs` table, `content_hash` on documents, `disputes.status` default to `'received'`, SQLite version check for RETURNING support |
| `backend/app.py` | Rewritten — thin route dispatchers, 12 routes, atomic idempotent insert, SSE, copilot on-demand routes |
| `requirements.txt` | Added `google-genai`, `sse-starlette` |
| `.env` | Added `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_RPM` |

## Files unchanged from Phase 1

| File | Why unchanged |
|---|---|
| `backend/domain.py` | Dispute/Document dataclasses — duck-typed, compatible with scoring |
| `backend/dispute_normalizer.py` | Webhook normalization — infrastructure-agnostic |
| `backend/webhook_listener.py` | Phase 0 reference implementation |
| `models/scoring.py` | `evaluate_dispute()` — called unchanged from job handler |
| `models/contradiction_rules.py` | Optional[str] patch — already correct |
| `models/train_and_evaluate.py` | Training pipeline — untouched |
| `data/evidence_requirements.py` | Single source of truth |
| `data/synthetic_generator.py` | Synthetic data — Phase 1 |

---

## Schema additions

### `jobs` table (v2 — async job queue)

```sql
CREATE TABLE jobs (
    job_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type       TEXT NOT NULL,        -- 'score.case' | 'document.process' | 'draft.response' | 'contest.submit'
    dispute_id     TEXT NOT NULL,
    payload        TEXT,                 -- JSON, job-specific args
    status         TEXT DEFAULT 'pending', -- pending -> in_progress -> done | failed | dead_letter
    attempts       INTEGER DEFAULT 0,
    max_attempts   INTEGER DEFAULT 3,
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);
```

### `documents` table — added `content_hash TEXT` for extraction caching

### `disputes` table — `status` default changed to `'received'` (was `'ingested'`)

---

## Key design decisions

### 1. Atomic idempotent insert (Section 5.3)

The webhook route uses `repo.idempotent_insert_dispute()` which runs `BEGIN IMMEDIATE` + `SELECT` + `INSERT` + `INSERT job` in a single transaction. This prevents the race condition where concurrent webhook deliveries all pass the idempotency check and enqueue duplicate jobs.

```python
was_present, job_id = repo.idempotent_insert_dispute(row=..., job_type="score.case", ...)
if was_present:
    return {"status": "already_ingested"}
```

### 2. CaseRepository Protocol

All raw SQL lives in `SQLiteRepository`. Pipeline code (normalizer, scoring, copilot) only imports the Protocol type. This makes the SQLite→Postgres swap a config change, not a rewrite.

### 3. Two-layer retry

- **Wrapper-level**: 1 exponential backoff retry inside `ResilientClient` (catches transient blips)
- **Job-level**: `max_attempts=3` with dead-letter at exhaustion (catches real failures)

### 4. On-demand copilot (not pre-generated)

`explain()`, `investigate()`, `recommend()` are called via GET routes when a human opens a case — not pre-generated for every dispute. Only `draft()` runs automatically (inside `draft.response` job).

### 5. Citation integrity check (Section 5.5)

Before persisting a draft, every `fact_id` in citations is verified against `extracted_facts`. If the LLM hallucinates a citation, the draft is rejected and logged as `copilot.citation_integrity_failure`.

---

## Routes (12 application routes)

| Route | Method | Purpose |
|---|---|---|
| `/api/webhooks/razorpay` | POST | Webhook receiver → enqueue (returns 200 in <100ms) |
| `/disputes` | GET | Priority-sorted case queue |
| `/disputes/{id}` | GET | Full case detail + jobs |
| `/disputes/{id}/documents` | POST | Upload evidence → enqueue document.process |
| `/disputes/{id}/contest` | POST | Submit contest → requires human.approved |
| `/disputes/{id}/copilot/explain` | GET | On-demand copilot explanation |
| `/disputes/{id}/copilot/investigate` | GET | On-demand investigation steps |
| `/disputes/{id}/copilot/recommend` | GET | On-demand strategy recommendation |
| `/disputes/{id}/audit` | GET | Full audit trail |
| `/events` | GET | SSE live event stream |
| `/metrics` | GET | Aggregate dashboard numbers |
| `/healthz` | GET | Health check |

---

## AI provider: Google Gemini (free tier)

- **Model**: `gemini-2.5-flash` (configured in `.env`)
- **Used for**: OCR (document.process) + copilot (explain/investigate/recommend/draft)
- **Rate limit**: 14 RPM (token bucket in resilience wrapper)
- **Circuit breaker**: 5 consecutive failures → open → 30s cooldown

---

## Edge cases handled

| Case | How handled |
|---|---|
| 5.1 Zero documents | completeness=0, check_name_match skips |
| 5.2 Malformed webhook | 400 with field-level errors, no job enqueued |
| 5.3 Duplicate webhook | Atomic idempotent insert, returns already_ingested |
| 5.4 Past deadline | past_deadline flag computed at read time |
| 5.5 LLM failure | Job retries (3 attempts), then dead_letter. Draft not persisted. |
| 5.6 Document upload after scoring | document.process → score.case chain fires |
| 5.7 SQLite write locking | WAL mode + BEGIN IMMEDIATE + busy_timeout=30s |
| 5.8 Empty database | /disputes returns [], /metrics returns zeroed values |
| 5.9 Customer name not resolvable | check_name_match returns [] when None |
| 5.10 Stale in_progress job | Detected on worker startup, requeued or dead-lettered |

---

## Smoke test results

```
Step 1:  Empty database returns clean defaults         ✓
Step 2:  Valid webhook -> fully scored/gated            ✓
Step 3:  Duplicate webhook -> idempotent                ✓
Step 4:  Malformed payload -> 400                       ✓
Step 5:  Out-of-scope code -> out_of_scope              ✓
Step 6:  Zero-document dispute -> completeness=0        ✓
Step 7:  Document upload -> re-score chain fires        ✓
Step 8:  Audit trail queryable for 5+ disputes          ✓
Step 9:  Circuit breaker trips on provider failure      ✓
Step 10: Stale in_progress job detected                 ✓
Step 11: Concurrent webhooks -> only 1 job enqueued     ✓

RESULTS: 39 passed, 0 failed
```

---

## What's deferred to Phase 3

- Real Gemini OCR for document images (currently uses form-provided facts as fallback)
- Frontend (React + Tailwind)
- Response editor (edit/regenerate/approve)
- Live UI animations on SSE events
- Postgres swap (repository Protocol makes this a config change)
