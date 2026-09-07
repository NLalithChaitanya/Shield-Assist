/**
 * ReplaceEvidenceModal — modal for replacing a specific evidence document.
 * Shows the current issue, allows uploading a replacement, and tracks processing state.
 * Uses existing uploadDocument API under the hood, routed through the replace endpoint.
 */

import { useState, useRef, useCallback } from 'react';
import { X, Upload, RefreshCw, Check, AlertTriangle } from 'lucide-react';
import { replaceEvidence } from '../../lib/api';
import { SLOT_NAMES } from '../../lib/types';
import type { ContradictionFlag } from '../../lib/types';

interface ReplaceEvidenceModalProps {
  disputeId: string;
  slot: string;
  flag: ContradictionFlag;
  onClose: () => void;
  onComplete?: () => void;
}

type ReplaceState = 'idle' | 'uploading' | 'processing' | 'done' | 'error';

export default function ReplaceEvidenceModal({
  disputeId,
  slot,
  flag,
  onClose,
  onComplete,
}: ReplaceEvidenceModalProps) {
  const [state, setState] = useState<ReplaceState>('idle');
  const [error, setError] = useState('');
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const slotName = SLOT_NAMES[slot] ?? slot.replace(/_/g, ' ');

  const handleFile = useCallback(async (file: File) => {
    setState('uploading');
    setError('');

    try {
      setState('processing');
      await replaceEvidence(disputeId, slot, file, 'clear');
      setState('done');
      // Notify parent that replacement is complete
      // The SSE events will trigger React Query invalidation,
      // which will cause the parent to re-render with updated data.
      onComplete?.();
    } catch (err) {
      setState('error');
      setError(err instanceof Error ? err.message : 'Upload failed');
    }
  }, [disputeId, slot, onComplete]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }, [handleFile]);

  const onFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  }, [handleFile]);

  // Prevent duplicate uploads while processing
  const isProcessing = state === 'uploading' || state === 'processing';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={onClose}>
      <div
        className="bg-surface-raised rounded-lg shadow-lg border border-line max-w-sm w-full mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-line">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-caution-bg flex items-center justify-center">
              <AlertTriangle size={12} className="text-caution" />
            </div>
            <span className="text-[13px] font-semibold text-ink">Replace evidence</span>
          </div>
          <button
            onClick={onClose}
            disabled={isProcessing}
            className="text-ink-muted hover:text-ink text-[18px] leading-none px-1 disabled:opacity-40"
          >
            ×
          </button>
        </div>

        {/* Content */}
        <div className="p-4">
          {/* Issue context */}
          <div className="mb-3 p-2.5 rounded-md bg-caution-bg border border-caution/15">
            <div className="text-[11px] font-medium text-caution mb-0.5">
              Replacing: {slotName}
            </div>
            <div className="text-[10px] text-ink-muted">
              {flag.rule_type === 'amount_mismatch' && 'The amounts in this document don\'t match. Upload a corrected document.'}
              {flag.rule_type === 'date_order_violation' && 'The date in this document is inconsistent. Upload a corrected document.'}
              {flag.rule_type === 'name_mismatch' && 'The customer name doesn\'t match. Upload a corrected document.'}
              {flag.rule_type === 'order_id_mismatch' && 'The order number doesn\'t match. Upload a corrected document.'}
              {!['amount_mismatch', 'date_order_violation', 'name_mismatch', 'order_id_mismatch'].includes(flag.rule_type) && 'Upload a corrected document for this evidence slot.'}
            </div>
          </div>

          {/* Idle: upload zone */}
          {state === 'idle' && (
            <div
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-lg p-5 text-center cursor-pointer transition-colors ${
                dragOver ? 'border-signal bg-signal-bg/30' : 'border-line hover:border-signal/30'
              }`}
            >
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.png,.jpg,.jpeg"
                onChange={onFileSelect}
                className="hidden"
              />
              <Upload size={18} className="mx-auto mb-2 text-ink-faint" />
              <p className="text-[12px] text-ink-muted mb-1">
                Drop corrected document or <span className="text-signal">choose file</span>
              </p>
              <p className="text-[10px] text-ink-faint">PDF · PNG · JPG</p>
            </div>
          )}

          {/* Uploading / Processing */}
          {(state === 'uploading' || state === 'processing') && (
            <div className="border border-signal/20 rounded-lg bg-signal-bg/30 p-4">
              <div className="flex items-center gap-2 mb-2">
                <RefreshCw size={14} className="text-signal animate-spin" />
                <span className="text-[12px] font-medium text-ink">
                  {state === 'uploading' ? 'Uploading…' : 'Processing…'}
                </span>
              </div>
              <div className="text-[11px] text-ink-muted ml-[22px] space-y-0.5">
                {state === 'processing' && (
                  <>
                    <div>Extracting evidence and recalculating your case…</div>
                    <div className="text-[10px] text-ink-faint">
                      Existing facts for {slotName} will be replaced.
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {/* Done */}
          {state === 'done' && (
            <div className="border border-money/20 rounded-lg bg-money-bg p-4">
              <div className="flex items-center gap-2">
                <Check size={14} className="text-money" />
                <div>
                  <div className="text-[12px] font-medium text-money">
                    Evidence replaced ✓
                  </div>
                  <div className="text-[10px] text-ink-muted mt-0.5">
                    Your case is being re-evaluated. Scores will update automatically.
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Error */}
          {state === 'error' && (
            <div className="border border-urgent/20 rounded-lg bg-urgent-bg p-4">
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <X size={14} className="text-urgent" />
                  <span className="text-[12px] font-medium text-urgent">Upload failed</span>
                </div>
                <button
                  onClick={() => { setState('idle'); setError(''); }}
                  className="text-[11px] text-urgent underline"
                >
                  Retry
                </button>
              </div>
              <p className="text-[11px] text-urgent/80 ml-[22px]">{error}</p>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-line flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={isProcessing}
            className="text-[12px] font-medium text-ink-muted hover:text-ink border border-line rounded px-3 py-1.5 hover:bg-surface-overlay transition-colors disabled:opacity-40"
          >
            {state === 'done' ? 'Close' : 'Cancel'}
          </button>
        </div>
      </div>
    </div>
  );
}
