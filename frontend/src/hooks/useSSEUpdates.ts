/**
 * useSSEUpdates — connects to the backend's /events SSE stream
 * and invalidates React Query caches on live events.
 *
 * SSE is the primary update path. Polling (useQueries refetchInterval)
 * is the safety net for missed events or reconnect gaps.
 */

import { useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { subscribeSSE } from '../lib/api';

export function useSSEUpdates() {
  const queryClient = useQueryClient();
  const subscribedRef = useRef(false);

  useEffect(() => {
    // Prevent double-subscription in React strict mode
    if (subscribedRef.current) return;
    subscribedRef.current = true;

    const unsubscribe = subscribeSSE((eventType, data) => {
      switch (eventType) {
        case 'dispute.received':
          // New dispute arrived — refresh the dispute list and metrics
          queryClient.invalidateQueries({ queryKey: ['disputes'] });
          queryClient.invalidateQueries({ queryKey: ['metrics'] });
          break;

        case 'document.uploaded':
          // Document uploaded — refresh the specific dispute detail + list
          if (data.dispute_id) {
            queryClient.invalidateQueries({ queryKey: ['dispute', data.dispute_id] });
          }
          queryClient.invalidateQueries({ queryKey: ['disputes'] });
          break;

        case 'document.extracted':
          // OCR facts extracted — refresh dispute detail (scores will update too)
          if (data.dispute_id) {
            queryClient.invalidateQueries({ queryKey: ['dispute', data.dispute_id] });
            queryClient.invalidateQueries({ queryKey: ['audit', data.dispute_id] });
          }
          break;

        case 'scores.computed':
          // Scores updated — refresh dispute detail + list (strength/gate may change)
          if (data.dispute_id) {
            queryClient.invalidateQueries({ queryKey: ['dispute', data.dispute_id] });
          }
          queryClient.invalidateQueries({ queryKey: ['disputes'] });
          queryClient.invalidateQueries({ queryKey: ['metrics'] });
          break;

        case 'gate.decision':
          // Gate decision changed — refresh everything dispute-related
          if (data.dispute_id) {
            queryClient.invalidateQueries({ queryKey: ['dispute', data.dispute_id] });
          }
          queryClient.invalidateQueries({ queryKey: ['disputes'] });
          break;

        case 'draft.ready':
          // Draft generated — refresh dispute detail + audit
          if (data.dispute_id) {
            queryClient.invalidateQueries({ queryKey: ['dispute', data.dispute_id] });
            queryClient.invalidateQueries({ queryKey: ['audit', data.dispute_id] });
          }
          break;

        case 'human.approved':
          // Human approved — refresh dispute detail + audit
          if (data.dispute_id) {
            queryClient.invalidateQueries({ queryKey: ['dispute', data.dispute_id] });
            queryClient.invalidateQueries({ queryKey: ['audit', data.dispute_id] });
          }
          break;

        case 'contest.submitted':
          // Contest submitted — refresh dispute detail + list + audit
          if (data.dispute_id) {
            queryClient.invalidateQueries({ queryKey: ['dispute', data.dispute_id] });
            queryClient.invalidateQueries({ queryKey: ['audit', data.dispute_id] });
          }
          queryClient.invalidateQueries({ queryKey: ['disputes'] });
          break;

        default:
          // Unknown event type — no-op
          break;
      }
    });

    return () => {
      unsubscribe();
      subscribedRef.current = false;
    };
  }, [queryClient]);
}
