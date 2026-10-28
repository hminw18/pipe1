import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'

import { del, get, post, type Schemas } from '../api/client'
import { formatDateTime } from '../format'
import { errorText } from '../errors'
import { EnrollmentForm, RecoveryCodeList } from '../session/LoginFlow'
import { useWithReauth } from '../session/context'
import { useMe, useSession } from '../session/context'

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="section">
      <h2>{title}</h2>
      {children}
    </section>
  )
}

function useStatus() {
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const run = async (action: () => Promise<string | void>) => {
    setMessage(null)
    setError(null)
    try {
      const result = await action()
      if (result) setMessage(result)
    } catch (failure) {
      setError(errorText(failure))
    }
  }
  const view = (
    <>
      {message && <p className="success">{message}</p>}
      {error && <p className="error">{error}</p>}
    </>
  )
  return { run, view }
}

function PasswordPanel() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const { run, view } = useStatus()
  return (
    <Panel title="비밀번호 변경">
      <form
        className="stack narrow"
        onSubmit={(event) => {
          event.preventDefault()
          void run(async () => {
            await post('/api/auth/password', { current_password: current, new_password: next })
            setCurrent('')
            setNext('')
            return '비밀번호를 변경했습니다. 다른 기기의 로그인은 종료되었습니다.'
          })
        }}
      >
        <label>
          현재 비밀번호
          <input type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required />
        </label>
        <label>
          새 비밀번호 (12자 이상)
          <input type="password" minLength={12} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required />
        </label>
        {view}
        <button className="primary">변경</button>
      </form>
    </Panel>
  )
}

function EmailPanel() {
  const withReauth = useWithReauth()
  const me = useMe()
  const [email, setEmail] = useState('')
  const { run, view } = useStatus()
  return (
    <Panel title="로그인 이메일">
      <p>현재: {me.email}</p>
      <form
        className="inline-form"
        onSubmit={(event) => {
          event.preventDefault()
          void run(async () => {
            const result = await withReauth(() => post<Schemas['Accepted']>('/api/auth/email-change', { new_email: email }))
            setEmail('')
            return result.message
          })
        }}
      >
        <input type="email" placeholder="새 이메일" value={email} onChange={(e) => setEmail(e.target.value)} required />
        <button className="primary">변경 메일 받기</button>
      </form>
      {view}
    </Panel>
  )
}

function MfaPanel() {
  const withReauth = useWithReauth()
  const { refresh } = useSession()
  const me = useMe()
  const [setup, setSetup] = useState<Schemas['EnrollmentStart'] | null>(null)
  const [codes, setCodes] = useState<string[] | null>(null)
  const { run, view } = useStatus()
  const isAdmin = me.role !== 'inspector'

  if (codes) {
    return (
      <Panel title="2단계 인증">
        <RecoveryCodeList codes={codes} onDone={() => setCodes(null)} />
      </Panel>
    )
  }
  if (setup) {
    return (
      <Panel title="2단계 인증 등록">
        <EnrollmentForm
          secret={setup.secret}
          uri={setup.otpauth_uri}
          onSubmit={async (code) => {
            const result = await withReauth(() => post<Schemas['RecoveryCodes']>('/api/auth/mfa/setup/confirm', { code }))
            setSetup(null)
            setCodes(result.recovery_codes)
            await refresh()
          }}
        />
      </Panel>
    )
  }
  return (
    <Panel title="2단계 인증">
      <p>{me.mfa_enrolled ? '사용 중' : '사용 안 함'}</p>
      <div className="actions">
        {!me.mfa_enrolled && (
          <button className="primary" onClick={() => void run(async () => setSetup(await withReauth(() => post<Schemas['EnrollmentStart']>('/api/auth/mfa/setup'))))}>
            등록
          </button>
        )}
        {me.mfa_enrolled && (
          <button onClick={() => void run(async () => setCodes((await withReauth(() => post<Schemas['RecoveryCodes']>('/api/auth/mfa/recovery-codes'))).recovery_codes))}>
            복구 코드 다시 발급
          </button>
        )}
        {me.mfa_enrolled && !isAdmin && (
          <button
            className="danger"
            onClick={() => {
              if (!window.confirm('2단계 인증을 해제할까요?')) return
              void run(async () => {
                await withReauth(() => del('/api/auth/mfa'))
                await refresh()
                return '2단계 인증을 해제했습니다.'
              })
            }}
          >
            해제
          </button>
        )}
      </div>
      {isAdmin && <p className="muted">관리자 계정은 2단계 인증을 해제할 수 없습니다.</p>}
      {view}
    </Panel>
  )
}

function SessionsPanel() {
  const queryClient = useQueryClient()
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => get<Schemas['SessionInfo'][]>('/api/auth/sessions') })
  const { run, view } = useStatus()
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['sessions'] })
  return (
    <Panel title="로그인된 기기">
      <div className="tbl-wrap">
      <table className="list">
        <thead>
          <tr>
            <th>기기</th>
            <th>IP</th>
            <th>로그인</th>
            <th>마지막 활동</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sessions.data?.map((s) => (
            <tr key={s.id}>
              <td className="wide">{s.user_agent ?? '-'} {s.current && <span className="tag">· 현재 기기</span>}</td>
              <td className="num">{s.ip ?? '-'}</td>
              <td className="num">{formatDateTime(s.created_at)}</td>
              <td className="num">{formatDateTime(s.last_seen_at)}</td>
              <td>
                {!s.current && (
                  <button className="link" onClick={() => void run(async () => { await del(`/api/auth/sessions/${s.id}`); await refresh() })}>로그아웃</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
      <button
        className="danger align-start"
        onClick={() => void run(async () => { await post('/api/auth/sessions/revoke-others'); await refresh(); return '다른 모든 기기에서 로그아웃했습니다.' })}
      >
        로그아웃 (모든 기기)
      </button>
      {view}
    </Panel>
  )
}

export function SettingsPage() {
  return (
    <div className="page">
      <div className="page-head">
        <h1>계정 설정</h1>
      </div>
      <PasswordPanel />
      <EmailPanel />
      <MfaPanel />
      <SessionsPanel />
    </div>
  )
}
