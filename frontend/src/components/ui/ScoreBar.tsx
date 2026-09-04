/**
 * ScoreBar — horizontal bar for completeness/quality/consistency.
 * Design Direction §9: bars, never donuts or radial gauges.
 */

interface ScoreBarProps {
  label: string;
  value: number; // 0-100
  color?: 'signal' | 'money' | 'urgent' | 'caution' | 'default';
  showLabel?: boolean;
  animate?: boolean;
}

const colorMap = {
  signal: { bar: 'bg-signal', text: 'text-signal' },
  money: { bar: 'bg-money', text: 'text-money' },
  urgent: { bar: 'bg-urgent', text: 'text-urgent' },
  caution: { bar: 'bg-caution', text: 'text-caution' },
  default: { bar: 'bg-ink', text: 'text-ink' },
};

function getScoreColor(value: number) {
  if (value >= 90) return 'money';
  if (value >= 70) return 'caution';
  return 'urgent';
}

export default function ScoreBar({ label, value, color, showLabel = true, animate = true }: ScoreBarProps) {
  const resolvedColor = color || getScoreColor(value);
  const { bar, text } = colorMap[resolvedColor];
  const clamped = Math.min(100, Math.max(0, value));

  return (
    <div className="flex items-center gap-3">
      {showLabel && (
        <span className="text-[12px] text-ink-muted w-[90px] shrink-0">{label}</span>
      )}
      <div className="flex-1 h-[6px] bg-surface-sunken rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${bar} ${animate ? 'score-bar-animate' : ''}`}
          style={{ width: `${clamped}%` }}
        />
      </div>
      <span className={`font-data text-[12px] font-medium ${text} w-[38px] text-right`}>
        {Math.round(clamped)}%
      </span>
    </div>
  );
}
