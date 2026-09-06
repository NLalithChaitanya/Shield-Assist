# 🛡️ Shield Assist

### AI Evidence Verifier & Response Copilot for Razorpay Service Chargebacks

> **Razorpay Buildathon · Track 02 — AI Risk Manager**
> **Sub-problem — Chargeback Evidence Responder**

[![FastAPI](https://img.shields.io/badge/FastAPI-Backend-009688?logo=fastapi\&logoColor=white)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-Frontend-61DAFB?logo=react\&logoColor=111827)](https://react.dev/)
[![Gemini](https://img.shields.io/badge/Gemini-AI%20Copilot-4285F4?logo=google\&logoColor=white)](https://ai.google.dev/)
[![scikit-learn](https://img.shields.io/badge/scikit--learn-ML-F7931E?logo=scikit-learn\&logoColor=white)](https://scikit-learn.org/)
[![Razorpay](https://img.shields.io/badge/Razorpay-Test%20Mode-3395FF)](https://razorpay.com/)

---

# 🎯 The Problem

When a merchant receives a service chargeback, the difficult question is not simply:

> **"Will I win this dispute?"**

The real operational question is:

> **"Do I have enough consistent, high-quality evidence to safely contest it?"**

Merchants may have invoices, customer communication, delivery records, service proofs, policies, and other documents — but those documents can be:

* missing,
* incomplete,
* low quality,
* inconsistent,
* contradictory,
* or unrelated to the disputed transaction.

A system that generates a persuasive response without verifying the underlying evidence can make the workflow **faster but less safe**.

---

# 💡 What is Shield Assist?

**Shield Assist is an AI-powered evidence verification and dispute-response copilot for Razorpay service chargebacks.**

It converts a raw dispute and a collection of merchant documents into an explainable, evidence-backed decision.

```text
UNDERSTAND
    ↓
PROVE
    ↓
VERIFY
    ↓
SCORE
    ↓
GATE
    ↓
RESPOND
    ↓
APPROVE
    ↓
CONTEST
```

Shield Assist:

1. **Understands the dispute** and its reason code.
2. Determines **what must be proved** for that dispute.
3. Maps required evidence categories to merchant documents.
4. Extracts structured facts from uploaded evidence.
5. Verifies **completeness, quality, and consistency**.
6. Detects cross-document contradictions.
7. Estimates **case strength and win probability**.
8. Applies a **deterministic safety gate**.
9. Uses Gemini as a **grounded copilot** to investigate, recommend, and draft.
10. Requires **human approval** before submission.
11. Maintains an **immutable audit trail** across the entire lifecycle.

---

# 🔒 Core Safety Principle

> **AI should make dispute operations faster without becoming the authority that decides what gets submitted.**

The LLM never decides whether a case is safe to prepare.

The safety decision is enforced by deterministic application logic.

```text
ML Probability
      +
Evidence Readiness
      +
Completeness
      +
Consistency
      +
No Unresolved Contradictions
      ↓
DETERMINISTIC SAFETY GATE
```

And even after a response is prepared:

```text
AI Draft
   ↓
Citation Validation
   ↓
Human Review
   ↓
Human Approval
   ↓
Contest Submission
```

---

# 🏆 Judge's 60-Second Summary

**Shield Assist solves a specific gap: service chargeback evidence response.**

A Razorpay dispute enters the system.

Shield Assist first understands the reason code and determines **what evidence must be proved**.

Merchant documents are then processed and converted into traceable structured facts.

The system evaluates:

* **Completeness**
* **Quality**
* **Consistency**
* **Contradictions**

It then estimates case strength and win probability.

But the model does **not** get the final say.

A deterministic safety gate requires the evidence to satisfy multiple independent conditions before AI-assisted preparation is allowed.

If the evidence is incomplete or contradictory, the case is routed for review.

When preparation is allowed, Gemini acts as a grounded copilot:

* Explain
* Investigate
* Recommend
* Draft

Every factual claim in the generated response must resolve to a real extracted fact from a merchant document.

Finally, a human must approve the response before contest submission, and the complete lifecycle is recorded in an immutable audit trail.

### The result

**91.0% precision for the full safety-gated workflow on the held-out synthetic test set**, compared with **77.6% precision for the classifier alone**.

> ## **Don't just predict the outcome. Understand the evidence, bound the AI, and help the merchant act safely.**

---

# 💡 Why Service Chargebacks?

Razorpay's SHIELD documentation distinguishes fraud chargebacks from service chargebacks:

> **“SHIELD covers all the fraud chargebacks but not the service chargebacks.”**

That creates a clear product opportunity around the **evidence-heavy service dispute workflow**.

| Chargeback                       | SHIELD            | Core problem                                          |
| -------------------------------- | ----------------- | ----------------------------------------------------- |
| Fraud / unauthorized transaction | ✅ Covered         | Transaction-level fraud detection                     |
| **Service chargeback**           | **❌ Not covered** | **Evidence assembly, validation, and representation** |

Shield Assist is therefore **not another fraud detector**.

It focuses on disputes involving claims such as:

* Goods/services not provided
* Refund not processed
* Account debited but no confirmation
* Business not responding
* Goods/services not received
* Goods/services not as described

---

# 🧠 How Shield Assist Works

```text
                    RAZORPAY
                       │
                       ▼
              CHARGEBACK / DISPUTE
                       │
                       ▼
             ┌─────────────────────┐
             │ DISPUTE UNDERSTAND. │
             │                     │
             │ Reason code         │
             │ Amount              │
             │ Transaction         │
             │ Deadline            │
             │ Customer claim      │
             └──────────┬──────────┘
                        │
                        ▼
              ┌─────────────────────┐
              │ WHAT MUST BE PROVED?│
              └──────────┬──────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
       Required Evidence       Dispute Facts
              │                     │
              └──────────┬──────────┘
                         ▼
                 MERCHANT EVIDENCE
                         │
                         ▼
              ┌─────────────────────┐
              │ EVIDENCE INTELLIGENCE│
              │                     │
              │ OCR / Extraction    │
              │ Structured Facts    │
              └──────────┬──────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │ EVIDENCE VERIFIER   │
              │                     │
              │ Completeness        │
              │ Quality             │
              │ Consistency         │
              │ Contradictions      │
              └──────────┬──────────┘
                         │
                         ▼
                EVIDENCE READINESS
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        Case Strength        Win Probability
                                    │
                                    ▼
                         DETERMINISTIC GATE
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
                 PREPARE          REVIEW       LOW PRIORITY
                    │               │
                    └───────┬───────┘
                            ▼
                     DISPUTE COPILOT
                 Explain / Investigate
                 Recommend / Draft
                            │
                            ▼
                   GROUNDED RESPONSE
                   + EVIDENCE CITATIONS
                            │
                            ▼
                      HUMAN APPROVAL
                            │
                            ▼
                  RAZORPAY CONTEST API
                            │
                            ▼
                       AUDIT TRAIL
```

---

# 1️⃣ Understand the Dispute

A dispute enters through:

```http
POST /api/webhooks/razorpay
```

The webhook handler:

* verifies **HMAC-SHA256** against the raw request body,
* deduplicates repeated events,
* normalizes the Razorpay payload,
* creates the case,
* immediately returns `200 OK`,
* sends expensive processing to the background queue.

The dispute provides information such as:

* dispute ID,
* reason code,
* amount,
* `respond_by`,
* transaction/order context.

---

# 2️⃣ What Must Be Proved?

Different dispute reasons require different evidence.

Shield Assist converts the reason code into an **evidence requirement model**.

### Supported reason codes

| Code       | Network  | Reason                              | Required evidence                                                               |
| ---------- | -------- | ----------------------------------- | ------------------------------------------------------------------------------- |
| `RZP01`    | Razorpay | Goods/Services Not Provided         | Proof of service, customer communication, T&C                                   |
| `RZP04`    | Razorpay | Refund Not Processed                | Refund proof, billing proof, customer communication, refund/cancellation policy |
| `RZP05`    | Razorpay | Account Debited but No Confirmation | Billing proof, access/activity log, customer communication, T&C                 |
| `RZP06`    | Razorpay | Business Not Responding             | Proof of service, billing proof, customer communication                         |
| `UPI 1064` | UPI      | Goods/Services Not Received         | Proof of service, customer communication, T&C                                   |
| `UPI 1062` | UPI      | Goods/Services Not As Described     | Proof of service, customer communication, refund/cancellation policy            |

The evidence requirements follow Razorpay's documented suggested documents.

This means the system does not simply ask:

> "What documents were uploaded?"

It asks:

> **"What evidence is required to defend this particular dispute, and do we actually have it?"**

---

# 3️⃣ Evidence Intelligence

Merchant documents are processed into structured evidence.

```text
Document
   │
   ├── MIME + size validation
   ├── Content hashing
   ├── PDF → images
   ├── Quality assessment
   ├── Gemini Vision OCR / extraction
   ├── Structured fact extraction
   ├── Normalization
   └── Content-hash cache
```

The system extracts facts such as:

* names,
* dates,
* amounts,
* order IDs,
* tracking IDs,
* delivery information,
* refund information.

Every extracted fact retains its source document.

This creates the foundation for **traceable evidence citations**.

---

# 4️⃣ Evidence Verification

This is the heart of Shield Assist.

Every case is evaluated across three independent dimensions.

| Dimension        | What it answers                                                   |
| ---------------- | ----------------------------------------------------------------- |
| **Completeness** | Do we have the evidence categories required for this reason code? |
| **Quality**      | Are the documents legible and usable?                             |
| **Consistency**  | Do the documents agree on important facts?                        |

## Completeness

```text
required evidence present
────────────────────────── × 100
required evidence
```

## Quality

Documents are checked before their contents are trusted.

The implementation uses **Laplacian variance** for blur/degradation detection, including PDF rendering before assessment.

## Consistency

Cross-document checks include:

* invoice amount vs. dispute amount,
* delivery date vs. complaint date,
* customer name across documents,
* tracking/order IDs,
* logically inconsistent dates.

---

# ⚠️ 5️⃣ Contradiction Detection

A case can be **complete and still unsafe**.

Example:

```text
Invoice
Order ID = ORD-1042
Amount  = ₹38,500

Dispute
Order ID = ORD-9917

Delivery Proof
Signed delivery date conflicts
with complaint timeline
```

Shield Assist surfaces these contradictions explicitly.

> **Contradictions block preparation regardless of how high the ML probability is.**

This is one of the most important differences between Shield Assist and a probability-only system.

---

# 🤖 6️⃣ Case Strength & Win Probability

Shield Assist uses a lightweight **GradientBoostingClassifier**.

### Training data

* **3,000 synthetic disputes**
* training / validation / held-out test separation
* amount features
* document counts
* completeness
* quality
* reason-code features
* consistency and contradiction signals treated as gate signals rather than simply classifier features

### Why synthetic data?

Real merchant dispute data is not available for a hackathon.

The generator instead follows a Razorpay-shaped structure:

* reason-code taxonomy,
* evidence-slot taxonomy,
* realistic document combinations,
* realistic amounts,
* injected contradictions,
* outcome uncertainty.

The held-out test set is never used for threshold selection, calibration, or model tuning.

---

# 🎚️ 7️⃣ Probability Calibration

Raw model probabilities are not automatically trustworthy.

Shield Assist uses **isotonic regression** on the validation split to calibrate the output.

| Metric                     |     Result |
| -------------------------- | ---------: |
| Brier score                | **0.0923** |
| Expected Calibration Error | **0.0247** |

The objective is not simply to make the model more confident.

It is to make the displayed probability **more meaningful**.

---

# 🛡️ 8️⃣ Deterministic Safety Gate

The safety gate is the primary decision boundary.

A case can enter **PREPARE** only when all required conditions pass:

```text
Win probability       ≥ selected threshold
Completeness          ≥ 90%
Consistency           ≥ 90%
Required evidence     = present
Unresolved conflicts  = 0
```

| Decision           | Meaning                                               |
| ------------------ | ----------------------------------------------------- |
| 🟢 **PREPARE**     | Evidence is strong enough for AI-assisted preparation |
| 🟡 **REVIEW**      | Human investigation is required                       |
| ⚪ **LOW PRIORITY** | Case is currently weak or not urgent                  |

### Why multiple conditions?

Because:

> **Probability alone is not enough.**

A classifier can be confident for the wrong reasons.

The deterministic gate adds independent evidence constraints around the model.

```text
Classifier precision
       77.6%
         │
         │ + completeness
         │ + consistency
         │ + contradiction checks
         ▼
Full gate precision
       91.0%
```

---

# 📊 9️⃣ Evaluation Results

All reported model metrics are from the **held-out synthetic evaluation data**.

## ML classifier

| Metric    |     Score |
| --------- | --------: |
| Precision | **0.777** |
| Recall    | **0.834** |
| F1        | **0.805** |

## Full multi-condition safety gate

| Metric                 |           Score |
| ---------------------- | --------------: |
| **Precision**          |       **0.910** |
| Recall                 |       **0.822** |
| F1                     |       **0.864** |
| FP rate among approved |        **9.0%** |
| Auto-prepared cases    |   **223 / 901** |
| Safety ceiling         | **15% FP rate** |

### Key result

> **91.0% precision with the full safety gate vs. 77.6% with the classifier alone.**

The improvement comes from combining:

> **Probability + Completeness + Consistency + Contradiction Freedom**

rather than trusting a single model score.

---

# 💰 False-Positive Cost Analysis

Using the project's illustrative operations-cost assumption:

| Approach                      | False positives | Precision | Wasted effort |
| ----------------------------- | --------------: | --------: | ------------: |
| Always approve                |             654 |     27.4% |       ₹86,982 |
| Probability threshold only    |              60 |     77.6% |        ₹7,980 |
| **Full multi-condition gate** |          **20** | **91.0%** |    **₹2,660** |

The full gate eliminates **97% of false positives** versus always approving.

> ⚠️ These ROI figures are simulated on the synthetic test batch and are **not production claims**.

---

# ✍️ 1️⃣0️⃣ Grounded AI Copilot

Once a case reaches the copilot, Gemini provides four capabilities.

### Explain

Explain why a case is strong or weak in plain language.

### Investigate

Surface contradictions and relationships between documents.

### Recommend

Identify missing evidence and suggest what the merchant should upload next.

### Draft

Generate the dispute response using only supported merchant evidence.

---

# 🔗 Evidence Integrity

Shield Assist does not rely on a prompt saying:

> "Do not hallucinate."

It adds a **code-level citation integrity check**.

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
AI Draft
   ↓
Citation Validation
   ↓
❌ Fact Not Found
   ↓
DRAFT REJECTED
```

This creates an inspectable grounding chain between the generated response and the merchant's original evidence.

---

# 👤 1️⃣1️⃣ Human Approval Boundary

Shield Assist is a **copilot, not an autonomous dispute bot**.

| AI can                     | AI cannot                |
| -------------------------- | ------------------------ |
| Explain a case             | Autonomously submit      |
| Investigate contradictions | Bypass the safety gate   |
| Identify evidence gaps     | Override contradictions  |
| Recommend next evidence    | Fabricate evidence       |
| Draft a response           | Approve its own response |

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

# ⏱️ 1️⃣2️⃣ Deadline & Priority Intelligence

Disputes are also time-sensitive.

Shield Assist considers:

* `respond_by`,
* dispute amount,
* win probability,
* deadline urgency,
* evidence readiness.

### 🔴 Act now

Deadline < 6 hours, or high-value/high-probability case.

### 🟡 Review soon

Deadline < 24 hours, or medium probability.

### 🟢 Low priority

Deadline > 24 hours, or low probability.

The merchant sees **what matters most first**, rather than a flat case list.

---

# 🎬 1️⃣3️⃣ The Demo

The strongest demonstration is **two cases**, not a feature tour.

## Case 1 — Evidence makes the case stronger

**Case:** `disp_delivery_test`
**Amount:** ₹42,000
**Reason:** `RZP01 — Goods/Services Not Provided`

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

## Case 2 — The gate blocks a bad case

**Case:** `disp_final_cache`
**Amount:** ₹38,500
**Reason:** `RZP01`

All required evidence categories are present.

But the documents contain contradictions:

* invoice date inconsistent with the dispute timeline,
* invoice order ID differs from the dispute order ID,
* additional cross-document contradictions.

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

### The key demo moment

> **Shield Assist is bounded, not merely confident.**

---

# 🧪 1️⃣4️⃣ Adversarial Safety Testing

The repository contains **23 automated safety tests** targeting the core safety boundary.

| Scenario                                 | Expected                       |
| ---------------------------------------- | ------------------------------ |
| High probability + complete + consistent | ✅ PREPARE                      |
| High probability + incomplete            | ❌ BLOCK                        |
| High probability + contradiction         | ❌ BLOCK                        |
| Below-threshold probability + complete   | ❌ NOT PREPARE                  |
| Low probability + complete               | LOW PRIORITY                   |
| Boundary values                          | Correct deterministic behavior |
| AI attempts self-approval                | ❌ BLOCKED                      |

The key invariant is:

> **ML probability can never override deterministic safety conditions.**

---

# 🔌 1️⃣5️⃣ Razorpay Integration

## Verified in Test Mode

| Capability                       | Status        |
| -------------------------------- | ------------- |
| API authentication               | ✅ Verified    |
| Documents API                    | ✅ Verified    |
| Real `doc_*` IDs                 | ✅ Verified    |
| Webhook HMAC-SHA256 verification | ✅ Verified    |
| Webhook idempotency              | ✅ Verified    |
| Dispute simulator pipeline       | ✅ Implemented |

## Test Mode limitation

Razorpay Test Mode does not provide merchant-created contestable disputes.

Therefore, a complete Contest API submission cannot be exercised against a sandbox-created dispute.

The implementation still includes:

* contest request construction,
* evidence-slot mapping,
* real Razorpay document IDs,
* human approval enforcement,
* idempotency,
* error handling.

The final Contest API call can be validated against the first real contestable dispute in Live Mode.

### Hackathon demo architecture

```text
Production

RAZORPAY
    │
    ▼
DISPUTE WEBHOOK
    │
    ▼
SHIELD ASSIST


Hackathon

DEMO DISPUTE GENERATOR
    │
    ▼
Razorpay-shaped dispute payload
    │
    ▼
SHIELD ASSIST
```

Example simulated dispute:

```json
{
  "dispute_id": "disp_demo_001",
  "payment_id": "pay_demo_001",
  "amount": 42000,
  "currency": "INR",
  "reason_code": "RZP01",
  "reason": "Service not provided",
  "respond_by": "2026-09-15"
}
```

---

# ⚙️ 1️⃣6️⃣ Async Processing & Live Updates

OCR and LLM generation can take seconds, so expensive operations run asynchronously.

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

* `document.process`
* `score.case`
* `draft.response`
* `contest.submit`

The current queue is SQLite-backed and in-process, with an interface that can be swapped for Celery + Redis in production.

Shield Assist uses **Server-Sent Events (SSE)** for:

* document processing progress,
* score updates,
* gate changes,
* draft readiness.

---

# 🧾 1️⃣7️⃣ Immutable Audit Trail

Every important lifecycle event is appended to an audit log.

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

### Lifecycle

```text
dispute.received
       ↓
document.uploaded
       ↓
document.extracted
       ↓
scores.computed
       ↓
contradiction.detected
       ↓
gate.decision
       ↓
copilot.drafted
       ↓
human.approved
       ↓
contest.submitted
       ↓
dispute.resolved
```

This gives a complete chain:

> **Input → extraction → evidence analysis → decision → AI output → human approval → submission**

---

# 🏗️ 1️⃣8️⃣ Technical Architecture

## Intelligence Architecture

```text
                         RAZORPAY
                            │
                            ▼
                   CHARGEBACK / DISPUTE
                            │
                            ▼
                 DISPUTE UNDERSTANDING
                            │
                            ▼
                  WHAT MUST BE PROVED?
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
          Required Evidence       Dispute Facts
                 │                     │
                 └──────────┬──────────┘
                            ▼
                   MERCHANT EVIDENCE
                            │
                            ▼
                  EVIDENCE INTELLIGENCE
                    OCR / Extraction
                    Structured Facts
                            │
                            ▼
                    EVIDENCE VERIFIER
                ┌───────────┼───────────┐
                ▼           ▼           ▼
          Completeness   Quality   Consistency
                            │
                            ▼
                      Contradictions
                            │
                            ▼
                    EVIDENCE READINESS
                            │
                 ┌──────────┴──────────┐
                 ▼                     ▼
           Case Strength        Win Probability
                                      │
                                      ▼
                            DETERMINISTIC GATE
                                      │
                       ┌──────────────┼──────────────┐
                       ▼              ▼              ▼
                    PREPARE         REVIEW      LOW PRIORITY
                       │
                       ▼
                  DISPUTE COPILOT
                       │
                       ▼
              GROUNDED RESPONSE
              + EVIDENCE CITATIONS
                       │
                       ▼
                 HUMAN APPROVAL
                       │
                       ▼
              RAZORPAY CONTEST API
                       │
                       ▼
                  AUDIT TRAIL
```

## Infrastructure Architecture

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
             ┌──────────┼──────────┐
             ▼          ▼          ▼
        DOCUMENT     EVIDENCE    COPILOT
       INTELLIGENCE INTELLIGENCE  GEMINI
             │          │          │
             └──────────┼──────────┘
                        ▼
┌─────────────────────────────────────────────────────┐
│                  STORAGE LAYER                      │
│ SQLite Repository │ Immutable Docs │ Audit Log     │
│ Extraction Cache                                    │
└─────────────────────────────────────────────────────┘
```

---

# 🛡️ 1️⃣9️⃣ Security & Resilience

## Security

* Secrets loaded from environment variables.
* `.env` excluded from version control.
* Razorpay webhook signatures verified against the raw request body.
* Constant-time signature comparison.
* Credentials never returned through APIs.
* Logs do not expose credentials.
* Contest submission supports dry-run mode.

## Resilience

| Pattern             | Purpose                                      |
| ------------------- | -------------------------------------------- |
| Circuit breaker     | Prevent cascading Gemini failures            |
| Rate limiter        | Protect free-tier AI quota                   |
| Exponential backoff | Recover from transient API failures          |
| Idempotency         | Prevent duplicate webhook/submission effects |
| Dead-letter queue   | Preserve failed jobs for inspection          |

---

# 🗂️ 2️⃣0️⃣ Project Structure

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

# 🚀 2️⃣1️⃣ Getting Started

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

# 🧪 2️⃣2️⃣ Running Tests

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

# 🧩 2️⃣3️⃣ Tech Stack

| Layer             | Technology                        | Purpose                                |
| ----------------- | --------------------------------- | -------------------------------------- |
| Frontend          | React + Tailwind + TanStack Query | Merchant-facing case workspace         |
| Backend           | FastAPI                           | APIs, webhooks, service boundaries     |
| OCR / Extraction  | Gemini                            | Multimodal document understanding      |
| Copilot           | Gemini                            | Explain, investigate, recommend, draft |
| ML                | scikit-learn GradientBoosting     | Case strength / win probability        |
| Calibration       | Isotonic regression               | More reliable probabilities            |
| Live updates      | SSE                               | Server → client streaming              |
| Queue             | SQLite-backed workers             | Zero external queue dependency         |
| Storage           | SQLite + Repository Pattern       | Hackathon-simple, production-swappable |
| PDF processing    | PyMuPDF                           | Multi-page PDF rendering               |
| Quality detection | Laplacian variance                | Detect degraded evidence               |
| Payments platform | Razorpay APIs                     | Dispute/evidence integration           |

---

# ⚠️ 2️⃣4️⃣ Known Limitations

### 1. Contest API cannot be fully E2E tested in Test Mode

Razorpay Test Mode does not expose merchant-created disputes.

### 2. Gemini free-tier limits

Transient provider errors can occur.

Retry, rate limiting, circuit-breaking, and graceful degradation are implemented where applicable.

### 3. Synthetic ML data

The classifier is evaluated on synthetic disputes.

Therefore, reported metrics **do not claim production performance**.

### 4. SQLite

The current repository is appropriate for a single-process hackathon deployment.

The same repository interface can be moved to PostgreSQL for production.

---

# 🧭 Why Shield Assist Is Different

Most AI demos stop at:

```text
Upload → LLM → Answer
```

Shield Assist adds the controls required for a high-stakes evidence workflow:

```text
Real Integration
       +
Evidence Taxonomy
       +
"What Must Be Proved?"
       +
Document Intelligence
       +
Completeness
       +
Quality
       +
Consistency
       +
Contradiction Detection
       +
Calibrated ML
       +
Deterministic Safety Gate
       +
Citation Integrity
       +
Human Approval
       +
Async Processing
       +
Resilience
       +
Immutable Audit Trail
```

The core philosophy is:

> **AI should make dispute operations faster without becoming the authority that decides what gets submitted.**

---

# 🏁 Final Takeaway

Shield Assist is not trying to build an AI that says:

> **"I think you'll win."**

It builds an evidence-aware workflow that asks:

> **"What must be proved?"**

> **"Do we have the evidence?"**

> **"Is the evidence usable?"**

> **"Do the documents agree?"**

> **"Is the case strong enough to prepare?"**

> **"Can every claim in the response be traced back to merchant evidence?"**

And only then:

> **"Should a human approve this for submission?"**

### The final proposition

# **Understand the dispute.**

# **Verify the evidence.**

# **Bound the AI.**

# **Help the merchant act safely.**

---

## 📜 Source & Scope

This README is based on the **Shield Assist Product Specification v2.1** and the existing project README.

Product scope, Razorpay integration details, evidence mapping, architecture, evaluation methodology, demo flow, limitations, and implementation choices have been preserved while reorganizing the README around the product's core evidence-verification workflow.
