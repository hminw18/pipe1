import { describe, expect, it } from 'vitest'

import { formatDate, formatDateTime, formatTimestamp } from '../format'

describe('format', () => {
  it('shows dates in Korea time as YYYY-MM-DD HH:mm', () => {
    expect(formatDateTime('2026-10-27T15:05:00Z')).toBe('2026-10-28 00:05')
    expect(formatDate('2026-10-27T15:05:00Z')).toBe('2026-10-28')
    expect(formatDateTime(null)).toBe('-')
  })

  it('formats video timestamps to the millisecond', () => {
    expect(formatTimestamp(252_480)).toBe('00:04:12.480')
  })
})
