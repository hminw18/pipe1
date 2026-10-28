import { useState } from 'react'

import { ApiError, get, post, type Schemas } from '../api/client'
import { useReports } from '../api/queries'
import { Modal } from '../components/Modal'
import { errorText } from '../errors'

const FIELD_LABELS: Record<string, string> = {
  report_number: '보고서 번호',
  pipe_number: '관로 번호',
  survey_date: '조사일',
  buried_years: '매설 연수',
  length_m: '연장',
  total_drive_distance_m: '총주행거리',
  upstream_manhole: '상류맨홀',
  downstream_manhole: '하류맨홀',
}

async function waitForJob(id: string, onProgress: (job: Schemas['JobOut']) => void): Promise<Schemas['JobOut']> {
  for (;;) {
    const job = await get<Schemas['JobOut']>(`/api/jobs/${id}`)
    onProgress(job)
    if (job.status === 'succeeded' || job.status === 'failed') return job
    await new Promise((resolve) => setTimeout(resolve, 1500))
  }
}

export function ExportBar({ report }: { report: Schemas['ReportDetail'] }) {
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [pdfDialog, setPdfDialog] = useState(false)
  const missing = report.export_missing.map((field) => FIELD_LABELS[field] ?? field)

  async function run(body: Record<string, unknown>) {
    setError(null)
    setStatus('준비 중…')
    try {
      const job = await post<Schemas['JobOut']>(`/api/reports/${report.id}/exports`, body)
      const done = await waitForJob(job.id, (current) => setStatus(`${current.status === 'running' ? '생성 중' : '대기 중'}…`))
      if (done.status === 'failed' || !done.download_url) throw new Error(done.error ?? '보고서 생성에 실패했습니다.')
      window.location.assign(done.download_url)
      setStatus(null)
    } catch (failure) {
      setStatus(null)
      if (failure instanceof ApiError && failure.code === 'comparison_mismatch') {
        const fields = (failure.detail as { fields: string[] }).fields
        setError(`보수후 보고서와 다른 항목: ${fields.join(', ')}`)
      } else {
        setError(errorText(failure))
      }
    }
  }

  return (
    <div className="export-bar">
      {!report.export_ready && (
        <span className="missing">
          필수 항목 {missing.length}개 누락 · {missing.join(', ')}
        </span>
      )}
      {status && <span className="status">{status}</span>}
      {error && <span className="missing">{error}</span>}
      <button disabled={!report.export_ready || status !== null} onClick={() => void run({ kind: 'excel' })} title="Excel 보고서">
        Excel
      </button>
      <button disabled={!report.export_ready || status !== null} onClick={() => setPdfDialog(true)} title="PDF 보고서">
        PDF
      </button>
      {pdfDialog && (
        <PdfDialog
          report={report}
          onClose={() => setPdfDialog(false)}
          onSubmit={(body) => {
            setPdfDialog(false)
            void run({ kind: 'pdf', ...body })
          }}
        />
      )}
    </div>
  )
}

function PdfDialog({
  report,
  onClose,
  onSubmit,
}: {
  report: Schemas['ReportDetail']
  onClose: () => void
  onSubmit: (body: Record<string, unknown>) => void
}) {
  const reports = useReports(report.business_id)
  const [type, setType] = useState('inspection')
  const [after, setAfter] = useState('')
  return (
    <Modal title="PDF 보고서" onClose={onClose}>
      <form
        className="stack"
        onSubmit={(event) => {
          event.preventDefault()
          onSubmit(type === 'comparison' ? { report_type: type, after_report_id: after } : { report_type: type })
        }}
      >
        <label>
          종류
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="inspection">조사 보고서</option>
            <option value="post_repair">보수후 보고서</option>
            <option value="comparison">보수 비교 보고서 (BEFORE/AFTER)</option>
          </select>
        </label>
        {type === 'comparison' && (
          <label>
            보수후(AFTER) 보고서
            <select value={after} onChange={(e) => setAfter(e.target.value)} required>
              <option value="">선택</option>
              {report.versions
                .filter((v) => v.id !== report.id)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    {report.report_number} {v.version_name}
                  </option>
                ))}
              {reports.data
                ?.filter((r) => r.report_group_id !== report.report_group_id)
                .map((r) => (
                  <option key={r.latest_report_id} value={r.latest_report_id}>
                    {r.report_number} / {r.pipe_number}
                  </option>
                ))}
            </select>
          </label>
        )}
        <p className="hint">비교 보고서는 영상·결함을 제외한 모든 입력 정보가 같아야 합니다(보고서 번호 제외).</p>
        <button className="primary">생성</button>
      </form>
    </Modal>
  )
}
