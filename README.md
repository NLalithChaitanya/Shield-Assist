# 🛡️ Shield Assist
### AI Dispute Resolution Copilot for Razorpay Merchants

> **Razorpay Buildathon · Track 02 — AI Risk Manager**  
> **Sub-problem — Chargeback Evidence Responder**

[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react&logoColor=111827)](https://react.dev/)
[![Gemini](https://img.shields.io/badge/Gemini-AI%20Copilot-4285F4?logo=google&logoColor=white)](https://ai.google.dev/)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-ML-F7931E?logo=scikit-learn&logoColor=white)](https://scikit-learn.org/)
[![Razorpay](https://img.shields.io/badge/Razorpay-Test%20Mode-3395FF)](https://razorpay.com/)

---

## 🎯 What is Shield Assist?

**Shield Assist is an AI-powered evidence intelligence and dispute-resolution copilot for service chargebacks.**

When a merchant receives a dispute, Shield Assist does not simply predict whether the merchant will win.

It:

1. **Understands the dispute** and its reason code.
2. **Audits the evidence** for completeness, quality, and consistency.
3. **Finds missing or contradictory evidence.**
4. **Estimates case strength and win probability.**
5. **Applies a deterministic safety gate** before preparing a response.
6. **Generates a grounded response** where factual claims are traceable to merchant documents.
7. **Requires human approval** before submission.
8. **Maintains an immutable audit trail** from webhook → evidence → decision → submission.

> 🔒 **Evidence Integrity Mode**
>
> Shield Assist never creates or modifies evidence. Every factual claim in an AI-generated response must be traceable to a specific merchant-provided document.

---

## 💡 Why service chargebacks?

Razorpay's SHIELD documentation explicitly distinguishes fraud chargebacks from service chargebacks:

> **“SHIELD covers all the fraud chargebacks but not the service chargebacks.”**

That creates a clear product gap.

| Chargeback | Existing SHIELD coverage | Core problem |
|---|---|---|
| Fraud / unauthorized transaction | ✅ Covered by SHIELD | Transaction-level fraud detection |
| **Service chargeback** | **❌ Not covered** | **Evidence assembly, validation, and representation** |

Shield Assist is deliberately **not another fraud detector**.

It focuses on the evidence-heavy workflow for service disputes:

- Goods/services not provided
- Refund not processed
- Account debited but no confirmation
- Business not responding
- Goods/services not received
- Goods/services not as described

---

# 🧠 The Core Idea

A merchant should never have to ask:

> **“Do I have enough evidence to fight this dispute?”**

Shield Assist turns that question into an explainable decision:

```text
                DISPUTE ARRIVES
                       │
                       ▼
              Understand the case
                       │
                       ▼
             What evidence is required?
                       │
             ┌─────────┼─────────┐
             ▼         ▼         ▼
        Completeness  Quality  Consistency
             │         │         │
             └─────────┼─────────┘
                       ▼
              Evidence Intelligence
                       │
              ┌────────┴────────┐
              ▼                 ▼
        Case Strength      Win Probability
              │                 │
              └────────┬────────┘
                       ▼
             DETERMINISTIC GATE
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
       PREPARE       REVIEW     LOW PRIORITY
          │            │
          └──────┬─────┘
                 ▼
           AI COPILOT
     Explain • Investigate
     Recommend • Draft
                 │
                 ▼
        HUMAN APPROVAL REQUIRED
                 │
                 ▼
        RAZORPAY CONTEST API
                 │
                 ▼
             AUDIT TRAIL
```

### The critical design decision

**The LLM never decides whether a case is safe to prepare.**

The gate is deterministic and auditable.

The AI is used for what it is good at:

- explaining,
- investigating,
- recommending,
- drafting.

The safety boundary is enforced in code.

---

# 🔄 End-to-End Product Flow

## 1. Dispute ingestion

A Razorpay dispute enters through:

```http
POST /api/webhooks/razorpay
```

The webhook handler:

- verifies **HMAC-SHA256** against the raw request body,
- deduplicates repeated events,
- normalizes the Razorpay payload,
- creates the case,
- immediately returns `200 OK`,
- sends heavy work to the background queue.

The dispute provides information such as:

- dispute ID,
- reason code,
- amount,
- `respond_by`,
- transaction/order context.

---

## 2. Evidence collection

Merchants upload supporting documents.

Documents can be sent through Razorpay's Documents API and receive real `doc_*` IDs.

Shield Assist maps those documents to the evidence slots required by the dispute reason code.

### Supported reason codes

| Code | Network | Reason | Required evidence |
|---|---|---|---|
| `RZP01` | Razorpay | Goods/Services not Provided | Proof of service, customer communication, T&C |
| `RZP04` | Razorpay | Refund not Processed | Refund proof, billing proof, customer communication, refund/cancellation policy |
| `RZP05` | Razorpay | Account Debited but No Confirmation | Billing proof, access/activity log, customer communication, T&C |
| `RZP06` | Razorpay | Business Not Responding | Proof of service, billing proof, customer communication |
| `UPI 1064` | UPI | Goods/Services Not Received | Proof of service, customer communication, T&C |
| `UPI 1062` | UPI | Goods/Services Not As Described | Proof of service, customer communication, refund/cancellation policy |

The evidence requirements follow Razorpay's documented suggested documents.

---

# 📄 3. Document Intelligence

Every uploaded document passes through:

```text
Document
   │
   ├── MIME + size validation
   ├── Content hashing
   ├── PDF → images (PyMuPDF)
   ├── Quality assessment
   ├── Gemini Vision OCR / extraction
   ├── Structured fact extraction
   ├── Normalization
   └── Content-hash cache
```

The system extracts structured facts such as:

- names,
- dates,
- amounts,
- order IDs,
- tracking IDs,
- delivery information,
- refund information.

Every extracted fact retains its source document, enabling traceable citations later.

### Quality detection

Document quality is checked before evidence is trusted.

The implementation uses **Laplacian variance** for blur detection, including PDF rendering before assessment.

---

# 📊 4. Evidence Intelligence

Shield Assist evaluates every case across **three independent dimensions**:

| Score | What it measures |
|---|---|
| **Completeness** | Are all evidence categories required for this reason code present? |
| **Quality** | Are the documents legible and usable? |
| **Consistency** | Do documents agree on important facts? |

### Completeness

```text
required evidence present
────────────────────────── × 100
required evidence
```

### Consistency

Cross-document checks include:

- invoice amount vs. dispute amount,
- delivery date vs. complaint date,
- customer name across documents,
- tracking/order IDs across evidence,
- logically inconsistent dates.

---

# ⚠️ 5. Contradiction Detection

A case can be **complete** and still be unsafe.

Example:

```text
Invoice:
Order ID = ORD-1042
Amount  = ₹38,500

Dispute:
Order ID = ORD-9917

Delivery Proof:
Signed delivery date conflicts with complaint timeline
```

Shield Assist does not hide this behind a probability score.

It surfaces the contradiction explicitly.

> **Contradictions block preparation regardless of how high the ML probability is.**

This is a key difference from a probability-only system.

---

# 🤖 6. Win Probability Model

Shield Assist uses a lightweight **GradientBoostingClassifier**.

### Training data

- **3,000 synthetic disputes**
- training / validation / held-out test separation
- amount, document counts, completeness, quality and reason-code features
- consistency and contradiction signals kept as gate signals rather than simply becoming classifier features

### Why synthetic data?

Real merchant dispute data is not available for a hackathon.

Instead, the generator follows the real Razorpay-shaped structure:

- reason-code taxonomy,
- evidence-slot taxonomy,
- realistic document combinations,
- realistic amounts,
- injected contradictions,
- outcome uncertainty.

The test set is never used for threshold selection, calibration, or model tuning.

---

# 🎚️ 7. Probability Calibration

A raw ML probability is not automatically trustworthy.

Shield Assist calibrates the model using **isotonic regression** on the validation split.

| Metric | Calibrated result |
|---|---:|
| Brier score | **0.0923** |
| Expected Calibration Error | **0.0247** |

The objective is to make the displayed probability more meaningful rather than simply more confident.

---

# 🛡️ 8. The Deterministic Safety Gate

The gate is the primary safety boundary.

A case can enter **PREPARE** only when **all** conditions pass:

```text
Win probability       ≥ selected threshold
Completeness          ≥ 90%
Consistency           ≥ 90%
Required evidence     = present
Unresolved conflicts  = 0
```

| Decision | Meaning |
|---|---|
| 🟢 **PREPARE** | Evidence is strong enough for AI-assisted preparation |
| 🟡 **REVIEW** | Human investigation is required |
| ⚪ **LOW PRIORITY** | Case is currently weak or not urgent |

### Why multiple conditions?

Because **probability alone is not enough**.

A classifier can be confident for the wrong reasons. The gate adds independent evidence constraints around it.

This produced a major improvement:

```text
Classifier precision      77.6%
             │
             │ + completeness
             │ + consistency
             │ + contradiction checks
             ▼
Full gate precision       91.0%
```

---

# ✍️ 9. Grounded AI Copilot

When the workflow reaches the copilot, Gemini provides four distinct capabilities:

### Explain
Explain why a case is strong or weak in plain language.

### Investigate
Surface contradictions between documents.

### Recommend
Identify missing evidence and suggest what the merchant should upload next.

### Draft
Generate the dispute response using only supported merchant evidence.

---

# 🔗 Evidence Integrity

The copilot is not trusted merely because a prompt says “do not hallucinate.”

Shield Assist adds a **code-level citation integrity check**.

Every factual claim must resolve through:

```text
citation
   ↓
fact_id
   ↓
extracted_facts
   ↓
document_id
   ↓
merchant document
```

If the model references a nonexistent fact:

```text
AI draft
   ↓
citation validation
   ↓
❌ fact not found
   ↓
DRAFT REJECTED
```

This creates an inspectable, provider-agnostic grounding guarantee.

---

# 👤 10. Human Approval Boundary

Shield Assist is a copilot, **not an autonomous dispute bot**.

| AI **can** | AI **cannot** |
|---|---|
| Explain a case | Autonomously submit |
| Investigate contradictions | Bypass the safety gate |
| Identify evidence gaps | Override contradictions |
| Recommend next evidence | Fabricate evidence |
| Draft a response | Approve its own response |

Submission requires:

```text
Draft exists
     +
human.approved audit event
     +
status = ready
     ↓
Contest submission allowed
```

---

# ⏱️ 11. Deadline & Priority Intelligence

Disputes are also time-sensitive.

Shield Assist uses `respond_by` together with:

- dispute amount,
- win probability,
- deadline urgency,
- evidence readiness.

### 🔴 Act now
Deadline < 6 hours, or high-value/high-probability case.

### 🟡 Review soon
Deadline < 24 hours, or medium probability.

### 🟢 Low priority
Deadline > 24 hours, or low probability.

The merchant sees **what matters most first**, rather than a flat case list.

---

# ⚙️ 12. Async Processing

OCR and LLM generation can take seconds, so expensive operations are asynchronous.

```text
Webhook received
       │
       ▼
enqueue job
       │
       ▼
return 200 OK
       │
       ▼
background worker
       │
       ├── normalize
       ├── OCR
       ├── score
       ├── detect contradictions
       └── generate draft
       │
       ▼
SSE event
       │
       ▼
dashboard updates live
```

### Job types

- `document.process`
- `score.case`
- `draft.response`
- `contest.submit`

The current queue is SQLite-backed and in-process, with an interface that can be swapped for Celery + Redis in production.

---

# 📡 13. Live Updates

Shield Assist uses **Server-Sent Events (SSE)**.

The dashboard receives:

- document processing progress,
- score updates,
- gate changes,
- draft readiness.

SSE is sufficient because the dominant requirement is server → client streaming.

---

# 🧾 14. Immutable Audit Trail

Every important lifecycle event is appended to an audit log:

```json
{
  "timestamp": "...",
  "case_id": "...",
  "event_type": "gate.decision",
  "actor": "system",
  "data": {
    "decision": "REVIEW",
    "failed_conditions": [
      "consistency < 90%",
      "contradiction detected"
    ]
  }
}
```

### Events

```text
dispute.received
document.uploaded
document.extracted
scores.computed
contradiction.detected
gate.decision
copilot.drafted
human.approved
contest.submitted
dispute.resolved
```

This gives a complete chain:

> **Input → extraction → evidence analysis → decision → AI output → human approval → submission.**

---

# 🏗️ Architecture

## Intelligence Flow

```text
                         RAZORPAY
                            │
                            ▼
                    Dispute Webhook
                            │
                            ▼
                  Dispute Normalizer
                            │
              ┌─────────────┴─────────────┐
              │                           │
              ▼                           ▼
        Dispute Data               Merchant Evidence
                                      │
                                      ▼
                              Document Intelligence
                               Gemini OCR + extraction
                                      │
                                      ▼
                                Structured Facts
                                      │
                   ┌──────────────────┼──────────────────┐
                   ▼                  ▼                  ▼
             Completeness          Quality          Consistency
                   │                  │                  │
                   └──────────────────┼──────────────────┘
                                      ▼
                              Evidence Intelligence
                                      │
                         ┌────────────┴────────────┐
                         ▼                         ▼
                  Case Strength             Win Probability
                         │                         │
                         └────────────┬────────────┘
                                      ▼
                            Deterministic Gate
                                      │
                         ┌────────────┼────────────┐
                         ▼            ▼            ▼
                      PREPARE       REVIEW    LOW PRIORITY
                         │            │
                         └─────┬──────┘
                               ▼
                         AI COPILOT
              Explain / Investigate / Recommend / Draft
                               │
                               ▼
                     HUMAN APPROVAL REQUIRED
                               │
                               ▼
                       Razorpay Contest API
                               │
                               ▼
                          Audit Trail
```

## Infrastructure Flow

```text
┌─────────────────────────────────────────────────────┐
│                 MERCHANT DASHBOARD                  │
│        React + Tailwind + TanStack Query            │
│   Case Queue │ Case Detail │ Evidence │ Audit      │
└───────────────────────┬─────────────────────────────┘
                        │ HTTP / SSE
                        ▼
┌─────────────────────────────────────────────────────┐
│                    FASTAPI API                      │
│ Webhooks │ REST │ SSE │ Signature │ Idempotency    │
└───────────────────────┬─────────────────────────────┘
                        │ enqueue
                        ▼
┌─────────────────────────────────────────────────────┐
│              SQLITE-BACKED JOB QUEUE                │
│ document.process │ score.case │ draft.response     │
│ contest.submit                                      │
└───────────────────────┬─────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
     DOCUMENT       EVIDENCE       COPILOT
   INTELLIGENCE   INTELLIGENCE       GEMINI
          │             │             │
          └─────────────┼─────────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│                  STORAGE LAYER                      │
│ SQLite Repository │ Immutable Docs │ Audit Log     │
│ Extraction Cache                                    │
└─────────────────────────────────────────────────────┘
```

---

# 📈 Evaluation Results

All model metrics are from held-out synthetic evaluation data.

## ML classifier

| Metric | Score |
|---|---:|
| Precision | **0.777** |
| Recall | **0.834** |
| F1 | **0.805** |

## Full multi-condition safety gate

| Metric | Score |
|---|---:|
| **Precision** | **0.910** |
| Recall | **0.822** |
| F1 | **0.864** |
| FP rate among approved | **9.0%** |
| Auto-prepared cases | **223 / 901** |
| Safety ceiling | **15% FP rate** |

### Key result

The full gate reaches **91.0% precision**, compared with **77.6% for the classifier alone**.

That demonstrates the value of combining:

> **probability + completeness + consistency + contradiction freedom**

rather than trusting a single model score.

---

# 💰 False-Positive Cost Analysis

Using the project's illustrative ops-cost assumption:

| Approach | False positives | Precision | Wasted effort |
|---|---:|---:|---:|
| Always approve | 654 | 27.4% | ₹86,982 |
| Probability threshold only | 60 | 77.6% | ₹7,980 |
| **Full multi-condition gate** | **20** | **91.0%** | **₹2,660** |

The full gate eliminates **97% of false positives** versus always approving.

All merchant-facing ROI figures are explicitly treated as **simulated on the synthetic test batch**, not production claims.

---

# 🧪 Adversarial Safety Testing

The repository contains **23 automated safety tests** targeting the core safety boundary.

| Scenario | Expected |
|---|---|
| High probability + complete + consistent | ✅ PREPARE |
| High probability + incomplete | ❌ BLOCK |
| High probability + contradiction | ❌ BLOCK |
| Below-threshold probability + complete | ❌ NOT PREPARE |
| Low probability + complete | LOW PRIORITY |
| Boundary values | Correct deterministic behavior |
| AI attempts self-approval | ❌ BLOCKED |

The key invariant is:

> **ML probability can never override deterministic safety conditions.**

---

# 🎬 The Demo Story

The strongest demonstration is **two cases**, not a feature tour.

## Beat 1 — Evidence makes the case stronger

**Case:** `disp_delivery_test`  
**Amount:** ₹42,000  
**Reason:** `RZP01 — Goods/Services not Provided`

### Step 1 — Partial evidence

Only Proof of Service exists.

```text
Completeness: 33%
Win probability: ~10%
Decision: LOW PRIORITY
```

### Step 2 — More evidence

Customer Communication is uploaded.

```text
Completeness: 67%
Win probability: ~10%
Decision: LOW PRIORITY
```

The system intentionally does **not** manufacture a smooth probability increase.

### Step 3 — Required evidence complete

Terms & Conditions are uploaded.

```text
Completeness: 100%
Win probability: ~81%
Decision: PREPARE
```

A grounded response is generated with citations to extracted facts.

The merchant can:

```text
Click citation
      ↓
Open source fact/document
      ↓
Review response
      ↓
Approve
      ↓
human.approved audit event
      ↓
Submission becomes available
```

---

## Beat 2 — The gate blocks a bad case

**Case:** `disp_final_cache`  
**Amount:** ₹38,500  
**Reason:** `RZP01`

All required evidence categories are present.

But the documents contain contradictions, including:

- invoice date inconsistent with the dispute timeline,
- invoice order ID different from the dispute order ID,
- additional cross-document contradictions.

Result:

```text
Completeness: HIGH
Probability: potentially HIGH
Contradictions: PRESENT
                     │
                     ▼
                 ❌ BLOCK
```

No response is automatically generated.

The merchant sees the actual failing conditions in plain language.

### This is the key demo moment

> **Shield Assist is bounded, not merely confident.**

---

# 🔌 Razorpay Integration

## Verified in Test Mode

| Capability | Status |
|---|---|
| API authentication | ✅ Verified |
| Documents API | ✅ Verified |
| Real `doc_*` IDs | ✅ Verified |
| Webhook HMAC-SHA256 verification | ✅ Verified |
| Webhook idempotency | ✅ Verified |
| Dispute simulator pipeline | ✅ Implemented |

### Test Mode limitation

Razorpay Test Mode does not provide merchant-created contestable disputes.

Therefore, a complete Contest API submission cannot be exercised against a sandbox-created dispute.

The implementation still includes:

- contest request construction,
- evidence-slot mapping,
- real Razorpay document IDs,
- human approval enforcement,
- idempotency,
- error handling.

The final Contest API call can be validated against the first real contestable dispute in Live Mode.

---

# 🛡️ Security & Resilience

### Security

- Secrets loaded from environment variables.
- `.env` excluded from version control.
- Razorpay webhook signatures verified against the raw request body.
- Constant-time signature comparison.
- Credentials never returned through APIs.
- Logs do not expose credentials.
- Contest submission supports dry-run mode.

### Resilience

| Pattern | Purpose |
|---|---|
| Circuit breaker | Prevent cascading Gemini failures |
| Rate limiter | Protect free-tier AI quota |
| Exponential backoff | Recover from transient API failures |
| Idempotency | Prevent duplicate webhook/submission effects |
| Dead-letter queue | Preserve failed jobs for inspection |

---

# 🗂️ Project Structure

```text
shield-assist/
│
├── backend/
│   ├── app.py
│   ├── razorpay_config.py
│   ├── razorpay_client.py
│   ├── dispute_normalizer.py
│   ├── domain.py
│   ├── repository.py
│   ├── db.py
│   ├── copilot.py
│   ├── job_queue.py
│   ├── resilience.py
│   ├── quality_detection.py
│   │
│   └── jobs/
│       ├── score_job.py
│       ├── document_job.py
│       ├── draft_job.py
│       └── contest_job.py
│
├── data/
│   ├── evidence_requirements.py
│   ├── synthetic_generator.py
│   └── synthetic_generator_v2.py
│
├── models/
│   ├── scoring.py
│   ├── contradiction_rules.py
│   ├── calibrate.py
│   └── train_and_evaluate.py
│
├── tests/
│   ├── test_razorpay_integration.py
│   ├── test_safety_gate.py
│   ├── test_resilience.py
│   ├── test_quality_detection.py
│   └── test_razorpay_client.py
│
├── scripts/
│   ├── smoke_test_backend.py
│   ├── test_razorpay_integration.py
│   └── evaluate.py
│
└── frontend/
    └── React + Tailwind + TanStack Query
```

---

# 🚀 Getting Started

## 1. Configure environment

```bash
cp .env.example .env
```

Add:

```env
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
GEMINI_API_KEY=...
```

## 2. Install dependencies

```bash
pip install -r requirements.txt
```

## 3. Start backend

```bash
uvicorn backend.app:app --reload --port 8000
```

## 4. Start frontend

```bash
cd frontend
npm run dev
```

---

# 🧪 Running Tests

### Full test suite

```bash
python -m pytest tests/ -v
```

### Backend smoke test

```bash
python scripts/smoke_test_backend.py
```

### Razorpay integration

```bash
python scripts/test_razorpay_integration.py
```

### Integration without live Razorpay

```bash
python scripts/test_razorpay_integration.py --skip-live
```

### Evaluation report

```bash
python scripts/evaluate.py
```

---

# 🧩 Tech Stack

| Layer | Technology | Why |
|---|---|---|
| Frontend | React + Tailwind + TanStack Query | Merchant-facing case workspace |
| Backend | FastAPI | Async APIs, webhooks, service boundaries |
| OCR / extraction | Gemini | Multimodal document understanding |
| Copilot | Gemini | Explain, investigate, recommend, draft |
| ML | scikit-learn GradientBoosting | Lightweight and CPU-friendly |
| Calibration | Isotonic regression | More reliable probabilities |
| Live updates | SSE | Simple server → client streaming |
| Queue | SQLite-backed workers | Zero external queue dependency |
| Storage | SQLite + Repository Pattern | Hackathon-simple, production-swappable |
| PDF processing | PyMuPDF | Multi-page PDF rendering |
| Quality detection | Laplacian variance | Detect degraded evidence |
| Payments platform | Razorpay APIs | Real dispute/evidence integration |

---

# ⚠️ Known Limitations

### 1. Contest API cannot be fully E2E tested in Test Mode

Razorpay Test Mode does not expose merchant-created disputes.

### 2. Gemini free-tier limits

Transient provider errors can occur. Retry, rate limiting, and circuit-breaking are implemented, with graceful degradation where applicable.

### 3. Synthetic ML data

The classifier is evaluated on synthetic disputes. Reported metrics therefore **do not claim production performance**.

### 4. SQLite

The current repository is appropriate for a single-process hackathon deployment.

A production deployment can move the same repository interface to PostgreSQL.

---

# 🧭 What Makes Shield Assist Different?

Most AI demos stop at:

```text
Upload → LLM → Answer
```

Shield Assist adds the engineering required for a high-stakes workflow:

```text
Real integration
      +
Evidence taxonomy
      +
Document intelligence
      +
Three-way evidence scoring
      +
Contradiction detection
      +
Calibrated ML
      +
Deterministic safety gate
      +
Citation integrity
      +
Human approval
      +
Async processing
      +
Resilience
      +
Immutable audit trail
```

The core philosophy is:

> **AI should make dispute operations faster without becoming the authority that decides what gets submitted.**

---

# 🏁 Judge's 60-Second Summary

**Shield Assist solves a specific gap: service chargeback evidence response.**

A Razorpay dispute enters the system. Shield Assist determines what evidence is required for that reason code, processes the merchant's documents, extracts traceable facts, scores completeness/quality/consistency, detects contradictions, and estimates win probability.

But the ML model does **not** get the final say.

A deterministic safety gate requires strong evidence across multiple dimensions before the system can prepare a response. If the case is incomplete or contradictory, it is routed to a human.

When preparation is allowed, Gemini acts as a grounded copilot: it explains the case, recommends missing evidence, investigates contradictions, and drafts a response whose factual claims must resolve to real extracted facts.

Finally, a human must approve the response before contest submission, and the entire lifecycle is recorded in an immutable audit trail.

### The result

**91.0% precision for the full safety-gated workflow on the held-out synthetic test set**, compared with **77.6% precision for the classifier alone**.

That is the core proposition of Shield Assist:

> ## **Don't just predict the outcome. Understand the evidence, bound the AI, and help the merchant act safely.**

---

## 📜 Source & Scope

This README is based on the project's **Shield Assist Product Specification v2.1** and the existing project README. Product scope, Razorpay integration details, evidence mapping, architecture, evaluation methodology, demo flow, limitations, and implementation choices have been preserved from those sources.

