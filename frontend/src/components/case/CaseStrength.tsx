/**
 * CaseStrength — the central evidence-health concept.
 * Redesigned: supporting detail, not the headline.
 * Shows strength number + qualifier, with breakdown collapsed by default.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
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

  return (
    <div className="border border-line rounded-lg bg-surface-raised p-5">
      {/* Primary: Strength number + qualifier, win probability */}
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
              <span className={`text-[13px] font-semibold ${winProb.color}`}>
                {winProb.text}
              </span>
              <span className="font-data text-[14px] text-ink-muted">
                ({Math.round(scores.win_probability * 100)}%)
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Collapsed breakdown toggle */}
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

      {/* Expanded breakdown */}
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
