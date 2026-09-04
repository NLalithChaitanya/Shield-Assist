/**
 * TypeScript types mapping to the Shield Assist backend API.
 * Every type mirrors the exact JSON shape from FastAPI routes.
 */

// ─── Dispute list item (GET /disputes) ───
export interface DisputeListItem {
  dispute_id: string;
  reason_code: string;
  amount_paise: number;
  currency: string;
  status: string;
  respond_by: string | null;
  past_deadline: boolean;
  source: string;
  scores: {
    win_probability: number | null;
    completeness: number | null;
    quality: number | null;
    consistency: number | null;
  } | null;
  gate_action: string | null;
  gate_passed: boolean | null;
  priority: number | null;
  ingested_at: string;
  updated_at: string;
}

// ─── Full dispute detail (GET /disputes/{id}) ───
export interface DisputeDetail {
  dispute_id: string;
  payment_id: string | null;
  reason_code: string;
  network: string;
  amount_paise: number;
  currency: string;
  respond_by: string | null;
  past_deadline: boolean;
  status: string;
  source: string;
  scores: {
    win_probability: number | null;
    completeness: number | null;
    quality: number | null;
    consistency: number | null;
    missing_required_slots: string[];
    contradiction_flags: ContradictionFlag[];
    computed_at: string | null;
  } | null;
  gate: {
    action: string;
    passed: boolean;
    failing_conditions: string[];
  } | null;
  priority: number | null;
  documents: Document[];
  draft: Draft | null;
  jobs: Job[];
  ingested_at: string;
  updated_at: string;
}

// ─── Document ───
export interface Document {
  document_id: string;
  evidence_slot: string;
  quality: string;
  local_path: string | null;
  mime_type: string | null;
  size_bytes: number | null;
  razorpay_doc_id: string | null;
  facts: Record<string, string>;
  uploaded_at: string;
}

// ─── Contradiction flag ───
export interface ContradictionFlag {
  rule_type: string;
  severity: string;
  documents_involved: string[];
  detail: string;
}

// ─── Draft response ───
export interface Draft {
  summary_text: string;
  citations: Citation[];
  generated_at: string;
}

// ─── Citation ───
export interface Citation {
  claim: string;
  fact_id: number;
  document_id: string;
}

// ─── Job ───
export interface Job {
  job_id: number;
  job_type: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}

// ─── Audit entry (GET /disputes/{id}/audit) ───
export interface AuditResponse {
  dispute_id: string;
  entries: AuditEntry[];
}

export interface AuditEntry {
  log_id: number;
  stage: string;
  detail: Record<string, unknown>;
  success: boolean;
  error_detail: string | null;
  logged_at: string;
}

// ─── Metrics (GET /metrics) ───
export interface Metrics {
  total_disputes: number;
  by_status: Record<string, number>;
  auto_prepared: number;
  human_review: number;
  low_priority: number;
  likely_recoverable_paise: number;
  avg_completeness: number;
  avg_quality: number;
  avg_consistency: number;
  avg_win_probability: number;
  note: string;
}

// ─── Razorpay Integration Status ───
export interface IntegrationStatusCheck {
  name: string;
  status: 'pass' | 'warn' | 'fail';
  label: string;
}

export interface IntegrationStatus {
  provider: string;
  mode: string;
  overall: 'healthy' | 'degraded' | 'unhealthy';
  checks: IntegrationStatusCheck[];
}

export interface RazorpayHealth {
  provider: string;
  mode: string;
  configured: boolean;
  authenticated: boolean;
  message: string;
}

export interface SimulateDisputeResponse {
  dispute_id: string;
  payment_id: string;
  reason_code: string;
  amount_paise: number;
  source: string;
  scenario: string;
  simulated: boolean;
  documents: Array<{ document_id: string; evidence_slot: string }>;
  job_id: number;
  status: string;
  message: string;
}

// ─── Evidence requirements by reason code ───
export interface EvidenceRequirement {
  slot_key: string;
  slot_name: string;
  required: boolean;
  weight: number;
}

// ─── SSE events ───
export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

// ─── Reason code metadata ───
export const REASON_CODES: Record<string, { name: string; network: string; required_slots: string[] }> = {
  RZP01: {
    name: 'Goods/Services not Provided',
    network: 'razorpay',
    required_slots: ['proof_of_service', 'customer_communication', 'term_and_conditions'],
  },
  RZP04: {
    name: 'Refund not Processed',
    network: 'razorpay',
    required_slots: ['refund_confirmation', 'billing_proof', 'customer_communication', 'refund_cancellation_policy'],
  },
  RZP05: {
    name: 'Account Debited but No Confirmation',
    network: 'razorpay',
    required_slots: ['billing_proof', 'access_activity_log', 'customer_communication', 'term_and_conditions'],
  },
  RZP06: {
    name: 'Business Not Responding',
    network: 'razorpay',
    required_slots: ['proof_of_service', 'billing_proof', 'customer_communication'],
  },
  UPI1064: {
    name: 'Goods/Services Not Received',
    network: 'upi',
    required_slots: ['proof_of_service', 'customer_communication', 'term_and_conditions'],
  },
  UPI1062: {
    name: 'Goods/Services Not As Described',
    network: 'upi',
    required_slots: ['proof_of_service', 'customer_communication', 'refund_cancellation_policy'],
  },
};

// ─── Human-readable slot names ───
export const SLOT_NAMES: Record<string, string> = {
  shipping_proof: 'Shipping Proof',
  billing_proof: 'Billing Proof',
  cancellation_proof: 'Cancellation Proof',
  customer_communication: 'Customer Communication',
  proof_of_service: 'Proof of Service',
  explanation_letter: 'Explanation Letter',
  refund_confirmation: 'Refund Confirmation',
  access_activity_log: 'Access/Activity Log',
  refund_cancellation_policy: 'Refund/Cancellation Policy',
  term_and_conditions: 'Terms & Conditions',
  others: 'Other Evidence',
};

// ─── Helpers ───
export function formatPaise(paise: number): string {
  const rupees = paise / 100;
  if (rupees >= 100000) {
    return `\u20B9${(rupees / 100000).toFixed(1)}L`;
  }
  return `\u20B9${rupees.toLocaleString('en-IN')}`;
}

export function formatPaiseFull(paise: number): string {
  return `\u20B9${(paise / 100).toLocaleString('en-IN', { minimumFractionDigits: 0 })}`;
}

export function timeUntil(isoDate: string | null): string {
  if (!isoDate) return '';
  const now = new Date();
  const target = new Date(isoDate);
  const diff = target.getTime() - now.getTime();
  if (diff <= 0) return 'Past deadline';
  const hours = Math.floor(diff / (1000 * 60 * 60));
  const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
  if (hours > 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  return `${hours}h ${minutes}m`;
}

export function getPriorityLabel(dispute: DisputeListItem): 'act_now' | 'review' | 'low_priority' {
  if (dispute.gate_action === 'prepare') return 'review';
  if (dispute.gate_action === 'review') return 'review';
  if (dispute.gate_action === 'low_priority') return 'low_priority';
  // Default: derive from deadline
  if (dispute.past_deadline) return 'act_now';
  if (dispute.respond_by) {
    const hours = (new Date(dispute.respond_by).getTime() - Date.now()) / (1000 * 60 * 60);
    if (hours < 6) return 'act_now';
    if (hours < 24) return 'review';
  }
  return 'low_priority';
}

export function reasonCodeLabel(code: string): string {
  return REASON_CODES[code]?.name ?? code;
}

export function caseStrength(scores: { completeness: number | null; quality: number | null; consistency: number | null } | null): number {
  if (!scores) return 0;
  const c = scores.completeness ?? 0;
  const q = scores.quality ?? 0;
  const s = scores.consistency ?? 0;
  return Math.round(c * 0.4 + q * 0.2 + s * 0.4);
}
