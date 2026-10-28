import { createContext, useContext } from 'react'

import type { Schemas } from '../api/client'

export type Me = Schemas['Me']

export type SessionContextValue = {
  me: Me | null
  loading: boolean
  refresh: () => Promise<Me | null>
  logout: () => Promise<void>
  markActive: () => void
}

export const SessionContext = createContext<SessionContextValue | null>(null)

export function useSession(): SessionContextValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession outside SessionProvider')
  return value
}

export function useMe(): Me {
  const { me } = useSession()
  if (!me) throw new Error('not signed in')
  return me
}

type Run = <T>(action: () => Promise<T>) => Promise<T>

export const ReauthContext = createContext<Run | null>(null)

/** Run a sensitive action, prompting for the password if the server asks (Reauth.tsx). */
export function useWithReauth(): Run {
  const value = useContext(ReauthContext)
  if (!value) throw new Error('useWithReauth outside ReauthProvider')
  return value
}
