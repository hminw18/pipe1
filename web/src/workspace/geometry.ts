export type Rect = { x: number; y: number; w: number; h: number }

/**
 * Convert a rectangle drawn over a <video> (object-fit: contain) from element pixels to video
 * pixels, accounting for letterboxing. Returns null if the rectangle misses the picture.
 */
export function elementRectToVideoRect(
  rect: Rect,
  element: { width: number; height: number },
  video: { width: number; height: number },
): Rect | null {
  if (!video.width || !video.height || !element.width || !element.height) return null
  const scale = Math.min(element.width / video.width, element.height / video.height)
  const offsetX = (element.width - video.width * scale) / 2
  const offsetY = (element.height - video.height * scale) / 2
  const clamp = (value: number, max: number) => Math.max(0, Math.min(max, value))
  const x1 = clamp((Math.min(rect.x, rect.x + rect.w) - offsetX) / scale, video.width)
  const y1 = clamp((Math.min(rect.y, rect.y + rect.h) - offsetY) / scale, video.height)
  const x2 = clamp((Math.max(rect.x, rect.x + rect.w) - offsetX) / scale, video.width)
  const y2 = clamp((Math.max(rect.y, rect.y + rect.h) - offsetY) / scale, video.height)
  const w = Math.round(x2 - x1)
  const h = Math.round(y2 - y1)
  if (w < 2 || h < 2) return null
  return { x: Math.round(x1), y: Math.round(y1), w, h }
}

/** Inverse of elementRectToVideoRect, for drawing a saved region over the video. */
export function videoRectToElementRect(
  rect: Rect,
  element: { width: number; height: number },
  video: { width: number; height: number },
): Rect {
  const scale = Math.min(element.width / video.width, element.height / video.height)
  const offsetX = (element.width - video.width * scale) / 2
  const offsetY = (element.height - video.height * scale) / 2
  return { x: offsetX + rect.x * scale, y: offsetY + rect.y * scale, w: rect.w * scale, h: rect.h * scale }
}
