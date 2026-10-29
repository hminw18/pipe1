import { useQueryClient } from '@tanstack/react-query'
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react'

import { patch, post, type Schemas } from '../api/client'
import { keys } from '../api/queries'
import { formatBytes, formatTimestamp } from '../format'
import { errorText } from '../errors'
import { useSession } from '../session/context'
import { elementRectToVideoRect, videoRectToElementRect, type Rect } from './geometry'
import { useStopAnalysis, useVideo, type PlayerApi } from './hooks'
import { grabFrame, type LocalVideo } from './localVideo'

type Segment = { start_time: number; end_time: number; candidates?: { time?: number; timestamp?: number }[] }

const SPEEDS = [0.5, 1, 2]

function Uploader({
  label,
  replacing,
  busy,
  onFile,
}: {
  label: string
  replacing: boolean
  busy: boolean
  onFile: (file: File) => void
}) {
  return (
    <label className="file-button">
      {label}
      <input
        type="file"
        accept=".avi,.mkv,.mov,.mp4"
        hidden
        disabled={busy}
        onChange={(event) => {
          const file = event.target.files?.[0]
          event.target.value = ''
          if (!file) return
          if (replacing && !window.confirm('기존 영상을 교체할까요? 결함과 캡처 이미지는 유지됩니다.')) return
          onFile(file)
        }}
      />
    </label>
  )
}

/** Upload and server-processing state, shown under the player while the local file plays. */
function LocalStatus({ local, info }: { local: LocalVideo; info: Schemas['VideoOut'] | null | undefined }) {
  if (local.error) return <p className="job-line error">업로드 실패: {local.error}</p>
  if (!local.uploaded) {
    return (
      <p className="job-line">
        업로드 <span className="mono">{local.progress}%</span> <progress max={100} value={local.progress} /> 내 PC의 파일로
        재생 중입니다. 업로드가 끝날 때까지 이 페이지를 닫지 마세요.
      </p>
    )
  }
  if (info?.transcode_status === 'failed') {
    return <p className="job-line error">{info.processing_error ?? '영상 처리에 실패했습니다.'}</p>
  }
  if (info?.transcode_status !== 'ready') return <p className="job-line">업로드 완료 · 서버에서 영상 처리 중…</p>
  return null
}

export const VideoPanel = forwardRef<
  PlayerApi,
  {
    reportId: string
    canEdit: boolean
    defects: Schemas['DefectOut'][]
    local: LocalVideo | null
    onFile: (file: File) => void
    onTime?: (ms: number) => void
  }
>(function VideoPanel({ reportId, canEdit, defects, local, onFile, onTime }, ref) {
  const video = useVideo(reportId)
  const analysis = useStopAnalysis(reportId)
  const queryClient = useQueryClient()
  const { markActive } = useSession()
  const element = useRef<HTMLVideoElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [timeMs, setTimeMs] = useState(0)
  const [drawing, setDrawing] = useState(false)
  const [draft, setDraft] = useState<Rect | null>(null)
  const [error, setError] = useState<string | null>(null)
  const resumeAt = useRef<number | null>(null)
  const [stageSize, setStageSize] = useState({ width: 0, height: 0 })
  const [videoSize, setVideoSize] = useState({ width: 0, height: 0 })
  const [elementDuration, setElementDuration] = useState(0)
  const info = video.data
  const ready = info?.transcode_status === 'ready'
  // The local file plays from the moment it is chosen; otherwise the processed copy.
  const src = local?.url ?? (ready ? info?.playback_url : null)
  const fps = info?.fps || 30
  const durationMs = (info?.duration_seconds || elementDuration) * 1000
  const uploading = Boolean(local && !local.uploaded && !local.error)

  const seekMs = useCallback((ms: number) => {
    const node = element.current
    if (!node) return
    node.currentTime = Math.max(0, Math.min(ms / 1000, node.duration || ms / 1000))
  }, [])

  useImperativeHandle(
    ref,
    () => ({
      currentMs: () => Math.round((element.current?.currentTime ?? 0) * 1000),
      seekMs,
      pause: () => element.current?.pause(),
      toggle: () => {
        const node = element.current
        if (!node) return
        if (node.paused) void node.play()
        else node.pause()
      },
      stepFrames: (frames) => {
        const node = element.current
        if (!node) return
        node.pause()
        node.currentTime = Math.max(0, node.currentTime + frames / fps)
      },
      jumpSeconds: (seconds) => {
        const node = element.current
        if (node) node.currentTime = Math.max(0, node.currentTime + seconds)
      },
      captureFrame: async () => (local && element.current ? grabFrame(element.current) : null),
    }),
    [fps, seekMs, local],
  )

  useEffect(() => {
    if (element.current) element.current.playbackRate = speed
  }, [speed])

  useEffect(() => {
    const node = stage.current
    if (!node) return
    const observer = new ResizeObserver(([entry]) =>
      setStageSize({ width: entry.contentRect.width, height: entry.contentRect.height }),
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [src])

  // Signed playback URLs last 60 minutes; when one expires mid-review, fetch a fresh one and
  // resume at the same position (docs/Web_Auth_Spec.md §10).
  const onError = async () => {
    if (local) return
    resumeAt.current = Math.round((element.current?.currentTime ?? 0) * 1000)
    await queryClient.invalidateQueries({ queryKey: keys.video(reportId) })
  }

  const latestJob = analysis.data?.[0]
  const segments: Segment[] = (latestJob?.status === 'succeeded' && (latestJob.result?.segments as Segment[])) || []

  async function startAnalysis() {
    setError(null)
    try {
      await post(`/api/reports/${reportId}/stop-analysis`)
      await queryClient.invalidateQueries({ queryKey: keys.jobs(reportId, 'stop_analysis') })
    } catch (failure) {
      setError(errorText(failure))
    }
  }

  function pointer(event: ReactPointerEvent) {
    const box = stage.current!.getBoundingClientRect()
    return { x: event.clientX - box.left, y: event.clientY - box.top }
  }

  async function saveRoi() {
    const node = element.current
    const box = stage.current?.getBoundingClientRect()
    if (!draft || !node || !box) return
    const roi = elementRectToVideoRect(
      draft,
      { width: box.width, height: box.height },
      { width: node.videoWidth, height: node.videoHeight },
    )
    if (!roi) return
    try {
      const updated = await patch<Schemas['VideoOut']>(`/api/reports/${reportId}/video`, { depth_roi: roi })
      queryClient.setQueryData(keys.video(reportId), updated)
      setDrawing(false)
      setDraft(null)
    } catch (failure) {
      setError(errorText(failure))
    }
  }

  const header = (
    <header>
      <h2>영상</h2>
      {(info || local) && (
        <span className="meta">
          {local?.name ?? info?.original_filename}
          {info && ready && (
            <span className="mono">
              {' · '}
              {info.width && info.height ? `${info.width}×${info.height} · ` : ''}
              {info.fps ? `${Number(info.fps.toFixed(2))}fps · ` : ''}
              {formatBytes(info.size_bytes)}
            </span>
          )}
        </span>
      )}
    </header>
  )

  if (video.isLoading) {
    return (
      <section className="ws-section">
        {header}
        <div className="video-empty">영상 불러오는 중…</div>
      </section>
    )
  }
  if (!src) {
    return (
      <section className="ws-section">
        {header}
        <div className="video-empty">
          {!info ? (
            <p>등록된 영상이 없습니다.</p>
          ) : info.transcode_status === 'failed' ? (
            <p className="error">{info.processing_error ?? '영상 처리에 실패했습니다.'}</p>
          ) : info.transcode_status === 'uploading' ? (
            <p>업로드가 끝나지 않았습니다. 영상 파일을 다시 선택하면 바로 재생하며 다시 올립니다.</p>
          ) : (
            <p>
              서버에서 영상 처리 중입니다… <span className="mono">{formatBytes(info.size_bytes)}</span> 원본 파일을
              선택하면 기다리지 않고 바로 검토할 수 있습니다.
            </p>
          )}
          {canEdit && (
            <Uploader
              label={!info ? '영상 등록' : info.transcode_status === 'failed' ? '영상 교체' : '영상 파일 선택'}
              replacing={false}
              busy={false}
              onFile={onFile}
            />
          )}
        </div>
      </section>
    )
  }

  const intrinsic = {
    width: videoSize.width || info?.width || 0,
    height: videoSize.height || info?.height || 0,
  }
  const savedRoi =
    info?.depth_roi && stageSize.width && intrinsic.width
      ? videoRectToElementRect(
          { x: info.depth_roi[0], y: info.depth_roi[1], w: info.depth_roi[2], h: info.depth_roi[3] },
          stageSize,
          intrinsic,
        )
      : null
  const candidateCount = segments.reduce((sum, segment) => sum + (segment.candidates?.length ?? 0), 0)
  const step = (frames: number) => {
    const node = element.current
    if (!node) return
    node.pause()
    node.currentTime = Math.max(0, node.currentTime + frames / fps)
  }
  const jump = (seconds: number) => {
    const node = element.current
    if (node) node.currentTime = Math.max(0, node.currentTime + seconds)
  }

  return (
    <section className="ws-section">
      {header}
      <div>
        <div
          className={`video-stage${drawing ? ' drawing' : ''}`}
          ref={stage}
          onPointerDown={(event) => {
            if (!drawing) return
            const start = pointer(event)
            setDraft({ ...start, w: 0, h: 0 })
            ;(event.target as HTMLElement).setPointerCapture(event.pointerId)
          }}
          onPointerMove={(event) => {
            if (!drawing || !draft || event.buttons !== 1) return
            const at = pointer(event)
            setDraft({ x: draft.x, y: draft.y, w: at.x - draft.x, h: at.y - draft.y })
          }}
        >
          <video
            ref={element}
            src={src ?? undefined}
            preload="metadata"
            crossOrigin="anonymous"
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onLoadedMetadata={(event) => {
              setVideoSize({ width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight })
              setElementDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)
              if (resumeAt.current !== null) {
                seekMs(resumeAt.current)
                resumeAt.current = null
              }
            }}
            onTimeUpdate={(event) => {
              const ms = Math.round(event.currentTarget.currentTime * 1000)
              setTimeMs(ms)
              onTime?.(ms)
              if (!event.currentTarget.paused) markActive()
            }}
            onError={() => void onError()}
          />
          {savedRoi && !drawing && <div className="roi saved" ref={(node) => positionBox(node, savedRoi)} />}
          {draft && <div className="roi draft" ref={(node) => positionBox(node, normalize(draft))} />}
        </div>

        <div
          className="timeline"
          onClick={(event) => {
            const box = event.currentTarget.getBoundingClientRect()
            seekMs(((event.clientX - box.left) / box.width) * durationMs)
          }}
        >
          {info?.thumbnail_url && <img src={info.thumbnail_url} alt="" className="timeline-strip" />}
          {segments.map((segment, index) => (
            <div
              key={`s${index}`}
              className="timeline-segment"
              title={`의심구간 ${formatTimestamp(segment.start_time * 1000)}`}
              ref={(node) => positionSpan(node, segment.start_time * 1000, segment.end_time * 1000, durationMs)}
            />
          ))}
          {segments.flatMap((segment, index) =>
            (segment.candidates ?? []).map((candidate, c) => {
              const at = (candidate.time ?? candidate.timestamp ?? segment.start_time) * 1000
              return (
                <button
                  key={`c${index}-${c}`}
                  className="timeline-candidate"
                  aria-label={`후보 프레임 ${formatTimestamp(at)}`}
                  title={`후보 프레임 ${formatTimestamp(at)}`}
                  ref={(node) => positionAt(node, at, durationMs)}
                  onClick={(event) => {
                    event.stopPropagation()
                    seekMs(at)
                  }}
                />
              )
            }),
          )}
          {defects.map((defect) => {
            const label = `${formatTimestamp(defect.timestamp_ms)} ${defect.grade ?? ''} ${defect.defect_item ?? defect.condition_item ?? ''}`
            return (
              <button
                key={defect.id}
                className={`timeline-defect grade-${GRADE_CLASS[defect.grade ?? ''] ?? 'none'}`}
                aria-label={label}
                title={label}
                ref={(node) => positionAt(node, defect.timestamp_ms, durationMs)}
                onClick={(event) => {
                  event.stopPropagation()
                  seekMs(defect.timestamp_ms)
                }}
              />
            )
          })}
          <div className="timeline-cursor" ref={(node) => positionAt(node, timeMs, durationMs)} />
        </div>

        <div className="transport">
          <button
            className="icon"
            onClick={() => (playing ? element.current?.pause() : void element.current?.play())}
            aria-label={playing ? '일시정지' : '재생'}
            title="재생/일시정지 (Space)"
          >
            {playing ? <PauseIcon /> : <PlayIcon />}
          </button>
          <button className="mono" onClick={() => jump(-5)} aria-label="5초 뒤로" title="5초 뒤로 (Shift+←)">
            −5s
          </button>
          <button className="icon" onClick={() => step(-1)} aria-label="이전 프레임" title="이전 프레임 (←)">
            <ChevronIcon d="M7.5 2.5 4 6l3.5 3.5" />
          </button>
          <button className="icon" onClick={() => step(1)} aria-label="다음 프레임" title="다음 프레임 (→)">
            <ChevronIcon d="M4.5 2.5 8 6 4.5 9.5" />
          </button>
          <button className="mono" onClick={() => jump(5)} aria-label="5초 앞으로" title="5초 앞으로 (Shift+→)">
            +5s
          </button>
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="재생 속도">
            {SPEEDS.map((value) => (
              <option key={value} value={value}>
                {value.toFixed(1)}×
              </option>
            ))}
          </select>
          <span className="time">
            {formatTimestamp(timeMs)} <span className="total">/ {formatTimestamp(durationMs)}</span>
          </span>
          <span className="keys" aria-hidden="true">
            <span className="kbd">Space</span>
            <span className="kbd">← →</span>프레임<span className="kbd">⇧← →</span>5초
          </span>
          <span className="spacer" />
          {latestJob?.status === 'succeeded' && (
            <span className="counts">
              의심구간 <span className="mono">{segments.length}</span> · 후보 <span className="mono">{candidateCount}</span>
            </span>
          )}
          {canEdit && (
            <>
              {drawing ? (
                <>
                  <button className="primary" onClick={() => void saveRoi()} disabled={!draft}>
                    깊이 영역 저장
                  </button>
                  <button
                    onClick={() => {
                      setDrawing(false)
                      setDraft(null)
                    }}
                  >
                    취소
                  </button>
                </>
              ) : (
                <button
                  onClick={() => {
                    element.current?.pause()
                    setDrawing(true)
                  }}
                  title="영상 위에서 거리 숫자 영역을 드래그하세요"
                >
                  깊이 영역
                </button>
              )}
              <button
                onClick={() => void startAnalysis()}
                disabled={!ready || latestJob?.status === 'queued' || latestJob?.status === 'running'}
                title={ready ? undefined : '업로드와 서버 처리가 끝나면 분석할 수 있습니다'}
              >
                의심구간 분석
              </button>
              <Uploader label="영상 교체" replacing={Boolean(info)} busy={uploading} onFile={onFile} />
            </>
          )}
        </div>
        {local && <LocalStatus local={local} info={info} />}
        {drawing && <p className="job-line">영상 위에서 거리 숫자가 표시되는 영역을 드래그한 뒤 저장하세요.</p>}
        {latestJob && (latestJob.status === 'queued' || latestJob.status === 'running') && (
          <p className="job-line">
            의심구간 분석 <span className="mono">{latestJob.progress}%</span> {latestJob.message ?? ''}
            <progress max={100} value={latestJob.progress} />
          </p>
        )}
        {latestJob?.status === 'failed' && <p className="job-line error">{latestJob.error}</p>}
        {error && <p className="job-line error">{error}</p>}
      </div>
    </section>
  )
})

const GRADE_CLASS: Record<string, string> = { 대: 'l', 중: 'm', 소: 's' }

function PlayIcon() {
  return (
    <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
      <path d="M3 1.5v9l7-4.5z" fill="currentColor" />
    </svg>
  )
}

function PauseIcon() {
  return (
    <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12">
      <path d="M3 2h2v8H3zM7 2h2v8H7z" fill="currentColor" />
    </svg>
  )
}

function ChevronIcon({ d }: { d: string }) {
  return (
    <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
      <path d={d} />
    </svg>
  )
}

function normalize(rect: Rect): Rect {
  return {
    x: Math.min(rect.x, rect.x + rect.w),
    y: Math.min(rect.y, rect.y + rect.h),
    w: Math.abs(rect.w),
    h: Math.abs(rect.h),
  }
}

// Positions are set through the CSSOM (element.style), which the strict CSP permits, rather
// than inline style attributes.
function positionBox(node: HTMLElement | null, rect: Rect) {
  if (!node) return
  node.style.left = `${rect.x}px`
  node.style.top = `${rect.y}px`
  node.style.width = `${rect.w}px`
  node.style.height = `${rect.h}px`
}

function positionAt(node: HTMLElement | null, ms: number, durationMs: number) {
  if (node && durationMs > 0) node.style.left = `${Math.min(100, (ms / durationMs) * 100)}%`
}

function positionSpan(node: HTMLElement | null, startMs: number, endMs: number, durationMs: number) {
  if (!node || durationMs <= 0) return
  node.style.left = `${(startMs / durationMs) * 100}%`
  node.style.width = `${Math.max(0.3, ((endMs - startMs) / durationMs) * 100)}%`
}
