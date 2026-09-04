# Phase 0 — Completion Summary

Companion to `EXECUTION_PLAN.md`. This records what was actually done, verified, and learned during Phase 0, so later phases (and judge questions) can point back to something concrete instead of "we assumed X."

**Status: ✅ Phase 0 gate met.**

---

## What the Phase 0 gate required

> ✅ You have a real Razorpay test-mode webhook hitting a local endpoint and printing the raw payload.
> ✅ You can point to the exact doc/line for every schema decision made later (no invented fields).

Both are satisfied — details below.

---

## 1. Real webhook, real signature, real payload — proven end-to-end

Rather than stopping at "the code should work," we proved it live:

- FastAPI listener (`backend/webhook_listener.py`) running locally on port 8000
- Exposed publicly via **Cloudflare Tunnel** (`cloudflared tunnel --url http://localhost:8000`) — chosen over ngrok after ngrok's Windows binary was flagged by Windows Defender mid-setup; Cloudflare's quick tunnel required no account, no authtoken, and worked on the first try
- Registered the tunnel URL + `/api/webhooks/razorpay` as a webhook in the Razorpay dashboard (Test Mode), with a self-generated `RAZORPAY_WEBHOOK_SECRET`
- Triggered a real test payment (₹500, Netbanking method — card payments were blocked on this account with an "international cards not supported" error even for domestic test card numbers, so Netbanking was used instead) via a minimal checkout page hitting the real Orders API
- **Result:** a genuine, Razorpay-signed `payment.captured` webhook was delivered, HMAC-SHA256 signature verification passed (confirmed by the *absence* of the "skipping verification" log warning, meaning `RAZORPAY_WEBHOOK_SECRET` was correctly loaded via `python-dotenv` and actually checked), the payload was printed and persisted to `data/captured_webhooks/`, and the endpoint returned `200 OK`

This is real proof the ingestion plumbing works — not a simulated request against our own code.

### Known limitation (documented, not hidden)
No self-serve way exists to trigger a real `payment.dispute.created` event in Razorpay Test Mode — disputes are bank/issuer-initiated, not merchant-triggerable via API or dashboard, and no such option turned up anywhere in Razorpay's own documentation. This is an inherent constraint of the platform, not a gap in our setup. The `payment.captured` test above proves the same webhook delivery + verification + capture pipeline that a dispute event would use.

---

## 2. Schema grounding — every field sourced, not invented

| Artifact | Source | Verified |
|---|---|---|
| `payment.dispute.created` payload shape | razorpay.com/docs/webhooks/disputes | ✅ fetched directly, saved to `/docs/razorpay_schemas/dispute_webhook_schema.json` |
| `POST /v1/documents` contract | razorpay.com/docs/api/documents/create | ✅ fetched directly, saved to `/docs/razorpay_schemas/documents_api_contract.md` |
| `POST /v1/disputes/{id}/contest` contract | razorpay.com/docs/api/disputes/contest | ✅ fetched directly, same file |
| Reason-code → required-evidence mapping (all 6 in-scope codes) | razorpay.com/docs/payments/disputes/submit-evidence | ✅ fetched directly, cross-checked field-by-field against `/data/evidence_requirements.py` |

### Correction made during Phase 0
The initial evidence-slot mapping (written before the Submit Evidence page was fetched) had `SHIPPING_PROOF` for RZP01, RZP06, and UPI1064. Once the real docs were pulled, all three were corrected to `PROOF_OF_SERVICE` — Razorpay's actual language is "proof of service/product delivery," a different real API field from shipping. `PRODUCT_SPEC.md`'s evidence table and `/data/evidence_requirements.py` have both been updated to match. This is exactly the kind of retrofit Phase 0 exists to prevent happening later, in Phase 2 or 3.

### One real bug caught
`PRODUCT_SPEC.md` originally referenced the evidence slot as `terms_and_conditions`. The real Razorpay field is `term_and_conditions` — singular "term." Fixed in both the spec and the code.

---

## 3. Environment setup — what it actually took (worth remembering for the README)

- **Python 3.11** recommended — mature scikit-learn/pandas/FastAPI support, no need for bleeding-edge compatibility
- **Razorpay account creation** now requires a Business PAN (personal PAN accepted if unregistered) as part of sign-up itself — a Jan 2026 onboarding change, ahead of where Test Mode has historically sat; this is separate from RazorpayX, which is a different Razorpay product with its own test mode and dashboard — worth double-checking you're in the Payment Gateway product, not RazorpayX, when setting up
- **ngrok** hit two Windows-specific snags (PATH not refreshing post-winget-install, then Defender flagging the self-updated binary) — **Cloudflare Tunnel** was a smoother alternative with zero account/token friction for quick tunnels
- **Domestic test cards** (`4111 1111 1111 1111`, etc.) were rejected as "international" on this account even with correct test numbers; **Netbanking** test mode (mock bank page, Success/Failure buttons) worked immediately and is the more reliable path going forward for triggering any test-mode payment event

---

## Deliverables produced in Phase 0

- `/docs/razorpay_schemas/dispute_webhook_schema.json` — sourced webhook payload contract
- `/docs/razorpay_schemas/documents_api_contract.md` — sourced Documents + Contest API contract
- `/data/evidence_requirements.py` — verified reason-code → evidence-slot mapping, single source of truth for later phases
- `/backend/webhook_listener.py` — working FastAPI listener with HMAC verification, tested against a real signed webhook
- `data/captured_webhooks/*.json` — a real captured payload sample (`payment.captured`) for reference
- Repo scaffolding (`/data`, `/models`, `/backend`, `/frontend`, `/docs`), `requirements.txt`, `.env.example`
- `PRODUCT_SPEC.md` corrected in two places (evidence slot typo, evidence-slot table)

---

## What this unlocks for Phase 1

Phase 1's synthetic dispute + evidence generator can now build directly against `/data/evidence_requirements.py` with confidence the reason-code → evidence-slot mapping is real, not guessed — meaning the completeness score built on top of it won't need retrofitting later.
