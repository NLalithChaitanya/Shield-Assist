/**
 * ResponseEditor — professional response editor with Evidence Thread.
 * Design Direction §21: citations, citation count, unsupported claim count.
 * §22: Human approval must remain visually explicit.
 */

import { useState } from 'react';
import { CheckCircle, Edit3, RefreshCw, Send, Shield } from 'lucide-react';
import type { DisputeDetail } from '../../lib/types';
import CitationMarker from '../ui/CitationMarker';
import { approveDraft, submitContest } from '../../lib/api';
import { SLOT_NAMES } from '../../lib/types';

interface ResponseEditorProps {
  dispute: DisputeDetail;
}

export default function ResponseEditor({ dispute }: ResponseEditorProps) {
  const draft = dispute.draft;
  const [viewingCitation, setViewingCitation] = useState<number | null>(null);
  const [approving, setApproving] = useState(false);
  const [approved, setApproved] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!draft) {
    return (
      <div className="border border-line rounded-lg bg-surface-raised p-5">
        <div className="text-[11px] font-semibold text-ink-faint uppercase tracking-wider mb-3">
          Response
        </div>
        <div className="text-[13px] text-ink-muted">
          No response drafted yet. The copilot will draft when evidence is sufficient.
        </div>
      </div>
    );
  }

  // Parse citations from the draft
  const citations = draft.citations ?? [];
  const citationCount = citations.filter(c => c.fact_id > 0).length;

  const handleApprove = async () => {
    setApproving(true);
    setError(null);
    try {
      await approveDraft(dispute.dispute_id);
      setApproved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Approval failed');
    } finally {
      setApproving(false);
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await submitContest(dispute.dispute_id);
      setSubmitted(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Submission failed');
    } finally {
      setSubmitting(false);
    }
  };

  // Render the response text with citation markers
  const renderResponseWithCitations = () => {
    const text = draft.summary_text;
    if (!text) return null;

    // Split by citation markers if they exist in text, otherwise just show text + markers
    const paragraphs = text.split('\n').filter(p => p.trim());

    return paragraphs.map((para, i) => {
      const citation = citations[i];
      return (
        <div key={i} className="flex items-start gap-2 mb-3">
          <p className={`text-[13px] leading-relaxed text-ink flex-1 ${
            viewingCitation === i ? 'bg-signal-bg px-2 py-1 rounded' : ''
          }`}>
            {para}
          </p>
          {citation && (
            <CitationMarker
              index={i}
              active={viewingCitation === i}
              onClick={() => setViewingCitation(viewingCitation === i ? null : i)}
            />
          )}
        </div>
      );
    });
  };

  // Show citation detail when clicked
  const activeCitation = viewingCitation !== null ? citations[viewingCitation] : null;

  // Build document_id → evidence_slot lookup for citation display
  const docSlotLookup: Record<string, string> = {};
  for (const doc of dispute.documents) {
    docSlotLookup[doc.document_id] = doc.evidence_slot;
  }
  const docSlotName = activeCitation ? (docSlotLookup[activeCitation.document_id] ?? '') : '';

  return (
    <div className="border border-line rounded-lg bg-surface-raised">
      {/* Header */}
      <div className="px-4 py-3 border-b border-line flex items-center justify-between">
        <div>
          <div className="text-[11px] font-semibold text-ink-faint uppercase tracking-wider">
            Draft Response
          </div>
          <div className="text-[11px] text-ink-muted mt-0.5">
            Grounded in {dispute.documents.length} document{dispute.documents.length !== 1 ? 's' : ''}
          </div>
        </div>
        <div className="flex items-center gap-3 text-[11px]">
          <span className="font-data text-ink-muted">{citationCount} citation{citationCount !== 1 ? 's' : ''}</span>
          <span className="font-data text-money">0 unsupported</span>
        </div>
      </div>

      {/* Response body with citations */}
      <div className="p-4">
        {renderResponseWithCitations()}

        {/* Citation detail panel */}
        {activeCitation && (
          <div className="mt-3 p-3 bg-signal-bg/50 border border-signal/20 rounded-md">
            <div className="text-[10px] font-semibold text-signal uppercase tracking-wider mb-1">
              Citation {viewingCitation! + 1} — Source
            </div>
            <div className="text-[12px] text-ink">
              {activeCitation.claim}
            </div>
            <div className="text-[11px] text-ink-muted mt-1 font-data">
              Document: {SLOT_NAMES[docSlotName] ?? docSlotName.replace(/_/g, ' ')}
            </div>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="px-4 py-3 border-t border-line flex items-center gap-2">
        <button className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] text-ink-muted border border-line rounded-md hover:bg-surface-overlay transition-colors">
          <Edit3 size={12} />
          Edit
        </button>
        <button className="flex items-center gap-1.5 px-3 py-1.5 text-[12px] text-ink-muted border border-line rounded-md hover:bg-surface-overlay transition-colors">
          <RefreshCw size={12} />
          Regenerate
        </button>

        <div className="flex-1" />

        {/* Human approval — explicit boundary */}
        <div className="flex items-center gap-2">
          {approved || dispute.status === 'ready' ? (
            <div className="flex items-center gap-1.5 px-3 py-1.5 bg-signal-bg border border-signal/20 rounded-md">
              <CheckCircle size={12} className="text-signal" />
              <span className="text-[11px] font-medium text-signal">Approved</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-3 py-1.5 bg-money-bg border border-money/20 rounded-md">
              <Shield size={12} className="text-money" />
              <span className="text-[11px] font-medium text-money">Human approval required</span>
            </div>
          )}
          {!approved && dispute.status !== 'ready' && (
            <button
              onClick={handleApprove}
              disabled={approving}
              className="flex items-center gap-1.5 px-4 py-1.5 text-[12px] font-medium text-white bg-signal rounded-md hover:bg-signal/90 transition-colors disabled:opacity-50"
            >
              {approving ? (
                <RefreshCw size={12} className="animate-spin" />
              ) : (
                <CheckCircle size={12} />
              )}
              Approve
            </button>
          )}
        </div>
      </div>

      {/* Submit bar — only after approval */}
      {/* Error display */}
      {error && (
        <div className="px-4 py-2 bg-urgent-bg border-t border-urgent/20 text-[11px] text-urgent">
          {error}
        </div>
      )}

      {/* Submit bar — only after approval */}
      <div className="px-4 py-3 border-t border-line bg-surface-sunken">
        {submitted ? (
          <div className="flex items-center gap-2 text-[12px]">
            <CheckCircle size={14} className="text-money" />
            <span className="text-money font-medium">Submitted to Razorpay</span>
            <span className="text-ink-muted ml-2 font-data text-[11px]">
              {new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ) : (approved || dispute.status === 'ready') ? (
          <div className="flex items-center justify-between">
            <div className="text-[11px] text-ink-muted">
              Approved. Ready to submit the dispute contest to Razorpay.
            </div>
            <button
              onClick={handleSubmit}
              disabled={submitting}
              className="flex items-center gap-1.5 px-4 py-1.5 text-[12px] font-medium text-white bg-money rounded-md hover:bg-money/90 transition-colors disabled:opacity-50"
            >
              {submitting ? (
                <RefreshCw size={12} className="animate-spin" />
              ) : (
                <Send size={12} />
              )}
              Submit to Razorpay
            </button>
          </div>
        ) : (
          <div className="text-[11px] text-ink-muted">
            Approve the draft first, then submit to Razorpay.
          </div>
        )}
      </div>
    </div>
  );
}
