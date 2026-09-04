/**
 * EmptyState — intentional empty states, not blank screens.
 * Design Direction §35: explain the product, don't fill space.
 */

import { FileQuestion, Shield, AlertCircle } from 'lucide-react';

interface EmptyStateProps {
  type: 'no_disputes' | 'no_evidence' | 'no_contradictions' | 'no_draft' | 'error';
  title?: string;
  description?: string;
  action?: React.ReactNode;
}

const configs = {
  no_disputes: {
    icon: Shield,
    title: "You're clear for now.",
    description: 'No disputes currently require action. New Razorpay disputes will appear here automatically.',
  },
  no_evidence: {
    icon: FileQuestion,
    title: 'No evidence uploaded yet.',
    description: 'Upload the documents you have. Shield Assist will classify and analyze them.',
  },
  no_contradictions: {
    icon: Shield,
    title: 'No contradictions detected.',
    description: 'The available evidence is internally consistent.',
  },
  no_draft: {
    icon: FileQuestion,
    title: 'No response drafted yet.',
    description: 'The copilot will draft a response when evidence is sufficient.',
  },
  error: {
    icon: AlertCircle,
    title: 'Something requires attention.',
    description: 'The previous action could not be completed.',
  },
};

export default function EmptyState({ type, title, description, action }: EmptyStateProps) {
  const config = configs[type];
  const Icon = config.icon;

  return (
    <div className="flex flex-col items-center justify-center py-16 px-8 text-center">
      <div className="w-10 h-10 rounded-full bg-surface-sunken flex items-center justify-center mb-4">
        <Icon size={20} className="text-ink-faint" />
      </div>
      <h3 className="text-[14px] font-medium text-ink mb-1">{title || config.title}</h3>
      <p className="text-[12px] text-ink-muted max-w-[280px]">{description || config.description}</p>
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
