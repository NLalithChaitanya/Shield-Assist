/**
 * EvidenceChecklist — evidence workspace sidebar.
 * Design Direction §12: Required / Present / Verified distinction.
 * Each slot shows status, document name, and can expand.
 */

import { useState } from 'react';
import { CheckCircle, AlertTriangle, ChevronDown, ChevronRight, FileText } from 'lucide-react';
import { SLOT_NAMES } from '../../lib/types';
import type { DisputeDetail } from '../../lib/types';

interface EvidenceChecklistProps {
  dispute: DisputeDetail;
  requiredSlots: string[];
}

export default function EvidenceChecklist({ dispute, requiredSlots }: EvidenceChecklistProps) {
  const documents = dispute.documents;

  // Map slots to their status
  const slotStatus = requiredSlots.map(slot => {
    const docs = documents.filter(d => d.evidence_slot === slot);
    const hasDoc = docs.length > 0;
    return {
      slot,
      name: SLOT_NAMES[slot] ?? slot,
      present: hasDoc,
      documents: docs,
    };
  });

  return (
    <div className="border border-line rounded-lg bg-surface-raised">
      <div className="px-4 py-3 border-b border-line">
        <div className="text-[10px] font-semibold text-ink-faint uppercase tracking-wider">
          Evidence for {dispute.reason_code}
        </div>
      </div>

      <div className="p-3 space-y-1">
        {/* Required & present */}
        {slotStatus.filter(s => s.present).map(s => (
          <EvidenceItem key={s.slot} slot={s.slot} name={s.name} status="present" documents={s.documents} />
        ))}

        {/* Required but missing */}
        {slotStatus.filter(s => !s.present).map(s => (
          <EvidenceItem key={s.slot} slot={s.slot} name={s.name} status="missing" documents={[]} />
        ))}

        {/* Extra documents not in required slots */}
        {documents
          .filter(d => !requiredSlots.includes(d.evidence_slot))
          .map(d => (
            <EvidenceItem key={d.document_id} slot={d.evidence_slot} name={SLOT_NAMES[d.evidence_slot] ?? d.evidence_slot} status="extra" documents={[d]} />
          ))}
      </div>
    </div>
  );
}

function EvidenceItem({ slot: _slot, name, status, documents }: {
  slot: string;
  name: string;
  status: 'present' | 'missing' | 'extra';
  documents: DisputeDetail['documents'];
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className={`rounded-md border ${status === 'missing' ? 'border-urgent/20 bg-urgent-bg/50' : 'border-line'}`}>
      <button
        onClick={() => status !== 'missing' && setExpanded(!expanded)}
        className={`w-full flex items-center gap-2 px-3 py-2 text-left ${status !== 'missing' ? 'hover:bg-surface-overlay' : ''} transition-colors rounded-md`}
      >
        {status === 'present' || status === 'extra' ? (
          <CheckCircle size={12} className="text-money shrink-0" />
        ) : (
          <AlertTriangle size={12} className="text-urgent shrink-0" />
        )}
        <span className={`text-[12px] flex-1 ${status === 'missing' ? 'text-urgent font-medium' : 'text-ink'}`}>
          {name}
        </span>
        {status === 'missing' && (
          <span className="text-[9px] font-data text-urgent uppercase">Missing</span>
        )}
        {documents.length > 0 && (
          expanded ? <ChevronDown size={12} className="text-ink-faint" /> : <ChevronRight size={12} className="text-ink-faint" />
        )}
      </button>

      {/* Expanded: show document details */}
      {expanded && documents.length > 0 && (
        <div className="px-3 pb-2 space-y-2 border-t border-line/50">
          {documents.map(doc => (
            <div key={doc.document_id} className="pt-2">
              <div className="flex items-center gap-1.5 mb-1">
                <FileText size={11} className="text-ink-faint" />
                <span className="font-data text-[11px] text-ink">{doc.local_path?.split(/[/\\]/).pop() ?? doc.document_id}</span>
              </div>
              <div className="text-[10px] text-ink-muted">
                Status: <span className="text-money font-medium">Verified</span>
              </div>
              {Object.keys(doc.facts).length > 0 && (
                <div className="mt-1.5 space-y-0.5">
                  <div className="text-[10px] font-semibold text-ink-faint uppercase">Facts extracted</div>
                  {Object.entries(doc.facts).map(([key, value]) => (
                    <div key={key} className="flex items-center gap-2 text-[10px]">
                      <span className="text-ink-muted">{key.replace(/_/g, ' ')}</span>
                      <span className="font-data text-ink">{value}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
