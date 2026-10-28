import { describe, expect, it } from 'vitest'

import { elementRectToVideoRect, videoRectToElementRect } from '../workspace/geometry'

describe('elementRectToVideoRect', () => {
  // 1920x1080 video shown in an 800x600 box: scale 800/1920, 75px letterbox top and bottom.
  const element = { width: 800, height: 600 }
  const video = { width: 1920, height: 1080 }

  it('maps through scale and letterbox', () => {
    expect(elementRectToVideoRect({ x: 100, y: 175, w: 200, h: 50 }, element, video)).toEqual({
      x: 240,
      y: 240,
      w: 480,
      h: 120,
    })
  })

  it('accepts rectangles drawn right-to-left', () => {
    expect(elementRectToVideoRect({ x: 300, y: 225, w: -200, h: -50 }, element, video)).toEqual({
      x: 240,
      y: 240,
      w: 480,
      h: 120,
    })
  })

  it('clamps to the picture and rejects tiny rectangles', () => {
    expect(elementRectToVideoRect({ x: 0, y: 0, w: 1, h: 1 }, element, video)).toBeNull()
    expect(elementRectToVideoRect({ x: 700, y: 500, w: 200, h: 200 }, element, video)).toEqual({
      x: 1680,
      y: 1020,
      w: 240,
      h: 60,
    })
  })

  it('round-trips with videoRectToElementRect', () => {
    const back = videoRectToElementRect({ x: 240, y: 240, w: 480, h: 120 }, element, video)
    expect(back).toEqual({ x: 100, y: 175, w: 200, h: 50 })
  })
})
