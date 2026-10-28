import { useQueryClient } from '@tanstack/react-query'

import { patch, type Schemas } from '../api/client'
import { keys, useTaxonomy } from '../api/queries'
import { AutoField } from './AutoField'

type Report = Schemas['ReportDetail']

// Report information in display order; `true` marks fields required for export.
const REPORT_FIELDS: [string, string, boolean?][] = [
  ['report_number', '보고서 번호', true],
  ['pipe_number', '관로 번호', true],
  ['survey_purpose', '조사 목적'],
  ['survey_date', '조사일', true],
  ['buried_years', '매설 연수', true],
  ['inspector', '조사자'],
  ['contractor', '시공사'],
  ['treatment_area', '처리구역'],
  ['drainage_area', '배수구역'],
  ['drainage_district', '배수분구'],
  ['drain_type', '배수방식'],
  ['drain_system', '배제방식'],
  ['province', '시도'],
  ['city_county', '시군구'],
  ['town', '읍면동'],
  ['village', '리'],
  ['lot_number', '지번'],
  ['road_address', '도로명 주소'],
  ['pipe_type', '관종'],
  ['category', '구분'],
  ['specification', '규격'],
]
const MANHOLE_COLUMNS: [string, string][] = [
  ['manhole_number', '맨홀번호'],
  ['manhole_type', '맨홀종류'],
  ['internal_material', '내부재질'],
  ['cover_material', '뚜껑재질'],
  ['el_m', 'E.L(m)'],
  ['size', '크기'],
  ['depth_m', '깊이(m)'],
  ['invert', '인버트'],
  ['ladder_shape', '사다리'],
  ['latitude', '위도'],
  ['longitude', '경도'],
]

function useReportUpdate(report: Report) {
  const queryClient = useQueryClient()
  return async (path: string, body: Record<string, unknown>) => {
    const updated = await patch<Report>(`/api/reports/${report.id}${path}`, body)
    queryClient.setQueryData(keys.report(report.id), updated)
    if ('report_number' in body || 'pipe_number' in body) {
      await queryClient.invalidateQueries({ queryKey: keys.reports(report.business_id) })
    }
  }
}

function fieldValue(report: Report, field: string): string | null {
  if (field === 'report_number') return report.report_number
  if (field === 'pipe_number') return report.pipe_number
  return report.fields[field] ?? null
}

export function ReportInfoTable({ report }: { report: Report }) {
  const update = useReportUpdate(report)
  return (
    <section className="ws-section">
      <header>
        <h2>보고서 정보</h2>
      </header>
      <div className="info-grid">
        {REPORT_FIELDS.map(([field, label, required]) => {
          const value = fieldValue(report, field)
          const missing = required && (value == null || value === '')
          return (
            <label key={field} className={`info-cell${missing ? ' missing' : ''}`}>
              <span>
                {label}
                {required && ' *'}
              </span>
              <AutoField
                label={label}
                value={value}
                required={required}
                placeholder="입력"
                onSave={(next) => update('', { [field]: next })}
              />
            </label>
          )
        })}
      </div>
    </section>
  )
}

export function PipeSection({ report }: { report: Report }) {
  const update = useReportUpdate(report)
  const taxonomy = useTaxonomy()
  const pipe = report.pipe_information as Record<string, number | boolean | null>
  const survey = report.actual_survey as Record<string, string | number | null>
  const upstream = report.manholes.upstream?.manhole_number ?? '상류'
  const downstream = report.manholes.downstream?.manhole_number ?? '하류'
  const asNumber = (value: string | null) => (value === null ? null : Number(value))

  return (
    <section className="ws-section">
      <header>
        <h2>관로 정보</h2>
      </header>
      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th />
              {MANHOLE_COLUMNS.map(([, label]) => (
                <th key={label}>{label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(['upstream', 'downstream'] as const).map((role) => (
              <tr key={role}>
                <th>{role === 'upstream' ? '상류 *' : '하류 *'}</th>
                {MANHOLE_COLUMNS.map(([field, label]) => (
                  <td key={field}>
                    <AutoField
                      label={`${role === 'upstream' ? '상류' : '하류'} ${label}`}
                      value={report.manholes[role]?.[field] ?? null}
                      onSave={(value) => update(`/manholes/${role}`, { [field]: value })}
                    />
                  </td>
                ))}
              </tr>
            ))}
            <tr>
              <th>연장(m) *</th>
              <td>
                <AutoField
                  label="연장"
                  type="number"
                  required
                  value={pipe.length_m as number | null}
                  onSave={(value) => update('/pipe-information', { length_m: asNumber(value) })}
                />
              </td>
              <th>총주행(m) *</th>
              <td>
                <AutoField
                  label="총주행거리"
                  type="number"
                  required
                  value={pipe.total_drive_distance_m as number | null}
                  onSave={(value) => update('/pipe-information', { total_drive_distance_m: asNumber(value) })}
                />
              </td>
              <th>완주</th>
              <td>{pipe.is_completed ? '완주' : '미완주'}</td>
              <th>미주행(m)</th>
              <td className="num">{pipe.undriven_distance_m ?? '—'}</td>
              <td colSpan={4} />
            </tr>
          </tbody>
        </table>
      </div>
      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th />
              <th>주행방향 → 맨홀</th>
              <th>발생지점(m)</th>
              <th>미주행사유</th>
              <th className="left">설명</th>
            </tr>
          </thead>
          <tbody>
            {(['start', 'end'] as const).map((side) => (
              <tr key={side}>
                <th>{side === 'start' ? '시작→끝' : '끝→시작'}</th>
                <td className="num">{side === 'start' ? `${upstream} → ${downstream}` : `${downstream} → ${upstream}`}</td>
                <td>
                  <AutoField
                    label="발생지점"
                    type="number"
                    value={survey[`${side}_occurrence_point_m`] as number | null}
                    onSave={(value) => update('/actual-survey', { [`${side}_occurrence_point_m`]: asNumber(value) })}
                  />
                </td>
                <td>
                  <AutoField
                    label="미주행사유"
                    value={survey[`${side}_undriven_reason`] as string | null}
                    options={taxonomy.data?.undriven_reasons ?? []}
                    onSave={(value) => update('/actual-survey', { [`${side}_undriven_reason`]: value })}
                  />
                </td>
                <td className="wide">
                  <AutoField
                    label="미주행사유설명"
                    value={survey[`${side}_undriven_reason_detail`] as string | null}
                    onSave={(value) => update('/actual-survey', { [`${side}_undriven_reason_detail`]: value })}
                  />
                </td>
              </tr>
            ))}
            <tr>
              <th>조사내용</th>
              <td colSpan={4} className="wide">
                <AutoField
                  label="조사내용"
                  multiline
                  placeholder="입력"
                  value={survey.survey_content as string | null}
                  onSave={(value) => update('/actual-survey', { survey_content: value })}
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  )
}
