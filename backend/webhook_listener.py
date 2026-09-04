"""
Phase 0 dummy webhook listener.

Route matches the agreed path: POST /api/webhooks/razorpay
(distinct from the future Phase 2 production ingest route, which will
live at /disputes/ingest per EXECUTION_PLAN.md — this file's only job
is proving webhook delivery + signature verification work end-to-end.)

Run:
    uvicorn backend.webhook_listener:app --reload --port 8000

"""

import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv          # <-- add this
from fastapi import FastAPI, Header, HTTPException, Request

load_dotenv()                            # <-- add this, before reading env vars

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("shield_assist.webhook_listener")

app = FastAPI(title="Shield Assist — Phase 0 Webhook Listener")

CAPTURE_DIR = Path(__file__).resolve().parent.parent / "data" / "captured_webhooks"
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)

RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")


def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    """HMAC-SHA256 over the RAW body, per Razorpay's docs — never
    re-serialize the parsed JSON before signing/verifying, or the
    signature will mismatch even with the correct secret."""
    if not RAZORPAY_WEBHOOK_SECRET:
        logger.warning(
            "RAZORPAY_WEBHOOK_SECRET not set — skipping signature "
            "verification. OK for local smoke testing only."
        )
        return True
    if not signature:
        return False
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@app.post("/api/webhooks/razorpay")
async def receive_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
):
    raw_body = await request.body()

    if not _verify_signature(raw_body, x_razorpay_signature):
        logger.error("Webhook signature verification failed — rejecting.")
        raise HTTPException(status_code=401, detail="invalid signature")

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.exception("Received non-JSON webhook body")
        raise HTTPException(status_code=400, detail="malformed JSON body")

    event = payload.get("event", "unknown_event")
    logger.info("Received webhook event=%s", event)
    print(json.dumps(payload, indent=2))

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    out_path = CAPTURE_DIR / f"{ts}_{event}.json"
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("Captured payload -> %s", out_path)

    return {"status": "received", "event": event}


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


