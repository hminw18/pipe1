// Local-first video: the chosen file plays straight from disk while it uploads in the
// background, and defect frames are grabbed from that playback, so nobody waits for the upload
// or the server's processing before reviewing.

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'

import { keys } from '../api/queries'
import { errorText } from '../errors'
import { uploadVideo } from './upload'

export type LocalVideo = {
  url: string
  name: string
  progress: number // 0-100
  uploaded: boolean
  error: string | null
}

export function useLocalVideo(reportId: string) {
  const queryClient = useQueryClient()
  const [local, setLocal] = useState<LocalVideo | null>(null)
  const current = useRef<string | null>(null)

  // A different report starts without a local file; release the old one's memory.
  useEffect(() => {
    return () => {
      if (current.current) URL.revokeObjectURL(current.current)
      current.current = null
      setLocal(null)
    }
  }, [reportId])

  const uploading = Boolean(local && !local.uploaded && !local.error)
  useEffect(() => {
    if (!uploading) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [uploading])

  const start = useCallback(
    async (file: File) => {
      if (current.current) URL.revokeObjectURL(current.current)
      const url = URL.createObjectURL(file)
      current.current = url
      const update = (change: Partial<LocalVideo>) =>
        setLocal((state) => (state && state.url === url ? { ...state, ...change } : state))
      setLocal({ url, name: file.name, progress: 0, uploaded: false, error: null })
      try {
        await uploadVideo(reportId, file, (sent, total) => update({ progress: Math.round((sent / total) * 100) }), () =>
          // The video row exists once the upload has started: captures can attach to it.
          queryClient.invalidateQueries({ queryKey: keys.video(reportId) }),
        )
        update({ progress: 100, uploaded: true })
      } catch (failure) {
        update({ error: errorText(failure) })
      }
      await queryClient.invalidateQueries({ queryKey: keys.video(reportId) })
      await queryClient.invalidateQueries({ queryKey: keys.report(reportId) })
    },
    [queryClient, reportId],
  )

  return { local, start }
}

/** The frame the video element is showing, as base64 PNG at the video's full resolution. */
export async function grabFrame(video: HTMLVideoElement): Promise<string | null> {
  if (!video.videoWidth || !video.videoHeight) return null
  const canvas = document.createElement('canvas')
  canvas.width = video.videoWidth
  canvas.height = video.videoHeight
  canvas.getContext('2d')?.drawImage(video, 0, 0)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, 'image/png'))
  if (!blob) return null
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(blob)
  })
  return dataUrl.slice(dataUrl.indexOf(',') + 1)
}
