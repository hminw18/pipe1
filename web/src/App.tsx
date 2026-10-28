import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'

import { OrgAdminPage, PlatformPage } from './admin/AdminPages'
import { ApiError } from './api/client'
import { AppShell } from './components/AppShell'
import { BusinessesPage, ProjectsPage, ReportsPage } from './pages/ListPages'
import { BrandMark, ConfirmEmailPage, InvitePage, LoginPage, ResetPasswordPage } from './pages/PublicPages'
import { SettingsPage } from './pages/SettingsPage'
import { ReauthProvider } from './session/Reauth'
import { useSession } from './session/context'
import { SessionProvider } from './session/SessionProvider'
import { ReportWorkspace } from './workspace/ReportWorkspace'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // 401/403/404 are answers, not transient failures.
      retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
})

const PLATFORM_PATHS = ['/platform', '/settings']

function RequireSession({ children, roles }: { children: ReactNode; roles?: string[] }) {
  const { me, loading } = useSession()
  const location = useLocation()
  if (loading) return <div className="page">불러오는 중…</div>
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  const home = me.role === 'platform_admin' ? '/platform' : '/projects'
  if (roles && !roles.includes(me.role)) return <Navigate to={home} replace />
  // Platform admins belong to no organization and have no project data
  // (docs/Web_Auth_Spec.md §2): keep them on the operator and account pages.
  if (me.role === 'platform_admin' && !PLATFORM_PATHS.some((path) => location.pathname.startsWith(path))) {
    return <Navigate to="/platform" replace />
  }
  return <>{children}</>
}

function LegalPage({ title }: { title: string }) {
  return (
    <main className="auth-page">
      <section className="auth-card">
        <BrandMark />
        <h1>{title}</h1>
        <p>정식 서비스 시작 전에 법률 검토를 거쳐 게시됩니다.</p>
      </section>
    </main>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <SessionProvider>
          <ReauthProvider>
            <Routes>
              <Route path="/login" element={<LoginPage />} />
              <Route path="/invite" element={<InvitePage />} />
              <Route path="/reset-password" element={<ResetPasswordPage />} />
              <Route path="/confirm-email" element={<ConfirmEmailPage />} />
              <Route path="/legal/terms" element={<LegalPage title="이용약관" />} />
              <Route path="/legal/privacy" element={<LegalPage title="개인정보 처리방침" />} />
              <Route
                element={
                  <RequireSession>
                    <AppShell />
                  </RequireSession>
                }
              >
                <Route path="/projects" element={<ProjectsPage />} />
                <Route path="/projects/:projectId" element={<BusinessesPage />} />
                <Route path="/businesses/:businessId" element={<ReportsPage />} />
                <Route path="/reports/:reportId" element={<ReportWorkspace />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route
                  path="/admin"
                  element={
                    <RequireSession roles={['org_admin']}>
                      <OrgAdminPage />
                    </RequireSession>
                  }
                />
                <Route
                  path="/platform"
                  element={
                    <RequireSession roles={['platform_admin']}>
                      <PlatformPage />
                    </RequireSession>
                  }
                />
              </Route>
              <Route path="*" element={<Navigate to="/projects" replace />} />
            </Routes>
          </ReauthProvider>
        </SessionProvider>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
