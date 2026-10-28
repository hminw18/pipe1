// Session timing rules (docs/Web_Auth_Spec.md §6), kept pure for testing.

export const HEARTBEAT_INTERVAL_MS = 5 * 60_000
export const WARNING_BEFORE_MS = 2 * 60_000
// Activity in this window counts as "the tab is in active use".
export const ACTIVE_WINDOW_MS = 5 * 60_000

export type SessionTimes = { idleExpiresAt: number; absoluteExpiresAt: number }
export type SessionState = 'ok' | 'idle-warning' | 'absolute-warning' | 'expired'

export function sessionState(times: SessionTimes, now: number): SessionState {
  const expiresAt = Math.min(times.idleExpiresAt, times.absoluteExpiresAt)
  if (now >= expiresAt) return 'expired'
  if (times.absoluteExpiresAt - now <= WARNING_BEFORE_MS) return 'absolute-warning'
  if (times.idleExpiresAt - now <= WARNING_BEFORE_MS) return 'idle-warning'
  return 'ok'
}

export function shouldHeartbeat(lastActivity: number, lastHeartbeat: number, now: number): boolean {
  return now - lastActivity <= ACTIVE_WINDOW_MS && now - lastHeartbeat >= HEARTBEAT_INTERVAL_MS
}
