/**
 * ProcessingStatus — explicit operational states for document processing.
 * Design Direction §14: communicate real work, not "AI is thinking..."
 */

import { Check, Loader2, Minus } from 'lucide-react';

interface ProcessingStep {
  label: string;
  status: 'done' | 'active' | 'pending';
}

interface ProcessingStatusProps {
  fileName: string;
  steps: ProcessingStep[];
}

export default function ProcessingStatus({ fileName, steps }: ProcessingStatusProps) {
  return (
    <div className="border border-line rounded-lg bg-surface-raised p-4">
      <p className="text-[12px] font-medium text-ink mb-3">
        Processing <span className="font-data">{fileName}</span>
      </p>
      <div className="space-y-2">
        {steps.map((step) => (
          <div key={step.label} className="flex items-center gap-2.5">
            <div className="w-4 h-4 flex items-center justify-center shrink-0">
              {step.status === 'done' && <Check size={12} className="text-money" />}
              {step.status === 'active' && <Loader2 size={12} className="text-signal animate-spin" />}
              {step.status === 'pending' && <Minus size={12} className="text-ink-faint" />}
            </div>
            <span className={`text-[12px] ${step.status === 'active' ? 'text-ink font-medium' : step.status === 'done' ? 'text-ink-muted' : 'text-ink-faint'}`}>
              {step.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
