import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { post, type Schemas } from '../api/client'
import { readTokenFromFragment } from '../format'
import { errorText } from '../errors'
import { EnrollmentForm, LoginFlow, RecoveryCodeList } from '../session/LoginFlow'
import { useSession } from '../session/context'

export function BrandMark() {
  return (
    <div className="brand">
      <img src="/pipe1-mark.svg" alt="" width={20} height={18} />
      Pipe1
    </div>
  )
}

function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="auth-page">
      <section className="auth-card">
        <BrandMark />
        <h1>{title}</h1>
        {children}
        <footer className="legal-links">
          <a href="/legal/terms">이용약관</a> · <a href="/legal/privacy">개인정보 처리방침</a>
        </footer>
      </section>
    </main>
  )
}

/** Tokens arrive in the URL fragment; remove them from the address bar once read. */
function useFragmentToken(): string | null {
  const [token] = useState(() => readTokenFromFragment())
  useEffect(() => {
    if (window.location.hash) {
      window.history.replaceState(null, '', window.location.pathname)
    }
  }, [])
  return token
}

export function LoginPage() {
  const { refresh } = useSession()
  const navigate = useNavigate()
  return (
    <AuthCard title="로그인">
      <LoginFlow
        onSignedIn={async () => {
          await refresh()
          navigate('/projects', { replace: true })
        }}
      />
      <p>
        <Link to="/reset-password">비밀번호를 잊으셨나요?</Link>
      </p>
    </AuthCard>
  )
}

export function InvitePage() {
  const token = useFragmentToken()
  const { refresh } = useSession()
  const navigate = useNavigate()
  const [preview, setPreview] = useState<Schemas['InvitationPreview'] | null>(null)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [enroll, setEnroll] = useState<{ secret: string; uri: string } | null>(null)
  const [codes, setCodes] = useState<string[] | null>(null)

  useEffect(() => {
    if (!token) return
    post<Schemas['InvitationPreview']>('/api/auth/invitations/preview', { token })
      .then(setPreview)
      .catch((failure) => setError(errorText(failure)))
  }, [token])

  const finish = async () => {
    await refresh()
    navigate('/projects', { replace: true })
  }

  if (!token) return <AuthCard title="초대">유효하지 않은 링크입니다.</AuthCard>
  if (codes) {
    return (
      <AuthCard title="복구 코드">
        <RecoveryCodeList codes={codes} onDone={() => void finish()} />
      </AuthCard>
    )
  }
  if (enroll) {
    return (
      <AuthCard title="2단계 인증 등록">
        <EnrollmentForm
          secret={enroll.secret}
          uri={enroll.uri}
          onSubmit={async (code) => {
            const result = await post<Schemas['RecoveryCodes']>('/api/auth/mfa/enrollment/confirm', { code })
            setCodes(result.recovery_codes)
          }}
        />
      </AuthCard>
    )
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (password !== confirm) {
      setError('비밀번호가 서로 다릅니다.')
      return
    }
    try {
      const result = await post<Schemas['LoginResponse']>('/api/auth/invitations/accept', { token, password })
      if (result.mfa_enrollment_required) {
        const start = await post<Schemas['EnrollmentStart']>('/api/auth/mfa/enrollment')
        setEnroll({ secret: start.secret, uri: start.otpauth_uri })
      } else {
        await finish()
      }
    } catch (failure) {
      setError(errorText(failure))
    }
  }

  return (
    <AuthCard title="계정 만들기">
      {preview && (
        <p>
          {preview.organization_name ?? 'Pipe1 운영팀'}에서 <strong>{preview.email}</strong> 계정으로
          초대했습니다.
        </p>
      )}
      <form className="stack" onSubmit={submit}>
        <PasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} />
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={!preview}>
          계정 만들기
        </button>
      </form>
    </AuthCard>
  )
}

function PasswordFields(props: {
  password: string
  confirm: string
  onPassword: (value: string) => void
  onConfirm: (value: string) => void
}) {
  return (
    <>
      <label>
        새 비밀번호 (12자 이상)
        <input
          type="password"
          minLength={12}
          maxLength={128}
          value={props.password}
          onChange={(e) => props.onPassword(e.target.value)}
          autoComplete="new-password"
          required
        />
      </label>
      <label>
        새 비밀번호 확인
        <input
          type="password"
          value={props.confirm}
          onChange={(e) => props.onConfirm(e.target.value)}
          autoComplete="new-password"
          required
        />
      </label>
      <p className="hint">유출된 적 있는 비밀번호, 이메일이 포함된 비밀번호는 사용할 수 없습니다.</p>
    </>
  )
}

export function ResetPasswordPage() {
  const token = useFragmentToken()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (!token) {
    return (
      <AuthCard title="비밀번호 재설정">
        {message ? (
          <p>{message}</p>
        ) : (
          <form
            className="stack"
            onSubmit={async (event) => {
              event.preventDefault()
              const result = await post<Schemas['Accepted']>('/api/auth/password-reset', { email })
              setMessage(result.message)
            }}
          >
            <label>
              이메일
              <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
            </label>
            <button className="primary">재설정 메일 받기</button>
          </form>
        )}
      </AuthCard>
    )
  }
  return (
    <AuthCard title="새 비밀번호 설정">
      {message ? (
        <p>
          {message} <Link to="/login">로그인</Link>
        </p>
      ) : (
        <form
          className="stack"
          onSubmit={async (event) => {
            event.preventDefault()
            if (password !== confirm) {
              setError('비밀번호가 서로 다릅니다.')
              return
            }
            try {
              await post('/api/auth/password-reset/complete', { token, new_password: password })
              setMessage('비밀번호가 변경되었습니다. 모든 기기에서 로그아웃되었습니다.')
            } catch (failure) {
              setError(errorText(failure))
            }
          }}
        >
          <PasswordFields password={password} confirm={confirm} onPassword={setPassword} onConfirm={setConfirm} />
          {error && <p className="error">{error}</p>}
          <button className="primary">변경</button>
        </form>
      )}
    </AuthCard>
  )
}

export function ConfirmEmailPage() {
  const token = useFragmentToken()
  const [state, setState] = useState<'pending' | 'done' | 'failed'>(token ? 'pending' : 'failed')
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!token) return
    post('/api/auth/email-change/confirm', { token })
      .then(() => setState('done'))
      .catch((failure) => {
        setError(errorText(failure))
        setState('failed')
      })
  }, [token])
  return (
    <AuthCard title="이메일 변경 확인">
      {state === 'pending' && <p>확인 중…</p>}
      {state === 'done' && (
        <p>
          로그인 이메일이 변경되었습니다. <Link to="/login">로그인</Link>
        </p>
      )}
      {state === 'failed' && <p className="error">{error ?? '유효하지 않은 링크입니다.'}</p>}
    </AuthCard>
  )
}
