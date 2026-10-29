import QRCode from 'qrcode'
import { useEffect, useRef, useState, type FormEvent } from 'react'

import { post, type Schemas } from '../api/client'
import { errorText } from '../errors'

type Step =
  | { kind: 'credentials' }
  | { kind: 'mfa' }
  | { kind: 'enroll'; secret: string; uri: string }
  | { kind: 'recovery-codes'; codes: string[] }

function QrCanvas({ uri }: { uri: string }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    // Drawn on a canvas: the CSP does not allow data: image URLs.
    if (canvas.current) void QRCode.toCanvas(canvas.current, uri, { width: 200, margin: 1 })
  }, [uri])
  return <canvas ref={canvas} aria-label="인증 앱 등록 QR 코드" />
}

export function RecoveryCodeList({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <div className="stack">
      <p>
        복구 코드를 안전한 곳에 보관하세요. 인증 앱을 쓸 수 없을 때 한 번씩 사용할 수 있으며, 다시
        표시되지 않습니다.
      </p>
      <ol className="recovery-codes">
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ol>
      <button className="primary" onClick={onDone}>
        보관했습니다
      </button>
    </div>
  )
}

export function EnrollmentForm({
  secret,
  uri,
  onSubmit,
}: {
  secret: string
  uri: string
  onSubmit: (code: string) => Promise<void>
}) {
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  return (
    <form
      className="stack"
      onSubmit={async (event) => {
        event.preventDefault()
        setBusy(true)
        setError(null)
        try {
          await onSubmit(code)
        } catch (failure) {
          setError(errorText(failure))
        } finally {
          setBusy(false)
        }
      }}
    >
      <p>인증 앱(Google Authenticator 등)으로 QR 코드를 스캔한 뒤 표시된 6자리 코드를 입력하세요.</p>
      <QrCanvas uri={uri} />
      <p className="muted">
        직접 입력: <code>{secret}</code>
      </p>
      <label>
        인증 코드
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
          required
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy}>
        등록
      </button>
    </form>
  )
}

export function LoginFlow({
  initialEmail = '',
  onSignedIn,
}: {
  initialEmail?: string
  onSignedIn: () => Promise<void> | void
}) {
  const [step, setStep] = useState<Step>({ kind: 'credentials' })
  const [email, setEmail] = useState(initialEmail)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      setBusy(false)
    }
  }

  async function startEnrollment() {
    const start = await post<Schemas['EnrollmentStart']>('/api/auth/mfa/enrollment')
    setStep({ kind: 'enroll', secret: start.secret, uri: start.otpauth_uri })
  }

  async function submitCredentials(event: FormEvent) {
    event.preventDefault()
    await run(async () => {
      const result = await post<Schemas['LoginResponse']>('/api/auth/login', { email, password })
      setPassword('')
      if (result.mfa_required) setStep({ kind: 'mfa' })
      else if (result.mfa_enrollment_required) await startEnrollment()
      else await onSignedIn()
    })
  }

  async function submitCode(event: FormEvent) {
    event.preventDefault()
    await run(async () => {
      await post('/api/auth/mfa', { code })
      await onSignedIn()
    })
  }

  if (step.kind === 'recovery-codes') {
    return <RecoveryCodeList codes={step.codes} onDone={() => void onSignedIn()} />
  }
  if (step.kind === 'enroll') {
    return (
      <div className="stack">
        <h3>2단계 인증 등록</h3>
        <p className="muted">운영 관리자 계정은 2단계 인증이 필요합니다.</p>
        <EnrollmentForm
          secret={step.secret}
          uri={step.uri}
          onSubmit={async (value) => {
            const result = await post<Schemas['RecoveryCodes']>('/api/auth/mfa/enrollment/confirm', {
              code: value,
            })
            setStep({ kind: 'recovery-codes', codes: result.recovery_codes })
          }}
        />
      </div>
    )
  }
  if (step.kind === 'mfa') {
    return (
      <form className="stack" onSubmit={submitCode}>
        <label>
          인증 코드 또는 복구 코드
          <input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="one-time-code"
            autoFocus
            required
          />
        </label>
        {error && <p className="error">{error}</p>}
        <button className="primary" disabled={busy}>
          확인
        </button>
      </form>
    )
  }
  return (
    <form className="stack" onSubmit={submitCredentials}>
      <label>
        이메일
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
          required
        />
      </label>
      <label>
        비밀번호
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
        />
      </label>
      {error && <p className="error">{error}</p>}
      <button className="primary" disabled={busy}>
        로그인
      </button>
    </form>
  )
}
