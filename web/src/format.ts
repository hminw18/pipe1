/** MM:SS.s, with hours only when asked (videos of an hour or more) or when ms needs them. */
export function formatTimestamp(ms: number, withHours = ms >= 3_600_000): string {
  const total = Math.max(0, Math.floor(ms))
  const hours = Math.floor(total / 3_600_000)
  const minutes = Math.floor((total % 3_600_000) / 60_000)
  const seconds = Math.floor((total % 60_000) / 1000)
  const tenths = Math.floor((total % 1000) / 100)
  const pad = (value: number) => String(value).padStart(2, '0')
  const clock = `${pad(minutes)}:${pad(seconds)}.${tenths}`
  return withHours ? `${pad(hours)}:${clock}` : clock
}

const SEOUL_PARTS = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

function seoulParts(iso: string): Record<string, string> {
  return Object.fromEntries(SEOUL_PARTS.formatToParts(new Date(iso)).map((part) => [part.type, part.value]))
}

/** `2026-10-27 14:05` in Korea time. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '-'
  const p = seoulParts(iso)
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}`
}

/** `2026-10-27` in Korea time. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-'
  const p = seoulParts(iso)
  return `${p.year}-${p.month}-${p.day}`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`
}

export function readTokenFromFragment(hash: string = window.location.hash): string | null {
  const params = new URLSearchParams(hash.replace(/^#/, ''))
  return params.get('token')
}
