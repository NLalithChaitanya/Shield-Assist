/**
 * AuditPage — global activity view across all disputes.
 * Uses individual dispute audit trails aggregated.
 */

import { useDisputes, useAudit } from '../../hooks/useQueries';
import EmptyState from '../ui/EmptyState';


export default function AuditPage() {
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
        <EmptyState type="no_disputes" title="No activity yet" description="Audit events will appear here as disputes are processed." />
      </div>
    );
  }

  return (
    <div className="max-w-[900px] mx-auto px-8 py-8">
      <h1 className="text-[18px] font-semibold text-ink mb-1">Activity</h1>
      <p className="text-[12px] text-ink-muted mb-6">
        Audit trail across all disputes.
      </p>

      <div className="space-y-4">
        {disputes.map(d => (
          <AuditDisputeSection key={d.dispute_id} disputeId={d.dispute_id} />
        ))}
      </div>
    </div>
  );
}

function AuditDisputeSection({ disputeId }: { disputeId: string }) {
  const { data: audit } = useAudit(disputeId);
  const entries = audit?.entries ?? [];

  if (entries.length === 0) return null;

  return (
    <div className="border border-line rounded-lg bg-surface-raised">
      <div className="px-4 py-2 border-b border-line">
        <span className="font-data text-[12px] text-ink font-medium">#{disputeId.slice(-4)}</span>
      </div>
      <div className="p-3">
        {entries.slice(0, 5).map(entry => {
          const time = new Date(entry.logged_at).toLocaleTimeString('en-IN', {
            hour: '2-digit',
            minute: '2-digit',
          });
          return (
            <div key={entry.log_id} className="flex items-center gap-3 py-1.5">
              <span className="font-data text-[11px] text-ink-faint w-14">{time}</span>
              <span className="text-[12px] text-ink">{entry.stage}</span>
              {entry.success === false && <span className="text-[10px] text-urgent">failed</span>}
            </div>
          );
        })}
        {entries.length > 5 && (
          <div className="text-[11px] text-ink-faint mt-1 pl-17">
            +{entries.length - 5} more events
          </div>
        )}
      </div>
    </div>
  );
}
