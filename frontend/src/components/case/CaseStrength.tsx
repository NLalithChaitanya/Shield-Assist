/**
 * CaseStrength — the central evidence-health concept.
 * Redesigned: gate status is the primary message; score is supporting detail.
 * Shows gate status banner, strength number + qualifier, breakdown.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, ShieldAlert, ShieldCheck } from 'lucide-react';
import { caseStrength } from '../../lib/types';
import type { DisputeDetail } from '../../lib/types';
import ScoreBar from '../ui/ScoreBar';

interface CaseStrengthProps {
  dispute: DisputeDetail;
}

function getStrengthLabel(strength: number): { text: string; color: string } {
  if (strength >= 80) return { text: 'Strong — ready to prepare', color: 'text-money' };
  if (strength >= 60) return { text: 'Moderate — some improvements possible', color: 'text-caution' };
  if (strength >= 40) return { text: 'Weak — needs more evidence', color: 'text-urgent' };
  return { text: 'Very weak — significant gaps', color: 'text-urgent' };
}

function getWinProbabilityLabel(prob: number): { text: string; color: string } {
  if (prob >= 0.7) return { text: 'High', color: 'text-money' };
  if (prob >= 0.4) return { text: 'Moderate', color: 'text-caution' };
  return { text: 'Low', color: 'text-urgent' };
}

function getBlockedReasons(dispute: DisputeDetail): string[] {
  const reasons: string[] = [];
  const g = dispute.gate;
  if (!g || g.passed) return reasons;
  const failing = g.failing_conditions || [];
  const contradictionCount = dispute.scores?.contradiction_flags?.length ?? 0;
  if (contradictionCount > 0) {
    reasons.push(`${contradictionCount} evidence contradiction${contradictionCount !== 1 ? 's' : ''} must be resolved`);
  }
  const missingSlots = dispute.scores?.missing_required_slots ?? [];
  if (missingSlots.length > 0) {
    reasons.push(`${missingSlots.length} required document${missingSlots.length !== 1 ? 's' : ''} missing`);
  }
  // Only mention probability if it's the ONLY failing condition (not when contradictions exist)
  const hasProbFail = failing.some(f => f.includes('win_probability'));
  if (hasProbFail && contradictionCount === 0 && missingSlots.length === 0) {
    reasons.push('Win probability below threshold');
  }
  return reasons;
}

export default function CaseStrength({ dispute }: CaseStrengthProps) {
  const scores = dispute.scores;
  const strength = caseStrength(scores);
  const [breakdownExpanded, setBreakdownExpanded] = useState(false);

  if (!scores) {
    return (
      <div className="border border-line rounded-lg bg-surface-raised p-4">
        <div className="text-[11px] font-semibold text-ink-faint uppercase tracking-wide mb-1">
          Case Strength
        </div>
        <div className="text-[13px] text-ink-muted">Awaiting scoring...</div>
      </div>
    );
  }

  const completeness = scores.completeness ?? 0;
  const quality = scores.quality ?? 0;
  const consistency = scores.consistency ?? 0;
  const strengthInfo = getStrengthLabel(strength);
  const winProb = scores.win_probability != null ? getWinProbabilityLabel(scores.win_probability) : null;
  const isBlocked = dispute.gate != null && !dispute.gate.passed;
  const isReady = dispute.gate != null && dispute.gate.passed;
  const contradictionCount = scores.contradiction_flags?.length ?? 0;
  const blockedReasons = isBlocked ? getBlockedReasons(dispute) : [];

  return (
    <div className="border border-line rounded-lg bg-surface-raised p-5">
      {/* ─── Gate Status Banner ─── */}
      {isBlocked && (
        <div className="flex items-center gap-2.5 px-3 py-2.5 mb-4 rounded-md bg-urgent-bg border border-urgent/15">
          <ShieldAlert size={16} className="text-urgent shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-semibold text-urgent">
              Response blocked — cannot prepare until resolved
            </div>
            {blockedReasons.length > 0 && (
              <div className="text-[11px] text-ink-muted mt-0.5">
                {blockedReasons.join('. ')}.
              </div>
            )}
          </div>
        </div>
      )}

      {isReady && (
        <div className="flex items-center gap-2.5 px-3 py-2.5 mb-4 rounded-md bg-money-bg border border-money/15">
          <ShieldCheck size={16} className="text-money shrink-0" />
          <div className="text-[12px] font-semibold text-money">
            Ready to prepare response
          </div>
        </div>
      )}

      {/* ─── Primary: Strength number + qualifier, win probability ─── */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-baseline gap-2">
            <span className="font-data text-[32px] font-bold text-ink leading-none count-animate">
              {strength}
            </span>
            <span className="font-data text-[14px] text-ink-faint">/100</span>
          </div>
          <div className={`text-[12px] font-medium mt-1 ${strengthInfo.color}`}>
            {strengthInfo.text}
          </div>
        </div>

        {scores.win_probability != null && winProb && (
          <div className="text-right">
            <div className="text-[11px] text-ink-muted">
              Estimated chance of winning
            </div>
            <div className="flex items-baseline gap-1 justify-end">
              <span className={`text-[13px] font-semibold ${isBlocked && winProb.color === 'text-money' ? 'text-caution' : winProb.color}`}>
                {winProb.text}{' '}
              </span>
              <span className="font-data text-[14px] text-ink-muted">
                ({Math.round(scores.win_probability * 100)}%)
              </span>
            </div>
          </div>
        )}
      </div>

      {/* ─── Probability override warning (when high prob + blocked) ─── */}
      {isBlocked && scores.win_probability != null && scores.win_probability >= 0.7 && contradictionCount > 0 && (
        <div className="mt-3 px-3 py-2 rounded-md bg-caution-bg border border-caution/15">
          <div className="text-[11px] text-caution">
            High win probability does not override evidence contradictions.
            Resolve contradictions before Shield Assist can prepare a response.
          </div>
        </div>
      )}

      {/* ─── Collapsed breakdown toggle ─── */}
      <button
        onClick={() => setBreakdownExpanded(!breakdownExpanded)}
        className="flex items-center gap-1.5 mt-3 pt-3 border-t border-line w-full text-left hover:bg-surface-overlay -mx-1 px-1 rounded transition-colors"
      >
        {breakdownExpanded ? (
          <ChevronDown size={12} className="text-ink-faint" />
        ) : (
          <ChevronRight size={12} className="text-ink-faint" />
        )}
        <span className="text-[11px] text-ink-muted">
          Score breakdown
        </span>
      </button>

      {/* ─── Expanded breakdown ─── */}
      {breakdownExpanded && (
        <div className="space-y-2.5 pt-3">
          <ScoreBar label="Completeness" value={completeness} />
          <ScoreBar label="Quality" value={quality} />
          <ScoreBar label="Consistency" value={consistency} />
        </div>
      )}
    </div>
  );
}
