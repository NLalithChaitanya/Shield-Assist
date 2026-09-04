/**
 * UploadEvidence — drag-and-drop upload.
 * Design Direction §13: reduce friction, system infers category.
 * §14: communicate real work, not "AI is thinking..."
 */

import { useState, useRef, useCallback } from 'react';
import { Upload, FileText, Check, RefreshCw, X } from 'lucide-react';
import { uploadDocument } from '../../lib/api';

interface UploadEvidenceProps {
  disputeId: string;
}

interface UploadState {
  status: 'idle' | 'uploading' | 'processing' | 'done' | 'error';
  fileName?: string;
  error?: string;
  progress?: string;
}

const PROCESSING_STEPS = [
  'Uploading document',
  'Reading document',
  'Extracting facts',
  'Checking consistency',
  'Updating case',
];

export default function UploadEvidence({ disputeId }: UploadEvidenceProps) {
  const [state, setState] = useState<UploadState>({ status: 'idle' });
  const [currentStep, setCurrentStep] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(async (file: File) => {
    setState({ status: 'uploading', fileName: file.name });
    setCurrentStep(0);

    // Simulate processing steps while upload + job runs
    const stepInterval = setInterval(() => {
      setCurrentStep(prev => Math.min(prev + 1, PROCESSING_STEPS.length - 1));
    }, 2000);

    try {
      setState({ status: 'processing', fileName: file.name });
      await uploadDocument(disputeId, file, 'others', 'clear');
      clearInterval(stepInterval);
      setCurrentStep(PROCESSING_STEPS.length - 1);
      setState({ status: 'done', fileName: file.name });

      // Reset after 3 seconds
      setTimeout(() => {
        setState({ status: 'idle' });
        setCurrentStep(0);
      }, 3000);
    } catch (err) {
      clearInterval(stepInterval);
      setState({
        status: 'error',
        fileName: file.name,
        error: err instanceof Error ? err.message : 'Upload failed',
      });
    }
  }, [disputeId]);

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

  if (state.status === 'processing' || state.status === 'uploading') {
    return (
      <div className="border border-signal/20 rounded-lg bg-signal-bg/30 p-4">
        <div className="flex items-center gap-2 mb-3">
          <FileText size={14} className="text-signal" />
          <span className="text-[12px] font-medium text-ink">
            Processing <span className="font-data">{state.fileName}</span>
          </span>
        </div>
        <div className="space-y-1.5">
          {PROCESSING_STEPS.map((step, i) => (
            <div key={step} className="flex items-center gap-2">
              <div className="w-3.5 h-3.5 flex items-center justify-center shrink-0">
                {i < currentStep ? (
                  <Check size={10} className="text-money" />
                ) : i === currentStep ? (
                  <RefreshCw size={10} className="text-signal animate-spin" />
                ) : (
                  <div className="w-1.5 h-1.5 rounded-full bg-ink-faint" />
                )}
              </div>
              <span className={`text-[11px] ${i === currentStep ? 'text-ink font-medium' : i < currentStep ? 'text-ink-muted' : 'text-ink-faint'}`}>
                {step}
              </span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (state.status === 'done') {
    return (
      <div className="border border-money/20 rounded-lg bg-money-bg p-4">
        <div className="flex items-center gap-2">
          <Check size={14} className="text-money" />
          <span className="text-[12px] font-medium text-money">
            <span className="font-data">{state.fileName}</span> processed
          </span>
        </div>
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div className="border border-urgent/20 rounded-lg bg-urgent-bg p-4">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <X size={14} className="text-urgent" />
            <span className="text-[12px] font-medium text-urgent">Processing paused</span>
          </div>
          <button
            onClick={() => setState({ status: 'idle' })}
            className="text-[11px] text-urgent underline"
          >
            Retry
          </button>
        </div>
        <p className="text-[11px] text-urgent/80">{state.error}</p>
        <p className="text-[11px] text-ink-muted mt-1">Your original document is unchanged.</p>
      </div>
    );
  }

  // Idle: drag and drop zone
  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      onClick={() => fileInputRef.current?.click()}
      className={`border-2 border-dashed rounded-lg p-6 text-center cursor-pointer transition-colors ${
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
      <Upload size={20} className="mx-auto mb-2 text-ink-faint" />
      <p className="text-[12px] text-ink-muted mb-1">
        Drop a document here or <span className="text-signal">choose file</span>
      </p>
      <p className="text-[10px] text-ink-faint">
        PDF · PNG · JPG
      </p>
      <div className="mt-3 text-[10px] text-ink-faint space-y-0.5">
        <div>Shield Assist will:</div>
        <div>✓ classify the document</div>
        <div>✓ extract relevant facts</div>
        <div>✓ check quality</div>
        <div>✓ compare with existing evidence</div>
      </div>
    </div>
  );
}
