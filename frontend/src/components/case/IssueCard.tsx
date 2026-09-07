/**
 * IssueCard — individual contradiction/issue card with actions.
 * Shows the issue details and provides "View evidence" and "Replace document" actions.
 * Uses existing design system components and tokens.
 */

import { useState } from 'react';
import { AlertTriangle, Eye, RefreshCw } from 'lucide-react';
import { SLOT_NAMES } from '../../lib/types';
import type { DisputeDetail, ContradictionFlag } from '../../lib/types';
import ReplaceEvidenceModal from './ReplaceEvidenceModal';

interface IssueCardProps {
  dispute: DisputeDetail;
  flag: ContradictionFlag;
  onUpdate?: () => void;
}

function getDocLabel(docId: string, dispute: DisputeDetail): string {
  const doc = dispute.documents.find(d => d.document_id === docId);
  if (doc) {
    const slotName = SLOT_NAMES[doc.evidence_slot] ?? doc.evidence_slot.replace(/_/g, ' ');
    return slotName;
  }
  return `Document ${docId.slice(0, 8)}`;
}

function getIssueTitle(ruleType: string): string {
  switch (ruleType) {
    case 'amount_mismatch': return 'Amount mismatch';
    case 'date_order_violation': return 'Date inconsistency';
    case 'name_mismatch': return 'Customer identity mismatch';
    case 'order_id_mismatch': return 'Order number mismatch';
    default: return 'Evidence issue';
  }
}

function getIssueDetail(flag: ContradictionFlag, dispute: DisputeDetail): string {
  const docLabels = flag.documents_involved.map(d => getDocLabel(d, dispute));

  if (flag.rule_type === 'date_order_violation') {
    return `${docLabels[0] || 'A document'} has a date that falls after the dispute date.`;
  }
  if (flag.rule_type === 'order_id_mismatch') {
    const match = flag.detail.match(/order ID '([^']+)'/g);
    if (match && match.length >= 2) {
      const docOrderId = match[0].match(/'([^']+)'/)?.[1] ?? '';
      const disputeOrderId = match[1].match(/'([^']+)'/)?.[1] ?? '';
      return `Document references order ${docOrderId}, but the dispute order is ${disputeOrderId}.`;
    }
    return `${docLabels[0] || 'A document'} references a different order number.`;
  }
  if (flag.rule_type === 'name_mismatch') {
    const match = flag.detail.match(/name '([^']+)'/g);
    if (match && match.length >= 2) {
      const docName = match[0].match(/'([^']+)'/)?.[1] ?? '';
      const disputeName = match[1].match(/'([^']+)'/)?.[1] ?? '';
      return `Document shows customer "${docName}", but the dispute customer is "${disputeName}".`;
    }
    return `${docLabels[0] || 'A document'} shows a different customer name.`;
  }
  // Fallback for amount_mismatch or unknown
  return flag.detail.length > 120 ? flag.detail.slice(0, 120) + '…' : flag.detail;
}

function getAffectedSlot(flag: ContradictionFlag, dispute: DisputeDetail): string | null {
  // Find the evidence slot of the first involved document
  if (flag.documents_involved.length === 0) return null;
  const docId = flag.documents_involved[0];
  const doc = dispute.documents.find(d => d.document_id === docId);
  return doc?.evidence_slot ?? null;
}

export default function IssueCard({ dispute, flag, onUpdate }: IssueCardProps) {
  const [showViewer, setShowViewer] = useState(false);
  const [showReplaceModal, setShowReplaceModal] = useState(false);

  const title = getIssueTitle(flag.rule_type);
  const detail = getIssueDetail(flag, dispute);
  const affectedSlot = getAffectedSlot(flag, dispute);
  const docLabels = flag.documents_involved.map(d => getDocLabel(d, dispute));

  return (
    <>
      <div className="p-3 rounded-md bg-surface-raised border border-line">
        {/* Header row: icon + title + severity badge */}
        <div className="flex items-center gap-2 mb-1.5">
          <AlertTriangle size={12} className="text-urgent shrink-0" />
          <span className="text-[12px] font-semibold text-ink flex-1">{title}</span>
          <span className={`text-[9px] font-data font-medium px-1.5 py-0.5 rounded border ${
            flag.severity === 'high'
              ? 'bg-urgent-bg text-urgent border-urgent/20'
              : 'bg-caution-bg text-caution border-caution/20'
          }`}>
            {flag.severity === 'high' ? 'HIGH' : 'MEDIUM'}
          </span>
        </div>

        {/* Detail text */}
        <p className="text-[11px] text-ink-muted leading-relaxed mb-2 ml-[18px]">
          {detail}
        </p>

        {/* Involved documents */}
        {docLabels.length > 0 && (
          <div className="ml-[18px] mb-2.5 flex flex-wrap gap-1">
            {docLabels.map((label, i) => (
              <span key={i} className="text-[10px] font-data text-ink-faint bg-surface-sunken px-1.5 py-0.5 rounded">
                {label}
              </span>
            ))}
          </div>
        )}

        {/* Action buttons */}
        <div className="flex items-center gap-2 ml-[18px]">
          <button
            onClick={() => setShowViewer(true)}
            className="flex items-center gap-1.5 text-[11px] font-medium text-signal hover:text-signal/80 border border-signal/20 rounded px-2.5 py-1 hover:bg-signal-bg/30 transition-colors"
          >
            <Eye size={11} />
            View evidence
          </button>
          {affectedSlot && (
            <button
              onClick={() => setShowReplaceModal(true)}
              className="flex items-center gap-1.5 text-[11px] font-medium text-caution hover:text-caution/80 border border-caution/20 rounded px-2.5 py-1 hover:bg-caution-bg/30 transition-colors"
            >
              <RefreshCw size={11} />
              Replace document
            </button>
          )}
        </div>
      </div>

      {/* Evidence Viewer */}
      {showViewer && (
        <EvidenceIssueViewer
          dispute={dispute}
          flag={flag}
          onClose={() => setShowViewer(false)}
        />
      )}

      {/* Replace Document Modal */}
      {showReplaceModal && affectedSlot && (
        <ReplaceEvidenceModal
          disputeId={dispute.dispute_id}
          slot={affectedSlot}
          flag={flag}
          onClose={() => setShowReplaceModal(false)}
          onComplete={onUpdate}
        />
      )}
    </>
  );
}


// ─── Evidence Issue Viewer ───

interface EvidenceIssueViewerProps {
  dispute: DisputeDetail;
  flag: ContradictionFlag;
  onClose: () => void;
}

function EvidenceIssueViewer({ dispute, flag, onClose }: EvidenceIssueViewerProps) {
  const docLabels = flag.documents_involved.map(d => getDocLabel(d, dispute));

  // Build a table showing the expected vs actual values
  const renderFactComparison = () => {
    if (flag.rule_type === 'amount_mismatch') {
      // Parse amounts from detail: "₹42,000.00 vs ₹42,500.00"
      const amounts = flag.detail.match(/₹[\d,]+\.\d+/g) || [];
      return (
        <div className="space-y-2">
          <FactRow label="Expected amount" value={`₹${(dispute.amount_paise / 100).toLocaleString('en-IN')}`} expected />
          {amounts.map((amt, i) => (
            <FactRow key={i} label={`Document ${i + 1} (${docLabels[i] || 'unknown'})`} value={amt} />
          ))}
        </div>
      );
    }

    if (flag.rule_type === 'name_mismatch') {
      const names = flag.detail.match(/'([^']+)'/g) || [];
      return (
        <div className="space-y-2">
          {names.map((name, i) => (
            <FactRow
              key={i}
              label={i === 0 ? `Document (${docLabels[0] || 'unknown'})` : 'Dispute customer'}
              value={name.replace(/'/g, '')}
              expected={i > 0}
            />
          ))}
        </div>
      );
    }

    if (flag.rule_type === 'order_id_mismatch') {
      const ids = flag.detail.match(/order ID '([^']+)'/g) || [];
      return (
        <div className="space-y-2">
          {ids.map((id, i) => (
            <FactRow
              key={i}
              label={i === 0 ? `Document (${docLabels[0] || 'unknown'})` : 'Dispute order'}
              value={id.replace(/order ID '|'/g, '')}
              expected={i > 0}
            />
          ))}
        </div>
      );
    }

    if (flag.rule_type === 'date_order_violation') {
      const dates = flag.detail.match(/\d{4}-\d{2}-\d{2}/g) || [];
      return (
        <div className="space-y-2">
          {dates.map((date, i) => (
            <FactRow
              key={i}
              label={i === 0 ? `Document (${docLabels[0] || 'unknown'})` : 'Dispute date'}
              value={date}
              expected={i > 0}
            />
          ))}
        </div>
      );
    }

    // Fallback: show raw detail
    return (
      <div className="text-[11px] text-ink-muted leading-relaxed">
        {flag.detail}
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div
        className="bg-surface-raised rounded-lg shadow-lg border border-line max-w-md w-full mx-4 max-h-[80vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-line">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-urgent-bg flex items-center justify-center">
              <AlertTriangle size={12} className="text-urgent" />
            </div>
            <span className="text-[13px] font-semibold text-ink">{getIssueTitle(flag.rule_type)}</span>
          </div>
          <button
            onClick={onClose}
            className="text-ink-muted hover:text-ink text-[18px] leading-none px-1"
          >
            ×
          </button>
        </div>

        {/* Content */}
        <div className="p-4">
          <p className="text-[12px] text-ink-muted mb-4 leading-relaxed">
            {getIssueDetail(flag, dispute)}
          </p>

          {/* Fact comparison */}
          <div className="border border-line rounded-md p-3 bg-surface-sunken mb-3">
            <div className="text-[10px] font-semibold text-ink-faint uppercase tracking-wider mb-2">
              Evidence comparison
            </div>
            {renderFactComparison()}
          </div>

          {/* Source documents */}
          {flag.documents_involved.length > 0 && (
            <div className="border border-line rounded-md p-3">
              <div className="text-[10px] font-semibold text-ink-faint uppercase tracking-wider mb-2">
                Source documents
              </div>
              {flag.documents_involved.map(docId => (
                  <div key={docId} className="flex items-center gap-2 py-1">
                    <div className="w-1.5 h-1.5 rounded-full bg-ink-faint shrink-0" />
                    <span className="text-[11px] text-ink font-data">{docId.slice(0, 16)}</span>
                    <span className="text-[10px] text-ink-muted">
                      ({getDocLabel(docId, dispute)})
                    </span>
                  </div>
              ))}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-line flex justify-end">
          <button
            onClick={onClose}
            className="text-[12px] font-medium text-ink-muted hover:text-ink border border-line rounded px-3 py-1.5 hover:bg-surface-overlay transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}


// ─── Fact comparison row ───

function FactRow({ label, value, expected }: { label: string; value: string; expected?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <span className="text-[11px] text-ink-muted">{label}</span>
      <span className={`font-data text-[11px] font-medium ${expected ? 'text-money' : 'text-ink'}`}>
        {value}
      </span>
    </div>
  );
}
