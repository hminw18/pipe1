import { useCallback, useRef, useState, type ReactNode } from 'react'

import { ApiError, post } from '../api/client'
import { Modal } from '../components/Modal'
import { errorText } from '../errors'
import { ReauthContext, useSession } from './context'

// Sensitive actions (project delete, ownership transfer, email change, role change, MFA
// changes) need a password entered in the last 10 minutes (docs/Web_Auth_Spec.md §6).
// withReauth runs the action; if the server asks for re-authentication, it prompts, then
// retries once.

type Run = <T>(action: () => Promise<T>) => Promise<T>

export function ReauthProvider({ children }: { children: ReactNode }) {
  const { me } = useSession()
  const [open, setOpen] = useState(false)
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const pending = useRef<{ resolve: () => void; reject: (reason: unknown) => void } | null>(null)

  const prompt = () =>
    new Promise<void>((resolve, reject) => {
      pending.current = { resolve, reject }
      setPassword('')
      setCode('')
      setError(null)
      setOpen(true)
    })

  const run: Run = useCallback(async (action) => {
    try {
      return await action()
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 403 && failure.code === 'reauthentication_required') {
        await prompt()
        return await action()
      }
      throw failure
    }
  }, [])

  return (
    <ReauthContext.Provider value={run}>
      {children}
      {open && (
        <Modal
          title="본인 확인"
          onClose={() => {
            setOpen(false)
            pending.current?.reject(new Error('취소했습니다.'))
          }}
        >
          <form
            className="stack"
            onSubmit={async (event) => {
              event.preventDefault()
              try {
                await post('/api/auth/reauthenticate', { password, code: code || null })
                setOpen(false)
                pending.current?.resolve()
              } catch (failure) {
                setError(errorText(failure))
              }
            }}
          >
            <p>보안을 위해 비밀번호를 다시 입력하세요.</p>
            <label>
              비밀번호
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                autoFocus
                required
              />
            </label>
            {me?.mfa_enrolled && (
              <label>
                인증 코드
                <input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" required />
              </label>
            )}
            {error && <p className="error">{error}</p>}
            <button className="primary">확인</button>
          </form>
        </Modal>
      )}
    </ReauthContext.Provider>
  )
}
