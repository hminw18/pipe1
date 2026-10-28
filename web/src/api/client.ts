// Fetch wrapper for the same-origin API (docs/Web_Auth_Spec.md §6-§7).
// - Session cookie is HttpOnly; the browser sends it automatically.
// - State-changing requests carry the CSRF token from GET /api/auth/me and a JSON body.
// - A 401 outside the login endpoints means the session ended; listeners show the
//   in-place re-login dialog.

import type { components } from './schema'

export type Schemas = components['schemas']

const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const AUTH_FLOW_PATHS = [
  '/api/auth/login',
  '/api/auth/mfa',
  '/api/auth/invitations',
  '/api/auth/password-reset',
  '/api/auth/email-change/confirm',
]
export const SESSION_ENDED_EVENT = 'pipe1:session-ended'

let csrfToken: string | null = null

export function setCsrfToken(token: string | null): void {
  csrfToken = token
}

export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : messageOf(detail) ?? `요청 실패 (${status})`)
    this.status = status
    this.detail = detail
  }

  get code(): string | undefined {
    return typeof this.detail === 'object' && this.detail !== null && 'code' in this.detail
      ? String((this.detail as { code: unknown }).code)
      : undefined
  }
}

function messageOf(detail: unknown): string | undefined {
  if (typeof detail === 'object' && detail !== null && 'message' in detail) {
    return String((detail as { message: unknown }).message)
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return '입력값을 확인하세요.'
  }
  return undefined
}

function isAuthFlow(path: string): boolean {
  return AUTH_FLOW_PATHS.some((prefix) => path.startsWith(prefix))
}

export async function api<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (UNSAFE.has(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401 && !isAuthFlow(path) && path !== '/api/auth/me') {
    window.dispatchEvent(new CustomEvent(SESSION_ENDED_EVENT))
  }
  if (!response.ok) {
    let detail: unknown = undefined
    try {
      detail = ((await response.json()) as { detail?: unknown }).detail
    } catch {
      detail = undefined
    }
    throw new ApiError(response.status, detail ?? `요청 실패 (${response.status})`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const get = <T>(path: string) => api<T>('GET', path)
export const post = <T>(path: string, body: unknown = {}) => api<T>('POST', path, body)
export const patch = <T>(path: string, body: unknown) => api<T>('PATCH', path, body)
export const put = <T>(path: string, body: unknown) => api<T>('PUT', path, body)
export const del = <T = void>(path: string) => api<T>('DELETE', path)
