import { ApiError } from './api/client'

export function errorText(error: unknown): string {
  return error instanceof ApiError || error instanceof Error ? error.message : '오류가 발생했습니다.'
}
