/**
 * PriorityBadge — label-based priority indicator.
 * Design Direction §6: use label + text, not just colored dots.
 */

interface PriorityBadgeProps {
  priority: 'act_now' | 'review' | 'low_priority';
  compact?: boolean;
}

const config = {
  act_now: {
    label: 'ACT NOW',
    classes: 'bg-urgent-bg text-urgent border-urgent/20',
  },
  review: {
    label: 'REVIEW SOON',
    classes: 'bg-caution-bg text-caution border-caution/20',
  },
  low_priority: {
    label: 'LOW PRIORITY',
    classes: 'bg-surface-sunken text-ink-muted border-line',
  },
};

export default function PriorityBadge({ priority, compact = false }: PriorityBadgeProps) {
  const { label, classes } = config[priority];

  return (
    <span
      className={`inline-flex items-center font-data font-medium border rounded ${classes} ${
        compact ? 'text-[9px] px-1.5 py-0.5' : 'text-[10px] px-2 py-0.5'
      }`}
    >
      {label}
    </span>
  );
}
