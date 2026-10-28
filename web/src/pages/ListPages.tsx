import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { del, get, patch, post, type Schemas } from '../api/client'
import { keys, useBusiness, useBusinesses, useProject, useProjects, useReports } from '../api/queries'
import { Modal } from '../components/Modal'
import { formatDate, formatDateTime } from '../format'
import { errorText } from '../errors'
import { useWithReauth } from '../session/context'
import { useMe } from '../session/context'

function useAction() {
  const [error, setError] = useState<string | null>(null)
  const run = async (action: () => Promise<unknown>) => {
    setError(null)
    try {
      await action()
    } catch (failure) {
      setError(errorText(failure))
    }
  }
  return { error, run }
}

function EmptyRow({ colSpan, text }: { colSpan: number; text: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="left faint">
        {text}
      </td>
    </tr>
  )
}

// --- Projects -----------------------------------------------------------------------

export function ProjectsPage() {
  const projects = useProjects()
  const queryClient = useQueryClient()
  const withReauth = useWithReauth()
  const me = useMe()
  const navigate = useNavigate()
  const { error, run } = useAction()
  const [name, setName] = useState('')
  const [visibility, setVisibility] = useState<'private' | 'org'>('org')
  const [transfer, setTransfer] = useState<Schemas['ProjectOut'] | null>(null)
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.projects })

  return (
    <div className="page">
      <div className="page-head">
        <h1>프로젝트</h1>
        <span className="count">{projects.data?.length ?? ''}</span>
      </div>
      <form
        className="create-row"
        onSubmit={(event: FormEvent) => {
          event.preventDefault()
          void run(async () => {
            const project = await post<Schemas['ProjectOut']>('/api/projects', { project_name: name, visibility })
            setName('')
            await refresh()
            navigate(`/projects/${project.id}`)
          })
        }}
      >
        <input
          className="wide"
          placeholder="새 프로젝트 이름"
          aria-label="프로젝트 이름"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
        <select aria-label="공개 범위" value={visibility} onChange={(e) => setVisibility(e.target.value as 'private' | 'org')}>
          <option value="org">조직 공개</option>
          <option value="private">비공개 (본인 및 관리자만)</option>
        </select>
        <button className="primary">만들기</button>
      </form>
      {error && <p className="error">{error}</p>}
      <div className="tbl-wrap">
      <table className="list">
        <thead>
          <tr>
            <th className="left">프로젝트</th>
            <th>공개 범위</th>
            <th>소유자</th>
            <th>사업</th>
            <th>생성일</th>
            <th className="actions-col" aria-label="작업" />
          </tr>
        </thead>
        <tbody>
          {projects.data?.length === 0 && <EmptyRow colSpan={6} text="프로젝트가 없습니다. 위에서 새 프로젝트를 만드세요." />}
          {projects.data?.map((p) => (
            <tr key={p.id}>
              <td className="wide">
                <Link to={`/projects/${p.id}`}>{p.project_name}</Link>
              </td>
              <td>{p.visibility === 'org' ? '조직' : '비공개'}</td>
              <td>{p.owner_email}</td>
              <td className="num">{p.business_count}</td>
              <td className="num">{formatDate(p.created_at)}</td>
              <td>
                <div className="row-actions">
                {p.can_manage && (
                  <>
                    <button
                      className="link"
                      onClick={() => {
                        const next = window.prompt('프로젝트 이름', p.project_name)
                        if (next) void run(async () => { await patch(`/api/projects/${p.id}`, { project_name: next }); await refresh() })
                      }}
                    >
                      이름 변경
                    </button>
                    <button
                      className="link"
                      onClick={() =>
                        void run(async () => {
                          await patch(`/api/projects/${p.id}`, { visibility: p.visibility === 'org' ? 'private' : 'org' })
                          await refresh()
                        })
                      }
                    >
                      {p.visibility === 'org' ? '비공개로' : '조직 공개로'}
                    </button>
                  </>
                )}
                <button
                  className="link"
                  onClick={() =>
                    void run(async () => {
                      await post(`/api/projects/${p.id}/duplicate`, { visibility: p.visibility })
                      await refresh()
                    })
                  }
                >
                  복제
                </button>
                {me.role === 'org_admin' && (
                  <button className="link" onClick={() => setTransfer(p)}>
                    소유자 변경
                  </button>
                )}
                {p.can_manage && (
                  <button
                    className="link danger"
                    onClick={() => {
                      if (!window.confirm(`'${p.project_name}' 프로젝트와 모든 영상·보고서를 삭제할까요?`)) return
                      void run(async () => {
                        await withReauth(() => del(`/api/projects/${p.id}`))
                        await refresh()
                      })
                    }}
                  >
                    삭제
                  </button>
                )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <p className="hint">행에 마우스를 올리면 작업이 표시됩니다. 삭제는 비밀번호 재확인이 필요합니다.</p>
      {transfer && <TransferDialog project={transfer} onClose={() => { setTransfer(null); void refresh() }} />}
    </div>
  )
}

function TransferDialog({ project, onClose }: { project: Schemas['ProjectOut']; onClose: () => void }) {
  const withReauth = useWithReauth()
  const users = useQuery({
    queryKey: ['admin-users'],
    queryFn: () => get<Schemas['UserSummary'][]>('/api/admin/users'),
  }).data
  const [owner, setOwner] = useState('')
  const { error, run } = useAction()
  return (
    <Modal title="소유자 변경" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          void run(async () => {
            await withReauth(() => post(`/api/projects/${project.id}/transfer`, { owner_user_id: owner }))
            onClose()
          })
        }}
      >
        <label>
          새 소유자
          <select value={owner} onChange={(e) => setOwner(e.target.value)} required>
            <option value="">선택</option>
            {users
              ?.filter((u) => u.status === 'active' && u.id !== project.owner_user_id)
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.email}
                </option>
              ))}
          </select>
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary">변경</button>
      </form>
    </Modal>
  )
}

// --- Businesses ---------------------------------------------------------------------

const BUSINESS_FIELDS: [keyof Schemas['BusinessCreate'], string][] = [
  ['business_code', '사업 코드'],
  ['business_name', '사업명'],
  ['client', '발주처'],
  ['business_start_date', '시작일'],
  ['business_end_date', '종료일'],
]

function BusinessDialog({
  initial,
  onSubmit,
  onClose,
}: {
  initial?: Schemas['BusinessOut']
  onSubmit: (values: Schemas['BusinessCreate']) => Promise<void>
  onClose: () => void
}) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(BUSINESS_FIELDS.map(([field]) => [field, (initial?.[field] as string | null) ?? ''])),
  )
  const { error, run } = useAction()
  return (
    <Modal title={initial ? '사업 수정' : '사업 추가'} onClose={onClose}>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          void run(async () => {
            await onSubmit({
              business_code: values.business_code,
              business_name: values.business_name,
              client: values.client || null,
              business_start_date: values.business_start_date || null,
              business_end_date: values.business_end_date || null,
            })
            onClose()
          })
        }}
      >
        {BUSINESS_FIELDS.map(([field, label]) => (
          <label key={field}>
            {label}
            <input
              value={values[field]}
              onChange={(e) => setValues({ ...values, [field]: e.target.value })}
              required={field === 'business_code' || field === 'business_name'}
            />
          </label>
        ))}
        {error && <p className="error">{error}</p>}
        <button className="primary">저장</button>
      </form>
    </Modal>
  )
}

export function BusinessesPage() {
  const { projectId = '' } = useParams()
  const project = useProject(projectId)
  const businesses = useBusinesses(projectId)
  const queryClient = useQueryClient()
  const { error, run } = useAction()
  const [dialog, setDialog] = useState<{ business?: Schemas['BusinessOut'] } | null>(null)
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: keys.businesses(projectId) })
    await queryClient.invalidateQueries({ queryKey: keys.projects })
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>{project.data?.project_name ?? '프로젝트'}</h1>
        <span className="muted">사업</span>
        <span className="count">{businesses.data?.length ?? ''}</span>
      </div>
      <div className="create-row">
        <button className="primary" onClick={() => setDialog({})}>
          사업 추가
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="tbl-wrap">
      <table className="list">
        <thead>
          <tr>
            <th>사업 코드</th>
            <th className="left">사업명</th>
            <th>발주처</th>
            <th>기간</th>
            <th>보고서</th>
            <th className="actions-col" aria-label="작업" />
          </tr>
        </thead>
        <tbody>
          {businesses.data?.length === 0 && <EmptyRow colSpan={6} text="등록된 사업이 없습니다." />}
          {businesses.data?.map((b) => (
            <tr key={b.id}>
              <td className="num">{b.business_code}</td>
              <td className="wide">
                <Link to={`/businesses/${b.id}`}>{b.business_name}</Link>
              </td>
              <td>{b.client}</td>
              <td className="num">
                {b.business_start_date || b.business_end_date ? `${b.business_start_date ?? ''} ~ ${b.business_end_date ?? ''}` : '—'}
              </td>
              <td className="num">{b.report_count}</td>
              <td>
                <div className="row-actions">
                <button className="link" onClick={() => setDialog({ business: b })}>수정</button>
                <button className="link" onClick={() => void run(async () => { await post(`/api/businesses/${b.id}/duplicate`, {}); await refresh() })}>
                  복제
                </button>
                <button
                  className="link danger"
                  onClick={() => {
                    if (!window.confirm(`'${b.business_name}' 사업과 모든 보고서를 삭제할까요?`)) return
                    void run(async () => { await del(`/api/businesses/${b.id}`); await refresh() })
                  }}
                >
                  삭제
                </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      {dialog && (
        <BusinessDialog
          initial={dialog.business}
          onClose={() => setDialog(null)}
          onSubmit={async (values) => {
            if (dialog.business) await patch(`/api/businesses/${dialog.business.id}`, values)
            else await post(`/api/projects/${projectId}/businesses`, values)
            await refresh()
          }}
        />
      )}
    </div>
  )
}

// --- Reports ----------------------------------------------------------------------

export function ReportsPage() {
  const { businessId = '' } = useParams()
  const business = useBusiness(businessId)
  const reports = useReports(businessId)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { error, run } = useAction()
  const [reportNumber, setReportNumber] = useState('')
  const [pipeNumber, setPipeNumber] = useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: keys.reports(businessId) })

  return (
    <div className="page">
      <div className="page-head">
        <h1>{business.data ? `${business.data.business_code} ${business.data.business_name}` : '사업'}</h1>
        <span className="muted">보고서</span>
        <span className="count">{reports.data?.length ?? ''}</span>
      </div>
      <form
        className="create-row"
        onSubmit={(event) => {
          event.preventDefault()
          void run(async () => {
            const report = await post<Schemas['ReportDetail']>(`/api/businesses/${businessId}/reports`, {
              report_number: reportNumber,
              pipe_number: pipeNumber,
            })
            setReportNumber('')
            setPipeNumber('')
            await refresh()
            navigate(`/reports/${report.id}`)
          })
        }}
      >
        <input
          className="mono"
          placeholder="보고서 번호"
          aria-label="보고서 번호"
          value={reportNumber}
          onChange={(e) => setReportNumber(e.target.value)}
          required
        />
        <input
          className="mono"
          placeholder="관로 번호"
          aria-label="관로 번호"
          value={pipeNumber}
          onChange={(e) => setPipeNumber(e.target.value)}
          required
        />
        <button className="primary">보고서 추가</button>
      </form>
      {error && <p className="error">{error}</p>}
      <div className="tbl-wrap">
      <table className="list">
        <thead>
          <tr>
            <th className="left">보고서 번호</th>
            <th>관로 번호</th>
            <th>버전</th>
            <th>마지막 수정</th>
            <th className="actions-col" aria-label="작업" />
          </tr>
        </thead>
        <tbody>
          {reports.data?.length === 0 && <EmptyRow colSpan={5} text="등록된 보고서가 없습니다." />}
          {reports.data?.map((r) => (
            <tr key={r.report_group_id}>
              <td className="left mono">
                <Link to={`/reports/${r.latest_report_id}`}>{r.report_number}</Link>
              </td>
              <td className="num">{r.pipe_number}</td>
              <td className="num">{r.version_count}</td>
              <td className="num">{formatDateTime(r.updated_at)}</td>
              <td>
                <div className="row-actions">
                <button className="link" onClick={() => void run(async () => { await post(`/api/report-groups/${r.report_group_id}/duplicate`, {}); await refresh() })}>
                  복제
                </button>
                <button
                  className="link danger"
                  onClick={() => {
                    if (!window.confirm(`보고서 ${r.report_number}의 모든 버전과 영상을 삭제할까요?`)) return
                    void run(async () => { await del(`/api/report-groups/${r.report_group_id}`); await refresh() })
                  }}
                >
                  삭제
                </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  )
}
