"""
backend/jobs/document_job.py

'document.process' job handler.

Extraction logic (explicit, no silent fallback):
  1. form_facts non-empty -> use as demo override, skip Gemini
  2. form_facts empty     -> Gemini OCR on the actual file (primary path)
  3. Gemini fails         -> raise, do NOT silently fall back

Source tag in audit log: 'form' | 'ocr' -- tells you which path ran.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from backend.job_queue import register_handler
from backend.repository import CaseRepository

logger = logging.getLogger("shield_assist.jobs.document")


OCR_PROMPT = """You are a document analysis engine for payment dispute evidence.
Extract facts from this document. Return ONLY a JSON object:
{"amount_paise": int, "event_date": "YYYY-MM-DD", "customer_name": str, "order_id": str, "confirmation": str}
Null for missing fields. No explanation.

IMPORTANT for amount_paise:
- Extract the PRE-TAX transaction amount (subtotal / net amount before GST/VAT/tax).
- This is what the customer was actually charged via the payment gateway.
- Do NOT extract the tax-inclusive total (which includes GST/VAT on top).
- If the document shows Subtotal + Tax = Total, use the Subtotal value.
- If only one amount is shown, use that.
- Convert to paise (multiply rupees by 100, e.g. Rs.38,500 = 3850000).

IMPORTANT for confirmation:
- If the document contains a customer statement confirming receipt, delivery, or satisfaction
  (e.g. an email reply, chat message, or signed acknowledgment), extract the exact quote.
- Use the customer's own words as closely as possible.
- Null if no customer confirmation statement is present."""


def _extract_facts_with_gemini(local_path):
    import base64
    import mimetypes
    from backend.copilot import _get_gemini_client

    client, model_name = _get_gemini_client()
    mime_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    with open(local_path, "rb") as f:
        file_bytes = f.read()

    file_data = {
        "inline_data": {
            "mime_type": mime_type,
            "data": base64.b64encode(file_bytes).decode("utf-8"),
        }
    }

    response = client.call(
        "models.generate_content",
        model=model_name,
        contents=[OCR_PROMPT, file_data],
    )

    text = getattr(response, "text", None)
    if text is None:
        raise ValueError("Gemini OCR returned no text content for document extraction")
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("\n", 1)
        text = parts[1] if len(parts) > 1 else text
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    raw_facts = json.loads(text)
    return {
        k: str(v).strip()
        for k, v in raw_facts.items()
        if v is not None and str(v).strip()
    }


@register_handler("document.process")
def handle_document_job(job, repo):
    """Process a newly uploaded document.

    Extraction priority:
      1. form_facts non-empty -> explicit override (demo/test control)
      2. form_facts empty     -> Gemini OCR (primary path)
      3. Gemini fails         -> raise, no silent fallback

    Audit log 'source' field: 'form' or 'ocr' tells you which ran.
    """
    dispute_id = job["dispute_id"]
    payload = json.loads(job.get("payload") or "{}")
    document_id = payload.get("document_id")
    form_facts = payload.get("facts", {})
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if not document_id:
        raise ValueError("Job missing document_id in payload")

    # --- Fact extraction ---
    extraction_source = "ocr"  # default: we will read the image

    if form_facts:
        # Explicit override: form facts provided (demo/test control)
        facts = form_facts
        extraction_source = "form"
        logger.info(
            "Form override: %d facts for %s (source=form)",
            len(facts), document_id,
        )
    else:
        # Primary path: Gemini OCR on the actual document
        docs = repo.get_documents(dispute_id)
        doc_row = next(
            (d for d in docs if d["document_id"] == document_id), None
        )
        local_path = doc_row.get("local_path") if doc_row else None

        if not local_path or not os.path.exists(local_path):
            raise FileNotFoundError(
                f"Document {document_id} has no file at {local_path!r}. "
                f"Upload a real file for Gemini OCR, or provide form facts."
            )

        facts = _extract_facts_with_gemini(local_path)
        extraction_source = "ocr"
        logger.info(
            "Gemini OCR: %d facts from %s (source=ocr)",
            len(facts), local_path,
        )

    # --- Save extracted facts ---
    fact_count = 0
    for fact_type, fact_value in facts.items():
        repo.save_fact({
            "document_id": document_id,
            "dispute_id": dispute_id,
            "fact_type": fact_type,
            "fact_value": str(fact_value),
            "extracted_at": now_iso,
        })
        fact_count += 1

    # --- Audit (source tag proves which path ran) ---
    repo.write_audit({
        "dispute_id": dispute_id,
        "stage": "extract",
        "detail": {
            "document_id": document_id,
            "fact_count": fact_count,
            "fact_types": list(facts.keys()),
            "source": extraction_source,
        },
        "success": True,
    })

    # --- Enqueue re-score ---
    repo.enqueue_job("score.case", dispute_id, {})

    logger.info(
        "Document %s: %d facts (source=%s), score.case enqueued",
        document_id, fact_count, extraction_source,
    )