import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { get, post, put, del, type Schemas } from '../api/client'
import { formatDateTime } from '../format'
import { errorText } from '../errors'
import { useWithReauth } from '../session/context'
import { useMe } from '../session/context'

const ROLE_LABELS: Record<string, string> = {
  inspector: '조사자',
  org_admin: '조직 관리자',
  platform_admin: '운영 관리자',
}
const STATUS_LABELS: Record<string, string> = { active: '활성', invited: '초대됨', disabled: '비활성' }

function useFeedback() {
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const run = async (action: () => Promise<string | void>) => {
    setError(null)
    setMessage(null)
    try {
      const result = await action()
      if (result) setMessage(result)
    } catch (failure) {
      setError(errorText(failure))
    }
  }
  return {
    run,
    view: (
      <>
        {message && <p className="success">{message}</p>}
        {error && <p className="error">{error}</p>}
      </>
    ),
  }
}

function AuditTable({ path }: { path: string }) {
  const events = useQuery({ queryKey: ['audit', path], queryFn: () => get<Schemas['AuditEventOut'][]>(path) })
  return (
    <section className="section">
      <h2>감사 기록</h2>
      <div className="tbl-wrap">
      <table className="list">
        <thead>
          <tr>
            <th>시각</th>
            <th>작업</th>
            <th>대상</th>
            <th>사용자</th>
            <th>IP</th>
          </tr>
        </thead>
        <tbody>
          {events.data?.map((e) => (
            <tr key={e.id}>
              <td className="num">{formatDateTime(e.created_at)}</td>
              <td>{e.action}</td>
              <td>
                {e.target_type} {e.target_id}
              </td>
              <td>{e.actor_user_id}</td>
              <td>{e.ip}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </section>
  )
}

export function OrgAdminPage() {
  const me = useMe()
  const withReauth = useWithReauth()
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['admin-users'], queryFn: () => get<Schemas['UserSummary'][]>('/api/admin/users') })
  const invitations = useQuery({
    queryKey: ['admin-invitations'],
    queryFn: () => get<Schemas['InvitationSummary'][]>('/api/admin/invitations'),
  })
  const [email, setEmail] = useState('')
  const [role, setRole] = useState<'inspector' | 'org_admin'>('inspector')
  const { run, view } = useFeedback()
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['admin-users'] })
    await queryClient.invalidateQueries({ queryKey: ['admin-invitations'] })
  }

  return (
    <div className="page">
      <div className="page-head">
        <h1>{me.organization_name} · 조직 관리</h1>
      </div>
      <section className="section">
        <h2>사용자 초대</h2>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            void run(async () => {
              await post('/api/admin/invitations', { email, role })
              setEmail('')
              await refresh()
              return '초대 메일을 보냈습니다.'
            })
          }}
        >
          <input type="email" placeholder="이메일" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <select value={role} onChange={(e) => setRole(e.target.value as 'inspector' | 'org_admin')}>
            <option value="inspector">조사자</option>
            <option value="org_admin">조직 관리자</option>
          </select>
          <button className="primary">초대</button>
        </form>
        {view}
        <h3>대기 중인 초대</h3>
        <div className="tbl-wrap">
        <table className="list">
          <thead>
            <tr>
              <th className="left">이메일</th>
              <th>역할</th>
              <th>만료</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {invitations.data?.map((i) => (
              <tr key={i.id}>
                <td className="wide">{i.email}</td>
                <td>{ROLE_LABELS[i.role]}</td>
                <td className="num">{formatDateTime(i.expires_at)}</td>
                <td className="actions">
                  <button onClick={() => void run(async () => { await post(`/api/admin/invitations/${i.id}/resend`); await refresh(); return '다시 보냈습니다.' })}>
                    다시 보내기
                  </button>
                  <button className="danger" onClick={() => void run(async () => { await del(`/api/admin/invitations/${i.id}`); await refresh() })}>
                    취소
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </section>
      <section className="section">
        <h2>사용자</h2>
        <div className="tbl-wrap">
        <table className="list">
          <thead>
            <tr>
              <th className="left">이메일</th>
              <th>역할</th>
              <th>상태</th>
              <th>2단계 인증</th>
              <th>마지막 로그인</th>
              <th className="actions-col" aria-label="작업" />
            </tr>
          </thead>
          <tbody>
            {users.data?.map((u) => (
              <tr key={u.id}>
                <td className="wide">{u.email}</td>
                <td>
                  <select
                    value={u.role}
                    disabled={u.id === me.id || u.status !== 'active'}
                    aria-label="역할"
                    onChange={(e) =>
                      void run(async () => {
                        await withReauth(() => put(`/api/admin/users/${u.id}/role`, { role: e.target.value }))
                        await refresh()
                        return '역할을 변경했습니다. 해당 사용자는 다시 로그인해야 합니다.'
                      })
                    }
                  >
                    <option value="inspector">조사자</option>
                    <option value="org_admin">조직 관리자</option>
                  </select>
                </td>
                <td>{STATUS_LABELS[u.status] ?? u.status}</td>
                <td>{u.mfa_enrolled ? '사용' : '-'}</td>
                <td className="num">{formatDateTime(u.last_login_at)}</td>
                <td className="actions">
                  {u.role === 'inspector' && u.mfa_enrolled && (
                    <button
                      onClick={() =>
                        void run(async () => {
                          if (!window.confirm(`${u.email}의 2단계 인증을 초기화할까요?`)) return
                          await withReauth(() => post(`/api/admin/users/${u.id}/mfa-reset`))
                          await refresh()
                          return '2단계 인증을 초기화했습니다.'
                        })
                      }
                    >
                      2단계 인증 초기화
                    </button>
                  )}
                  {u.id !== me.id && u.status === 'active' && (
                    <button
                      className="danger"
                      onClick={() =>
                        void run(async () => {
                          if (!window.confirm(`${u.email} 계정을 비활성화할까요? 즉시 로그아웃됩니다.`)) return
                          await post(`/api/admin/users/${u.id}/deactivate`)
                          await refresh()
                        })
                      }
                    >
                      비활성화
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </section>
      <AuditTable path="/api/admin/audit-events" />
    </div>
  )
}

export function PlatformPage() {
  const withReauth = useWithReauth()
  const queryClient = useQueryClient()
  const organizations = useQuery({
    queryKey: ['platform-orgs'],
    queryFn: () => get<Schemas['OrganizationSummary'][]>('/api/platform/organizations'),
  })
  const [name, setName] = useState('')
  const [adminEmail, setAdminEmail] = useState('')
  const [opsEmail, setOpsEmail] = useState('')
  const [userId, setUserId] = useState('')
  const { run, view } = useFeedback()
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['platform-orgs'] })

  return (
    <div className="page">
      <div className="page-head">
        <h1>운영 관리</h1>
      </div>
      <section className="section">
        <h2>조직 만들기</h2>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            void run(async () => {
              await post('/api/platform/organizations', { name, admin_email: adminEmail })
              setName('')
              setAdminEmail('')
              await refresh()
              return '조직을 만들고 관리자에게 초대 메일을 보냈습니다.'
            })
          }}
        >
          <input placeholder="조직 이름" value={name} onChange={(e) => setName(e.target.value)} required />
          <input type="email" placeholder="첫 관리자 이메일" value={adminEmail} onChange={(e) => setAdminEmail(e.target.value)} required />
          <button className="primary">만들기</button>
        </form>
        {view}
        <div className="tbl-wrap">
        <table className="list">
          <thead>
            <tr>
              <th className="left">조직</th>
              <th>상태</th>
              <th>생성일</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {organizations.data?.map((o) => (
              <tr key={o.id}>
                <td className="wide">{o.name}</td>
                <td>{STATUS_LABELS[o.status] ?? o.status}</td>
                <td className="num">{formatDateTime(o.created_at)}</td>
                <td className="actions">
                  {o.status === 'active' && (
                    <button
                      className="danger"
                      onClick={() =>
                        void run(async () => {
                          if (!window.confirm(`${o.name} 조직을 비활성화할까요? 모든 구성원이 즉시 로그아웃됩니다.`)) return
                          await withReauth(() => post(`/api/platform/organizations/${o.id}/deactivate`))
                          await refresh()
                        })
                      }
                    >
                      비활성화
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      </section>
      <section className="section">
        <h2>운영 관리자 초대</h2>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            void run(async () => {
              await withReauth(() => post('/api/platform/invitations', { email: opsEmail }))
              setOpsEmail('')
              return '초대 메일을 보냈습니다.'
            })
          }}
        >
          <input type="email" placeholder="이메일" value={opsEmail} onChange={(e) => setOpsEmail(e.target.value)} required />
          <button className="primary">초대</button>
        </form>
      </section>
      <section className="section">
        <h2>조직 관리자 2단계 인증 초기화</h2>
        <form
          className="inline-form"
          onSubmit={(event) => {
            event.preventDefault()
            void run(async () => {
              await withReauth(() => post(`/api/platform/users/${userId}/mfa-reset`))
              setUserId('')
              return '초기화했습니다.'
            })
          }}
        >
          <input placeholder="사용자 ID" value={userId} onChange={(e) => setUserId(e.target.value)} required />
          <button className="primary">초기화</button>
        </form>
      </section>
      <AuditTable path="/api/platform/audit-events" />
    </div>
  )
}
