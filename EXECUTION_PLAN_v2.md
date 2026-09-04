# Shield Assist — End-to-End Execution Plan (v2)

Companion to `PRODUCT_SPEC.md` (v2). This file is the build sequence: what to do in each phase, and the exact bar to clear before moving to the next one. Each phase has a **gate** — if the gate isn't met, don't proceed, fix it first. This is what prevents ending up with 12 half-built features instead of one working product.

**What changed from v1 of this plan:** Phase 2 is restructured around the async job queue + repository pattern instead of a synchronous request/response backend, resilience patterns (circuit breaker, rate limiter, retry, DLQ) are now explicit tasks, SSE replaces WebSocket, Razorpay integration tasks are now concrete (signature verification, idempotency) rather than generic, and the audit trail task expands to the full 10-event taxonomy. Phases 0, 1, 3, and 4 are functionally unchanged — Phase 0/1 are already complete and untouched below; Phase 3/4 get minor wording updates so they reference the new backend shape correctly.

---

## Phase 0 — Foundations & Razorpay grounding

**Objective:** lock every real-world reference point before writing product logic, so nothing built later has to be retrofitted.

Tasks:
- [x] Pull and save Razorpay's exact dispute webhook payload schema (`payment.dispute.created`)
- [x] Pull and save the Documents API contract (`POST /v1/documents`, response shape, supported file types)
- [x] Pull and save the full evidence-slot list and the reason-code → required-evidence mapping (already have this — verify no gaps for the 6 codes in scope)
- [x] Set up Razorpay Test Mode account/API keys, confirm webhook delivery works end-to-end with a dummy listener
- [x] Repo scaffolding: `/data`, `/models`, `/backend`, `/frontend`, `/docs` folders, git initialized

**Gate to exit Phase 0:**
✅ You have a real Razorpay test-mode webhook hitting a local endpoint and printing the raw payload.
✅ You can point to the exact doc/line for every schema decision made later (no invented fields).

**Status: Phase 0 complete.** See `PHASE_0_SUMMARY.md` for full details.

---

## Phase 1 — Data & core ML

**Objective:** a working, honestly-evaluated model of case strength and win probability, before any UI or agent behavior is built on top of it.

Tasks:
- [x] Evidence-requirements rule table, coded (done)
- [x] Synthetic dispute + document generator with defensible ground truth (done)
- [x] Train/test split enforced in code (done)
- [x] Classifier trained, rule-only baseline reported for comparison (done)
- [x] Extraction + gating pipeline function (done)
- [x] Extend features/labels to support the three-score breakdown (completeness, quality, consistency) instead of a single blended score
- [x] Build contradiction-detection rules (amount match, date-order checks, name match across documents) and inject some contradictory cases into the synthetic generator so the model/rules have something real to catch
- [x] Re-run evaluation with contradiction cases included — confirm precision/recall still honestly reported, confusion matrix still shown

**Gate to exit Phase 1:**
✅ `evaluate_dispute()` returns completeness, quality, consistency, contradiction flags, and win probability — all from one function call.
✅ Precision/recall/F1 reported on a real held-out split, with a baseline comparison, no leakage (no metric suspiciously at 1.00).
✅ At least a few synthetic test cases visibly demonstrate a detected contradiction blocking a would-be-auto-approved case.

**Status: Phase 1 complete.** See `PHASE_1_SUMMARY.md` for full results, the gate-threshold auto-selection methodology, and what changed from the original plan.

---

## Phase 2 — Backend, async infrastructure & agent orchestration

**Objective:** everything from Phase 1 accessible over an API, running through an async job queue with a repository-abstracted storage layer, resilient to AI-provider failures, plus the LLM copilot layer — so the frontend has something real and production-shaped to call, not mocked data.

### 2a — API layer & Razorpay integration
- [ ] FastAPI project setup, core routes: `POST /api/webhooks/razorpay` (webhook receiver), `GET /disputes`, `GET /disputes/{id}`, `GET /metrics`
- [ ] HMAC-SHA256 signature verification on the webhook route, checked against the **raw request body** (never re-serialized JSON)
- [ ] Idempotency via `payload.id` deduplication on webhook receipt
- [ ] Webhook route returns `200 OK` immediately; all processing happens async (see 2b) — no heavy work on the request thread
- [ ] Contest submission route: `POST /disputes/{dispute_id}/contest` with `action: submit`, idempotency key to prevent duplicate submissions to Razorpay

### 2b — Async job queue
- [ ] SQLite-backed, in-process job queue (worker threads), zero external dependencies
- [ ] Job types implemented: `document.process`, `score.case`, `draft.response`, `contest.submit`
- [ ] Webhook receipt enqueues `dispute.created` → worker normalizes payload → creates Case → triggers downstream jobs
- [ ] Dead letter queue: jobs failing after 3 retries land in DLQ, inspectable

### 2c — Storage layer (repository pattern)
- [ ] SQLite schema: disputes, documents, extracted_facts, scores, decisions, audit_log
- [ ] `CaseRepository` defined as a Protocol (`get`, `save`, `list_by_priority`, `find_by_dispute_id`) — no raw SQL in domain/pipeline code
- [ ] Concrete SQLite implementation wired behind the Protocol (confirms the abstraction is real, not decorative — swap-to-Postgres should be a config change only)
- [ ] Extraction cache keyed by document content hash (avoid re-extracting identical documents)

### 2d — Pipeline wiring & gate
- [ ] Phase 1 pipeline wired into `document.process` / `score.case` jobs — every incoming dispute is scored automatically as documents arrive
- [ ] Multi-condition gate implemented as its own function, callable and independently testable, using the auto-selected threshold from `models/gate_threshold.json`

### 2e — AI Copilot layer
- [ ] LLM copilot layer (Claude API): four functions — `explain()`, `investigate()`, `recommend()`, `draft()` — each takes the structured case data, none of them touches raw documents directly (only extracted facts), enforcing the "grounded, not fabricated" principle
- [ ] Citation tagging: `draft()` output includes, per sentence or claim, which extracted fact / document it came from
- [ ] Prompt-level enforcement: LLM instructed to refuse any claim it cannot cite to a specific merchant-provided document

### 2f — Resilience
- [ ] Circuit breaker around Gemini and Claude clients — degrade gracefully (queue/retry later) instead of hanging when a provider is down
- [ ] Rate limiter respecting Gemini free-tier (~14 RPM) and Claude quotas
- [ ] Exponential backoff + retry on Razorpay API calls and AI provider calls

### 2g — Audit trail & live updates
- [ ] Audit trail writer implementing the full event taxonomy: `dispute.received`, `document.uploaded`, `document.extracted`, `scores.computed`, `contradiction.detected`, `gate.decision`, `copilot.drafted`, `human.approved`, `contest.submitted`, `dispute.resolved` — append-only, queryable by dispute ID
- [ ] Priority scoring function (amount × probability × urgency × readiness) using `respond_by`
- [ ] SSE endpoint for live case updates (document progress, score updates, gate decisions, draft-ready) — not WebSocket

**Gate to exit Phase 2:**
✅ A dispute posted to the webhook endpoint (real Razorpay test-mode webhook or simulated, signature-verified) results in a fully scored, gated, drafted case retrievable via `GET /disputes/{id}`, with no manual steps and no blocking work on the request thread.
✅ Every claim in a drafted response can be traced back to a specific document via the API response structure.
✅ Audit trail is queryable and shows the full 10-event decision chain for at least 5 test cases.
✅ Killing the Claude/Gemini connection mid-run does not crash the worker — circuit breaker trips, job lands in DLQ or retries, system stays up.
✅ Sending the same webhook payload twice does not create a duplicate case or duplicate contest submission.
✅ Backend runs standalone and passes a smoke test script hitting every route — before any frontend work starts.

---

## Phase 3 — Dashboard UI/UX (v1: functional)

**Objective:** every backend capability is visible and usable, with no missing wiring — a working, if not fully polished, product.

Tasks:
- [ ] Frontend scaffold (React + Tailwind + TanStack Query), routing set up
- [ ] Case queue view: list of disputes, priority-sorted, color-coded by urgency/strength, pulling real data from the backend
- [ ] Live queue updates via SSE subscription (not polling) — reflects the Phase 2b/2g infrastructure
- [ ] Case detail view: evidence bundle, extracted facts, three-score breakdown, contradiction warnings if any, gate decision + reason
- [ ] Drafted response view with clickable citations that reveal the source document/fact
- [ ] "Evidence Gap Analysis" panel: what's missing, ranked by impact
- [ ] Business metrics bar (disputes count, auto-prepared, human-review, likely-recoverable, time-saved) — labeled "simulated on synthetic test batch"
- [ ] Evidence Integrity Mode banner visible somewhere persistent in the UI
- [ ] Basic states handled: loading, empty queue, error from backend, job-in-progress (document still processing async)

**Gate to exit Phase 3 (v1):**
✅ You can click through the entire flow — queue → case → evidence → draft → citation → decision — using only real backend data, zero hardcoded/mocked values left in the frontend.
✅ Every screen reflects actual pipeline output, not placeholder text.
✅ A newly-uploaded document visibly moves through processing → scored state via SSE, without a page refresh.
✅ No broken states when the queue is empty, a case has no documents, or a job is still in flight.

---

## Phase 4 — UI/UX polish & demo-readiness

**Objective:** turn the functional v1 into the "best possible product" experience — this is where visual design, motion, and the demo narrative get built deliberately, not as an afterthought.

Tasks:
- [ ] Visual design pass: consistent color language (urgency/strength), typography, spacing — apply a deliberate design direction, not framework defaults
- [ ] Live "dispute arriving" animation/transition for the queue, driven by real SSE events (not simulated)
- [ ] Response editor: edit / regenerate / approve workflow on the drafted summary
- [ ] The single-dispute demo flow (per `PRODUCT_SPEC.md`'s 10-step demo script) rehearsed as an actual UI path: upload → async processing indicator → score jump (64→91) → draft → citation click → "safe to submit"
- [ ] A second demo beat: a contradictory case where the gate blocks auto-submission despite high raw probability — built as an explicit, findable case in the demo dataset
- [ ] Loading states, transitions, and empty states redesigned to feel intentional, not default
- [ ] Mobile/smaller-viewport check if demo will ever be shown on anything but a laptop

**Gate to exit Phase 4:**
✅ The 2-3 minute demo script runs start to finish without needing narration to cover gaps ("imagine if this button did X") — every step is a real, clickable, working interaction, including the async processing beat.
✅ The contradiction-blocks-submission moment is demonstrable on demand, not something you have to hope shows up.
✅ A person unfamiliar with the project can follow the queue → case → decision flow without explanation.

---

## Phase 5 — Depth, hardening & submission packaging

**Objective:** close remaining gaps, make the story airtight for judge questioning, and package the submission.

Tasks:
- [ ] Real Razorpay Documents API integration tested against sandbox (not just webhook listening) — confirms actual upload flow works, not just simulated
- [ ] Batch/bulk dispute simulation script for a stronger live demo (queue populated with a realistic mix, not just the one scripted case) — also exercises the job queue and rate limiter under real concurrency
- [ ] Edge cases handled: dispute with zero documents, dispute past deadline, duplicate webhook delivery (idempotency), AI provider outage mid-case (circuit breaker), DLQ inspection walkthrough
- [ ] Re-verify metrics end-to-end after all pipeline changes (contradiction detection, three-score breakdown) — make sure the numbers in `PRODUCT_SPEC.md` still match what the code actually outputs
- [ ] README: setup instructions, architecture summary (intelligence flow + infrastructure flow), how to run the demo locally
- [ ] One-pager / pitch deck grounded in Razorpay's own quotes and your measured results
- [ ] Rehearse answers to likely judge questions: "why not real data," "why service chargebacks not fraud," "how do you know the LLM isn't hallucinating," "what happens if the model is wrong," "what happens if Claude/Gemini goes down mid-demo," "how do you avoid double-processing a webhook retry," "why SQLite and not Postgres" (repository pattern = swap is a config change, demonstrate if time allows)

**Gate to exit Phase 5 (submission-ready):**
✅ Every number in your spec/pitch matches what a fresh run of the code actually produces.
✅ README lets someone else clone and run the demo without you present.
✅ You can answer each of the rehearsed judge questions in under 30 seconds, pointing to something visible in the product.
✅ You can point to the DLQ, a circuit-breaker trip, or an idempotency-blocked duplicate live if a judge asks "prove it's actually resilient."

---

## How to use this file

Work top to bottom. Don't start Phase 3 UI work with an unfinished Phase 2 backend — mocked data in the frontend is exactly how projects end up with a nice-looking UI wired to nothing real, which falls apart under judge questioning. Each phase's gate is a checkpoint, not a suggestion — if you can't check every box, the next phase will cost more time than it saves.

Phase 2 is now meaningfully larger than in v1 of this plan (async queue + repository pattern + resilience, on top of the API/copilot/audit work that was already there). If time is tight, the smallest defensible cut is: keep the job queue and repository pattern (they're cheap to build correctly from the start and expensive to retrofit), and treat the circuit breaker / rate limiter / DLQ as the first things to simplify — a bare try/except with a logged failure still demonstrates the resilience *story* to judges even if it's not the full pattern.
