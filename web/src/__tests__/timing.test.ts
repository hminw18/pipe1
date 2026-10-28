import { describe, expect, it } from 'vitest'

import { ACTIVE_WINDOW_MS, HEARTBEAT_INTERVAL_MS, sessionState, shouldHeartbeat } from '../session/timing'

const MIN = 60_000

describe('sessionState', () => {
  const times = { idleExpiresAt: 120 * MIN, absoluteExpiresAt: 720 * MIN }
  it('is ok well before either timeout', () => expect(sessionState(times, 60 * MIN)).toBe('ok'))
  it('warns 2 minutes before the idle timeout', () => expect(sessionState(times, 118 * MIN)).toBe('idle-warning'))
  it('expires at the idle timeout', () => expect(sessionState(times, 120 * MIN)).toBe('expired'))
  it('absolute warning wins when it comes first', () =>
    expect(sessionState({ idleExpiresAt: 719 * MIN, absoluteExpiresAt: 720 * MIN }, 718.5 * MIN)).toBe('absolute-warning'))
})

describe('shouldHeartbeat', () => {
  it('beats when active and the last beat is old', () =>
    expect(shouldHeartbeat(1000, 0, HEARTBEAT_INTERVAL_MS + 1000)).toBe(true))
  it('does not beat when idle', () =>
    expect(shouldHeartbeat(0, 0, ACTIVE_WINDOW_MS + HEARTBEAT_INTERVAL_MS + 1)).toBe(false))
  it('does not beat too often', () => expect(shouldHeartbeat(1000, 500, 2000)).toBe(false))
})
