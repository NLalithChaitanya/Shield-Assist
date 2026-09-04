/**
 * AuditTimeline — merchant-readable activity timeline.
 * Redesigned: failed copilot entries are hidden or relabeled.
 * Only shown in the secondary "Activity Log" tab.
 */

import { useState } from 'react';
import { useAudit } from '../../hooks/useQueries';
import { ChevronDown, ChevronRight, CheckCircle, Clock, Upload, FileText, Shield, Send } from 'lucide-react';

interface AuditTimelineProps {
  disputeId: string;
}

const stageIcons: Record<string, React.ReactNode> = {
  ingest: <Clock size={12} className="text-ink-faint" />,
  queue: <Clock size={12} className="text-signal" />,
  upload: <Upload size={12} className="text-signal" />,
  extract: <FileText size={12} className="text-signal" />,
  score: <Shield size={12} className="text-signal" />,
  'copilot.explain': <Shield size={12} className="text-signal" />,
  'copilot.investigate': <Shield size={12} className="text-signal" />,
  'copilot.recommend': <Shield size={12} className="text-signal" />,
  'copilot.draft': <FileText size={12} className="text-signal" />,
  'human.approved': <CheckCircle size={12} className="text-money" />,
  'contest.queued': <Send size={12} className="text-money" />,
  'contest.submitted': <Send size={12} className="text-money" />,
};

const stageLabels: Record<string, string> = {
  ingest: 'Dispute received',
  queue: 'Processing queued',
  upload: 'Document uploaded',
  extract: 'Document reviewed',
  score: 'Case evaluated',
  'copilot.explain': 'Case explanation',
  'copilot.investigate': 'Document check',
  'copilot.recommend': 'Recommendation',
  'copilot.draft': 'Response drafted',
  'human.approved': 'Response approved',
  'contest.queued': 'Contest queued',
  'contest.submitted': 'Contest submitted',
};

// Stages that are infrastructure/internal — hide if failed
const HIDE_FAILED_STAGES = new Set([
  'copilot.explain',
  'copilot.investigate',
  'copilot.recommend',
  'copilot.draft',
  'queue',
]);

export default function AuditTimeline({ disputeId }: AuditTimelineProps) {
  const { data: audit, isLoading } = useAudit(disputeId);
  const [expanded, setExpanded] = useState<number | null>(null);

  if (isLoading) {
    return (
      <div className="border border-line rounded-lg bg-surface-raised p-4">
        <div className="h-4 w-32 bg-surface-sunken rounded animate-pulse" />
      </div>
    );
  }

  const allEntries = audit?.entries ?? [];

  // Filter: hide failed infrastructure entries, keep successful ones and non-infrastructure failures
  const entries = allEntries.filter(entry => {
    if (entry.success === false && HIDE_FAILED_STAGES.has(entry.stage)) {
      return false;
    }
    return true;
  });

  if (entries.length === 0) {
    return (
      <div className="border border-line rounded-lg bg-surface-raised p-4">
        <div className="text-[11px] font-semibold text-ink-faint uppercase tracking-wider mb-2">
          Case Activity
        </div>
        <div className="text-[12px] text-ink-muted">No activity recorded yet.</div>
      </div>
    );
  }

  return (
    <div className="border border-line rounded-lg bg-surface-raised">
      <div className="px-4 py-3 border-b border-line">
        <div className="text-[11px] font-semibold text-ink-faint uppercase tracking-wider">
          Case Activity
        </div>
        <div className="text-[11px] text-ink-muted mt-0.5">
          A timeline of what happened with this dispute.
        </div>
      </div>

      <div className="p-4">
        <div className="space-y-0">
          {entries.map((entry, i) => {
            const loggedDate = new Date(entry.logged_at);
            const now = new Date();
            const diffMs = now.getTime() - loggedDate.getTime();
            const diffSec = Math.floor(diffMs / 1000);
            const diffMin = Math.floor(diffSec / 60);
            const diffHr = Math.floor(diffMin / 60);
            let relativeTime: string;
            if (diffSec < 60) relativeTime = `${diffSec}s ago`;
            else if (diffMin < 60) relativeTime = `${diffMin}m ago`;
            else if (diffHr < 24) relativeTime = `${diffHr}h ago`;
            else relativeTime = loggedDate.toLocaleDateString('en-IN', { month: 'short', day: 'numeric' });
            const absoluteTime = loggedDate.toLocaleTimeString('en-IN', {
              hour: '2-digit',
              minute: '2-digit',
              second: '2-digit',
              timeZoneName: 'short',
            });
            const isExpanded = expanded === entry.log_id;
            const icon = stageIcons[entry.stage] ?? <Clock size={12} className="text-ink-faint" />;
            const label = stageLabels[entry.stage] ?? entry.stage;

            return (
              <div key={entry.log_id} className="flex gap-3 relative">
                {/* Timeline line */}
                {i < entries.length - 1 && (
                  <div className="absolute left-[7px] top-5 w-px h-[calc(100%-20px)] bg-line" />
                )}

                {/* Icon */}
                <div className="w-4 h-4 flex items-center justify-center shrink-0 mt-0.5 relative z-10 bg-surface-raised">
                  {icon}
                </div>

                {/* Content */}
                <div className="flex-1 pb-3">
                  <button
                    onClick={() => setExpanded(isExpanded ? null : entry.log_id)}
                    className="flex items-center gap-2 w-full text-left"
                  >
                    <span className="text-[12px] text-ink-muted font-data" title={`${absoluteTime}\n${entry.logged_at}`}>{relativeTime}</span>
                    <span className="text-[12px] text-ink font-medium">{label}</span>
                    {entry.success === false && (
                      <span className="text-[10px] text-ink-faint italic">(retrying)</span>
                    )}
                    {Object.keys(entry.detail).length > 0 && (
                      isExpanded ? <ChevronDown size={11} className="text-ink-faint ml-auto" /> : <ChevronRight size={11} className="text-ink-faint ml-auto" />
                    )}
                  </button>

                  {/* Expanded detail */}
                  {isExpanded && Object.keys(entry.detail).length > 0 && (
                    <div className="mt-1.5 pl-2 border-l border-line space-y-0.5">
                      {Object.entries(entry.detail).map(([key, value]) => (
                        <div key={key} className="text-[10px] text-ink-muted">
                          <span className="text-ink-faint">{key.replace(/_/g, ' ')}: </span>
                          <span className="font-data text-ink">{typeof value === 'object' ? JSON.stringify(value) : String(value)}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
