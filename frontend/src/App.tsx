/**
 * App — root component with routing, auth, and React Query provider.
 */

import { useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AppShell from './components/layout/AppShell';
import { useSSEUpdates } from './hooks/useSSEUpdates';
import LoginPage from './components/auth/LoginPage';
import OverviewPage from './components/overview/OverviewPage';
import DisputeQueue from './components/disputes/DisputeQueue';
import CaseWorkspace from './components/case/CaseWorkspace';
import EvidencePage from './components/overview/EvidencePage';
import AuditPage from './components/overview/AuditPage';
import IntegrationPage from './components/settings/IntegrationPage';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

export default function App() {
  const [authenticated, setAuthenticated] = useState(() => {
    return localStorage.getItem('shield-auth') === 'true';
  });

  if (!authenticated) {
    return (
      <QueryClientProvider client={queryClient}>
        <LoginPage onLogin={() => {
          localStorage.setItem('shield-auth', 'true');
          setAuthenticated(true);
        }} />
      </QueryClientProvider>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <SSEBridge />
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/disputes" element={<DisputeQueue />} />
            <Route path="/disputes/:id" element={<CaseWorkspace />} />
            <Route path="/evidence" element={<EvidencePage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/settings" element={<IntegrationPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function SSEBridge() {
  useSSEUpdates();
  return null;
}


