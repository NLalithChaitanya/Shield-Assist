/**
 * AppShell — the application container.
 * Sidebar left, header top, content center.
 */

import { useState, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';

export default function AppShell() {
  const [darkMode, setDarkMode] = useState(() => {
    return localStorage.getItem('shield-dark-mode') === 'true';
  });

  useEffect(() => {
    document.documentElement.classList.toggle('dark', darkMode);
    localStorage.setItem('shield-dark-mode', String(darkMode));
  }, [darkMode]);

  return (
    <div className="flex h-screen overflow-hidden bg-surface">
      <Sidebar />
      <div className="flex flex-col flex-1 min-w-0">
        <Header darkMode={darkMode} onToggleDarkMode={() => setDarkMode(!darkMode)} />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
