# Phase 2 — Backend, Async Infrastructure & Agent Orchestration — Build Plan (v2)

Companion to `EXECUTION_PLAN.md` (v2) and `PRODUCT_SPEC.md` (v2). This is the detailed build plan for Phase 2, written before code, per your build pattern. Covers build order, schema, the job queue, the repository abstraction, every route, resilience patterns, the LLM layer's constraints, and the edge cases that will actually break a live demo if not handled now.

**What changed from v1 of this plan:** the pipeline is no longer run inline inside a single synchronous ingest route. The webhook route now does signature verification + idempotency check + enqueue, and returns `200` immediately; a job queue with typed jobs (`document.process`, `score.case`, `draft.response`, `contest.submit`) does the actual work; storage goes through a `CaseRepository` Protocol instead of direct SQL calls scattered through pipeline code; AI provider calls are wrapped in a circuit breaker + rate limiter; live updates are SSE, not WebSocket/polling. The schema, edge-case handling, citation integrity check, and smoke-test structure from v1 of this plan carry over almost unchanged — they were infrastructure-agnostic to begin with, they just now execute inside jobs instead of inline in a request handler.

---

## 0. Objective and exit gate (per EXECUTION_PLAN.md v2)

**Objective:** everything from Phase 1 becomes callable over a real API, running through an async job queue with a repository-abstracted storage layer, resilient to AI-provider failures, so Phase 3's frontend has something real and production-shaped to render.

**Exit gate:**
- ✅ A dispute posted to the webhook endpoint (real or simulated, signature-verified) results in a fully scored, gated, drafted case retrievable via `GET /disputes/{id}`, with no manual steps and no blocking work on the request thread
- ✅ Every claim in a drafted response traces back to a specific document via the API response structure
- ✅ Audit trail is queryable and shows the full 10-event decision chain for ≥5 test cases
- ✅ Killing the Claude/Gemini connection mid-run does not crash the worker — circuit breaker trips, job lands in DLQ or retries, system stays up
- ✅ Sending the same webhook payload twice does not create a duplicate case or duplicate contest submission
- ✅ Backend runs standalone and passes a smoke test hitting every route — before frontend work starts

---

## 1. Build order, and why it's this order

1. **SQLite schema** — everything else writes to or reads from this; must exist first (unchanged from v1 of this plan)
2. **`CaseRepository` Protocol + SQLite implementation** — defined before any pipeline code touches storage, so nothing accidentally writes raw SQL that has to be retrofitted later
3. **Job queue scaffolding** — SQLite-backed, in-process worker threads, the four job types stubbed (no logic yet), dead letter queue wired
4. **Dispute normalizer** — the seam between "whatever shape a webhook/synthetic case arrives in" and "what `evaluate_dispute()` expects" (unchanged reasoning from v1 of this plan)
5. **Webhook route** — signature verification (reuses Phase 0 logic) + idempotency check + enqueue `dispute.created` job, returns `200` immediately
6. **`score.case` job + Phase 1 wiring** — the pipeline becomes live, but runs inside a worker, not the request handler
7. **Audit trail writer** — built *alongside* the job queue, not after, so no job run is ever unlogged, and DLQ entries are logged too
8. **Document storage + `document.process` job** — local disk for Phase 2, with the Phase 5 Razorpay Documents API migration path already noted in the schema; upload triggers `document.process` then re-enqueues `score.case`
9. **Resilience wrapper (circuit breaker + rate limiter)** — built around the Gemini/Claude clients *before* the copilot layer is wired to real calls, so the copilot is never tested against an unprotected client even in dev
10. **LLM copilot layer** (`explain/investigate/recommend/draft`) as `draft.response` job — built last among the core pipeline, same reasoning as v1: it depends on structured facts already existing, and building it earlier risks feeding it raw documents, breaking Evidence Integrity Mode
11. **Priority scoring + read routes** (`GET /disputes`, `GET /disputes/{id}`, `GET /metrics`)
12. **SSE endpoint** — last, since it's additive to already-working routes, not load-bearing for the gate; job queue events are what SSE actually streams, so it can't be built before step 3

---

## 2. Database schema (SQLite)

Schema is functionally the same tables as v1 of this plan, with two additions: a `jobs` table (queue + DLQ) and a `dead_letter_jobs` reference, plus `disputes.status` gets two new states for the async lifecycle.

```sql
CREATE TABLE disputes (
    dispute_id          TEXT PRIMARY KEY,      -- Razorpay disp_xxx, or synthetic dsp_xxx
    payment_id           TEXT,
    reason_code           TEXT NOT NULL,
    network               TEXT NOT NULL,        -- 'razorpay' | 'upi'
    amount_paise          INTEGER NOT NULL,
    currency               TEXT NOT NULL DEFAULT 'INR',
    respond_by             TEXT,                 -- ISO timestamp, from webhook
    status                  TEXT NOT NULL DEFAULT 'received',
    -- 'received' -> 'queued' -> 'ingested' -> 'scored' -> 'gated'
    --   -> 'drafted' -> 'awaiting_review' | 'ready'
    -- 'received' = webhook accepted, job enqueued, worker hasn't picked it up yet.
    -- Explicit status machine so a half-processed dispute is never
    -- ambiguous -- see Section 4 (partial-failure handling).
    source                  TEXT NOT NULL,        -- 'webhook' | 'simulated' | 'manual'
    raw_webhook_payload    TEXT,                 -- full JSON, for debugging/replay
    ingested_at             TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE documents (
    document_id       TEXT PRIMARY KEY,       -- Razorpay doc_xxx, or synthetic
    dispute_id         TEXT NOT NULL,
    evidence_slot       TEXT NOT NULL,
    local_path           TEXT,                  -- Phase 2 storage; optional post-Phase 5
    content_hash         TEXT,                  -- for extraction cache (v2 addition)
    mime_type            TEXT,
    size_bytes           INTEGER,
    quality               TEXT,                  -- 'clear' | 'degraded' | 'unknown'
    uploaded_at           TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE extracted_facts (
    fact_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id       TEXT NOT NULL,
    dispute_id         TEXT NOT NULL,
    fact_type           TEXT NOT NULL,          -- 'amount' | 'date' | 'name' | 'order_id' | ...
    fact_value           TEXT NOT NULL,
    extracted_at         TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(document_id),
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE scores (
    dispute_id           TEXT PRIMARY KEY,
    win_probability        REAL NOT NULL,
    completeness            REAL NOT NULL,
    quality                  REAL NOT NULL,
    consistency              REAL NOT NULL,
    missing_required_slots TEXT,               -- JSON array
    contradiction_flags     TEXT,               -- JSON array of structured flags
    computed_at              TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE decisions (
    decision_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id         TEXT NOT NULL,
    action               TEXT NOT NULL,          -- 'prepare' | 'review' | 'low_priority'
    passed                TEXT NOT NULL,          -- 'true' | 'false' (SQLite has no bool)
    failing_conditions   TEXT,                    -- JSON array
    priority_score        REAL,
    human_override        TEXT,                    -- NULL until a human acts
    decided_at             TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE drafts (
    draft_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id            TEXT NOT NULL,
    summary_text           TEXT NOT NULL,
    citations               TEXT NOT NULL,        -- JSON array: {claim, fact_id, document_id}
    approved               TEXT NOT NULL DEFAULT 'false',
    approved_by             TEXT,
    approved_at             TEXT,
    created_at              TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);

CREATE TABLE audit_log (
    log_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    dispute_id         TEXT,
    event_type          TEXT NOT NULL,          -- the 10-event taxonomy from PRODUCT_SPEC.md v2
    actor                 TEXT NOT NULL,          -- 'system' | 'worker' | editor_id
    success               TEXT NOT NULL,          -- 'true' | 'false'
    data                   TEXT,                    -- JSON payload, event-specific
    logged_at             TEXT NOT NULL
);

-- v2 addition: job queue + DLQ
CREATE TABLE jobs (
    job_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type             TEXT NOT NULL,          -- 'document.process' | 'score.case' | 'draft.response' | 'contest.submit'
    dispute_id           TEXT NOT NULL,
    payload               TEXT,                    -- JSON, job-specific args
    status                 TEXT NOT NULL DEFAULT 'pending',
    -- 'pending' -> 'in_progress' -> 'done' | 'failed' | 'dead_letter'
    attempts               INTEGER NOT NULL DEFAULT 0,
    max_attempts          INTEGER NOT NULL DEFAULT 3,
    last_error             TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    FOREIGN KEY (dispute_id) REFERENCES disputes(dispute_id)
);
```

Design note: `jobs.status = 'dead_letter'` after 3 failed `attempts` is the DLQ — deliberately kept as a status value on the same table rather than a separate physical table, since a hackathon-scale DLQ just needs to be inspectable (`SELECT * FROM jobs WHERE status='dead_letter'`), not a separate infrastructure component.

---

## 3. `CaseRepository` Protocol

Defined before any pipeline or job code touches storage — this is what makes the SQLite→Postgres swap (mentioned in `PRODUCT_SPEC.md` P2) a config change, not a rewrite, and it's also what makes jobs independently testable without a real DB.

```python
class CaseRepository(Protocol):
    def get(self, dispute_id: str) -> Optional[Dispute]: ...
    def save(self, dispute: Dispute) -> None: ...
    def list_by_priority(self, limit: int = 50) -> list[Dispute]: ...
    def find_by_dispute_id(self, dispute_id: str) -> Optional[Dispute]: ...
    # job-queue-specific methods, added for v2:
    def enqueue_job(self, job_type: str, dispute_id: str, payload: dict) -> int: ...
    def claim_next_job(self, job_type: str | None = None) -> Optional[Job]: ...
    def mark_job_done(self, job_id: int) -> None: ...
    def mark_job_failed(self, job_id: int, error: str) -> None: ...  # increments attempts, moves to dead_letter at max_attempts
```

`backend/repository/sqlite_repository.py` is the only file allowed to contain raw SQL for case/job data. Pipeline code (normalizer, scoring wiring, copilot layer) only ever imports the Protocol type, never `sqlite3` directly — confirms the abstraction is real, not decorative.

---

## 4. Job queue

SQLite-backed, in-process worker threads — zero external dependencies (no Redis/Celery), per `PRODUCT_SPEC.md`'s stated rationale (hackathon-pragmatic, production-swappable later).

**Worker loop (per worker thread):**
1. `claim_next_job()` — atomic claim (single `UPDATE ... WHERE status='pending' RETURNING ...` or equivalent SQLite-safe claim pattern, to avoid two workers grabbing the same job)
2. Dispatch to the matching job handler based on `job_type`
3. On success → `mark_job_done()`, write `audit_log` entry, enqueue any follow-up job (e.g. `document.process` completing enqueues `score.case`)
4. On failure → `mark_job_failed()` (increments `attempts`; if `attempts >= max_attempts`, status becomes `dead_letter`), write `audit_log` entry either way, so a DLQ'd job is still visible in the audit trail, not just the jobs table

**Job handlers (thin — real logic lives in Phase 1 modules / copilot layer):**
- `document.process` → OCR + fact extraction (Gemini, behind resilience wrapper — Section 6), cache check by `content_hash` before calling Gemini at all
- `score.case` → calls `evaluate_dispute()` from Phase 1, unchanged interface, writes to `scores` + `decisions` via the repository
- `draft.response` → calls copilot `draft()` (behind resilience wrapper), runs citation integrity check (Section 5.5) before persisting
- `contest.submit` → only runs after `human.approved` audit event exists for that dispute; calls Razorpay Contest API with an idempotency key

---

## 5. Edge cases — the part that actually protects the demo

Same nine cases as v1 of this plan, re-threaded through the job queue where the execution model changed. Numbering kept identical to the original plan for easy cross-reference; only the "how it's handled" changed where the async model matters.

### 5.1 Dispute with zero documents
- `evaluate_dispute()` must not crash on an empty `documents` list — unchanged from Phase 1
- `score.case` job should show `completeness: 0` in the persisted result, not raise — a zero-document case is a **valid, common state**
- Gate must correctly route this to `review` or `low_priority`; the job completes normally, it just doesn't reach `draft.response` if gated to `low_priority`
- Also means `customer_name` cannot be resolved yet — see 5.9

### 5.2 Malformed or partial webhook payload
- Missing required fields (`reason_code`, `amount`, `respond_by`) → webhook route returns `400` with a specific field-level error **before enqueueing anything** — no job is created for a payload that can't be normalized, and the raw payload is still logged to `audit_log` with `success='false'`
- Unknown/out-of-scope `reason_code` → caught at normalization (inside the `document.process`/`score.case` job, since normalization happens once a job runs, not at the webhook route which only checks structural validity) — dispute stored with `status='out_of_scope'`, job marked `done` (not `failed` — this isn't a retriable error), visible in the queue as "not currently handled"

### 5.3 Duplicate webhook delivery
Razorpay retries webhook delivery if it doesn't get a fast `200`. The webhook route must be **idempotent at the enqueue step**, not just at the pipeline level:
- Check `dispute_id` (+ event type) against the `disputes` table **before enqueueing** — if already present, return `200` immediately without creating a new job at all
- This is a stronger guarantee than v1's "idempotent ingest route," because it also prevents a duplicate job from ever entering the queue, not just a duplicate pipeline run
- Directly satisfies the `EXECUTION_PLAN.md` v2 Phase 2 exit gate: "sending the same webhook payload twice does not create a duplicate case"

### 5.4 Dispute past its deadline
- Same as v1 of this plan: `past_deadline: true` flag, computed at read time (`GET /disputes`, `GET /disputes/{id}`) from `respond_by` vs. current time — not something a job needs to compute or store, since "is it past deadline" changes continuously and shouldn't require re-running a job just because time passed

### 5.5 LLM layer failures (Claude API)
- The `draft.response` job is where this lives now, not an inline call. On failure (after resilience-wrapper retries are exhausted — Section 6), `mark_job_failed()` runs; if it hits `max_attempts`, the job goes to `dead_letter` and the dispute stays at `status='gated'` — scored and gated, but not drafted, exactly as in v1's plan, just reached via job failure instead of an in-request exception
- API response should surface `draft_status: 'failed'` (derivable from: gated status + a dead-lettered `draft.response` job for that dispute) so Phase 3 can show "draft generation failed, retry" — retry means re-enqueueing the job, which is a natural fit for the async model
- **Citation integrity check** (unchanged from v1's plan, still the concrete enforcement mechanism behind Evidence Integrity Mode): before persisting a draft, verify every citation references a `fact_id` that actually exists in `extracted_facts` for that dispute — if the LLM hallucinates a citation, reject the draft inside the job rather than storing a broken link, and log this as a distinct `audit_log` reason (not the same as an API failure) so a judge asking "how do you know it's not hallucinating" gets a concrete, inspectable answer

### 5.6 Document upload after initial scoring
- Uploading a document to an already-scored dispute enqueues `document.process` → which, on completion, enqueues a fresh `score.case` job — this is literally step 4 of the demo script (score jumps 64→91 after upload), and the job-queue model makes the re-score a natural consequence of the job chain rather than a special-cased upsert
- `scores` table still uses `dispute_id` as primary key (upsert), and `audit_log` gets a new `scores.computed` entry showing the before/after — same audit value as v1's plan

### 5.7 Concurrent access / SQLite write locking
- SQLite allows one writer at a time — now more relevant than in v1's plan, since multiple worker threads exist. The repository's job-claim step must use an atomic claim pattern (Section 4) specifically to prevent two workers processing the same job
- Each job handler wraps its own writes (score + decision, or draft + citations) in a single transaction, so a crash mid-job doesn't leave the DB half-written for that dispute — the job's own `attempts`/`status` field is the source of truth for what actually completed, same principle as v1's plan, now scoped to job boundaries instead of route boundaries

### 5.8 Empty database / empty queue state
- Unchanged from v1 of this plan: `GET /disputes` and `GET /metrics` on a fresh DB return clean empty/zeroed states, not errors

### 5.9 Customer-name anchor not yet resolvable
- Unchanged reasoning from v1 of this plan: `check_name_match` treats `customer_name is None` as "not yet checkable," not a contradiction or an error; `consistency_score` reflects which checks actually ran. The only difference is this now happens inside a `score.case` job — the logic itself, being pure Phase 1 code, is untouched

### 5.10 Job stuck `in_progress` after a crash (v2 addition — new failure mode from the async model itself)
- If a worker crashes mid-job, the job can be left at `status='in_progress'` forever, invisible to the DLQ (which only triggers on `mark_job_failed`)
- Mitigation: on worker startup, any job found `in_progress` with `updated_at` older than a short timeout (e.g. 2 minutes) is treated as failed and re-queued (or dead-lettered if already at `max_attempts`) — this is a real gap the async model introduces that the synchronous v1 design never had, so it gets its own edge case rather than being folded into 5.5

---

## 6. Resilience wrapper (circuit breaker + rate limiter)

Built once, wrapped around both the Gemini client (used by `document.process`) and the Claude client (used by `draft.response` and the other three copilot functions).

- **Circuit breaker**: after N consecutive failures (e.g. 5) to a given provider, the breaker opens — further calls fail fast without hitting the network, for a cooldown window, then half-opens to test recovery. This is what satisfies the exit-gate requirement "killing the AI connection mid-run does not crash the worker" — the job fails cleanly and predictably (→ retry/DLQ per Section 4) instead of hanging on a dead connection
- **Rate limiter**: token-bucket limiter respecting Gemini's free-tier ~14 RPM and Claude's configured quota, so a burst of documents/drafts doesn't trip provider-side rate limits mid-demo
- **Retry with backoff**: one retry with exponential backoff *inside* the resilience wrapper, before the job-level `attempts` counter (Section 4) even increments — this two-layer retry (wrapper-level for transient blips, job-level for real failures) is deliberate: most rate-limit hiccups resolve in the wrapper without ever touching the job's own attempt budget

---

## 7. Routes

| Route | Method | Behavior |
|---|---|---|
| `/api/webhooks/razorpay` | POST | Webhook receiver. HMAC-SHA256 signature verification against raw body (reuses Phase 0 logic). Idempotency check (5.3) *before* enqueueing. Enqueues `document.process`/`score.case` job chain. Returns `200` immediately — no pipeline work on the request thread. |
| `/disputes` | GET | List, priority-sorted, `past_deadline` flag computed at read time (5.4). Supports empty state (5.8). |
| `/disputes/{id}` | GET | Full case: scores, gate decision, draft (if any), documents, citations, current job status if still processing. |
| `/disputes/{id}/documents` | POST | Upload a document to an existing dispute; enqueues `document.process` → `score.case` chain (5.6). |
| `/disputes/{id}/audit` | GET | Full audit trail for one dispute — the gate's explicit "queryable by dispute ID" requirement. |
| `/disputes/{id}/contest` | POST | Requires a `human.approved` audit event to exist first; enqueues `contest.submit` with idempotency key. |
| `/metrics` | GET | Aggregate dashboard numbers, explicitly labeled "simulated on synthetic test batch" in the response itself. |
| `/events` | GET (SSE) | Server-sent events stream: document progress, score updates, gate decisions, draft-ready — replaces v1's WebSocket/polling route. |
| `/healthz` | GET | Already exists from Phase 0; kept as-is. |

---

## 8. LLM copilot layer — constraints, stated explicitly

Unchanged from v1 of this plan, with one addition:

- `explain()`, `investigate()`, `recommend()`, `draft()` each receive **only** the structured output of `evaluate_dispute()` plus `extracted_facts` rows — never a raw document, never a file path
- Every function call and its output logged to `audit_log` (event_type=`copilot.drafted` etc.)
- `draft()`'s output schema: `{"summary_text": str, "citations": [{"claim": str, "fact_id": int, "document_id": str}]}` — maps directly onto the `drafts` table
- **v2 addition**: `explain()`, `investigate()`, and `recommend()` are called for **both** `prepare`-gated and `review`-gated cases (per the corrected intelligence-flow diagram in `PRODUCT_SPEC.md` v2) — only `draft()`'s output is treated as a submission candidate; the other three are advisory regardless of gate outcome, and this distinction should be visible in the `copilot.drafted`-style audit entries (i.e. log which of the four abilities ran, not just that "the copilot ran")

---

## 9. Smoke test script (part of the exit gate)

Extends v1's script with job-queue- and resilience-specific checks.

`scripts/smoke_test_backend.py`:
1. Fresh empty DB → confirm `/disputes` and `/metrics` return clean empty states (5.8)
2. Post a valid simulated `payment.dispute.created` payload → confirm job chain runs to completion, `GET /disputes/{id}` shows scored+gated+drafted, with no request-thread blocking (measure webhook response time — should return in milliseconds, not wait for the pipeline)
3. Post the **same** payload again → confirm idempotency (5.3): no duplicate rows, and no duplicate job created
4. Post a malformed payload → confirm `400`, not `500`, no job enqueued, `audit_log` entry exists anyway
5. Post a dispute with an out-of-scope reason code → confirm graceful `status='out_of_scope'`, job marked `done` not `failed`
6. Post a zero-document dispute → confirm it scores as `completeness=0`, `check_name_match` correctly skipped (5.9)
7. Upload a document to an already-scored dispute → confirm the `document.process` → `score.case` job chain fires and re-scoring is reflected
8. Check `GET /disputes/{id}/audit` shows every stage above, for at least 5 distinct disputes
9. **(v2 addition)** Force a Claude/Gemini call to fail (e.g. point the client at an invalid endpoint or kill network mid-call) → confirm the circuit breaker trips, the job fails gracefully (not a worker crash), and after enough failures the job reaches `dead_letter` without taking down other workers
10. **(v2 addition)** Manually set a job's `status='in_progress'` with a stale `updated_at`, restart the worker → confirm it's detected and requeued/dead-lettered (5.10)
11. **(v2 addition)** Post the same webhook payload concurrently from two threads → confirm only one job is enqueued, not two (validates the atomic claim / idempotency-before-enqueue design in Section 5.3, under actual concurrency rather than just sequential double-posting)

---

## 10. What's deliberately deferred, not forgotten

- Real Razorpay Documents API integration → Phase 5, per plan; Phase 2 stores documents to local disk with the migration path already noted in the schema (`documents.local_path`)
- Postgres swap → P2 per `PRODUCT_SPEC.md`; the repository Protocol (Section 3) is what makes this a config change later, not something to attempt now
- Response editor (edit/regenerate/approve) → Phase 4, per plan
- SSE visual polish (live "dispute arriving" animation) → functional SSE events are enough to pass Phase 2's gate; the animation is explicitly a Phase 4 task

---

## Exit checklist (restated from EXECUTION_PLAN.md v2, mapped to what proves it)

- [ ] Ingest → fully scored/gated/drafted, zero manual steps, no request-thread blocking → proven by smoke test step 2
- [ ] Every draft claim traceable to a document → proven by citation integrity check (5.5) + smoke test
- [ ] Audit trail queryable, ≥5 test cases, full event taxonomy → proven by smoke test step 8
- [ ] AI provider outage doesn't crash a worker → proven by smoke test step 9
- [ ] Duplicate webhook (sequential and concurrent) doesn't duplicate a case → proven by smoke test steps 3 and 11
- [ ] All routes pass smoke test before frontend work begins → smoke test script itself is the gate
