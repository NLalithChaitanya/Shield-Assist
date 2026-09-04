/**
 * Sidebar — primary navigation.
 * Compact, information-dense, matches the Design Direction spec exactly.
 */

import { NavLink, useLocation } from 'react-router-dom';
import {
  LayoutDashboard,
  AlertTriangle,
  Clock,
  CheckCircle,
  FileText,
  Shield,
  Activity,
  Settings,
  ChevronRight,
  BarChart3,
} from 'lucide-react';

const navSections = [
  {
    label: null,
    items: [
      { to: '/', icon: LayoutDashboard, label: 'Overview' },
    ],
  },
  {
    label: 'DISPUTES',
    items: [
      { to: '/disputes', icon: BarChart3, label: 'All disputes' },
      { to: '/disputes?filter=act_now', icon: AlertTriangle, label: 'Act now' },
      { to: '/disputes?filter=review', icon: Clock, label: 'Review' },
      { to: '/disputes?filter=low_priority', icon: CheckCircle, label: 'Low priority' },
    ],
  },
  {
    label: 'EVIDENCE',
    items: [
      { to: '/evidence', icon: FileText, label: 'Documents' },
    ],
  },
  {
    label: 'AUDIT',
    items: [
      { to: '/audit', icon: Activity, label: 'Activity' },
    ],
  },
];

export default function Sidebar() {
  const location = useLocation();

  return (
    <nav
      className="flex flex-col h-screen w-[220px] border-r border-line bg-surface-raised shrink-0"
      aria-label="Primary navigation"
    >
      {/* Brand */}
      <div className="flex items-center gap-2 px-5 py-4 border-b border-line">
        <Shield size={18} className="text-signal" />
        <span className="text-[13px] font-semibold tracking-wide text-ink uppercase">
          Shield Assist
        </span>
      </div>

      {/* Navigation sections */}
      <div className="flex-1 overflow-y-auto py-2 px-3">
        {navSections.map((section) => (
          <div key={section.label ?? 'root'} className="mb-3">
            {section.label && (
              <div className="px-2 py-1.5 text-[10px] font-semibold tracking-wider text-ink-faint uppercase">
                {section.label}
              </div>
            )}
            {section.items.map((item) => {
              const Icon = item.icon;
              const isActive = item.to === '/'
                ? location.pathname === '/'
                : item.to.includes('?')
                  ? location.pathname + location.search === item.to
                  : location.pathname.startsWith(item.to);
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={`group flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-[13px] transition-colors duration-150 ${
                    isActive
                      ? 'bg-signal-bg text-signal font-medium'
                      : 'text-ink-muted hover:bg-surface-overlay hover:text-ink'
                  }`}
                >
                  <Icon size={15} strokeWidth={isActive ? 2 : 1.5} />
                  <span className="flex-1">{item.label}</span>
                  {isActive && <ChevronRight size={12} className="opacity-40" />}
                </NavLink>
              );
            })}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="border-t border-line px-3 py-2">          <NavLink
          to="/settings"
          className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-[13px] text-ink-muted hover:bg-surface-overlay hover:text-ink transition-colors"
        >
          <Settings size={15} strokeWidth={1.5} />
          <span>Integration</span>
        </NavLink>
      </div>
    </nav>
  );
}
