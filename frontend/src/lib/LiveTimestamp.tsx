import { useEffect, useState } from 'react'
import { formatTimestamp } from './time'

const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | null = null

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  if (timer === null) {
    timer = setInterval(() => {
      for (const l of listeners) l()
    }, 30_000)
  }
  return () => {
    listeners.delete(cb)
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer)
      timer = null
    }
  }
}

function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => subscribe(() => setNow(Date.now())), [])
  return now
}

export function LiveTimestamp({ iso }: { iso: string | Date }) {
  const now = useNow()
  return <>{formatTimestamp(iso, new Date(now))}</>
}
