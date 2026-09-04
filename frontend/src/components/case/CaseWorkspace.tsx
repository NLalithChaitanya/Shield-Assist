/**
 * CaseWorkspace — the most important screen.
 * Redesigned for merchant-first UX:
 *   1. Urgency banner (past deadline)
 *   2. Amount/reason/deadline header
 *   3. Action items (missing evidence, contradictions)
 *   4. Case Strength (collapsed/secondary)
 *   5. Response + Copilot
 *   6. Activity log (secondary tab)
 */

import { useState } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, AlertTriangle, Clock } from 'lucide-react';
import { useDispute } from '../../hooks/useQueries';
import { uploadDocument } from '../../lib/api';
import { formatPaiseFull, timeUntil, reasonCodeLabel, REASON_CODES, SLOT_NAMES } from '../../lib/types';
import type { DisputeDetail } from '../../lib/types';
import CaseStrength from './CaseStrength';
import EvidenceChecklist from './EvidenceChecklist';
import ResponseEditor from './ResponseEditor';
import AuditTimeline from './AuditTimeline';
import CopilotPanel from '../copilot/CopilotPanel';
import UploadEvidence from '../evidence/UploadEvidence';
import EmptyState from '../ui/EmptyState';

export default function CaseWorkspace() {
  const { id } = useParams<{ id: string }>();
  const { data: dispute, isLoading, error } = useDispute(id ?? null);
  const [activeTab, setActiveTab] = useState<'case' | 'activity'>('case');

  if (isLoading) {
    return (
      <div className="max-w-[900px] mx-auto px-8 py-8">
        <div className="space-y-4">
          <div className="h-8 w-48 bg-surface-sunken rounded animate-pulse" />
          <div className="h-24 bg-surface-sunken rounded-lg animate-pulse" />
          <div className="h-32 bg-surface-sunken rounded-lg animate-pulse" />
        </div>
      </div>
    );
  }

  if (error || !dispute) {
    return (
      <div className="max-w-[900px] mx-auto px-8 py-8">
        <EmptyState type="error" title="Dispute not found" description="This dispute may have been removed or does not exist." />
      </div>
    );
  }

  const requiredSlots = REASON_CODES[dispute.reason_code]?.required_slots ?? [];
  const missingCount = dispute.scores?.missing_required_slots.length ?? 0;
  const contradictionCount = dispute.scores?.contradiction_flags.length ?? 0;
  const hasIssues = !dispute.gate?.passed;
  const showActionItems = hasIssues && (missingCount > 0 || contradictionCount > 0);

  return (
    <div className="max-w-[900px] mx-auto px-8 py-6">
      {/* ─── PAST DEADLINE BANNER ─── */}
      {dispute.past_deadline && (
        <div className="flex items-center gap-2.5 px-4 py-3 mb-5 rounded-lg bg-urgent border border-urgent/30 text-white">
          <AlertTriangle size={18} className="shrink-0" />
          <div className="flex-1">
            <div className="text-[13px] font-semibold">
              This dispute is past its response deadline
            </div>
            <div className="text-[11px] opacity-80 mt-0.5">
              The deadline to respond was {dispute.respond_by ? new Date(dispute.respond_by).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : 'unknown'}. Submitting now may still be possible — contact Razorpay support if needed.
            </div>
          </div>
          <Clock size={16} className="shrink-0 opacity-70" />
        </div>
      )}

      {/* ─── BACK NAV ─── */}
      <Link to="/disputes" className="inline-flex items-center gap-1.5 text-[12px] text-ink-muted hover:text-ink mb-4 transition-colors">
        <ArrowLeft size={14} />
        Disputes
      </Link>

      {/* ─── CASE HEADER ─── */}
      <CaseHeader dispute={dispute} />

      {/* ─── TAB NAVIGATION ─── */}
      <div className="flex items-center gap-1 mt-5 border-b border-line">
        <button
          onClick={() => setActiveTab('case')}
          className={`px-3 py-2 text-[12px] font-medium border-b-2 transition-colors -mb-px ${
            activeTab === 'case'
              ? 'border-ink text-ink'
              : 'border-transparent text-ink-muted hover:text-ink'
          }`}
        >
          Case Review
        </button>
        <button
          onClick={() => setActiveTab('activity')}
          className={`px-3 py-2 text-[12px] font-medium border-b-2 transition-colors -mb-px ${
            activeTab === 'activity'
              ? 'border-ink text-ink'
              : 'border-transparent text-ink-muted hover:text-ink'
          }`}
        >
          Activity Log
        </button>
      </div>

      {activeTab === 'case' ? (
        <>
          {/* ─── ACTION ITEMS: Missing Evidence + Contradictions (FIRST) ─── */}
          {showActionItems && (
            <div className="mt-5">
              <ActionItemsPanel dispute={dispute} />
            </div>
          )}

          {/* ─── CASE STRENGTH (secondary, collapsed) ─── */}
          <div className="mt-5">
            <CaseStrength dispute={dispute} />
          </div>

          {/* ─── COPILOT ─── */}
          <div className="mt-5">
            <CopilotPanel dispute={dispute} />
          </div>

          {/* ─── EVIDENCE + RESPONSE ─── */}
          <div className="mt-5 grid grid-cols-[1fr_280px] gap-6">
            {/* Left: Response */}
            <div>
              <ResponseEditor dispute={dispute} />
            </div>

            {/* Right: Evidence sidebar */}
            <div className="space-y-4">
              <EvidenceChecklist dispute={dispute} requiredSlots={requiredSlots} />
              <UploadEvidence disputeId={dispute.dispute_id} />
            </div>
          </div>
        </>
      ) : (
        /* ─── ACTIVITY LOG (secondary tab) ─── */
        <div className="mt-5">
          <AuditTimeline disputeId={dispute.dispute_id} />
        </div>
      )}
    </div>
  );
}


// ─── Case Header ───
function CaseHeader({ dispute }: { dispute: DisputeDetail }) {
  const deadline = timeUntil(dispute.respond_by);

  return (
    <div className="flex items-start justify-between">
      <div>
        {/* Amount — most visually dominant */}
        <div className="font-data text-[28px] font-bold text-ink tracking-tight">
          {formatPaiseFull(dispute.amount_paise)}
        </div>

        {/* Reason code + name */}
        <div className="flex items-center gap-2 mt-1">
          <span className="font-data text-[13px] font-medium text-ink">{dispute.reason_code}</span>
          <span className="text-[13px] text-ink-muted">·</span>
          <span className="text-[13px] text-ink-muted">{reasonCodeLabel(dispute.reason_code)}</span>
        </div>

        {/* Dispute ID + deadline (deadline shown as text now, banner handles urgency) */}
        <div className="flex items-center gap-3 mt-1.5">
          <span className="font-data text-[11px] text-ink-faint">#{dispute.dispute_id}</span>
          {!dispute.past_deadline && (
            <>
              <span className="text-[11px] text-ink-faint">·</span>
              <span className="font-data text-[11px] text-ink-muted">
                {deadline} remaining
              </span>
            </>
          )}
        </div>
      </div>

      {/* Status chip */}
      <div className="flex items-center gap-2">
        <span className={`text-[11px] font-data font-medium px-2.5 py-1 rounded border ${
          dispute.status === 'scored' ? 'bg-signal-bg text-signal border-signal/20' :
          dispute.status === 'drafted' ? 'bg-money-bg text-money border-money/20' :
          'bg-surface-sunken text-ink-muted border-line'
        }`}>
          {dispute.status.toUpperCase()}
        </span>
      </div>
    </div>
  );
}


// ─── Action Items Panel (missing evidence + contradictions, merchant-first) ───
function ActionItemsPanel({ dispute }: { dispute: DisputeDetail }) {
  const scores = dispute.scores;
  const missingSlots = scores?.missing_required_slots ?? [];
  const contradictions = scores?.contradiction_flags ?? [];

  // Build a map from document_id to human-friendly name
  const docNameMap = new Map<string, string>();
  for (const doc of dispute.documents) {
    const slotName = SLOT_NAMES[doc.evidence_slot] ?? doc.evidence_slot.replace(/_/g, ' ');
    docNameMap.set(doc.document_id, slotName);
  }

  const getDocLabel = (docId: string) => {
    if (docNameMap.has(docId)) return `Your ${docNameMap.get(docId)} document`;
    return `Document ${docId.slice(0, 8)}`;
  };

  // Translate contradiction rule types into plain language
  const translateContradiction = (flag: { rule_type: string; detail: string; documents_involved: string[] }) => {
    const docLabels = flag.documents_involved.map(d => getDocLabel(d));

    if (flag.rule_type === 'date_order_violation') {
      return `${docLabels[0] || 'A document'} has a date that falls after the dispute date — this looks backwards and needs checking.`;
    }
    if (flag.rule_type === 'order_id_mismatch') {
      // Try to extract the two order IDs from the detail string
      const orderIdMatch = flag.detail.match(/order ID '([^']+)'/g);
      if (orderIdMatch && orderIdMatch.length >= 2) {
        const docOrderId = orderIdMatch[0].match(/'([^']+)'/)?.[1] ?? '';
        const disputeOrderId = orderIdMatch[1].match(/'([^']+)'/)?.[1] ?? '';
        return `The order number on ${docLabels[0] || 'a document'} (${docOrderId}) doesn't match this dispute's order number (${disputeOrderId}) — please confirm this is the right document.`;
      }
      return `${docLabels[0] || 'A document'} references a different order number than this dispute — please confirm the document belongs to this case.`;
    }
    // Fallback: generic plain-language wrapping
    return `There's a potential issue with ${docLabels[0] || 'one of your documents'}. ${flag.detail}`;
  };

  return (
    <div className="border border-urgent/20 rounded-lg bg-urgent-bg/30">
      {/* Summary banner */}
      <div className="px-4 py-3 border-b border-urgent/10">
        <div className="flex items-center gap-2 mb-1">
          <AlertTriangle size={15} className="text-urgent shrink-0" />
          <span className="text-[13px] font-semibold text-urgent">
            This case needs your attention before we can prepare a response
          </span>
        </div>
        <p className="text-[12px] text-ink-muted ml-[23px]">
          {missingSlots.length > 0 && (
            <>You're missing {missingSlots.length} document{missingSlots.length !== 1 ? 's' : ''} Razorpay requires</>
          )}
          {missingSlots.length > 0 && contradictions.length > 0 && <> and </>}
          {contradictions.length > 0 && (
            <>something in your uploaded documents doesn't line up — we've flagged it below for you to check</>
          )}
          .
        </p>
      </div>

      <div className="p-4 space-y-4">
        {/* Missing Evidence with inline upload */}
        {missingSlots.length > 0 && (
          <div>
            <div className="text-[11px] font-semibold text-ink-faint uppercase tracking-wider mb-2">
              Missing documents
            </div>
            <div className="space-y-1.5">
              {missingSlots.map(slot => (
                <MissingEvidenceRow key={slot} slot={slot} disputeId={dispute.dispute_id} />
              ))}
            </div>
          </div>
        )}

        {/* Contradictions */}
        {contradictions.length > 0 && (
          <div>
            <div className="text-[11px] font-semibold text-ink-faint uppercase tracking-wider mb-2">
              Issues to check
            </div>
            <div className="space-y-2">
              {contradictions.map((flag, i) => (
                <div key={i} className="flex items-start gap-2 p-2.5 rounded-md bg-surface-raised border border-line">
                  <AlertTriangle size={12} className="text-urgent mt-0.5 shrink-0" />
                  <span className="text-[12px] text-ink leading-relaxed">
                    {translateContradiction(flag)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


// ─── Missing Evidence Row with inline upload button ───
function MissingEvidenceRow({ slot, disputeId }: { slot: string; disputeId: string }) {
  const slotName = SLOT_NAMES[slot] ?? slot.replace(/_/g, ' ');
  const [uploading, setUploading] = useState(false);

  const inputId = `upload-${slot}-${disputeId}`;

  const handleUpload = () => {
    const input = document.getElementById(inputId) as HTMLInputElement | null;
    if (input) input.click();
  };

  const onFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      await uploadDocument(disputeId, file, slot, 'clear');
    } catch {
      // Error handled by UploadEvidence component state
    } finally {
      setUploading(false);
      e.target.value = '';
    }
  };

  return (
    <div className="flex items-center gap-2 p-2 rounded-md border border-urgent/15 bg-surface-raised">
      <div className="w-1.5 h-1.5 rounded-full bg-urgent shrink-0" />
      <span className="text-[12px] text-ink flex-1">{slotName}</span>
      <input
        id={inputId}
        type="file"
        accept=".pdf,.png,.jpg,.jpeg"
        onChange={onFileChange}
        className="hidden"
      />
      <button
        onClick={handleUpload}
        disabled={uploading}
        className="text-[11px] font-medium text-signal hover:text-signal/80 border border-signal/20 rounded px-2.5 py-0.5 hover:bg-signal-bg/50 transition-colors disabled:opacity-50"
      >
        {uploading ? 'Uploading…' : 'Upload'}
      </button>
    </div>
  );
}
