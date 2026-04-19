const SECOND = 1000
const MINUTE = 60 * SECOND
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

function formatDuration(ms: number): string {
  if (ms < MINUTE) {
    const s = Math.floor(ms / SECOND)
    return `${s} second${s === 1 ? '' : 's'}`
  }
  if (ms < HOUR) {
    const m = Math.floor(ms / MINUTE)
    return `${m} minute${m === 1 ? '' : 's'}`
  }
  if (ms < DAY) {
    const h = Math.floor(ms / HOUR)
    return `${h} hour${h === 1 ? '' : 's'}`
  }
  const d = Math.floor(ms / DAY)
  if (d < 30) return `${d} day${d === 1 ? '' : 's'}`
  const mo = Math.floor(d / 30)
  if (mo < 12) return `${mo} month${mo === 1 ? '' : 's'}`
  const y = Math.floor(d / 365)
  return `${y} year${y === 1 ? '' : 's'}`
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

function formatDate(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  )
}

export function formatTimestamp(iso: string | Date, now: Date = new Date()): string {
  const d = typeof iso === 'string' ? new Date(iso) : iso
  const diff = now.getTime() - d.getTime()

  if (diff < 45 * SECOND) return 'just now'

  if (diff < 12 * HOUR || isSameDay(d, now)) {
    return `${formatDuration(diff)} ago`
  }

  const yesterday = new Date(now)
  yesterday.setDate(yesterday.getDate() - 1)
  if (isSameDay(d, yesterday)) {
    return `yesterday at ${formatTime(d)}`
  }

  return `${formatDate(d)} at ${formatTime(d)}`
}
