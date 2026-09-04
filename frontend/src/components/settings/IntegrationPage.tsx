/**
 * IntegrationPage — Razorpay Integration settings.
 *
 * Displays real-time integration status, connection health,
 * and a "Test Connection" button. Never exposes credentials.
 */

import { useState, useCallback } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  Shield,
  CheckCircle,
  AlertTriangle,
  XCircle,
  Loader2,
  Zap,
  RefreshCw,
} from 'lucide-react';
import {
  getRazorpayHealth,
  getRazorpayStatus,
  testRazorpayConnection,
  simulateDispute,
} from '../../lib/api';
import type {
  IntegrationStatus,
  IntegrationStatusCheck,
  RazorpayHealth,
  SimulateDisputeResponse,
} from '../../lib/types';

// ─── Status icon mapping ───────────────────────────────────────────

function StatusIcon({ status }: { status: 'pass' | 'warn' | 'fail' }) {
  switch (status) {
    case 'pass':
      return <CheckCircle size={16} className="text-green-500" />;
    case 'warn':
      return <AlertTriangle size={16} className="text-amber-500" />;
    case 'fail':
      return <XCircle size={16} className="text-red-500" />;
  }
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    healthy: 'bg-green-50 text-green-700 border-green-200',
    degraded: 'bg-amber-50 text-amber-700 border-amber-200',
    unhealthy: 'bg-red-50 text-red-700 border-red-200',
    test: 'bg-blue-50 text-blue-700 border-blue-200',
    live: 'bg-red-50 text-red-700 border-red-200',
  };

  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-[11px] font-medium border ${
        colors[status] || 'bg-gray-50 text-gray-700 border-gray-200'
      }`}
    >
      {status.toUpperCase()}
    </span>
  );
}

// ─── Check row component ───────────────────────────────────────────

function CheckRow({ check }: { check: IntegrationStatusCheck }) {
  return (
    <div className="flex items-start gap-3 py-2.5">
      <div className="mt-0.5 shrink-0">
        <StatusIcon status={check.status} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-ink">{check.name}</div>
        <div className="text-[12px] text-ink-muted mt-0.5">{check.label}</div>
      </div>
    </div>
  );
}

// ─── Main component ────────────────────────────────────────────────

export default function IntegrationPage() {
  const queryClient = useQueryClient();

  // ── Fetch integration status ─────────────────────────────────────
  const {
    data: status,
    isLoading: statusLoading,
    error: statusError,
  } = useQuery<IntegrationStatus>({
    queryKey: ['razorpay-status'],
    queryFn: getRazorpayStatus,
    refetchOnWindowFocus: true,
    staleTime: 30_000,
  });

  // ── Test Connection mutation ─────────────────────────────────────
  const testMutation = useMutation<RazorpayHealth>({
    mutationFn: testRazorpayConnection,
    onSuccess: () => {
      // Refresh status after successful test
      queryClient.invalidateQueries({ queryKey: ['razorpay-status'] });
    },
  });

  // ── Simulate dispute ─────────────────────────────────────────────
  const simulateMutation = useMutation<SimulateDisputeResponse, Error, string>({
    mutationFn: (scenario: string) =>
      simulateDispute(scenario as 'good' | 'bad' | 'incomplete'),
  });

  const handleTestConnection = useCallback(() => {
    testMutation.mutate();
  }, [testMutation]);

  const handleSimulate = useCallback(
    (scenario: string) => {
      simulateMutation.mutate(scenario);
    },
    [simulateMutation],
  );

  // ── Render ───────────────────────────────────────────────────────
  return (
    <div className="max-w-[800px] mx-auto px-8 py-8">
      {/* Page header */}
      <div className="flex items-center gap-3 mb-6">
        <Shield size={20} className="text-signal" />
        <h1 className="text-[18px] font-semibold text-ink">
          Razorpay Integration
        </h1>
      </div>

      {/* Status overview card */}
      <div className="bg-surface-raised rounded-lg border border-line p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <h2 className="text-[14px] font-semibold text-ink">
              Connection Status
            </h2>
            {status && <StatusBadge status={status.overall} />}
          </div>

          <button
            onClick={handleTestConnection}
            disabled={testMutation.isPending}
            className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-md text-[12px] font-medium
                       bg-signal text-white hover:bg-signal-hover transition-colors
                       disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {testMutation.isPending ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Zap size={14} />
            )}
            Test Connection
          </button>
        </div>

        {/* Test result banner */}
        {testMutation.isSuccess && testMutation.data && (
          <div
            className={`rounded-md px-3.5 py-2.5 mb-4 text-[12px] ${
              testMutation.data.authenticated
                ? 'bg-green-50 text-green-800 border border-green-200'
                : 'bg-amber-50 text-amber-800 border border-amber-200'
            }`}
          >
            {testMutation.data.authenticated
              ? `Connected to Razorpay ${testMutation.data.mode === 'test' ? 'Test Mode' : 'Live Mode'}`
              : testMutation.data.message}
          </div>
        )}

        {testMutation.isError && (
          <div className="rounded-md px-3.5 py-2.5 mb-4 text-[12px] bg-red-50 text-red-800 border border-red-200">
            {testMutation.error.message || 'Connection test failed'}
          </div>
        )}

        {/* Loading state */}
        {statusLoading && (
          <div className="flex items-center gap-2 text-[13px] text-ink-muted py-4">
            <Loader2 size={16} className="animate-spin" />
            Loading integration status...
          </div>
        )}

        {/* Error state */}
        {statusError && (
          <div className="text-[13px] text-red-600 py-4">
            Failed to load integration status. Is the backend running?
          </div>
        )}

        {/* Check list */}
        {status && (
          <div className="divide-y divide-line">
            {status.checks.map((check) => (
              <CheckRow key={check.name} check={check} />
            ))}
          </div>
        )}
      </div>

      {/* Contest API limitation notice */}
      <div className="bg-surface-raised rounded-lg border border-line p-5 mb-6">
        <h3 className="text-[13px] font-semibold text-ink mb-2">
          Contest API — Test Mode Limitation
        </h3>
        <p className="text-[12px] text-ink-muted leading-relaxed">
          Contest submission cannot be fully end-to-end verified in Razorpay
          Test Mode because sandbox disputes are not merchant-created. The
          Contest API client is implemented and will function correctly in Live
          Mode. In Test Mode, the system builds and validates the contest
          request but reports the platform limitation clearly.
        </p>
      </div>

      {/* Dispute Simulator */}
      <div className="bg-surface-raised rounded-lg border border-line p-5">
        <h3 className="text-[13px] font-semibold text-ink mb-1">
          Dispute Simulator
        </h3>
        <p className="text-[12px] text-ink-muted mb-4">
          Generate simulated Razorpay dispute webhooks for demo and testing.
          Simulated disputes enter the same pipeline as real webhooks.
        </p>

        <div className="flex gap-2">
          {(['good', 'bad', 'incomplete'] as const).map((scenario) => (
            <button
              key={scenario}
              onClick={() => handleSimulate(scenario)}
              disabled={simulateMutation.isPending}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[12px] font-medium
                         border border-line text-ink-muted hover:bg-surface-overlay hover:text-ink
                         transition-colors disabled:opacity-50"
            >
              {simulateMutation.isPending && simulateMutation.variables === scenario ? (
                <Loader2 size={12} className="animate-spin" />
              ) : (
                <RefreshCw size={12} />
              )}
              {scenario.charAt(0).toUpperCase() + scenario.slice(1)} case
            </button>
          ))}
        </div>

        {/* Simulation result */}
        {simulateMutation.isSuccess && simulateMutation.data && (
          <div className="mt-4 rounded-md bg-blue-50 border border-blue-200 px-3.5 py-2.5">
            <div className="text-[12px] font-medium text-blue-800 mb-1">
              Simulated dispute created: {simulateMutation.data.scenario}
            </div>
            <div className="text-[11px] text-blue-700">
              Dispute ID: {simulateMutation.data.dispute_id} | Status:{' '}
              {simulateMutation.data.status} | Documents:{' '}
              {simulateMutation.data.documents.length}
            </div>
          </div>
        )}

        {simulateMutation.isError && (
          <div className="mt-4 rounded-md bg-red-50 border border-red-200 px-3.5 py-2.5 text-[12px] text-red-700">
            {simulateMutation.error.message || 'Simulation failed'}
          </div>
        )}
      </div>

      {/* Environment info */}
      {status && (
        <div className="mt-6 text-[11px] text-ink-faint">
          Environment: <StatusBadge status={status.mode} /> | Provider:{' '}
          {status.provider} | Overall: {status.overall}
        </div>
      )}
    </div>
  );
}
