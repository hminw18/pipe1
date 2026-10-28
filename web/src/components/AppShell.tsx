import { Link, NavLink, Outlet } from 'react-router-dom'

import { useBusiness, useBusinesses, useProject, useProjects, useReport, useReports } from '../api/queries'
import { useSession } from '../session/context'
import { useSelection } from './useSelection'

function NavSection({ title, className, children }: { title: string; className?: string; children: React.ReactNode }) {
  return (
    <section className="nav-section">
      <h2>{title}</h2>
      <ul className={className}>{children}</ul>
    </section>
  )
}

function NavItem({ to, selected, label, aside }: { to: string; selected: boolean; label: string; aside?: React.ReactNode }) {
  return (
    <li>
      <Link to={to} className={selected ? 'selected' : undefined} aria-current={selected ? 'page' : undefined}>
        <span>{label}</span>
        {aside}
      </Link>
    </li>
  )
}

export function AppShell() {
  const { me, logout } = useSession()
  const { projectId, businessId, reportId } = useSelection()
  const projects = useProjects(me?.role !== 'platform_admin')
  const businesses = useBusinesses(projectId)
  const reports = useReports(businessId)
  const project = useProject(projectId)
  const business = useBusiness(businessId)
  const report = useReport(reportId)
  const home = me?.role === 'platform_admin' ? '/platform' : '/projects'

  return (
    <div className="shell">
      <header className="topbar">
        <Link to={home} className="brand">
          <img src="/pipe1-mark.svg" alt="" width={20} height={18} />
          Pipe1
        </Link>
        <nav className="breadcrumbs" aria-label="경로">
          {me?.role !== 'platform_admin' && <Link to="/projects">프로젝트</Link>}
          {project.data && (
            <>
              <span aria-hidden="true">/</span>
              <Link to={`/projects/${project.data.id}`}>{project.data.project_name}</Link>
            </>
          )}
          {business.data && (
            <>
              <span aria-hidden="true">/</span>
              <Link to={`/businesses/${business.data.id}`}>
                {business.data.business_code} {business.data.business_name}
              </Link>
            </>
          )}
          {report.data && (
            <>
              <span aria-hidden="true">/</span>
              <span className="current mono">{report.data.report_number}</span>
            </>
          )}
        </nav>
        <div className="user-menu">
          {me?.role === 'org_admin' && <NavLink to="/admin">조직 관리</NavLink>}
          {me?.role === 'platform_admin' && <NavLink to="/platform">운영 관리</NavLink>}
          <NavLink to="/settings">{me?.email}</NavLink>
          <button className="link" onClick={() => void logout()}>
            로그아웃
          </button>
        </div>
      </header>
      <div className="shell-body">
        <aside className="sidebar">
          {me?.role === 'platform_admin' ? (
            <p className="sidebar-note">운영 관리자 계정은 조직의 프로젝트에 접근하지 않습니다.</p>
          ) : (
            <>
              <NavSection title="프로젝트">
                {projects.data?.map((p) => (
                  <NavItem
                    key={p.id}
                    to={`/projects/${p.id}`}
                    selected={p.id === projectId}
                    label={p.project_name}
                    aside={
                      p.visibility === 'private' ? (
                        <span className="tag">비공개</span>
                      ) : (
                        <span className="count">{p.business_count}</span>
                      )
                    }
                  />
                ))}
              </NavSection>
              <NavSection title="사업">
                {!projectId && <li className="nav-empty">프로젝트를 선택하세요</li>}
                {businesses.data?.map((b) => (
                  <NavItem
                    key={b.id}
                    to={`/businesses/${b.id}`}
                    selected={b.id === businessId}
                    label={`${b.business_code} ${b.business_name}`}
                    aside={<span className="count">{b.report_count}</span>}
                  />
                ))}
              </NavSection>
              <NavSection title="보고서" className="reports">
                {!businessId && <li className="nav-empty">사업을 선택하세요</li>}
                {reports.data?.map((r) => (
                  <NavItem
                    key={r.report_group_id}
                    to={`/reports/${r.latest_report_id}`}
                    selected={report.data?.report_group_id === r.report_group_id}
                    label={`${r.report_number} / ${r.pipe_number}`}
                  />
                ))}
              </NavSection>
            </>
          )}
        </aside>
        <main className="main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
