import { afterEach, describe, expect, it, vi } from 'vitest'

import { api, ApiError, SESSION_ENDED_EVENT, setCsrfToken } from '../api/client'
import { readTokenFromFragment } from '../format'

function mockFetch(status: number, body: unknown = {}) {
  const fn = vi.fn().mockImplementation(async () => new Response(status === 204 ? null : JSON.stringify(body), { status }))
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  setCsrfToken(null)
})

describe('api client', () => {
  it('sends the CSRF token on state-changing requests only', async () => {
    setCsrfToken('token-1')
    const fetchMock = mockFetch(200, { ok: true })
    await api('POST', '/api/projects', { project_name: 'x' })
    await api('GET', '/api/projects')
    const [, postInit] = fetchMock.mock.calls[0]
    const [, getInit] = fetchMock.mock.calls[1]
    expect(postInit.headers['X-CSRF-Token']).toBe('token-1')
    expect(postInit.headers['Content-Type']).toBe('application/json')
    expect(postInit.credentials).toBe('same-origin')
    expect(getInit.headers['X-CSRF-Token']).toBeUndefined()
  })

  it('signals session end on 401 outside the login flow', async () => {
    mockFetch(401, { detail: '로그인이 필요합니다.' })
    const listener = vi.fn()
    window.addEventListener(SESSION_ENDED_EVENT, listener)
    await expect(api('GET', '/api/projects')).rejects.toBeInstanceOf(ApiError)
    expect(listener).toHaveBeenCalledOnce()
    mockFetch(401, { detail: '이메일 또는 비밀번호가 올바르지 않습니다.' })
    await expect(api('POST', '/api/auth/login', {})).rejects.toThrow('이메일 또는 비밀번호')
    expect(listener).toHaveBeenCalledOnce()
    window.removeEventListener(SESSION_ENDED_EVENT, listener)
  })

  it('exposes structured error codes', async () => {
    mockFetch(403, { detail: { code: 'reauthentication_required', message: '비밀번호를 다시 입력하세요.' } })
    const error = (await api('DELETE', '/api/projects/1').catch((e: unknown) => e)) as ApiError
    expect(error.code).toBe('reauthentication_required')
    expect(error.message).toBe('비밀번호를 다시 입력하세요.')
  })
})

describe('readTokenFromFragment', () => {
  it('reads the token from the URL fragment', () => {
    expect(readTokenFromFragment('#token=abc123')).toBe('abc123')
    expect(readTokenFromFragment('')).toBeNull()
  })
})
