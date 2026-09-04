/**
 * Header — top bar with Evidence Integrity indicator and user area.
 * Persistent across all views. Communicates the product's trust signal.
 */

import { Moon, Sun } from 'lucide-react';
import { useHealth } from '../../hooks/useQueries';

interface HeaderProps {
  darkMode: boolean;
  onToggleDarkMode: () => void;
}

export default function Header({ darkMode, onToggleDarkMode }: HeaderProps) {
  const { data: health } = useHealth();

  return (
    <header className="flex items-center justify-between h-12 px-5 border-b border-line bg-surface-raised shrink-0">
      {/* Left: breadcrumb area (populated by pages) */}
      <div className="flex items-center gap-3">
        {/* Evidence Integrity indicator */}
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-md bg-money-bg">
          <div className="w-1.5 h-1.5 rounded-full bg-money live-pulse" />
          <span className="text-[11px] font-medium text-money">
            Evidence Integrity
          </span>
        </div>
      </div>

      {/* Right: status + dark mode + user */}
      <div className="flex items-center gap-3">
        {/* Worker status */}
        {health && (
          <div className="flex items-center gap-1.5 text-[11px] text-ink-faint">
            <div className={`w-1.5 h-1.5 rounded-full ${health.workers_running ? 'bg-money' : 'bg-urgent'}`} />
            <span className="font-data">{health.version}</span>
          </div>
        )}

        {/* Dark mode toggle */}
        <button
          onClick={onToggleDarkMode}
          className="p-1.5 rounded-md text-ink-muted hover:bg-surface-overlay hover:text-ink transition-colors"
          aria-label={darkMode ? 'Switch to light mode' : 'Switch to dark mode'}
        >
          {darkMode ? <Sun size={15} /> : <Moon size={15} />}
        </button>

        {/* User avatar */}
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-signal-bg flex items-center justify-center">
            <span className="text-[11px] font-semibold text-signal">M</span>
          </div>
        </div>
      </div>
    </header>
  );
}
