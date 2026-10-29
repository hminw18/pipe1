import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { del, post, type Schemas } from '../api/client'
import { keys, useProject, useReport } from '../api/queries'
import { errorText } from '../errors'
import { DefectPanel, type DefectPanelApi } from './DefectPanel'
import { ExportBar } from './ExportBar'
import { PipeSection, ReportInfoTable } from './ReportTables'
import { useDefects, useVideo, type PlayerApi } from './hooks'
import { useLocalVideo } from './localVideo'
import { VideoPanel } from './VideoPanel'

const GRADE_KEYS: Record<string, string> = { '3': '대', '2': '중', '1': '소' }

function isTyping(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null
  return Boolean(element && (element.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(element.tagName)))
}

export function ReportWorkspace() {
  const { reportId = '' } = useParams()
  const report = useReport(reportId)
  const project = useProject(report.data?.project_id)
  const video = useVideo(reportId)
  const defects = useDefects(reportId)
  const { local, start: startLocal } = useLocalVideo(reportId)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const player = useRef<PlayerApi>(null)
  const defectPanel = useRef<DefectPanelApi>(null)
  const [error, setError] = useState<string | null>(null)
  // Anyone who can open the report may edit it (view == edit in the permission matrix).
  const canEdit = Boolean(project.data)

  // Keyboard shortcuts (Product_Requirements §7). Ignored while typing in a field, except Enter
  // which submits the defect form through the form itself.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (isTyping(event.target) || event.ctrlKey || event.metaKey || event.altKey) return
      const api = player.current
      switch (event.key) {
        case ' ':
          api?.toggle()
          break
        case 'c':
        case 'C':
          defectPanel.current?.capture()
          break
        case 'ArrowLeft':
          if (event.shiftKey) api?.jumpSeconds(-5)
          else api?.stepFrames(-1)
          break
        case 'ArrowRight':
          if (event.shiftKey) api?.jumpSeconds(5)
          else api?.stepFrames(1)
          break
        case 'Enter':
          defectPanel.current?.save()
          break
        case 'Delete':
          defectPanel.current?.deleteSelected()
          break
        default:
          if (event.key in GRADE_KEYS) defectPanel.current?.setGrade(GRADE_KEYS[event.key])
          else return
      }
      event.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (report.isLoading) return <div className="page">불러오는 중…</div>
  if (!report.data) return <div className="page">보고서를 찾을 수 없습니다.</div>
  const data: Schemas['ReportDetail'] = report.data

  async function newVersion() {
    setError(null)
    try {
      const created = await post<Schemas['ReportDetail']>(`/api/report-groups/${data.report_group_id}/versions`)
      await queryClient.invalidateQueries({ queryKey: keys.reports(data.business_id) })
      navigate(`/reports/${created.id}`)
    } catch (failure) {
      setError(errorText(failure))
    }
  }

  async function deleteVersion() {
    if (!window.confirm(`${data.version_name} 버전을 삭제할까요?`)) return
    try {
      await del(`/api/reports/${data.id}`)
      const remaining = data.versions.filter((v) => v.id !== data.id)
      await queryClient.invalidateQueries({ queryKey: keys.reports(data.business_id) })
      navigate(`/reports/${remaining[remaining.length - 1].id}`)
    } catch (failure) {
      setError(errorText(failure))
    }
  }

  return (
    <div className="workspace">
      <div className="ws-head">
        <h1>
          {data.report_number} <span className="sep">/</span> {data.pipe_number}
        </h1>
        <label>
          버전
          <select value={data.id} onChange={(e) => navigate(`/reports/${e.target.value}`)} aria-label="보고서 버전">
            {data.versions.map((v) => (
              <option key={v.id} value={v.id}>
                {v.version_name}
              </option>
            ))}
          </select>
        </label>
        {canEdit && (
          <button className="small" onClick={() => void newVersion()}>
            새 버전
          </button>
        )}
        {canEdit && data.versions.length > 1 && (
          <button className="small link danger" onClick={() => void deleteVersion()}>
            버전 삭제
          </button>
        )}
        <span className="spacer" />
        <ExportBar report={data} />
      </div>
      {error && <p className="error ws-error">{error}</p>}
      <ReportInfoTable report={data} />
      <PipeSection report={data} />
      <VideoPanel
        ref={player}
        reportId={reportId}
        canEdit={canEdit}
        defects={defects.data ?? []}
        local={local}
        onFile={(file) => void startLocal(file)}
      />
      <DefectPanel
        ref={defectPanel}
        reportId={reportId}
        // Capturing needs the video row; a local file supplies the frames before processing ends.
        canEdit={canEdit && Boolean(video.data) && (Boolean(local) || video.data?.transcode_status === 'ready')}
        hasRoi={Boolean(video.data?.depth_roi)}
        player={player}
      />
    </div>
  )
}
