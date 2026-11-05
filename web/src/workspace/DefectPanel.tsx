import { useQuery, useQueryClient } from '@tanstack/react-query'
import { forwardRef, useImperativeHandle, useState } from 'react'
import { createPortal } from 'react-dom'

import { ApiError, del, get, patch, post, type Schemas } from '../api/client'
import { keys, useTaxonomy } from '../api/queries'
import { formatTimestamp } from '../format'
import { errorText } from '../errors'
import { useDefects, useSuggestions, type PlayerApi } from './hooks'
import { isOpen, type Suggestion } from './suggestions'

type Defect = Schemas['DefectOut']
type Draft = {
  timestamp_ms: number | null
  drive_direction: string
  item_category: string
  kind: 'defect' | 'condition'
  condition_item: string
  defect_item: string
  grade: string
  quadrant: string
  distance_m: string
  manhole_defect_depth_m: string
  memo: string
  frame: string | null // base64 PNG grabbed from the local file; null: server extracts it
  suggestion: Suggestion | null // the AI suggestion this defect confirms
}

export type DefectPanelApi = {
  capture: () => void
  setGrade: (grade: string) => void
  save: () => void
  deleteSelected: () => void
}

const EMPTY: Draft = {
  timestamp_ms: null,
  drive_direction: '순주행',
  item_category: '관로',
  kind: 'defect',
  condition_item: '',
  defect_item: '',
  grade: '',
  quadrant: '',
  distance_m: '',
  manhole_defect_depth_m: '',
  memo: '',
  frame: null,
  suggestion: null,
}

function numberOrNull(value: string): number | null {
  return value.trim() === '' ? null : Number(value)
}

export const DefectPanel = forwardRef<
  DefectPanelApi,
  {
    reportId: string
    canEdit: boolean
    hasRoi: boolean
    player: React.RefObject<PlayerApi | null>
    // Where the AI suggestion list goes instead of inside this panel (temporary demo layout).
    suggestionSlot?: HTMLElement | null
  }
>(function DefectPanel({ reportId, canEdit, hasRoi, player, suggestionSlot }, ref) {
  const taxonomy = useTaxonomy()
  const defects = useDefects(reportId)
  const suggestions = useSuggestions(reportId)
  const queryClient = useQueryClient()
  const summary = useQuery({
    queryKey: keys.gradeSummary(reportId),
    queryFn: () => get<Schemas['GradeSummary']>(`/api/reports/${reportId}/grade-summary`),
  })
  const [draft, setDraft] = useState<Draft>(EMPTY)
  const [editing, setEditing] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showRejected, setShowRejected] = useState(false)

  const category = taxonomy.data?.categories.find((c) => c.category === draft.item_category)
  const definition = category?.defect_items.find((d) => d.item === draft.defect_item)
  const grades = definition ? (taxonomy.data?.grades ?? []).filter((g) => g in definition.scores) : []

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: keys.defects(reportId) })
    await queryClient.invalidateQueries({ queryKey: keys.gradeSummary(reportId) })
    await queryClient.invalidateQueries({ queryKey: keys.report(reportId) })
    await queryClient.invalidateQueries({ queryKey: keys.suggestions(reportId) })
  }

  async function capture() {
    if (!canEdit || !player.current) return
    player.current.pause()
    const at = player.current.currentMs()
    setEditing(null)
    setError(null)
    const frame = await player.current.captureFrame()
    setDraft((current) => ({ ...current, timestamp_ms: at, distance_m: '', frame }))
    if (hasRoi) {
      try {
        const suggestion = await post<Schemas['DistanceSuggestion']>(`/api/reports/${reportId}/distance`, {
          timestamp_ms: at,
          frame_png_base64: frame,
        })
        if (suggestion.distance_m != null) {
          setDraft((current) =>
            current.timestamp_ms === at ? { ...current, distance_m: String(suggestion.distance_m) } : current,
          )
        }
      } catch {
        // Recognition is a convenience; the user can type the distance.
      }
    }
  }

  function validGrades(itemCategory: string, item: string): string[] {
    const def = taxonomy.data?.categories.find((c) => c.category === itemCategory)?.defect_items.find((d) => d.item === item)
    return def ? (taxonomy.data?.grades ?? []).filter((g) => g in def.scores) : []
  }

  function setDefectItem(item: string) {
    const valid = validGrades(draft.item_category, item)
    // A defect with exactly one valid grade gets it automatically (Product_Requirements §4.8).
    setDraft({ ...draft, defect_item: item, grade: valid.length === 1 ? valid[0] : valid.includes(draft.grade) ? draft.grade : '' })
  }

  async function save() {
    if (!canEdit || busy) return
    setBusy(true)
    setError(null)
    const fields = {
      drive_direction: draft.drive_direction,
      item_category: draft.item_category || null,
      condition_item: draft.kind === 'condition' ? draft.condition_item || null : null,
      defect_item: draft.kind === 'defect' ? draft.defect_item || null : null,
      grade: draft.kind === 'defect' ? draft.grade || null : null,
      quadrant: draft.quadrant || null,
      distance_m: numberOrNull(draft.distance_m),
      manhole_defect_depth_m: numberOrNull(draft.manhole_defect_depth_m),
      memo: draft.memo || null,
    }
    try {
      if (editing) {
        await patch(`/api/defects/${editing}`, fields)
      } else {
        if (draft.timestamp_ms == null) throw new Error('먼저 화면을 캡처하세요 (C).')
        await post(`/api/reports/${reportId}/defects`, {
          ...fields,
          timestamp_ms: draft.timestamp_ms,
          frame_png_base64: draft.frame,
          suggestion_id: draft.suggestion?.id ?? null,
        })
      }
      setDraft({ ...EMPTY, drive_direction: draft.drive_direction, item_category: draft.item_category })
      setEditing(null)
      await refresh()
    } catch (failure) {
      setError(
        failure instanceof ApiError && failure.code === 'distance_required'
          ? '거리를 인식하지 못했습니다. 직접 입력하세요.'
          : errorText(failure),
      )
    } finally {
      setBusy(false)
    }
  }

  async function remove(id: string) {
    if (!window.confirm('선택한 결함을 삭제할까요?')) return
    await del(`/api/defects/${id}`)
    if (selected === id) setSelected(null)
    await refresh()
  }

  /** Fill the form from an AI suggestion and show its best frame; the inspector captures (C),
   *  corrects anything wrong and saves, which confirms the suggestion. */
  function apply(suggestion: Suggestion) {
    const valid = validGrades(suggestion.item_category, suggestion.defect_item)
    setEditing(null)
    setError(null)
    setDraft((current) => ({
      ...EMPTY,
      drive_direction: current.drive_direction,
      item_category: suggestion.item_category,
      defect_item: suggestion.defect_item,
      grade: valid.length === 1 ? valid[0] : '',
      suggestion,
    }))
    player.current?.pause()
    player.current?.seekMs(suggestion.best_ms)
  }

  async function setSuggestionStatus(suggestion: Suggestion, status: 'pending' | 'rejected') {
    try {
      await patch(`/api/suggestions/${suggestion.id}`, { status })
      if (draft.suggestion?.id === suggestion.id) setDraft({ ...draft, suggestion: null })
      await queryClient.invalidateQueries({ queryKey: keys.suggestions(reportId) })
    } catch (failure) {
      setError(errorText(failure))
    }
  }

  useImperativeHandle(ref, () => ({
    capture: () => void capture(),
    setGrade: (grade) => {
      if (draft.kind === 'defect' && grades.includes(grade)) setDraft({ ...draft, grade })
    },
    save: () => void save(),
    deleteSelected: () => {
      if (selected) void remove(selected)
    },
  }))

  function edit(defect: Defect) {
    setEditing(defect.id)
    setSelected(defect.id)
    setDraft({
      timestamp_ms: defect.timestamp_ms,
      drive_direction: defect.drive_direction,
      item_category: defect.item_category ?? '관로',
      kind: defect.condition_item ? 'condition' : 'defect',
      condition_item: defect.condition_item ?? '',
      defect_item: defect.defect_item ?? '',
      grade: defect.grade ?? '',
      quadrant: defect.quadrant ?? '',
      distance_m: String(defect.distance_m),
      manhole_defect_depth_m: defect.manhole_defect_depth_m == null ? '' : String(defect.manhole_defect_depth_m),
      memo: defect.memo ?? '',
      frame: null,
      suggestion: null,
    })
    player.current?.seekMs(defect.timestamp_ms)
  }

  const codes = new Map<string, string>()
  for (const c of taxonomy.data?.categories ?? []) {
    for (const d of c.defect_items) if (d.code) codes.set(d.item, d.code)
    for (const d of c.condition_items) if (d.code) codes.set(d.item, d.code)
  }

  const suggestionTable = (
    <SuggestionTable
      suggestions={suggestions.data?.suggestions ?? []}
      defects={defects.data ?? []}
      canEdit={canEdit}
      applied={draft.suggestion?.id ?? null}
      showRejected={showRejected}
      onToggleRejected={() => setShowRejected(!showRejected)}
      onSeek={(ms) => player.current?.seekMs(ms)}
      onApply={apply}
      onStatus={(suggestion, status) => void setSuggestionStatus(suggestion, status)}
    />
  )

  return (
    <section className="ws-section">
      <header>
        <h2>결함</h2>
        {summary.data && (
          <span className="grade-summary">
            대 {summary.data.large} · 중 {summary.data.medium} · 소 {summary.data.small} · 계 {summary.data.total}
          </span>
        )}
      </header>
      {canEdit && (
        <form
          className="defect-form"
          onSubmit={(event) => {
            event.preventDefault()
            void save()
          }}
        >
          <div className="defect-row">
            <div className="stack-label">
              <span className="field-label">
                캡처 시점 <span className="kbd">C</span>
              </span>
              <button
                type="button"
                className={`capture-box${draft.timestamp_ms == null ? ' empty' : ''}`}
                onClick={() => void capture()}
                title="현재 화면 캡처 (C)"
              >
                {draft.timestamp_ms == null ? '캡처' : formatTimestamp(draft.timestamp_ms)}
              </button>
            </div>
            <label>
              주행방향
              <select value={draft.drive_direction} onChange={(e) => setDraft({ ...draft, drive_direction: e.target.value })}>
                {taxonomy.data?.drive_directions.map((d) => <option key={d}>{d}</option>)}
              </select>
            </label>
            <label>
              분류
              <select
                value={draft.item_category}
                onChange={(e) => setDraft({ ...draft, item_category: e.target.value, condition_item: '', defect_item: '', grade: '' })}
              >
                {taxonomy.data?.categories.map((c) => <option key={c.category}>{c.category}</option>)}
              </select>
            </label>
            <label>
              구분
              <select value={draft.kind} onChange={(e) => setDraft({ ...draft, kind: e.target.value as Draft['kind'] })}>
                <option value="defect">결함</option>
                <option value="condition">상태</option>
              </select>
            </label>
            {draft.kind === 'condition' && (
              <label className="item-select">
                상태 항목
                <select value={draft.condition_item} onChange={(e) => setDraft({ ...draft, condition_item: e.target.value })}>
                  <option value="">선택</option>
                  {category?.condition_items.map((c) => (
                    <option key={c.item} value={c.item}>
                      {c.item} {c.code}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {draft.kind === 'defect' && (
              <>
                <label className="item-select">
                  결함 항목
                  <select value={draft.defect_item} onChange={(e) => setDefectItem(e.target.value)}>
                    <option value="">선택</option>
                    {category?.defect_items.map((d) => (
                      <option key={d.item} value={d.item}>
                        {d.item} {d.code} · {d.defect_type} · {Object.entries(d.scores).map(([g, score]) => `${g}${score}`).join(' ')}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  등급
                  <select value={draft.grade} onChange={(e) => setDraft({ ...draft, grade: e.target.value })} disabled={!grades.length}>
                    <option value="">—</option>
                    {grades.map((g) => (
                      <option key={g} value={g}>
                        {g} · {definition?.scores[g]}점
                      </option>
                    ))}
                  </select>
                </label>
              </>
            )}
            <label>
              사분면
              <select value={draft.quadrant} onChange={(e) => setDraft({ ...draft, quadrant: e.target.value })}>
                <option value="">—</option>
                {taxonomy.data?.quadrants.map((q) => <option key={q}>{q}</option>)}
              </select>
            </label>
            <label>
              거리(m) *
              <input
                type="number"
                step="any"
                value={draft.distance_m}
                onChange={(e) => setDraft({ ...draft, distance_m: e.target.value })}
              />
            </label>
            <label>
              맨홀 결함 깊이(m)
              <input
                type="number"
                step="any"
                value={draft.manhole_defect_depth_m}
                onChange={(e) => setDraft({ ...draft, manhole_defect_depth_m: e.target.value })}
              />
            </label>
          </div>
          <div className="memo-row">
            <input
              placeholder="메모"
              value={draft.memo}
              maxLength={2000}
              onChange={(e) => setDraft({ ...draft, memo: e.target.value })}
              aria-label="메모"
            />
            {editing && <span className="editing-tag">수정 중</span>}
            {draft.suggestion && (
              <span className="editing-tag">
                AI 제안 {draft.suggestion.code} {draft.suggestion.defect_item} · C로 캡처 후 저장
              </span>
            )}
            {(editing || draft.suggestion) && (
              <button
                type="button"
                onClick={() => {
                  setEditing(null)
                  setDraft(EMPTY)
                }}
              >
                취소
              </button>
            )}
            <button className="primary" disabled={busy}>
              {editing ? '수정 저장' : '저장'}
              <span className="kbd">Enter</span>
            </button>
          </div>
          {error && <p className="error">{error}</p>}
        </form>
      )}

      {suggestionSlot ? createPortal(suggestionTable, suggestionSlot) : suggestionTable}

      <div className="tbl-wrap">
        <table className="defect-list">
          <thead>
            <tr>
              <th>시간</th>
              <th>주행</th>
              <th>거리(m)</th>
              <th>분류</th>
              <th className="left">상태 / 결함</th>
              <th>등급</th>
              <th>사분면</th>
              <th>맨홀 깊이</th>
              <th>점수</th>
              <th className="left">메모</th>
              <th>이미지</th>
              <th className="actions-col" aria-label="작업" />
            </tr>
          </thead>
          <tbody>
            {defects.data?.length === 0 && (
              <tr>
                <td colSpan={12} className="left faint">
                  등록된 결함이 없습니다. 영상에서 C로 캡처한 뒤 항목을 고르고 Enter로 저장하세요.
                </td>
              </tr>
            )}
            {defects.data?.map((d) => {
              const item = d.defect_item ?? d.condition_item
              const code = item ? codes.get(item) : undefined
              return (
                <tr
                  key={d.id}
                  className={selected === d.id ? 'selected' : undefined}
                  onClick={() => {
                    setSelected(d.id)
                    player.current?.seekMs(d.timestamp_ms)
                  }}
                >
                  <td className="num">{formatTimestamp(d.timestamp_ms)}</td>
                  <td>{d.drive_direction}</td>
                  <td className="num">{d.distance_m.toFixed(2)}</td>
                  <td>{d.item_category}</td>
                  <td className="left">
                    {item} {code && <span className="code">{code}</span>}
                  </td>
                  <td>{d.grade ?? '—'}</td>
                  <td>{d.quadrant ?? '—'}</td>
                  <td className="num">{d.manhole_defect_depth_m ?? '—'}</td>
                  <td className="num">{d.score ?? '—'}</td>
                  <td className="wide">{d.memo}</td>
                  <td>
                    <a href={d.image_url} target="_blank" rel="noreferrer noopener" onClick={(e) => e.stopPropagation()}>
                      <img src={d.image_url} alt={`${formatTimestamp(d.timestamp_ms)} 캡처`} className="defect-thumb" loading="lazy" />
                    </a>
                  </td>
                  <td>
                    {canEdit && (
                      <div className="row-actions center">
                        <button
                          className="link"
                          onClick={(e) => {
                            e.stopPropagation()
                            edit(d)
                          }}
                        >
                          수정
                        </button>
                        <button
                          className="link danger"
                          onClick={(e) => {
                            e.stopPropagation()
                            void remove(d.id)
                          }}
                        >
                          삭제
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
})

function SuggestionTable({
  suggestions,
  defects,
  canEdit,
  applied,
  showRejected,
  onToggleRejected,
  onSeek,
  onApply,
  onStatus,
}: {
  suggestions: Suggestion[]
  defects: Defect[]
  canEdit: boolean
  applied: string | null
  showRejected: boolean
  onToggleRejected: () => void
  onSeek: (ms: number) => void
  onApply: (suggestion: Suggestion) => void
  onStatus: (suggestion: Suggestion, status: 'pending' | 'rejected') => void
}) {
  const open = suggestions.filter((s) => isOpen(s, defects))
  const rejected = suggestions.filter((s) => s.status === 'rejected')
  if (!open.length && !rejected.length) return null
  const rows = showRejected ? [...open, ...rejected].sort((a, b) => a.start_ms - b.start_ms) : open
  return (
    <div className="suggestions">
      <div className="suggestions-head">
        <h3>
          AI 제안 <span className="mono">{open.length}</span>
        </h3>
        <span className="spacer" />
        {rejected.length > 0 && (
          <button className="link quiet" onClick={onToggleRejected}>
            무시한 제안 {rejected.length} {showRejected ? '숨기기' : '보기'}
          </button>
        )}
      </div>
      <div className="tbl-wrap">
        <table className="defect-list">
          <thead>
            <tr>
              <th>구간</th>
              <th>분류</th>
              <th className="left">결함</th>
              <th>신뢰도</th>
              <th className="actions-col" aria-label="작업" />
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr
                key={s.id}
                className={s.id === applied ? 'selected' : s.status === 'rejected' ? 'rejected' : undefined}
                onClick={() => onSeek(s.best_ms)}
              >
                <td className="num">
                  {formatTimestamp(s.start_ms)} – {formatTimestamp(s.end_ms)}
                </td>
                <td>{s.item_category}</td>
                <td className="left">
                  {s.defect_item} <span className="code">{s.code}</span>
                </td>
                <td className="num">{Math.round(s.confidence * 100)}%</td>
                <td>
                  {canEdit && (
                    <div className="row-actions center">
                      {s.status === 'rejected' ? (
                        <button
                          className="link"
                          onClick={(e) => {
                            e.stopPropagation()
                            onStatus(s, 'pending')
                          }}
                        >
                          복원
                        </button>
                      ) : (
                        <>
                          <button
                            className="link"
                            onClick={(e) => {
                              e.stopPropagation()
                              onApply(s)
                            }}
                          >
                            적용
                          </button>
                          <button
                            className="link quiet"
                            onClick={(e) => {
                              e.stopPropagation()
                              onStatus(s, 'rejected')
                            }}
                          >
                            무시
                          </button>
                        </>
                      )}
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
