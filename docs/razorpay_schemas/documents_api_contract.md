# Razorpay Documents API — Contract
Source: https://razorpay.com/docs/api/documents/create/ (verified 2026-08-27)

## POST /v1/documents

**Content-Type:** `multipart/form-data` (NOT JSON — this trips people up)

### Request fields
| Field | Type | Required | Notes |
|---|---|---|---|
| `purpose` | string | yes | For dispute evidence, always `dispute_evidence` |
| `file` | binary | yes | The document itself |

### Request example
```bash
curl -u [KEY_ID]:[KEY_SECRET] \
  -X POST 'https://api.razorpay.com/v1/documents' \
  -H "Content-Type: multipart/form-data" \
  -F 'purpose=dispute_evidence' \
  -F 'file=@/path/to/file.jpeg'
```

### Response (success)
```json
{
  "id": "doc_EsyWjHrfzb59Re",
  "entity": "document",
  "purpose": "dispute_evidence",
  "name": "doc_19_12_2020.jpg",
  "mime_type": "image/png",
  "size": 2863,
  "created_at": 1590604200
}
```

### Constraints
- Max file size: **50,000 KB (50MB)**
- Allowed mime types for `dispute_evidence` purpose: `image/jpg`, `image/jpeg`, `image/png`, `application/pdf`
- Razorpay holds a short-lived per-merchant lock — concurrent uploads from the same merchant will 400

### Errors to handle explicitly in our client wrapper
| Error | Status | Cause |
|---|---|---|
| `The file field is required.` | 400 | multipart missing `file` part |
| `The purpose field is required.` | 400 | multipart missing `purpose` part |
| `invalid document upload purpose.` | 400 | bad `purpose` value |
| `Document upload already in progress.` | 400 | concurrent upload from same merchant |
| `The file may not be greater than 50000 kilobytes.` | 400 | size limit |
| `The file must be a file of type: {allowed types}.` | 400 | mime mismatch for purpose |
| Invalid key/secret | 401 | wrong mode (test vs live) or expired key |

---

## POST /v1/disputes/{dispute_id}/contest

Source: https://razorpay.com/docs/api/disputes/contest/

### Request body
| Field | Type | Notes |
|---|---|---|
| `<evidence_slot>` | array[string] | doc IDs for that slot, e.g. `"shipping_proof": ["doc_..", "doc_.."]` |
| `summary` | string | max 1000 chars — the textual evidence explanation |
| `amount` | integer | optional; defaults to full dispute amount if omitted |
| `action` | string | `"submit"` — **required** to actually submit; omitting this saves a draft only |

### Constraints Shield Assist must respect
- Minimum **one document ID** required across all slots to call `action: submit`
- Cannot contest after `respond_by` has elapsed (400 error)
- Cannot contest a dispute already `lost` or `won`
- Evidence slot key is `term_and_conditions` — **not** `terms_and_conditions`