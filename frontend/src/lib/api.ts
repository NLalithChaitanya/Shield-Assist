/**
 * API client for Shield Assist backend.
 * All requests go through Vite's proxy in development.
 */

import type { DisputeListItem, DisputeDetail, AuditResponse, Metrics } from './types';

const BASE = '';  // proxied through Vite

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(body.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof err.detail === 'string' ? err.detail : JSON.stringify(err.detail));
  }
  return res.json();
}

// ─── Disputes ───
export async function fetchDisputes(limit = 50): Promise<DisputeListItem[]> {
  return get(`/disputes?limit=${limit}`);
}

export async function fetchDispute(id: string): Promise<DisputeDetail> {
  return get(`/disputes/${id}`);
}

// ─── Audit ───
export async function fetchAudit(id: string): Promise<AuditResponse> {
  return get(`/disputes/${id}/audit`);
}

// ─── Metrics ───
export async function fetchMetrics(): Promise<Metrics> {
  return get('/metrics');
}

// ─── Document upload ───
export async function uploadDocument(
  disputeId: string,
  file: File,
  evidenceSlot: string,
  quality = 'clear',
): Promise<{ document_id: string; job_id: number; status: string }> {
  const form = new FormData();
  form.append('file', file);
  form.append('evidence_slot', evidenceSlot);
  form.append('quality', quality);
  return post(`/disputes/${disputeId}/documents`, form);
}

// ─── Approve draft (writes human.approved audit event) ───
export async function approveDraft(disputeId: string): Promise<{ status: string }> {
  return post(`/disputes/${disputeId}/approve`);
}

// ─── Edit draft ───
export async function updateDraft(disputeId: string, summaryText: string): Promise<{ status: string; draft: any }> {
  return post(`/disputes/${disputeId}/draft`, { summary_text: summaryText });
}

// ─── Regenerate draft ───
export async function regenerateDraft(disputeId: string): Promise<{ status: string; draft: any }> {
  return post(`/disputes/${disputeId}/draft/regenerate`);
}

// ─── Submit contest ───
export async function submitContest(disputeId: string): Promise<{ job_id: number; status: string }> {
  return post(`/disputes/${disputeId}/contest`);
}

// ─── Copilot streaming ───
export type CopilotAbility = 'explain' | 'investigate' | 'recommend' | 'draft' | 'navigate';

export interface CopilotError {
  message: string;
  errorType: string;    // 'rate_limit' | 'quota_exceeded' | 'circuit_breaker' | 'unknown'
  recoverable: boolean; // true = will resolve on its own (rate limit, circuit breaker)
}

export interface CopilotContextTurn {
  role: 'user' | 'assistant';
  text: string;
}

export function streamCopilot(
  disputeId: string,
  ability: CopilotAbility,
  onToken: (text: string) => void,
  onDone: () => void,
  onError: (error: CopilotError) => void,
  context?: { question?: string; history?: CopilotContextTurn[] },
): () => void {
  // Chat context is passed as query params so the backend can (a) answer
  // the merchant's ACTUAL question and (b) key its response cache on the
  // question — otherwise two different questions on the same case would
  // return the same canned ability answer.
  const params = new URLSearchParams();
  if (context?.question) params.set('q', context.question);
  if (context?.history?.length) params.set('history', JSON.stringify(context.history));
  const qs = params.toString();
  const url = `${BASE}/disputes/${disputeId}/copilot/${ability}${qs ? `?${qs}` : ''}`;
  const eventSource = new EventSource(url);

  const handleToken = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onToken(data.text);
    } catch { /* ignore parse errors */ }
  };

  const handleDone = () => {
    eventSource.close();
    onDone();
  };

  const handleError = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onError({
        message: data.error || 'Unknown error',
        errorType: data.error_type || 'unknown',
        recoverable: data.recoverable ?? false,
      });
    } catch {
      onError({ message: 'Connection lost', errorType: 'connection', recoverable: true });
    }
    eventSource.close();
  };

  eventSource.addEventListener('copilot.token', handleToken);
  eventSource.addEventListener('copilot.done', handleDone);
  eventSource.addEventListener('copilot.error', handleError);
  eventSource.onerror = () => {
    onError({ message: 'Connection lost', errorType: 'connection', recoverable: true });
    eventSource.close();
  };

  return () => eventSource.close();
}

// ─── SSE for live updates ───
// In dev, connect directly to the backend (port 8000) to bypass the
// Vite proxy, which buffers streaming responses and breaks EventSource.
// In production, the same-origin proxy handles SSE fine.
const SSE_URL = import.meta.env.DEV ? 'http://127.0.0.1:8000/events' : '/events';

export function subscribeSSE(
  onEvent: (eventType: string, data: Record<string, unknown>) => void,
): () => void {
  const eventSource = new EventSource(SSE_URL);

  const handler = (e: MessageEvent) => {
    try {
      const data = JSON.parse(e.data);
      onEvent(e.type, data);
    } catch { /* ignore */ }
  };

  // Listen for all known event types
  const eventTypes = [
    'dispute.received', 'document.uploaded', 'document.extracted',
    'scores.computed', 'draft.ready', 'human.approved',
    'contest.submitted',
  ];

  eventTypes.forEach(type => eventSource.addEventListener(type, handler));

  eventSource.onerror = () => {
    // Auto-reconnect is built into EventSource
  };

  return () => eventSource.close();
}

// ─── Razorpay Integration ───
export async function getRazorpayHealth(): Promise<import('./types').RazorpayHealth> {
  return get('/api/integrations/razorpay/health');
}

export async function getRazorpayStatus(): Promise<import('./types').IntegrationStatus> {
  return get('/api/integrations/razorpay/status');
}

export async function testRazorpayConnection(): Promise<import('./types').RazorpayHealth> {
  return post('/api/integrations/razorpay/test');
}

// ─── Dispute Simulator ───
export async function simulateDispute(
  scenario: 'good' | 'bad' | 'incomplete' = 'good',
  reasonCode = 'RZP01',
  amountPaise = 4200000,
): Promise<import('./types').SimulateDisputeResponse> {
  const params = new URLSearchParams({
    scenario,
    reason_code: reasonCode,
    amount_paise: String(amountPaise),
  });
  return post(`/api/dev/simulate/razorpay/dispute?${params.toString()}`);
}

// ─── Health check ───
export async function checkHealth(): Promise<{ status: string; version: string; workers_running: boolean }> {
  return get('/healthz');
}
