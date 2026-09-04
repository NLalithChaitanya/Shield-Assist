/**
 * EvidencePage — overview of all evidence across disputes.
 * Shows document inventory and evidence gaps.
 */

import { useDisputes } from '../../hooks/useQueries';
import { formatPaise, reasonCodeLabel } from '../../lib/types';
import EmptyState from '../ui/EmptyState';
import { AlertTriangle } from 'lucide-react';

export default function EvidencePage() {
  const { data: disputes, isLoading } = useDisputes();

  if (isLoading) {
    return (
      <div className="max-w-[900px] mx-auto px-8 py-8">
        <div className="space-y-3">
          {[1, 2, 3].map(i => <div key={i} className="h-16 bg-surface-sunken rounded-lg animate-pulse" />)}
        </div>
      </div>
    );
  }

  if (!disputes || disputes.length === 0) {
    return (
      <div className="max-w-[900px] mx-auto px-8 py-8">
        <EmptyState type="no_evidence" />
      </div>
    );
  }

  return (
    <div className="max-w-[900px] mx-auto px-8 py-8">
      <h1 className="text-[18px] font-semibold text-ink mb-1">Evidence</h1>
      <p className="text-[12px] text-ink-muted mb-6">
        All uploaded documents across disputes.
      </p>

      <div className="space-y-3">
        {disputes.map(d => (
          <div key={d.dispute_id} className="border border-line rounded-lg bg-surface-raised p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="font-data text-[12px] font-medium text-ink">#{d.dispute_id.slice(-4)}</span>
                <span className="text-[12px] text-ink-muted">{reasonCodeLabel(d.reason_code)}</span>
              </div>
              <span className="font-data text-[12px] text-ink">{formatPaise(d.amount_paise)}</span>
            </div>
            {d.gate_action === 'review' && (
              <div className="flex items-center gap-1.5 mt-1">
                <AlertTriangle size={11} className="text-caution" />
                <span className="text-[11px] text-caution">
                  Review required — evidence may be incomplete
                </span>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
