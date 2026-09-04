/**
 * OverviewPage — merchant command center.
 * Design Direction §5: "What should I work on right now?"
 * NOT an ML metrics dashboard. Business metrics, not model metrics.
 */

import { Link } from 'react-router-dom';
import { ArrowRight, Clock, AlertTriangle, CheckCircle } from 'lucide-react';
import { useDisputes, useMetrics } from '../../hooks/useQueries';
import { formatPaise, getPriorityLabel, reasonCodeLabel, timeUntil, caseStrength } from '../../lib/types';
import PriorityBadge from '../ui/PriorityBadge';

export default function OverviewPage() {
  const { data: disputes, isLoading } = useDisputes();
  const { data: metrics } = useMetrics();

  // Group by priority
  const actNow = disputes?.filter(d => getPriorityLabel(d) === 'act_now') ?? [];
  const review = disputes?.filter(d => getPriorityLabel(d) === 'review') ?? [];
  const lowPriority = disputes?.filter(d => getPriorityLabel(d) === 'low_priority') ?? [];

  const actNowAmount = actNow.reduce((sum, d) => sum + d.amount_paise, 0);
  const reviewAmount = review.reduce((sum, d) => sum + d.amount_paise, 0);
  const lowAmount = lowPriority.reduce((sum, d) => sum + d.amount_paise, 0);

  return (
    <div className="max-w-[900px] mx-auto px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-[22px] font-semibold text-ink mb-1">Dispute operations</h1>
        <p className="text-[13px] text-ink-muted">
          {disputes && disputes.length > 0
            ? `${disputes.length} dispute${disputes.length !== 1 ? 's' : ''} in the system.`
            : 'No disputes currently require action.'}
        </p>
      </div>

      {/* Priority summary */}
      {disputes && disputes.length > 0 && (
        <div className="grid grid-cols-3 gap-4 mb-8">
          <SummaryCard
            icon={<AlertTriangle size={14} className="text-urgent" />}
            label="Act now"
            count={actNow.length}
            amount={actNowAmount}
            color="urgent"
          />
          <SummaryCard
            icon={<Clock size={14} className="text-caution" />}
            label="Review soon"
            count={review.length}
            amount={reviewAmount}
            color="caution"
          />
          <SummaryCard
            icon={<CheckCircle size={14} className="text-ink-faint" />}
            label="Low priority"
            count={lowPriority.length}
            amount={lowAmount}
            color="default"
          />
        </div>
      )}

      {/* Active queue */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-[13px] font-semibold text-ink uppercase tracking-wide">Active disputes</h2>
          <Link
            to="/disputes"
            className="text-[12px] text-signal hover:underline flex items-center gap-1"
          >
            All disputes <ArrowRight size={12} />
          </Link>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            {[1, 2, 3].map(i => (
              <div key={i} className="h-16 bg-surface-sunken rounded-lg animate-pulse" />
            ))}
          </div>
        ) : disputes && disputes.length > 0 ? (
          <div className="space-y-1">
            {disputes.slice(0, 8).map(d => (
              <Link
                key={d.dispute_id}
                to={`/disputes/${d.dispute_id}`}
                className="flex items-center gap-4 px-4 py-3 rounded-lg bg-surface-raised border border-line hover:border-signal/30 transition-colors group"
              >
                <PriorityBadge priority={getPriorityLabel(d)} compact />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-data text-[12px] text-ink font-medium">
                      #{d.dispute_id.slice(-4)}
                    </span>
                    <span className="text-[12px] text-ink-muted">{reasonCodeLabel(d.reason_code)}</span>
                  </div>
                  <div className="text-[11px] text-ink-faint mt-0.5">
                    {timeUntil(d.respond_by)} remaining
                  </div>
                </div>
                <div className="text-right">
                  <div className="font-data text-[13px] font-medium text-ink">{formatPaise(d.amount_paise)}</div>
                  {d.scores && (
                    <div className="text-[11px] text-ink-faint mt-0.5">
                      Strength {caseStrength(d.scores)}
                    </div>
                  )}
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <div className="text-center py-12 text-[13px] text-ink-muted">
            You're clear for now.
          </div>
        )}
      </div>

      {/* Business metrics bar */}
      {metrics && metrics.total_disputes > 0 && (
        <div className="border-t border-line pt-4 mt-4">
          <div className="flex items-center gap-6 text-[11px] text-ink-faint">
            <span>Auto-prepared: <span className="font-data text-ink">{metrics.auto_prepared}</span></span>
            <span>Human review: <span className="font-data text-ink">{metrics.human_review}</span></span>
            <span>Recoverable: <span className="font-data text-money">{formatPaise(metrics.likely_recoverable_paise)}</span></span>
            <span className="ml-auto italic">{metrics.note}</span>
          </div>
        </div>
      )}
    </div>
  );
}

function SummaryCard({ icon, label, count, amount }: {
  icon: React.ReactNode;
  label: string;
  count: number;
  amount: number;
  color?: string;
}) {
  return (
    <div className={`border border-line rounded-lg p-4 bg-surface-raised`}>
      <div className="flex items-center gap-2 mb-2">
        {icon}
        <span className="text-[11px] font-semibold text-ink-muted uppercase tracking-wide">{label}</span>
      </div>
      <div className="font-data text-[20px] font-semibold text-ink">{count}</div>
      <div className="font-data text-[12px] text-ink-muted mt-0.5">
        {formatPaise(amount)} at risk
      </div>
    </div>
  );
}
