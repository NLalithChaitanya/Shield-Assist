/**
 * TanStack Query hooks for Shield Assist data fetching.
 * Caching, refetching, and optimistic updates.
 */

import { useQuery } from '@tanstack/react-query';
import { fetchDisputes, fetchDispute, fetchAudit, fetchMetrics, checkHealth } from '../lib/api';

export function useDisputes(limit = 50) {
  return useQuery({
    queryKey: ['disputes', limit],
    queryFn: () => fetchDisputes(limit),
    // SSE is primary update path (useSSEUpdates invalidates on events).
    // 30s polling is a safety net for missed events or reconnect gaps.
    refetchInterval: 30000,
    staleTime: 10000,
  });
}

export function useDispute(id: string | null) {
  return useQuery({
    queryKey: ['dispute', id],
    queryFn: () => fetchDispute(id!),
    enabled: !!id,
    // SSE invalidates on scores/gate/draft events for this dispute.
    // 15s polling is a safety net.
    refetchInterval: 15000,
    staleTime: 5000,
  });
}

export function useAudit(id: string | null) {
  return useQuery({
    queryKey: ['audit', id],
    queryFn: () => fetchAudit(id!),
    enabled: !!id,
    // SSE invalidates on draft/approve events for this dispute.
    // 30s polling is a safety net.
    refetchInterval: 30000,
  });
}

export function useMetrics() {
  return useQuery({
    queryKey: ['metrics'],
    queryFn: fetchMetrics,
    // Metrics don't come via SSE; 10s polling is the primary path.
    refetchInterval: 10000,
  });
}

export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: checkHealth,
    refetchInterval: 30000,
  });
}
