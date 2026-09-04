/**
 * GateDecision — first-class gate component.
 * Redesigned for merchants: plain language first, technical details collapsed.
 * Passed = READY. Blocked = REVIEW REQUIRED (reframed as "needs attention").
 */

import { useState } from 'react';
import { CheckCircle, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react';

interface GateDecisionProps {
  action: string;
  passed: boolean;
  failingConditions: string[];
  scores?: {
    win_probability: number | null;
    completeness: number | null;
    consistency: number | null;
  } | null;
}

// Map raw technical condition strings to plain-language merchant text
function translateCondition(condition: string): string {
  const lower = condition.toLowerCase();
  if (lower.includes('win_probability') || lower.includes('win probability')) {
    const match = lower.match(/([\d.]+)%/);
    const pct = match ? `${match[1]}%` : '';
    return `Estimated win chance (${pct}) is below the minimum threshold to auto-prepare a response.`;
  }
  if (lower.includes('completeness')) {
    return "Your evidence collection is incomplete \u2014 Razorpay requires more documents for this dispute type.";
  }
  if (lower.includes('consistency')) {
    return "Some details in your uploaded documents do not match each other \u2014 please review the flagged issues.";
  }
  if (lower.includes('contradiction') || lower.includes('unresolved')) {
    return "There are unresolved contradictions between your documents that need to be checked before we can proceed.";
  }
  if (lower.includes('missing') && lower.includes('evidence')) {
    return "Required documents are missing \u2014 see the \"Missing documents\" section above for what to upload.";
  }
  // Fallback: keep original but wrap nicely
  return condition;
}

export default function GateDecision({ action: _action, passed, failingConditions, scores }: GateDecisionProps) {
  const [showTechDetails, setShowTechDetails] = useState(false);

  if (passed) {
    return (
      <div className="border border-money/20 rounded-lg bg-money-bg p-4">
        <div className="flex items-center gap-2 mb-2">
          <CheckCircle size={16} className="text-money" />
          <span className="text-[13px] font-semibold text-money uppercase tracking-wide">
            Ready for your approval
          </span>
        </div>
        <p className="text-[12px] text-money/80 ml-6">
          All evidence checks passed. You can review the draft response and approve it for submission.
        </p>
      </div>
    );
  }

  // Blocked state — plain language first
  return (
    <div className="border border-urgent/20 rounded-lg bg-urgent-bg p-4">
      <div className="flex items-center gap-2 mb-2">
        <AlertTriangle size={16} className="text-urgent" />
        <span className="text-[13px] font-semibold text-urgent uppercase tracking-wide">
          Needs your attention
        </span>
      </div>
      <p className="text-[12px] text-ink-muted mb-3 ml-6">
        We cannot prepare a response for this dispute yet. Here is what needs to be resolved:
      </p>

      {/* Plain-language failing conditions */}
      <ul className="space-y-2 ml-6 mb-3">
        {failingConditions.map((condition, i) => (
          <li key={i} className="text-[12px] text-ink leading-relaxed">
            {"\u2022"} {translateCondition(condition)}
          </li>
        ))}
      </ul>

      {/* Collapsible technical details for power users */}
      <div className="ml-6">
        <button
          onClick={() => setShowTechDetails(!showTechDetails)}
          className="flex items-center gap-1.5 text-[11px] text-ink-faint hover:text-ink-muted transition-colors"
        >
          {showTechDetails ? <ChevronDown size={11} /> : <ChevronRight size={11} />}
          Show technical details
        </button>

        {showTechDetails && (
          <div className="mt-2 p-3 bg-surface-sunken rounded-md border border-line/50 space-y-1.5">
            {failingConditions.map((condition, i) => (
              <div key={i} className="text-[11px] font-data text-ink-muted">
                {condition}
              </div>
            ))}
            {scores?.win_probability != null && (
              <div className="text-[11px] font-data text-ink-muted pt-1.5 border-t border-line/50">
                {"win_probability: " + Math.round(scores.win_probability * 100) + "%"}
                {scores.completeness != null && (", completeness: " + scores.completeness + "%")}
                {scores.consistency != null && (", consistency: " + scores.consistency + "%")}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
