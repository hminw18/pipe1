import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'

import { ApiError, get, post, SESSION_ENDED_EVENT, setCsrfToken, type Schemas } from '../api/client'
import { Modal } from '../components/Modal'
import { SessionContext, type Me } from './context'
import { LoginFlow } from './LoginFlow'
import { sessionState, shouldHeartbeat } from './timing'

const ME_KEY = ['me'] as const

async function fetchMe(): Promise<Me | null> {
  try {
    const me = await get<Me>('/api/auth/me')
    setCsrfToken(me.csrf_token)
    return me
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      setCsrfToken(null)
      return null
    }
    throw error
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const meQuery = useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: Infinity, retry: false })
  const me = meQuery.data ?? null
  const [ended, setEnded] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const lastActivity = useRef(0)
  const lastHeartbeat = useRef(0)

  const refresh = useCallback(async () => {
    const current = await queryClient.fetchQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: 0 })
    lastHeartbeat.current = Date.now()
    setEnded(false)
    return current
  }, [queryClient])

  const heartbeat = useCallback(async () => {
    lastHeartbeat.current = Date.now()
    const expiry = await post<Schemas['SessionExpiry']>('/api/auth/heartbeat')
    queryClient.setQueryData<Me | null>(ME_KEY, (current) => (current ? { ...current, ...expiry } : current))
  }, [queryClient])

  const logout = useCallback(async () => {
    try {
      await post('/api/auth/logout')
    } finally {
      setCsrfToken(null)
      queryClient.clear()
      queryClient.setQueryData(ME_KEY, null)
    }
  }, [queryClient])

  const markActive = useCallback(() => {
    lastActivity.current = Date.now()
  }, [])

  // Activity: input events, plus markActive() from the video player while playing.
  useEffect(() => {
    const onActivity = () => {
      lastActivity.current = Date.now()
    }
    const events = ['pointerdown', 'keydown', 'wheel', 'pointermove'] as const
    events.forEach((name) => window.addEventListener(name, onActivity, { passive: true }))
    return () => events.forEach((name) => window.removeEventListener(name, onActivity))
  }, [])

  useEffect(() => {
    const onEnded = () => setEnded(true)
    window.addEventListener(SESSION_ENDED_EVENT, onEnded)
    return () => window.removeEventListener(SESSION_ENDED_EVENT, onEnded)
  }, [])

  useEffect(() => {
    if (!me) return
    const timer = window.setInterval(() => {
      const current = Date.now()
      setNow(current)
      if (shouldHeartbeat(lastActivity.current, lastHeartbeat.current, current)) {
        heartbeat().catch(() => undefined)
      }
    }, 15_000)
    return () => window.clearInterval(timer)
  }, [me, heartbeat])

  const state = me
    ? sessionState(
        { idleExpiresAt: Date.parse(me.idle_expires_at), absoluteExpiresAt: Date.parse(me.absolute_expires_at) },
        now,
      )
    : 'ok'
  const showRelogin = Boolean(me) && (ended || state === 'expired')

  const value = useMemo(
    () => ({ me, loading: meQuery.isLoading, refresh, logout, markActive }),
    [me, meQuery.isLoading, refresh, logout, markActive],
  )

  return (
    <SessionContext.Provider value={value}>
      {children}
      {me && !showRelogin && state === 'idle-warning' && (
        <Modal title="곧 자동 로그아웃됩니다">
          <p>2분 동안 활동이 없으면 보안을 위해 로그아웃됩니다. 작업 내용은 자동 저장되어 있습니다.</p>
          <div className="actions">
            <button className="primary" onClick={() => void heartbeat()}>
              계속 작업
            </button>
          </div>
        </Modal>
      )}
      {me && !showRelogin && state === 'absolute-warning' && (
        <Modal title="로그인 유지 시간이 곧 끝납니다">
          <p>보안을 위해 로그인은 최대 12시간 유지됩니다. 곧 다시 로그인해야 합니다.</p>
        </Modal>
      )}
      {showRelogin && me && (
        <Modal title="다시 로그인하세요">
          <p>세션이 만료되었습니다. 다시 로그인하면 지금 화면에서 이어서 작업할 수 있습니다.</p>
          <LoginFlow
            initialEmail={me.email}
            onSignedIn={async () => {
              await refresh()
              setNow(Date.now())
              await queryClient.invalidateQueries()
            }}
          />
        </Modal>
      )}
    </SessionContext.Provider>
  )
}
