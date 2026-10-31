import { describe, expect, it } from 'vitest'

import type { Schemas } from '../api/client'
import { boxAt, isOpen, type Suggestion } from '../workspace/suggestions'

const suggestion: Suggestion = {
  id: 's1',
  code: 'CL',
  item_category: '관로',
  defect_item: '균열(길이)',
  confidence: 0.9,
  start_ms: 10_000,
  end_ms: 12_000,
  best_ms: 11_000,
  best_box: [0.2, 0.2, 0.4, 0.4],
  boxes: [
    { t_ms: 10_000, box: [0.1, 0.1, 0.3, 0.3], score: 0.8 },
    { t_ms: 10_200, box: [0.2, 0.2, 0.4, 0.4], score: 0.9 },
  ],
  status: 'pending',
  defect_id: null,
  model_version: 'test',
}

const defect = (item: string, ms: number) =>
  ({ item_category: '관로', defect_item: item, timestamp_ms: ms }) as Schemas['DefectOut']

describe('AI suggestions', () => {
  it('stay open until rejected, confirmed, or the same defect is recorded around them', () => {
    expect(isOpen(suggestion, [])).toBe(true)
    expect(isOpen({ ...suggestion, status: 'rejected' }, [])).toBe(false)
    expect(isOpen(suggestion, [defect('균열(길이)', 13_500)])).toBe(false)
    expect(isOpen(suggestion, [defect('균열(원주)', 11_000)])).toBe(true)
    expect(isOpen(suggestion, [defect('균열(길이)', 20_000)])).toBe(true)
  })

  it('draw the nearest box sample, and nothing far from every sample', () => {
    expect(boxAt(suggestion, 10_050)).toEqual([0.1, 0.1, 0.3, 0.3])
    expect(boxAt(suggestion, 10_150)).toEqual([0.2, 0.2, 0.4, 0.4])
    expect(boxAt(suggestion, 11_500)).toBeNull()
    expect(boxAt({ ...suggestion, boxes: [] }, 11_000)).toBeNull()
  })
})
