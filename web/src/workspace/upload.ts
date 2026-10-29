// Direct browser → storage upload (docs/Web_Architecture.md §4). Multi-GB files and flaky
// connections are expected, so Cloud Storage uploads go in resumable chunks and pick up where
// they stopped after a network error.

import { post, type Schemas } from '../api/client'

const CHUNK_BYTES = 8 * 1024 * 1024 // must be a multiple of 256 KiB for GCS
const MAX_RETRIES = 8

type Progress = (sentBytes: number, totalBytes: number) => void

function xhr(
  method: string,
  url: string,
  body: Blob | null,
  headers: Record<string, string>,
  onProgress?: (loaded: number) => void,
): Promise<XMLHttpRequest> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest()
    request.open(method, url)
    Object.entries(headers).forEach(([name, value]) => request.setRequestHeader(name, value))
    if (onProgress) request.upload.onprogress = (event) => onProgress(event.loaded)
    request.onload = () => resolve(request)
    request.onerror = () => reject(new Error('네트워크 오류'))
    request.send(body)
  })
}

async function uploadSimplePut(session: Schemas['UploadSession'], file: File, progress: Progress) {
  const response = await xhr('PUT', session.url, file, session.headers, (loaded) => progress(loaded, file.size))
  if (response.status < 200 || response.status >= 300) throw new Error(`업로드 실패 (${response.status})`)
}

function committedBytes(response: XMLHttpRequest): number {
  const range = response.getResponseHeader('Range') // e.g. "bytes=0-8388607"
  const end = range ? Number(range.split('-')[1]) : -1
  return Number.isFinite(end) ? end + 1 : 0
}

async function uploadGcsResumable(session: Schemas['UploadSession'], file: File, progress: Progress) {
  const start = await fetch(session.url, { method: 'POST', headers: session.headers })
  const sessionUri = start.headers.get('Location')
  if (!start.ok || !sessionUri) throw new Error(`업로드를 시작할 수 없습니다 (${start.status})`)

  let offset = 0
  let retries = 0
  while (offset < file.size) {
    const end = Math.min(offset + CHUNK_BYTES, file.size)
    try {
      const response = await xhr(
        'PUT',
        sessionUri,
        file.slice(offset, end),
        { 'Content-Range': `bytes ${offset}-${end - 1}/${file.size}` },
        (loaded) => progress(offset + loaded, file.size),
      )
      if (response.status === 200 || response.status === 201) return
      if (response.status !== 308) throw new Error(`업로드 실패 (${response.status})`)
      offset = committedBytes(response)
      retries = 0
    } catch (error) {
      if (++retries > MAX_RETRIES) throw error
      await new Promise((resolve) => setTimeout(resolve, Math.min(30_000, 1000 * 2 ** retries)))
      // Ask how much the server already has, then continue from there.
      const status = await xhr('PUT', sessionUri, null, { 'Content-Range': `bytes */${file.size}` })
      if (status.status === 200 || status.status === 201) return
      offset = committedBytes(status)
    }
  }
}

export async function uploadVideo(
  reportId: string,
  file: File,
  progress: Progress,
  onStarted?: () => unknown,
): Promise<Schemas['VideoOut']> {
  const session = await post<Schemas['UploadSession']>(`/api/reports/${reportId}/video/uploads`, {
    filename: file.name,
    size_bytes: file.size,
  })
  void onStarted?.()
  if (session.protocol === 'put') await uploadSimplePut(session, file, progress)
  else await uploadGcsResumable(session, file, progress)
  return post<Schemas['VideoOut']>(`/api/video-uploads/${session.upload_id}/complete`)
}
