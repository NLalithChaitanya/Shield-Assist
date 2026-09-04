/**
 * DisputeQueue — primary working surface.
 * Design Direction §6: each row exposes priority, ID, reason, amount, deadline, strength, gate.
 * Amount and deadline are visually dominant.
 */

import { useState, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ArrowUpDown, Filter, Clock } from 'lucide-react';
import { useDisputes } from '../../hooks/useQueries';
import {
  formatPaise, getPriorityLabel, reasonCodeLabel, timeUntil, caseStrength,
} from '../../lib/types';
import type { DisputeListItem } from '../../lib/types';
import PriorityBadge from '../ui/PriorityBadge';
import EmptyState from '../ui/EmptyState';

type SortKey = 'priority' | 'amount' | 'deadline' | 'strength';

export default function DisputeQueue() {
  const { data: disputes, isLoading } = useDisputes();
  const [searchParams] = useSearchParams();
  const filterParam = searchParams.get('filter');
  const [sortKey, setSortKey] = useState<SortKey>('priority');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  // Filter
  const filtered = useMemo(() => {
    if (!disputes) return [];
    if (!filterParam) return disputes;
    return disputes.filter(d => getPriorityLabel(d) === filterParam);
  }, [disputes, filterParam]);

  // Sort
  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let cmp = 0;
      switch (sortKey) {
        case 'priority':
          cmp = (a.priority ?? 0) - (b.priority ?? 0);
          break;
        case 'amount':
          cmp = a.amount_paise - b.amount_paise;
          break;
        case 'deadline': {
          const aTime = a.respond_by ? new Date(a.respond_by).getTime() : Infinity;
          const bTime = b.respond_by ? new Date(b.respond_by).getTime() : Infinity;
          cmp = aTime - bTime;
          break;
        }
        case 'strength':
          cmp = caseStrength(a.scores) - caseStrength(b.scores);
          break;
      }
      return sortDir === 'desc' ? -cmp : cmp;
    });
  }, [filtered, sortKey, sortDir]);

  const filterLabel = filterParam === 'act_now' ? 'Act now' : filterParam === 'review' ? 'Review' : filterParam === 'low_priority' ? 'Low priority' : 'All';

  return (
    <div className="max-w-[1000px] mx-auto px-8 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-[18px] font-semibold text-ink">Disputes</h1>
          <p className="text-[12px] text-ink-muted mt-0.5">
            {filterLabel} — {sorted.length} dispute{sorted.length !== 1 ? 's' : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Filter size={14} className="text-ink-faint" />
        </div>
      </div>

      {/* Column headers */}
      <div className="grid grid-cols-[1fr_120px_100px_80px_100px] gap-3 px-4 py-2 border-b border-line text-[10px] font-semibold text-ink-faint uppercase tracking-wider">
        <button onClick={() => toggleSort('priority')} className="flex items-center gap-1 text-left">
          Dispute <ArrowUpDown size={10} />
        </button>
        <button onClick={() => toggleSort('amount')} className="flex items-center gap-1">
          Amount <ArrowUpDown size={10} />
        </button>
        <button onClick={() => toggleSort('deadline')} className="flex items-center gap-1">
          Deadline <ArrowUpDown size={10} />
        </button>
        <button onClick={() => toggleSort('strength')} className="flex items-center gap-1">
          Strength <ArrowUpDown size={10} />
        </button>
        <span>Gate</span>
      </div>

      {/* Rows */}
      {isLoading ? (
        <div className="space-y-1 mt-1">
          {[1, 2, 3, 4, 5].map(i => (
            <div key={i} className="h-14 bg-surface-sunken rounded-lg animate-pulse" />
          ))}
        </div>
      ) : sorted.length === 0 ? (
        <EmptyState type="no_disputes" />
      ) : (
        <div className="space-y-0.5 mt-1">
          {sorted.map(d => (
            <DisputeRow key={d.dispute_id} dispute={d} />
          ))}
        </div>
      )}
    </div>
  );
}

function DisputeRow({ dispute: d }: { dispute: DisputeListItem }) {
  const priority = getPriorityLabel(d);
  const strength = caseStrength(d.scores);

  return (
    <Link
      to={`/disputes/${d.dispute_id}`}
      className="grid grid-cols-[1fr_120px_100px_80px_100px] gap-3 items-center px-4 py-3 rounded-lg bg-surface-raised border border-line hover:border-signal/30 transition-colors"
    >
      {/* Dispute info */}
      <div className="min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <PriorityBadge priority={priority} compact />
          <span className="font-data text-[12px] text-ink font-medium">#{d.dispute_id.slice(-4)}</span>
        </div>
        <div className="text-[12px] text-ink-muted truncate">
          {reasonCodeLabel(d.reason_code)}
        </div>
      </div>

      {/* Amount */}
      <div className="font-data text-[13px] font-semibold text-ink">{formatPaise(d.amount_paise)}</div>

      {/* Deadline */}
      <div className="flex items-center gap-1">
        {d.past_deadline ? (
          <span className="text-[11px] font-data text-urgent font-medium">Past due</span>
        ) : (
          <>
            <Clock size={11} className="text-ink-faint" />
            <span className="text-[11px] font-data text-ink-muted">{timeUntil(d.respond_by)}</span>
          </>
        )}
      </div>

      {/* Strength */}
      <div className="font-data text-[13px] font-medium text-ink">{strength}</div>

      {/* Gate */}
      <div className="text-[11px]">
        {d.gate_action === 'prepare' ? (
          <span className="text-money font-medium">PREPARE</span>
        ) : d.gate_action === 'review' ? (
          <span className="text-caution font-medium">REVIEW</span>
        ) : d.gate_action === 'low_priority' ? (
          <span className="text-ink-faint">LOW</span>
        ) : (
          <span className="text-ink-faint">—</span>
        )}
      </div>
    </Link>
  );
}
