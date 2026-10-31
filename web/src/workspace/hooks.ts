import { useQuery } from '@tanstack/react-query'

import { ApiError, get, type Schemas } from '../api/client'
import { keys } from '../api/queries'

export type PlayerApi = {
  currentMs: () => number
  seekMs: (ms: number) => void
  pause: () => void
  toggle: () => void
  stepFrames: (frames: number) => void
  jumpSeconds: (seconds: number) => void
  /** Base64 PNG of the shown frame when playing the local file; null means capture server-side. */
  captureFrame: () => Promise<string | null>
}

export function useVideo(reportId: string) {
  return useQuery({
    queryKey: keys.video(reportId),
    queryFn: async () => {
      try {
        return await get<Schemas['VideoOut']>(`/api/reports/${reportId}/video`)
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null
        throw error
      }
    },
    // Poll while the worker processes the upload.
    refetchInterval: (query) => {
      const status = query.state.data?.transcode_status
      return status === 'pending' || status === 'processing' ? 5000 : false
    },
  })
}

/** Recent jobs of one kind, newest first; polls while the newest is unfinished. */
export function useJobs(reportId: string, kind: 'stop_analysis' | 'defect_detection') {
  return useQuery({
    queryKey: keys.jobs(reportId, kind),
    queryFn: () => get<Schemas['JobOut'][]>(`/api/reports/${reportId}/jobs?kind=${kind}`),
    refetchInterval: (query) => {
      const latest = query.state.data?.[0]
      return latest && (latest.status === 'queued' || latest.status === 'running') ? 2000 : false
    },
  })
}

export function useDefects(reportId: string) {
  return useQuery({
    queryKey: keys.defects(reportId),
    queryFn: () => get<Schemas['DefectOut'][]>(`/api/reports/${reportId}/defects`),
  })
}

export function useSuggestions(reportId: string) {
  return useQuery({
    queryKey: keys.suggestions(reportId),
    queryFn: () => get<Schemas['SuggestionList']>(`/api/reports/${reportId}/suggestions`),
  })
}
