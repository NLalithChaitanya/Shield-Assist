# Shield Assist — Product Specification (v2)
### The AI Dispute Resolution Copilot for Razorpay Merchants

**Track:** 02 — AI Risk Manager (Razorpay Buildathon)
**Sub-problem:** Chargeback evidence responder

---

## One-line pitch

When a service chargeback arrives, Shield Assist doesn't just predict whether the merchant will win. It understands the dispute, audits the evidence for completeness, quality and internal consistency, finds what's missing or contradictory, builds a grounded response with every claim traceable to a source document, and knows when a human needs to take over.

---

## Why this problem (not fraud-spike / return-risk / abuse-ring)

Razorpay's own SHIELD documentation states explicitly:

> "SHIELD covers all the fraud chargebacks but not the service chargebacks."

| Chargeback type | Covered by SHIELD? | What it needs |
|---|---|---|
| Fraud (unauthorized txn, stolen card) | Yes — ML risk engine, ~10,000 data points, 100+ rules | Transaction-level anomaly detection |
| Service (not received, not as described, refund not processed) | No — explicitly excluded | Evidence assembly & documentation review |

Fraud-spike detection, return-risk scoring, and abuse-ring detection all overlap with what SHIELD already does at scale — nobody can credibly out-build a system trained on 10M+ cards in a hackathon. Service chargebacks are a documented, named gap, not a redundant rebuild.

**Additional context validating the gap:**
- Manual fraud/dispute management costs merchants 5–15% of revenue annually in operational overhead, not just direct loss
- Chargebacks cost merchants 2–3% of international revenue
- Razorpay is actively marketing self-serve dispute dashboards and "automated dispute representation" — a signal this workflow is still slow/manual, not solved

---

## Scope: reason codes covered

Razorpay's own native reason codes plus UPI codes (most relevant to Indian merchants), each with an exact required-evidence checklist sourced directly from Razorpay's docs:

| Code | Network | Name | Required evidence | Evidence slot(s) |
|---|---|---|---|---|
| RZP01 | Razorpay | Goods/Services not Provided | proof of service/delivery, customer comms, T&C | proof_of_service, customer_communication, term_and_conditions |
| RZP04 | Razorpay | Refund not Processed | refund proof, bank statement, comms, refund policy | refund_confirmation, billing_proof, customer_communication, refund_cancellation_policy |
| RZP05 | Razorpay | Account Debited but No Confirmation | invoice, internal logs, comms, T&C | billing_proof, access_activity_log, customer_communication, term_and_conditions |
| RZP06 | Razorpay | Business Not Responding | proof of service/delivery, invoice, email comms | proof_of_service, billing_proof, customer_communication |
| UPI 1064 | UPI | Goods/Services Not Received | proof of service/delivery, comms, T&C | proof_of_service, customer_communication, term_and_conditions |
| UPI 1062 | UPI | Goods/Services Not As Described | product images, proof of delivery, comms, return policy | proof_of_service, customer_communication, refund_cancellation_policy |

Evidence-slot column verified directly against Razorpay's own "Suggested Documents" per reason code (not inferred) — see `/data/evidence_requirements.py`.

> **Note:** RZP06 and UPI 1064 use `proof_of_service`, not `shipping_proof` — Razorpay's actual language is "proof of service/product delivery," a distinct real API slot from shipping.

An additional real, documented code — RZP00 (catch-all, "Not Available") — exists but is intentionally left out of the 6-code scope above; it's a candidate for the P2 "additional reason codes" expansion if time allows.

**Source:** Razorpay Submit Evidence docs

---

## How the real Razorpay flow works (and where the copilot sits in it)

1. Dispute arrives via webhook (`payment.dispute.created`) with `reason_code`, `amount`, deadline (`respond_by`)
2. Merchant uploads documents via the Documents API (`POST /v1/documents`) — PDF/PNG/JPG — returns document IDs
3. Document IDs attach to evidence slots: `shipping_proof`, `billing_proof`, `cancellation_proof`, `customer_communication`, `proof_of_service`, `explanation_letter`, `refund_confirmation`, `access_activity_log`, `refund_cancellation_policy`, `term_and_conditions` (singular "term" — verified against Razorpay's real API, a common typo trap), others
4. A text summary accompanies the submission
5. `contest` is called with `action: submit` — minimum one document ID required; more relevant documents improve win odds

Shield Assist is the intelligence layer on top of merchant-uploaded documents — it never fabricates evidence, it works with what the merchant already has, audits it, and tells them what's missing.

---

## Product principle: Evidence Integrity Mode

Displayed visibly in the UI, not just a backend rule:

> 🔒 **Evidence Integrity Mode**
> Shield Assist never creates or modifies evidence. Every AI-generated claim in a draft response must be traceable to a specific merchant-provided document.

This is both a trust signal for judges and a real constraint enforced by the citation system below.

---

## Unified Architecture

The architecture has two equally important layers: the intelligence flow (what makes the product defensible) and the infrastructure flow (what makes it production-minded). Both are shown below.

### Intelligence Flow (the product's soul)

```
RAZORPAY
    │
    ▼
Dispute Webhook
(reason_code, amount, respond_by)
    │
    ▼
Dispute Normalizer
    │
    ┌────────────┴────────────┐
    ▼                         ▼
Dispute Data             Merchant Evidence
                               │
                     Documents (text/PDF/image)
                               │
                               ▼
                     Document Intelligence
               (Gemini OCR + fact extraction + tagging)
                               │
                               ▼
                      Structured Facts
                (dates, amounts, names, tracking IDs)
                               │
               ┌───────────────┼──────────────┐
               ▼               ▼               ▼
         Completeness      Quality       Consistency
        (required types    (legible /   (do documents
         present?)          degraded?)    agree with
                                           each other?)
               │               │               │
               └───────────────┼───────────────┘
                               ▼
                     Evidence Intelligence
                               │
                     ┌─────────┴─────────┐
                     ▼                   ▼
              Case Strength       Win Probability
              (0-100, explainable)  (classifier, %)
                     │                   │
                     └─────────┬─────────┘
                               ▼
                    Multi-Condition Gate
              (deterministic / auditable)
         (probability + completeness + consistency
                  + no unresolved contradiction)
                     ┌─────────┼─────────┐
                     ▼         ▼         ▼
                  Prepare   Review    Low Priority
                     │         │
                     └────┬────┘
                          ▼
               AI Copilot (Gemini)
         ┌───────────┼───────────┬───────────┐
         ▼           ▼           ▼           ▼
      Explain    Investigate  Recommend     Draft
    (why weak/  (find        (what to     (grounded
     strong)     contradict-  upload      response,
                  ions)        next)       cited)
                                               │
                                               ▼
                                ┌───────────────────────────┐
                                │ Human Approval — REQUIRED │
                                │ Merchant reviews/edits    │
                                └─────────────┬─────────────┘
                                               │ approve
                                               ▼
                                   Razorpay Contest API
                                               │
                                               ▼
                                        Audit Trail
                                               │
                                               ▼
                                     Outcome / Feedback
```

The LLM never decides the case — it explains, investigates, recommends, and drafts. Both `Prepare` and `Review` cases reach the Copilot: for `Prepare`, it drafts the response for approval; for `Review`, it assists the human with explanation, contradiction detail, and next-evidence recommendations. `Low Priority` cases are deferred. The gate decision itself is deterministic and auditable, using the classifier's win probability together with rule-based evidence conditions — it is never made by the LLM, so it's always inspectable and never a black box.

### Infrastructure Flow (production-minded)

```
┌─────────────────────────────────────────────────────────────────────┐
│                       MERCHANT DASHBOARD                            │
│                  React + Tailwind + TanStack Query                  │
│   Case Queue │ Case Detail │ Evidence Workspace │ Audit Viewer      │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ HTTP / SSE
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        FASTAPI API LAYER                            │
│   Webhooks │ REST API │ SSE Stream                                  │
│   Signature Verification + Idempotency Middleware                   │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │ enqueue
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                 JOB QUEUE (SQLite-backed, in-process)               │
│   document.process │ score.case │ draft.response │ contest.submit    │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
          ┌────────────────────────┼────────────────────────┐
          ▼                        ▼                        ▼
┌────────────────────┐  ┌────────────────────┐  ┌──────────────────────┐
│ DOCUMENT           │  │ EVIDENCE           │  │ COPILOT (Gemini)     │
│ INTELLIGENCE       │  │ INTELLIGENCE       │  │                      │
│ (Gemini OCR)       │  │ (scores + gate)    │  │ explain/investigate/ │
│                    │  │                    │  │ recommend/draft      │
└─────────┬──────────┘  └─────────┬──────────┘  └──────────┬───────────┘
          └────────────────────────┼────────────────────────┘
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         STORAGE LAYER                               │
│   SQLite Repository (Protocol-abstracted) │ Immutable Doc Store     │
│   Append-only Audit Log                   │ Extraction Cache        │
└─────────────────────────────────────────────────────────────────────┘

External Services (side dependencies):
┌───────────────────────────┐     ┌───────────────────────────┐
│ RAZORPAY                  │     │ AI PROVIDERS              │
│  • Webhooks (input)       │     │  • Gemini (free tier)     │
│  • Documents API          │     │    ├── OCR                │
│  • Contest API (output)   │     │    └── Copilot            │
└───────────────────────────┘     └───────────────────────────┘
```

---

## Infrastructure Layers (new in v2)

### 1. Async Processing via Job Queue

OCR takes 2–5 seconds per page. LLM drafting takes 3–10 seconds. The API must return `200 OK` immediately; all heavy work happens in background workers.

```
Webhook received → enqueue "dispute.created" → return 200
                                                │
                                                ▼
                              Worker picks job → normalize → create Case
                                                │
                                                ▼
                              SSE event pushed → Dashboard updates live
```

**Queue implementation:** SQLite-backed, in-process worker threads. Zero external dependencies (no Redis/Celery). Swappable for Celery + Redis in production via the same queue interface.

**Job types:**
- `document.process` — OCR + fact extraction + caching
- `score.case` — recompute completeness/quality/consistency/gate
- `draft.response` — Gemini generates grounded response
- `contest.submit` — human-approved submission to Razorpay

### 2. Repository Pattern for Storage

All storage access goes through a `CaseRepository` Protocol. Domain logic has no SQL. Today: SQLite. Tomorrow: Postgres. The swap is a config change, not a rewrite.

```python
class CaseRepository(Protocol):
    def get(self, case_id: str) -> Optional[Case]: ...
    def save(self, case: Case) -> None: ...
    def list_by_priority(self, limit: int = 50) -> list[Case]: ...
    def find_by_dispute_id(self, dispute_id: str) -> Optional[Case]: ...
```

### 3. Resilience Patterns

| Pattern | Where | Why |
|---|---|---|
| Circuit breaker | Gemini client (OCR + copilot) | If AI provider is down, system degrades gracefully instead of hanging |
| Rate limiter | Gemini (14 RPM safe) | Respect free-tier quota across both OCR and copilot calls |
| Exponential backoff + retry | Razorpay API, AI APIs | Handle transient failures |
| Idempotency keys | Webhook handler, contest submission | Prevent duplicate processing on Razorpay retries |
| Dead letter queue | Job queue | Failed jobs after 3 retries go to DLQ for inspection |

### 4. Audit Trail (append-only)

Every decision is logged immutably:

```json
{timestamp, case_id, event_type, actor, data}
```

**Events logged:**
- `dispute.received` — webhook payload hash
- `document.uploaded` — doc_id, content_hash, mime_type
- `document.extracted` — fact count, quality flag, extraction method
- `scores.computed` — completeness, quality, consistency, case_strength, win_probability
- `contradiction.detected` — fields that disagree
- `gate.decision` — prepare/review/low-priority + which condition failed
- `copilot.drafted` — draft_id, token count, citation count
- `human.approved` — editor_id, edits_made
- `contest.submitted` — Razorpay response, correlation_id
- `dispute.resolved` — win/loss outcome (from follow-up webhook)

This is the proof of "bounded, not just confident" — every AI claim, every gate decision, every human override is traceable.

### 5. SSE for Live Updates (not WebSocket)

The dashboard is mostly read-only from the server's perspective. SSE (Server-Sent Events) is unidirectional (server → client) and perfectly fits our use case:
- Document processing progress
- Score updates
- Gate decision changes
- Draft ready

Simpler than WebSocket, easier to debug, works with standard HTTP infrastructure.

### 6. Razorpay Integration Details

**Input (webhook):**
- Endpoint: `POST /api/webhooks/razorpay`
- HMAC-SHA256 signature verification against raw body (never re-serialize JSON)
- Idempotency via `payload.id` deduplication
- Fast 200 response; processing happens async

**Output (contest):**
- `POST /disputes/{dispute_id}/contest` with `action: submit`
- Requires at least one `document_id`
- Includes `summary` (the AI-drafted, human-approved response)
- Idempotency key prevents duplicate submissions

---

## Component Detail

### 1. Data ingestion
- Razorpay Test Mode webhook listener receives realistic dispute payloads (real schema, sandbox data)
- Synthetic evidence documents generated per dispute, matching Razorpay's evidence-slot taxonomy
- Webhook signature verified; payload normalized; case created; job enqueued

### 2. Document Intelligence (extraction layer)

**Primary extractor:** Gemini 1.5 Flash (free tier) — multimodal, handles Indian formats natively, outputs structured JSON with confidence scores.
**Fallback:** Tesseract + regex rules (for cost/latency-sensitive paths or when Gemini is rate-limited).

**Pipeline:**
1. Ingest → validate MIME + size → compute content hash
2. PDF → images via PyMuPDF (handles multi-page)
3. Quality assessment (blur detection via Laplacian variance on the actual file — PDFs rendered via PyMuPDF, images assessed directly; threshold=100, uniformity bypass=0.1)
4. Gemini Vision extraction → structured facts with citations
5. Normalize (dates → ISO 8601, amounts → float, names → canonical)
6. Cache by content hash (avoid re-extracting same document)

Each extracted fact is tagged with its source document, enabling citations later.

### 3. Evidence Intelligence — three separate, explainable scores

| Score | What it measures | Example |
|---|---|---|
| Completeness | Required evidence types present ÷ required for this reason code | 87% |
| Quality | Are documents legible/usable, not degraded | 94% |
| Consistency | Do documents agree with each other (amounts, names, dates match; no contradicting claims) | 71% |

Combined into a **Case Strength** score (0-100), reported alongside — not instead of — the classifier's win probability, and explicitly labeled as estimated from evidence quality, completeness, consistency, and simulated historical outcomes. This is more honest and more explainable than a single opaque probability.

### 4. Contradiction detection

Rule-based field comparison across documents:
- Does the invoice amount match the dispute amount?
- Does the delivery date precede the customer's "not received" complaint?
- Does the customer name match across documents?
- Do tracking IDs align between shipping proof and delivery receipt?

A detected contradiction (e.g. customer says "never received" but delivery proof shows signed receipt before the dispute date) is surfaced explicitly and blocks auto-submission regardless of how high the win-probability score is — this is the strongest signal of the system being bounded, not just confident.

### 5. Learned classifier (win probability)

Gradient boosting model trained on engineered features (completeness ratio, count present/missing/degraded, amount, reason code).
- Trained on 2,100 synthetic disputes, evaluated on 900 held-out
- Ground truth: rule-based completeness threshold + quality checks + injected realistic outcome noise (8% flip rate, modeling real-world bank discretion) — avoids label leakage / trivial reconstruction
- Deliberately kept as GradientBoosting, not a deep model — the differentiation is in evidence reasoning depth, not model complexity

### 6. Multi-condition gate (bounded & explainable — the "bar" requirement)

Auto-draft is allowed only if **all** hold:
- Win probability ≥ auto-selected threshold (currently ~50% — see note below)
- Evidence completeness ≥ 90%
- Evidence consistency ≥ 90%
- No unresolved contradiction
- All required evidence categories present

Otherwise → human review, with the specific failing condition shown. A single weak dimension (e.g. high probability but low consistency) is enough to block auto-submission — this is stricter and more defensible than a single-threshold gate.

**On the win-probability threshold — data-derived, not hand-picked:** the spec originally fixed this at 85%, set before any classifier existed to check it against. Once trained, the classifier's probability structurally topped out near 83–84% (an artifact of the 8% outcome-noise injected into ground truth, not a bug), so a fixed 85% floor made the full gate approve only 2 of 901 held-out cases — a non-functional gate, not a conservative one.

**The fix:** `GATE_MIN_WIN_PROBABILITY` is no longer hardcoded. It's selected by `models/train_and_evaluate.py::auto_select_gate_threshold()`, which sweeps candidate thresholds through the full gate on a held-out validation split (carved out of train, never touching test), and picks the lowest threshold that clears a 15%-FP-rate safety ceiling with a reliable sample size (≥20 approved cases). The result is saved to `models/gate_threshold.json` and loaded by `scoring.py` at runtime — the number is a trained artifact, inspectable and reproducible, not a value someone typed in.

This also means completeness/consistency/contradiction-freedom — not probability — turn out to be doing nearly all of the gate's real safety filtering, which is itself a finding worth stating plainly to judges.

### 7. AI Copilot (LLM layer — four abilities, not one)

Powered by the Gemini API — the same provider used for OCR, kept to a single AI vendor deliberately to simplify the resilience/rate-limit surface (one circuit breaker, one quota to manage) rather than juggling two providers under hackathon time constraints. Four distinct abilities:
- **Explain** — why is this case weak or strong, in plain language
- **Investigate** — surface detected contradictions between documents
- **Recommend** — what evidence should be uploaded next, and how much it would move the case-strength score
- **Draft** — write the grounded summary response, every factual sentence tagged with its source document (citation), so nothing is generated without traceability

**Prompt engineering:** strict citation-grounded generation. The LLM is instructed to refuse any claim it cannot cite to a specific merchant-provided document. This is how Evidence Integrity Mode is *prompted* — the enforcement that actually matters is a code-level check, not the prompt alone: before any draft is persisted, every citation's `fact_id` is verified against `extracted_facts` for that dispute, and a citation that doesn't resolve is rejected outright, regardless of which model produced it. That's the honest answer to "how do you know it's not hallucinating" — a provider-agnostic, inspectable guarantee, not a claim about model behavior.

### 8. Evidence Gap Analysis

> "You have: invoice, customer comms, T&C. Missing: delivery proof, tracking information. Impact: high — delivery proof is required for RZP01 and is the strongest missing evidence."

Directly reuses the missing list already computed by the rule layer, ranked by which missing item most affects the case-strength score.

### 9. Deadline & priority intelligence

Uses `respond_by` from the dispute webhook (already part of Razorpay's schema) to compute urgency.

`Priority = f(dispute amount, win probability, deadline urgency, evidence readiness)`

Dashboard surfaces cases as:
- 🔴 **Act now** — deadline < 6h OR high amount + high probability
- 🟡 **Review soon** — deadline < 24h OR medium probability
- 🟢 **Low priority** — deadline > 24h OR low probability

Not just a flat list — the merchant sees what matters most first.

### 10. Audit trail

Every case logged: input features → three scores → contradiction check → gate decision → action → human override (if any). See Infrastructure Layers §4 for full event taxonomy.

### 11. Dashboard — merchant command center, not an ML metrics page

- Live case queue sorted by priority, color-coded, with amount and deadline visible at a glance
- Case detail: evidence bundle, extracted facts, case-strength breakdown (completeness/quality/consistency), contradiction warnings, drafted response with clickable citations
- Business metrics bar: disputes this month, auto-prepared count, human-review count, likely recoverable amount, estimated time saved — clearly labeled as simulated on the synthetic test batch
- One clearly-shown weak/contradictory case where the agent declines to auto-act and escalates — demonstrates graceful, bounded failure handling

---

## Data & evaluation methodology

**Why synthetic data is legitimate here:** no hackathon team can ethically obtain real merchant dispute data. What's being evaluated is whether the methodology is rigorous, not whether the data is real.

- **Real-shaped schema** — evidence types, reason codes, and document slots follow Razorpay's actual published API/dispute structure
- **Defensible ground truth** — win/loss label derived from Razorpay's own required-evidence-per-reason-code table, not guessed thresholds, plus realistic outcome noise to avoid trivial separability
- **Enforced three-way split** — train (1,679) / validation (420, carved from train, used only for gate-threshold selection) / test (901, never touched until final reporting) — classifier never sees test labels during training, and the gate threshold is never tuned against test data either
- **Honest baseline comparison** — rule-only completeness threshold reported alongside the learned classifier

### Current results (held-out test set, n=901)

| Metric | Rule-only baseline | Learned classifier (uncalibrated) |
|---|---|---|
| Precision | 0.534 | 0.777 |
| Recall | 0.891 | 0.834 |
| F1 | 0.668 | 0.805 |

### Full multi-condition gate (test set, auto-selected threshold, calibrated model)

| Metric | Value |
|---|---|
| Precision | 0.910 |
| Recall | 0.822 |
| FP rate among gate-approved cases | 9.0% (ceiling: 15%) |
| Cases approved for auto-prepare | 223 of 901 (reliable sample) |

The gate's precision (0.910) is higher than the classifier's alone (0.777) — completeness, consistency, and contradiction-freedom filter out exactly the cases where the classifier alone would be wrong, which is the entire point of combining conditions rather than thresholding probability alone.

### Probability calibration

A raw classifier probability is not automatically an honest one — gradient-boosted models are known to be systematically over- or under-confident. `win_probability` is calibrated using `sklearn.calibration.CalibratedClassifierCV`, fit on the validation split (never train or test) with `cv='prefit'` so the underlying classifier itself is untouched.

**Method selection:** both isotonic regression and sigmoid (Platt) scaling were evaluated. Isotonic won decisively on both metrics below, despite the validation set being modest in size (420 cases) — a regime where sigmoid is typically expected to be safer. The improvement held on the fully held-out test set (never touched during calibration fitting), which is the check against isotonic simply overfitting the validation split.

| Metric | Uncalibrated | Calibrated (isotonic) | Change |
|---|---|---|---|
| Brier score | 0.0933 | 0.0923 | −1.0% |
| Expected Calibration Error (ECE) | 0.0384 | 0.0247 | −35.6% |

Classification performance (precision/recall/F1) moved by ≤1% — expected and correct, since calibration is not meant to change *what* the model predicts, only how honestly its confidence numbers reflect reality. What changed is that a stated "75% chance of winning" is now much closer to actually meaning 75%, not an overconfident raw score. Reliability diagram: `models/reliability_diagram.png`.

### Contradiction detector (test set, vs. injected ground truth)

Precision/recall both 1.000 on this split. This is expected, not a leakage red flag the way it would be for the classifier: the detector runs deterministic rules (amount/date/name/order-ID matching) against contradictions injected by an equally deterministic corruption step in the synthetic generator — there's no learned model here to overfit, and no feature shared with the win-probability classifier's training.

### False-positive cost accounting (concrete, test-set derived)

All figures below use the calibrated model's test-set confusion matrix (n=901, 20 FPs out of 223 gate-approved cases, precision 0.910). Where a rupee figure depends on an assumption rather than measured data, the assumption is explicitly labeled.

#### 1. Wasted ops effort (primary, defensible cost)

**Assumption (labeled):** Reviewing a dispute, preparing a response submission, and filing it with Razorpay takes approximately 20 minutes of ops/support staff time. Loaded cost for SMB support staff is assumed at ₹400/hour (covering salary, tools, overhead — this is an illustrative assumption, not a researched industry figure).

| Component | Value | Derivation |
|---|---|---|
| Per-case cost | ₹133 | 20 min × ₹400/hr ÷ 60 |
| False positives in test set | 20 | Calibrated model, full gate, test set |
| Gate-approved cases | 223 | 203 TP + 20 FP |
| Precision | 91.0% | 203 ÷ 223 |
| Total wasted effort (test set) | ₹2,660 | 20 FPs × ₹133 |
| **Wasted effort rate** | **₹295 per 100 disputes processed** | ₹2,660 ÷ 901 × 100 |

The rate (₹295 per 100 disputes) scales independently of dataset size. In a production environment processing 1,000 disputes/month, this estimates ₹2,950/month in wasted ops effort from false positives.

#### 2. Opportunity cost (secondary, qualitative)

Ops time spent on a false-positive case is time *not* spent on a true-positive case (gathering stronger evidence, following up with the customer) or on a borderline case where human judgment might tip it toward winning. This is real but hard to quantify without knowing the merchant's total dispute volume and staffing. The gate's 91.0% precision means that for every 10 cases the merchant acts on, roughly 9 will actually win — the opportunity cost of the 1 miss is bounded by the gate's accuracy.

#### 3. Reputational / issuing-bank cost (acknowledged, not quantified)

Issuing banks and card networks track representment win rates. A sustained pattern of losing disputes can lead to increased chargeback fees, higher processing rates, or in extreme cases, account termination (Visa's VDMP and Mastercard's ECM programs monitor these ratios). Industry data provides context: merchants' overall chargeback representment net win rates are estimated at 8.1% (Chargeflow, 2024), while those with structured evidence processes achieve 30–45% (PXP, 2026). Filing fees of $15–$100 per incident are common (StrictlyZero, 2026).

We do **not** assign an invented rupee figure to this cost. The reputational risk is real and is one of the reasons the gate uses a multi-condition design (probability + completeness + consistency + contradictions) rather than a single probability threshold — even a well-calibrated probability alone would produce too many losing submissions, as shown below.

#### 4. Gate value comparison — why multi-condition beats threshold-only

The multi-condition gate's primary economic value is *avoiding* false positives that a simpler approach would produce:

| Approach | FPs | Precision | Wasted effort (test set) | Wasted effort per 100 disputes |
|---|---|---|---|---|
| Always approve (no filtering) | 654 | 27.4% | ₹86,982 | ₹9,654 |
| Raw probability threshold only (≥0.50) | 60 | 77.6% | ₹7,980 | ₹886 |
| **Full multi-condition gate** | **20** | **91.0%** | **₹2,660** | **₹295** |

Reading this table:
- The **full gate avoids ₹5,320 in wasted effort** (40 fewer FPs) compared to using probability alone — this is the direct value of adding completeness, consistency, and contradiction checks on top of the classifier.
- Compared to an "always approve" baseline, the gate eliminates 97% of false positives (654 → 20), saving ₹84,322 per 901 disputes.
- The gate's 91.0% precision means a merchant can trust that when the system says "prepare this case," it will win roughly 9 out of 10 times — high enough to act on without second-guessing, low enough that the 1-in-10 miss is bounded.

#### 5. False negatives (the safer failure direction)

A false negative means the gate sends a actually-winnable case to human review instead of auto-preparing it. The cost is only the *delay* — the merchant still reviews and can submit manually. No response is filed prematurely, no ops effort is wasted on a losing case, and no reputational risk is incurred. This is by design: the gate is calibrated to err on the side of caution.

All merchant-facing ROI figures (₹ recoverable, time saved) are explicitly labeled "simulated on synthetic test batch" everywhere they appear in the UI — never presented as real-world claims.

---

## Meeting "the bar"

| Requirement | How it's met |
|---|---|
| One class of loss | Service chargebacks only, explicitly scoped to 6 named reason codes |
| Measured precision/recall on held-out test set | Computed from labeled synthetic split enforced in code |
| Honest metrics including false-positive cost | Both FP and FN costs quantified, rule baseline shown for comparison, ROI figures labeled as simulated |
| Strictly defense-only | Agent only responds to disputes already filed against a merchant; never fabricates evidence; zero offensive capability |

---

## The demo — two real cases, not a feature tour

Both cases below are real, live-verified runs through the actual pipeline (real Gemini OCR, real scoring, real gate decisions) — not scripted or hardcoded outputs.

### Beat 1 — the score jump (dispute: `disp_delivery_test`, ₹42,000, RZP01)

1. Dispute arrives with 1 of 3 required documents already uploaded (Proof of Service) — case strength weak, win probability ~10%, gate: `low_priority`
2. Merchant uploads Customer Communication — completeness rises (33% → 67%), but win probability stays flat at ~10% and the gate stays `low_priority`. This step is intentionally undramatic: it demonstrates the classifier correctly refusing to reward partial evidence, not a smooth cosmetic slope.
3. Merchant uploads Terms & Conditions (the final required document) — completeness hits 100%, win probability jumps ~10% → 81% (calibrated), the gate flips to `prepare`, and a grounded draft response auto-generates with citations to the real extracted facts.
4. Click a citation in the draft → the source document/fact it references opens.
5. Merchant clicks Approve → a `human.approved` audit event is recorded → Submit becomes available.

### Beat 2 — the gate blocks a bad case (dispute: `disp_final_cache`, ₹38,500, RZP01)

1. Case has all 3 required document types present, but scoring surfaces 6 real contradictions across the uploaded documents: the invoice date is roughly a year after the dispute's filed date, and the invoice's order ID doesn't match the dispute's order ID.
2. Despite the underlying documents otherwise looking complete, the gate correctly holds this case at `low_priority` — no draft is auto-generated, and the merchant sees the specific failing conditions in plain language (translated from raw rule names to descriptions like "the date on your Billing Proof document happens after the customer's complaint date").
3. This is the moment that proves "bounded," not just "confident" — a case that a naive probability-only threshold might have approved is correctly held back by the multi-condition gate.

Every step in both beats is backed by the audit trail: `dispute.received` → `document.uploaded` → `document.extracted` → `scores.computed` → `contradiction.detected` (Beat 2 only) → `gate.decision` → `copilot.drafted` (Beat 1 only) → `human.approved` (Beat 1 only).

---

## Build priority

### P0 — must have
- Razorpay Test Mode integration, real dispute webhook (signature + idempotency)
- Document upload + structured fact extraction (Gemini OCR + cache)
- Evidence-slot classification, completeness scoring
- Evidence quality scoring
- Contradiction detection (rule-based field comparison)
- Case-strength + win-probability model
- Multi-condition human-review gate
- Grounded response generation with citations (Gemini)
- Audit trail (append-only)
- Async job queue (SQLite-backed)

### P1 — make it feel like a real product
- Deadline-aware priority queue
- Evidence-gap "what's missing / what to upload next" recommendations
- Merchant-facing dashboard (business metrics, not ML metrics)
- Response editor (edit/regenerate/approve workflow)
- SSE for live case updates
- Circuit breaker + rate limiter for the Gemini client

### P2 — differentiation, if time allows
- Outcome feedback loop
- Historical case retrieval
- ~~Confidence calibration~~ — **completed**, see "Probability calibration" section above
- Batch dispute processing
- Additional reason codes beyond the initial 6
- Repository swap to Postgres (production readiness)

### Known limitations (stated plainly, not hidden)

**Contest API — hard platform limitation in Test Mode:**
The Contest API (`PATCH /v1/disputes/:id/contest`) is correctly implemented and tested against Razorpay's documented contract (request construction, error handling, idempotency, evidence-slot mapping). However, Razorpay's Test Mode provides no way to obtain a contestable `dispute_id` — disputes are exclusively bank/issuer-initiated, with no merchant-side creation API, no dashboard simulation tool, and no test dispute IDs provided by Razorpay. This was verified across Razorpay's documentation, their Postman workspace, and developer community forums. The result: `GET /v1/disputes` returns 0 disputes in sandbox, and any contest submission returns `400: The id provided does not exist`. This is a known gap compared to Stripe (`stripe trigger payment_dispute.created`) and Adyen (dispute simulation sandbox), both of which provide dispute simulation in test mode. The Contest API will be validated end-to-end on the first live dispute in production. All upstream steps — approval gating, idempotency, audit logging, evidence-slot building from real `doc_*` IDs — are real and tested.

**Document API — fully tested in sandbox:**
Document uploads (`POST /v1/documents`) are wired to Razorpay's real Documents API and verified end-to-end in test mode. A real PDF upload returns a real `doc_*` ID (e.g., `doc_TXalUDdDfBAsOr`), and the `razorpay_doc_id` is stored alongside the local document record. These real IDs flow into the contest payload via the evidence-slot builder. The extraction/scoring/gate pipeline downstream of upload operates on document content, not storage location, so it is unaffected by whether the file lives locally or on Razorpay's servers.

---

## Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | FastAPI (Python) | Async-native, clean dependency injection, great for webhooks |
| OCR | Gemini 1.5 Flash (free tier) | Free, multimodal, handles Indian formats natively, structured JSON output |
| Copilot LLM | Gemini API | Single-provider design (also used for OCR) — one quota, one circuit breaker; citation-grounding enforced by a code-level integrity check, not model choice |
| Live updates | SSE (not WebSocket) | Unidirectional server→client is all we need; simpler, easier to debug |
| Job queue | SQLite-backed in-process | Zero-dependency; swappable for Celery + Redis in production |
| Storage | SQLite + repository pattern | Hackathon-pragmatic; production-swappable via Protocol |
| ML | scikit-learn (GradientBoostingClassifier) | Fast, CPU-friendly, deliberately not over-engineered |
| Frontend | React + Tailwind + TanStack Query | Priority-sorted dashboard with case workspace |
| Data source | Razorpay Test Mode webhooks + synthetic evidence generator | Real schema, labeled, defensible ground truth |

**Key source:** Razorpay SHIELD blog — the line that justifies the entire product scope:
> "SHIELD covers all the fraud chargebacks but not the service chargebacks."

Razorpay Submit Evidence docs

---

## Changelog from v1

| Area | v1 | v2 |
|---|---|---|
| Architecture diagram | Intelligence flow only | Unified: intelligence + infrastructure |
| OCR provider | Not specified | Gemini 1.5 Flash (free tier) |
| Copilot LLM | Claude API | Gemini API — switched to a single-provider design during implementation, sharing OCR's client/quota/circuit-breaker |
| Live updates | WebSocket | SSE (simpler, fits use case) |
| Job queue | Not shown | SQLite-backed, in-process workers |
| Storage abstraction | SQLite directly | Repository pattern (Protocol) |
| Resilience | Not mentioned | Circuit breaker, rate limiter, retry, DLQ |
| Audit trail | Mentioned | Full event taxonomy |
| Razorpay integration | Conceptual | Explicit: signature, idempotency, contest API flow |
| External services | Downstream of storage | Side dependencies (input + output) |
| Build priorities | P0/P1/P2 | P0/P1/P2 with async + resilience added |
