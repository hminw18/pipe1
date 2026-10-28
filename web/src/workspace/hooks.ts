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
      return status === 'pending' || status === 'processing' ? 3000 : false
    },
  })
}

export function useStopAnalysis(reportId: string) {
  return useQuery({
    queryKey: keys.jobs(reportId, 'stop_analysis'),
    queryFn: () => get<Schemas['JobOut'][]>(`/api/reports/${reportId}/jobs?kind=stop_analysis`),
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
