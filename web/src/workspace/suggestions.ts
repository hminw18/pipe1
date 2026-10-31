import type { Schemas } from '../api/client'

export type Suggestion = Schemas['SuggestionOut']

// A defect recorded just outside a track (the operator often stops after passing it) still
// counts as recording it.
const RECORDED_MARGIN_MS = 2000
// Boxes are sampled about five times a second; further than this from a sample, none is drawn.
const BOX_HOLD_MS = 400

/** Waiting for the inspector: pending, and no defect of the same item recorded around it. */
export function isOpen(suggestion: Suggestion, defects: Schemas['DefectOut'][]): boolean {
  if (suggestion.status !== 'pending') return false
  return !defects.some(
    (defect) =>
      defect.item_category === suggestion.item_category &&
      defect.defect_item === suggestion.defect_item &&
      defect.timestamp_ms >= suggestion.start_ms - RECORDED_MARGIN_MS &&
      defect.timestamp_ms <= suggestion.end_ms + RECORDED_MARGIN_MS,
  )
}

/** The box to draw at a playback time: the nearest sample, if one is close enough. */
export function boxAt(suggestion: Suggestion, ms: number): number[] | null {
  if (ms < suggestion.start_ms - BOX_HOLD_MS || ms > suggestion.end_ms + BOX_HOLD_MS) return null
  let nearest: number[] | null = null
  let gap = BOX_HOLD_MS
  for (const sample of suggestion.boxes) {
    const distance = Math.abs(sample.t_ms - ms)
    if (distance <= gap) {
      gap = distance
      nearest = sample.box
    }
  }
  return nearest
}
