# Shield Assist — Product Design Direction v2

### The AI Dispute Resolution Copilot for Razorpay Merchants

---

## 1. Design thesis

Shield Assist is not a generic SaaS dashboard and it is not an AI chatbot.

It is a **merchant dispute operations system** sitting between a chargeback arriving and the merchant deciding whether to contest it.

The interface should communicate three things immediately:

1. **Something financially important needs attention.**
2. **The system can show exactly why it reached its conclusions.**
3. **The system will stop itself when the evidence is insufficient or contradictory.**

The product should therefore feel closer to a **financial operations terminal + evidence workspace** than a conventional AI product.

The visual language should communicate:

> **Calm under pressure. Precise with money. Transparent about uncertainty.**

The product's two foundational principles must remain visible throughout the experience:

### Evidence Integrity

Shield Assist never fabricates or modifies evidence.

Every factual claim in an AI-generated response must be traceable to a merchant-provided document.

### Bounded, not just confident

A high model probability must never be presented as permission to act by itself.

The system should visibly distinguish:

- evidence strength
- model probability
- deterministic gate decision
- human approval

A beautiful UI that makes every case look "successful" would undermine the product's strongest technical differentiator.

---

# 2. Product information hierarchy

The interface should prioritize information in this order:

### Level 1 — What needs attention?

- Deadline
- Amount at risk
- Case priority
- Current gate state

### Level 2 — Can we defend this case?

- Case Strength
- Completeness
- Quality
- Consistency
- Contradictions
- Missing evidence

### Level 3 — Why?

- Extracted facts
- Source documents
- Evidence requirements for the reason code
- Model explanation
- Contradiction explanation

### Level 4 — What should I do?

- Upload missing evidence
- Review contradiction
- Edit response
- Approve
- Submit to Razorpay

The interface should never force a merchant to understand the ML system before understanding what action is required.

---

# 3. Core application structure

The product should have four primary surfaces:

```text
┌─────────────────────────────────────────────────────────────┐
│ Shield Assist                           Integrity ●   User  │
├───────────────┬─────────────────────────────────────────────┤
│               │                                             │
│  COMMAND      │                                             │
│  CENTER       │              ACTIVE WORKSPACE               │
│               │                                             │
│  Overview     │                                             │
│  Disputes     │                                             │
│  Evidence     │                                             │
│  Audit Log    │                                             │
│               │                                             │
│  ─────────    │                                             │
│  Settings     │                                             │
│               │                                             │
└───────────────┴─────────────────────────────────────────────┘
```

The application should use:

- **Global navigation** on the left
- **Contextual workspace** in the center
- **Optional contextual inspector** on the right

The right panel should not be permanently occupied.

It should appear when the merchant needs deeper evidence inspection, citation inspection, or document comparison.

This gives the main workspace more breathing room than the original permanently fixed three-pane layout.

---

# 4. Primary navigation

The left navigation should remain deliberately small.

```text
SHIELD ASSIST

Overview

DISPUTES
  All disputes
  Act now
  Review
  Low priority

EVIDENCE
  Documents
  Evidence gaps

AUDIT
  Activity

────────────────

Settings
```

Avoid a long enterprise-style navigation tree.

The merchant's mental model is simple:

**What needs attention → What evidence do I have → What happened.**

---

# 5. Overview — Merchant command center

The Overview screen is not an ML metrics dashboard.

It should answer:

> "What should I work on right now?"

### Header

```text
Dispute operations

Good morning.
3 disputes need attention today.

[ All disputes → ]
```

### Priority summary

```text
┌──────────────────┬──────────────────┬──────────────────┐
│ ACT NOW          │ REVIEW SOON      │ LOW PRIORITY     │
│                  │                  │                  │
│ 3 cases          │ 7 cases          │ 18 cases         │
│ ₹74,500 at risk  │ ₹1.2L at risk    │ ₹2.4L at risk    │
└──────────────────┴──────────────────┴──────────────────┘
```

Do not turn these into colorful KPI cards.

The hierarchy should come from typography, spacing and restrained status indicators.

### Active queue

The first operational element should be a prioritized dispute list.

```text
Act now

#4471    ₹38,500    RZP01
          17h remaining
          Evidence incomplete

#4482    ₹21,000    UPI 1064
          5h remaining
          Contradiction detected

#4490    ₹15,000    RZP04
          3h remaining
          Ready for review
```

The amount and deadline should be visually dominant.

---

# 6. Dispute queue

The dispute queue is the merchant's primary working surface.

### Each row should expose

- Priority
- Dispute ID
- Reason code
- Customer/order reference
- Amount
- Deadline
- Case Strength
- Gate state
- Evidence state

Example:

```text
● ACT NOW

#4471
Goods/Services not Provided
RZP01

₹38,500
17h remaining

Case strength
64

REVIEW
Missing delivery proof
```

### Important

Do not use red/yellow/green as the primary information architecture.

Use:

- label
- text
- icon
- restrained color

For example:

```text
ACT NOW
REVIEW SOON
LOW PRIORITY
```

rather than relying only on colored dots.

---

# 7. Case workspace

Selecting a dispute opens the primary case workspace.

The layout should be:

```text
┌─────────────────────────────────────────────────────────────┐
│ ← Disputes                                                  │
│                                                             │
│ ₹38,500   RZP01                                             │
│ Goods/Services not Provided                                  │
│ 17h remaining                                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│ CASE STRENGTH                                               │
│ 64 / 100                                                    │
│                                                             │
│ Completeness   █████████░░ 87%                              │
│ Quality        ██████████░ 94%                              │
│ Consistency    ███████░░░░ 71%                              │
│                                                             │
├─────────────────────────────────────┬───────────────────────┤
│                                     │ Evidence               │
│ Response                            │                         │
│                                     │ ✓ Invoice              │
│ "The merchant fulfilled..."         │ ✓ Customer comms      │
│                                     │ ✓ Terms & conditions   │
│ ●                                   │ + Upload document      │
│                                     │                         │
│ [Review response]                   │ Missing                │
│                                     │ ! Delivery proof       │
│                                     │                         │
└─────────────────────────────────────┴───────────────────────┘
```

The case workspace should have a **strong vertical hierarchy**, rather than three equally weighted columns.

The user should always understand:

1. case status
2. case strength
3. evidence state
4. recommended action

before reading the generated response.

---

# 8. Case header

The case header is one of the most important parts of the application.

It should show:

```text
₹38,500

RZP01 · Goods/Services not Provided

Dispute #4471

17h 24m remaining
```

Then the gate state:

```text
REVIEW REQUIRED

Consistency is below threshold
1 contradiction detected
```

or:

```text
READY FOR HUMAN APPROVAL

All required evidence present
No unresolved contradictions
Gate conditions satisfied
```

Do not simply display:

> "AI confidence: 91%"

The merchant needs the operational decision first.

---

# 9. Case Strength

Case Strength is the central evidence-health concept.

It must never be visually confused with win probability.

### Primary display

```text
CASE STRENGTH

64
/100

Evidence is incomplete
```

Below:

```text
Completeness       87%
████████████░░

Quality            94%
█████████████░

Consistency        71%
██████████░░░
```

### Why bars

Bars provide direct comparison between dimensions.

Avoid:

- donut charts
- radial gauges
- speedometers
- giant circular AI scores

The system is communicating evidence dimensions, not advertising confidence.

---

# 10. Win probability

Win probability should be visually secondary.

For example:

```text
Estimated win probability
78%

Based on the trained classifier.
This is not the submission decision.
```

The final gate should sit separately below it.

This separation is essential.

```text
MODEL

78%
Estimated win probability


GATE

REVIEW REQUIRED

Consistency 71% < 90%
1 unresolved contradiction
```

This makes the bounded architecture visible in the UI.

---

# 11. Gate component

The gate is one of the product's strongest differentiators and deserves first-class treatment.

### Passed state

```text
READY FOR HUMAN APPROVAL

✓ Win probability threshold met
✓ Evidence completeness ≥ 90%
✓ Evidence consistency ≥ 90%
✓ No unresolved contradiction
✓ Required evidence present

The response can be prepared for submission.
Human approval is still required.
```

### Blocked state

```text
REVIEW REQUIRED

The system will not prepare this case for submission.

✕ Consistency 71% < 90%
✕ 1 unresolved contradiction

Win probability: 82%

Why blocked?
A high model probability cannot override
an unresolved evidence contradiction.

[ Investigate contradiction ]
```

The second version is particularly important for the demo.

It visibly proves:

> **The system can say no.**

---

# 12. Evidence workspace

Evidence is not simply a list of uploaded files.

It is the foundation of the product.

The evidence workspace should distinguish three concepts:

### Required

What the reason code requires.

### Present

What the merchant has supplied.

### Verified

What the system successfully extracted and validated.

Example:

```text
EVIDENCE FOR RZP01

Required evidence

✓ Proof of service
  delivery_receipt.pdf

✓ Customer communication
  customer_email.pdf

✓ Terms & conditions
  terms.pdf

! Missing
  Delivery proof
  High impact
```

Each evidence item can expand into:

```text
Delivery receipt.pdf

Status
Verified

Facts extracted
Order ID     ORD-2026-001234
Customer     Rahul Sharma
Amount       ₹38,500
Delivered    14 Aug 2026

Source
Page 1

[ Open document ]
```

---

# 13. Evidence upload

Uploading evidence should be extremely simple.

```text
Add evidence

Drop documents here
or
[ Choose files ]

PDF · PNG · JPG

Shield Assist will:
✓ classify the document
✓ extract relevant facts
✓ check quality
✓ compare it with existing evidence
```

Do not make the merchant choose an evidence category before upload.

The system should infer the category and let the merchant correct it.

This reduces friction and reinforces the intelligence of the product.

---

# 14. Processing state

Document processing must feel trustworthy.

Avoid generic:

> "AI is thinking..."

Use explicit operational states:

```text
Processing delivery_receipt.pdf

Uploading              ✓
Reading document       ✓
Extracting facts       ●
Checking consistency   —
Updating case          —
```

This aligns directly with the actual architecture.

The product should communicate work, not simulate personality.

---

# 15. Document viewer

Documents should open in a right-side inspector or focused document view.

The viewer should support:

- page navigation
- zoom
- extracted fact highlights
- citation anchors
- source metadata

Example:

```text
DELIVERY_RECEIPT.PDF

Page 1 / 1

┌──────────────────────────┐
│                          │
│     DELIVERY RECEIPT     │
│                          │
│ Customer: Rahul Sharma ● │
│ Amount: ₹38,500       ●  │
│ Order: ORD-001234     ●  │
│ Delivered: 14 Aug     ●  │
│                          │
└──────────────────────────┘

3 extracted facts
```

The document is the source of truth.

The interface should therefore visually distinguish:

**document fact**

from

**AI interpretation**.

---

# 16. Citation system — Evidence Thread

The Evidence Thread remains the signature interaction.

Every factual claim in a generated response receives a citation marker.

Example:

> The order was delivered to Rahul Sharma on 14 August 2026. ●

Clicking the marker should:

1. highlight the sentence
2. open the cited document
3. scroll to the relevant page/region
4. highlight the supporting fact

Conceptually:

```text
Response

The order was delivered on
14 August 2026. ●
                    │
                    │
                    ▼
              delivery_receipt.pdf
                    │
                    ▼
              Page 1
              Delivered:
              14 Aug 2026
```

This is the visual manifestation of Evidence Integrity Mode.

It should be more prominent than a conventional footnote system.

---

# 17. Copilot

The Copilot must **not look like ChatGPT**.

Do not use:

- chat bubbles
- avatars
- conversational typing animations
- "Ask AI" as the dominant interaction

The Copilot should behave like an **intelligent case analyst embedded in the workspace**.

Its four abilities should appear as contextual actions:

```text
COPILOT

[ Explain ]   [ Investigate ]
[ Recommend ] [ Draft response ]
```

Each produces a structured result.

---

# 18. Explain

Example:

```text
WHY IS THIS CASE WEAK?

The case has strong document quality,
but evidence is incomplete.

The missing delivery proof is the largest
remaining evidence gap.

Case strength impact
+18 estimated points
```

Keep explanations short by default.

Allow expansion for detail.

---

# 19. Investigate

Investigation should focus on contradictions.

Example:

```text
CONTRADICTION DETECTED

Customer communication
"I have not received the order."

Delivery receipt
Delivered: 14 Aug 2026
Recipient: Rahul Sharma

Potential conflict
The delivery record predates the
customer's non-receipt complaint.

[ Open both documents ]
```

The UI should show the conflicting facts side by side.

```text
DOCUMENT A                 DOCUMENT B

Customer email             Delivery receipt

"Not received"             Delivered
                           14 Aug 2026

                           Rahul Sharma
```

This makes contradiction detection tangible to judges.

---

# 20. Recommend

Recommendations should be action-oriented.

```text
NEXT BEST EVIDENCE

Delivery proof
HIGH IMPACT

Required for RZP01

Estimated case-strength impact
+18

Why?
It directly addresses the primary
missing evidence category.

[ Upload delivery proof ]
```

Avoid generic AI recommendations such as:

> "Consider adding more evidence."

The product should tell the merchant exactly what is missing and why it matters.

---

# 21. Draft response

The response editor is where the Copilot becomes operational.

Structure:

```text
DRAFT RESPONSE

Grounded in 4 documents

────────────────────────────────────

The order was delivered to
Rahul Sharma on 14 August 2026. ●

The transaction amount was ₹38,500. ●

The customer communication was received
after the recorded delivery date. ●

────────────────────────────────────

3 citations · 0 unsupported claims

[ Edit ] [ Regenerate ] [ Approve response ]
```

The interface should show:

```text
3 citations
0 unsupported claims
```

This is much more useful than showing token counts or generic AI confidence.

---

# 22. Human approval

Human approval must remain visually explicit.

Never make "Submit" appear as the natural next step directly after AI generation.

The flow should be:

```text
AI DRAFT
    ↓
MERCHANT REVIEW
    ↓
APPROVE
    ↓
RAZORPAY SUBMIT
```

Approval component:

```text
HUMAN APPROVAL REQUIRED

You are approving this response for
submission to Razorpay.

✓ 4 source documents
✓ 7 cited facts
✓ No unsupported claims
✓ Gate conditions satisfied

[ Approve response ]
```

After approval:

```text
APPROVED BY YOU

Ready to submit to Razorpay

[ Submit dispute ]
```

This creates a clear human-in-the-loop boundary.

---

# 23. Submission state

After submission:

```text
SUBMITTED

Razorpay accepted the contest request.

Submitted
29 Aug 2026 · 11:42 AM

Dispute
#4471

Correlation ID
rzp_corr_8F21...
```

The submission should then become part of the audit trail.

---

# 24. Audit trail

Audit should not be a developer-facing log.

It should be a merchant-readable timeline.

```text
CASE ACTIVITY

11:42 AM
Contest submitted
Human approved response

11:40 AM
Response approved
7 citations verified

11:39 AM
Draft generated
4 source documents

11:37 AM
Gate updated
Case moved from REVIEW → PREPARE

11:36 AM
Delivery proof uploaded

11:35 AM
Contradiction resolved
```

Each event can expand to show technical details when needed.

This preserves the production-minded architecture without overwhelming normal users.

---

# 25. Evidence Integrity Mode

This should be a persistent product-level status, not a decorative badge.

Header:

```text
● Evidence Integrity
```

Clicking it opens:

```text
EVIDENCE INTEGRITY MODE

Shield Assist never creates or modifies
merchant evidence.

AI-generated factual claims must resolve
to merchant-provided source documents.

Current case

7 factual claims
7 verified citations
0 unsupported claims
```

This is a strong judge-facing trust surface.

---

# 26. Priority system

Priority should be determined by the product's actual logic:

```text
Priority =
amount
+
deadline urgency
+
win probability
+
evidence readiness
```

But the UI should not expose a mathematical formula unless the merchant asks.

Instead:

```text
ACT NOW

17h remaining
₹38,500 at risk
High probability
Evidence gap requires action
```

The merchant needs the reason, not the equation.

---

# 27. Color system

The current color philosophy should be retained, with one important refinement:

**Color must represent semantic state, never branding decoration.**

## Light mode

```text
--surface           #F7F8FA
--surface-raised    #FFFFFF
--ink               #12161C
--ink-muted         #5B6472
--line              #E2E5EA

--signal            #1F6FEB
--money             #0F7A4A
--urgent            #C4331F
--caution           #B5750B
```

## Dark mode

```text
--surface           #0B0D11
--surface-raised    #14171D
--ink               #E7E9ED
--ink-muted         #8A94A3
--line              #242830

--signal            #4B8CFF
--money             #2FA968
--urgent            #E35C46
--caution           #D99A2B
```

### Semantic rules

`--signal`

Only:

- interaction
- active state
- citations
- verified/integrity state

`--money`

Only:

- monetary amounts
- financially positive / ready states

`--urgent`

Only:

- deadlines
- contradictions
- blocked states

`--caution`

Only:

- review states
- medium priority
- attention required

Never use these colors for decoration.

---

# 28. Typography

Retain the three-level typography system.

### Display / navigation

Söhne

Fallback:

General Sans

### Body

Inter

### Data

IBM Plex Mono

Use mono for:

- ₹ amounts
- dispute IDs
- order IDs
- reason codes
- timestamps
- extracted document facts
- document hashes
- correlation IDs

This creates an important visual distinction:

```text
SYSTEM INTERPRETATION

The case is missing delivery proof.


SOURCE FACT

₹38,500
ORD-2026-001234
14 AUG 2026
```

The typography itself reinforces evidence integrity.

---

# 29. Spacing and density

Shield Assist is an operational product.

Do not design it like a marketing SaaS application.

Use:

- compact rows
- clear grouping
- generous section separation
- dense data where necessary
- minimal decorative whitespace

Recommended base:

```text
4px spacing unit

4
8
12
16
24
32
48
64
```

The interface should feel information-dense without feeling cramped.

---

# 30. Cards

Cards should be used sparingly.

Avoid:

```text
┌──────────────┐
│ AI SCORE     │
│              │
│     91%      │
│              │
└──────────────┘
```

Prefer structured surfaces:

```text
CASE STRENGTH

91 / 100

Completeness     96%
Quality          94%
Consistency      92%
```

A card should exist because it creates a meaningful information boundary, not because every dashboard element needs a rounded rectangle.

---

# 31. Border radius and surfaces

Use restrained geometry.

Recommended:

- small controls: 6px
- inputs: 6–8px
- panels: 8–10px
- major surfaces: 10–12px

Avoid excessive:

- 20–32px rounded cards
- floating glass panels
- gradients
- glowing borders
- glassmorphism

The product deals with money and evidence.

The visual language should be precise rather than playful.

---

# 32. Icons

Icons should be functional and quiet.

Preferred icon meanings:

```text
Clock        deadline
File         evidence
Alert        contradiction
Check        verified
Shield       integrity
Arrow        action
Search       investigate
History      audit
Upload       evidence upload
```

Do not use emoji in the production interface.

The specification can use emoji in documentation, but the actual UI should use a consistent icon set.

---

# 33. Motion

Motion should communicate state change, not provide entertainment.

### Signature animation

The demo's key moment:

```text
64 → 91
```

After uploading delivery proof:

- bar animates for ~600ms
- number increments
- missing evidence changes to verified
- gate transitions from REVIEW to READY
- response becomes available

This should be the most noticeable animation in the product.

### Contradiction

A blocked condition briefly emphasizes once.

No:

- shaking cards
- flashing red
- bouncing icons
- alert sirens

### Processing

Use subtle progress states.

### Reduced motion

Respect:

```text
prefers-reduced-motion
```

In reduced-motion mode, values update immediately.

---

# 34. Demo mode considerations

The product should be optimized for the actual 2–3 minute demo.

The demo path should be almost impossible to misunderstand:

```text
DISPUTE ARRIVES
       ↓
₹38,500 · RZP01
17h remaining
       ↓
CASE STRENGTH 64
Missing delivery proof
       ↓
UPLOAD DOCUMENT
       ↓
Document processed
       ↓
64 → 91
       ↓
All gate conditions pass
       ↓
GROUNDED RESPONSE
       ↓
Click citation
       ↓
Source document opens
       ↓
HUMAN APPROVAL
       ↓
RAZORPAY SUBMIT
       ↓
AUDIT TRAIL
```

Then immediately demonstrate the bounded failure case:

```text
HIGH PROBABILITY

82%

BUT

REVIEW REQUIRED

Contradiction detected
Consistency 71%

Submission blocked
```

The second case should feel like a **feature**, not a failure.

---

# 35. Empty states

Empty states should explain the product rather than fill space.

### No active disputes

```text
You're clear for now.

No disputes currently require action.

New Razorpay disputes will appear here automatically.
```

### No evidence

```text
No evidence uploaded yet.

Upload the documents you have.
Shield Assist will classify and analyze them.
```

### No contradictions

```text
No contradictions detected.

The available evidence is internally consistent.
```

---

# 36. Error states

Errors should preserve trust.

Never show:

> "Something went wrong."

Instead:

```text
DOCUMENT PROCESSING PAUSED

Gemini could not process this document.

Your original document is unchanged.

You can retry processing or continue manually.

[ Retry ]
```

For an API failure:

```text
RAZORPAY SUBMISSION NOT COMPLETED

The request could not be confirmed.

No duplicate submission was created.

[ Retry submission ]
[ View audit event ]
```

The interface should make resilience visible.

---

# 37. Accessibility

Non-negotiable.

### Keyboard

Everything important must be keyboard accessible.

### Focus

Visible `--signal` focus ring.

### Color

Color is never the only signal.

Example:

```text
✕ REVIEW REQUIRED
```

not:

```text
[red badge]
```

### Contrast

All text and state indicators must meet appropriate WCAG contrast requirements.

### Motion

Respect `prefers-reduced-motion`.

### Responsive

At widths below approximately 900px:

```text
CASE
EVIDENCE
COPILOT
```

become contextual tabs rather than attempting to preserve a cramped multi-column layout.

---

# 38. Responsive architecture

Desktop:

```text
Navigation
    +
Main workspace
    +
Context inspector
```

Tablet:

```text
Navigation
    +
Main workspace

Inspector → slide-over
```

Mobile:

```text
Case header

[ Case ] [ Evidence ] [ Response ]

Active workspace

Bottom action area
```

The product should remain usable but does not need to optimize for mobile-first workflows.

This is fundamentally a merchant operations desktop application.

---

# 39. Core component system

Build the frontend around reusable semantic components.

```text
<AppShell />

<Sidebar />

<DisputeQueue />

<DisputeRow />

<CaseHeader />

<PriorityBadge />

<CaseStrength />

<ScoreBreakdown />

<GateDecision />

<EvidenceChecklist />

<EvidenceItem />

<DocumentViewer />

<FactList />

<ContradictionCard />

<CopilotPanel />

<ResponseEditor />

<CitationMarker />

<EvidenceThread />

<ApprovalBar />

<AuditTimeline />

<UploadEvidence />

<ProcessingStatus />
```

Avoid building one giant CaseDetail component.

The architecture should mirror the product architecture.

---

# 40. Data-to-UI mapping

The UI should map directly to the underlying product concepts.

| Product concept | UI representation |
|---|---|
| Reason code | Case header |
| Amount | Case header / queue |
| Respond-by | Deadline |
| Completeness | Score bar |
| Quality | Score bar |
| Consistency | Score bar |
| Case strength | Primary evidence score |
| Win probability | Secondary model metric |
| Gate | Explicit decision panel |
| Contradiction | Blocking warning |
| Missing evidence | Evidence gap |
| Extracted fact | Document fact |
| Citation | Evidence Thread |
| Copilot | Contextual analyst |
| Human approval | Explicit approval state |
| Contest API | Submission state |
| Audit log | Timeline |

This prevents the frontend from inventing a second conceptual model.

---

# 41. What the UI should deliberately avoid

Do not introduce:

- chatbot-first UI
- generic "AI magic" buttons
- giant confidence circles
- excessive gradients
- glassmorphism
- neon AI aesthetics
- animated robot/AI imagery
- decorative dashboards
- fake real-time animations
- excessive rounded cards
- unexplained AI scores
- automatic submission without human approval
- unsupported AI-generated evidence
- hidden gate logic

The product should never look like a generic AI wrapper.

---

# 42. Signature visual language

Shield Assist should be recognizable through four things:

### 1. Evidence Thread

Claims visibly connect to source documents.

### 2. Evidence bars

Completeness, quality and consistency are shown as comparable horizontal measurements.

### 3. Explicit gate

The system visibly separates:

```text
MODEL
↓
EVIDENCE
↓
GATE
↓
HUMAN
```

### 4. Source typography

Document-derived facts use IBM Plex Mono.

Together these become the visual identity of the product.

---

# 43. Final screen hierarchy

A judge seeing the main case screen for ten seconds should perceive:

```text
                    ₹38,500
                  RZP01 · 17h

               CASE STRENGTH
                    64/100

       Completeness   87%
       Quality        94%
       Consistency    71%

             REVIEW REQUIRED

       ✕ Consistency below threshold
       ✕ 1 contradiction detected

             Evidence
       ✓ Invoice
       ✓ Customer communication
       ✓ Terms & conditions
       ! Delivery proof missing

             [ Investigate ]
             [ Upload evidence ]
```

After the upload:

```text
                    ₹38,500
                  RZP01 · 17h

               CASE STRENGTH
                    91/100

       Completeness   100%
       Quality        94%
       Consistency    98%

          READY FOR HUMAN APPROVAL

       ✓ Required evidence present
       ✓ No unresolved contradictions
       ✓ Gate conditions satisfied

              DRAFT RESPONSE

       "The order was delivered..." ●
       "The transaction amount..." ●

       7 claims · 7 citations ·
       0 unsupported claims

             [ Review & Approve ]
```

That transformation is the product story.

---

# 44. Design principles — final

### 01 — Evidence before intelligence

The product should always make the evidence visible before asking the merchant to trust the AI.

### 02 — Decision before explanation

Tell the merchant what state the case is in, then explain why.

### 03 — Probability is not permission

Win probability never replaces the deterministic gate.

### 04 — AI is embedded, not anthropomorphized

Copilot functionality should exist inside the case workflow, not as a separate chatbot.

### 05 — Failure is a product feature

A blocked case demonstrates safety and boundedness.

### 06 — Money deserves visual priority

Amount and deadline should be immediately scannable.

### 07 — Every claim has provenance

The Evidence Thread should make source grounding visible.

### 08 — Human approval is a boundary

The final submission should always communicate that a human reviewed the response.

### 09 — Restraint creates trust

No visual element should exist purely because modern AI dashboards usually have it.

### 10 — The interface should show its work

A judge should be able to understand the reasoning path without opening developer tools.

---

# 45. Recommended build order

## P0 — Core demo experience

1. App shell
2. Dispute queue
3. Case workspace
4. Case header
5. Case Strength
6. Evidence checklist
7. Evidence upload
8. Document viewer
9. Gate decision
10. Contradiction state
11. Response editor
12. Citation interaction
13. Human approval
14. Submission state
15. Audit timeline

## P1 — Product polish

1. Overview command center
2. Evidence inspector
3. Explain
4. Investigate
5. Recommend
6. Processing states
7. SSE state updates
8. Responsive layout
9. Dark mode
10. Keyboard accessibility

## P2 — Differentiation

1. Historical case retrieval
2. Outcome feedback
3. Evidence impact simulation
4. Batch disputes
5. Additional reason codes
6. Confidence calibration

---

# 46. Final design direction

Shield Assist should not try to win the hackathon by looking like the most futuristic AI application.

It should win by making the **trust boundary visible**.

The strongest visual sequence is:

```text
MONEY AT RISK
      ↓
EVIDENCE
      ↓
WHAT THE SYSTEM FOUND
      ↓
WHAT IS MISSING
      ↓
WHAT CONTRADICTS
      ↓
WHAT THE MODEL ESTIMATES
      ↓
WHAT THE GATE ALLOWS
      ↓
WHAT THE HUMAN APPROVES
      ↓
WHAT WAS SUBMITTED
```

The product should feel like a system that is saying:

> **"Here is what happened. Here is the evidence. Here is what we know. Here is what we don't know. Here is what I recommend. And here is exactly where I will stop and ask you to decide."**

That is the visual expression of Shield Assist's core product thesis:

**Not just confident. Bounded.**