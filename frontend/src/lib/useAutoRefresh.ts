import { useEffect, useRef, useState } from 'react'

const DEFAULT_INTERVAL_MS = 3000

/**
 * Poll `fetcher` on an interval. Returns { data, error, reload } and keeps
 * the latest successful data across errors. Pauses polling when the tab is
 * hidden. Pair with the server's ETag middleware — a 304 response resolves
 * to the same object reference, so React skips rerenders for no-op ticks.
 */
export function useAutoRefresh<T>(
  fetcher: () => Promise<T>,
  deps: React.DependencyList = [],
  intervalMs: number = DEFAULT_INTERVAL_MS,
) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fetcherRef = useRef(fetcher)
  useEffect(() => {
    fetcherRef.current = fetcher
  })

  useEffect(() => {
    let cancelled = false

    const tick = async () => {
      try {
        const next = await fetcherRef.current()
        if (cancelled) return
        // Avoid spurious rerenders when the server returned 304 and the
        // api layer served the cached reference.
        setData((prev) => (Object.is(prev, next) ? prev : next))
        setError(null)
      } catch (e) {
        if (cancelled) return
        setError(String(e))
      }
    }

    tick()
    let timer: number | undefined
    const start = () => {
      if (timer !== undefined) return
      timer = window.setInterval(tick, intervalMs)
    }
    const stop = () => {
      if (timer === undefined) return
      window.clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.hidden) {
        stop()
      } else {
        tick()
        start()
      }
    }
    const onFocus = () => {
      // Re-focus may fire while the tab was already visible (e.g. switching
      // between windows on the same monitor). Fetch immediately so the user
      // sees fresh data, and re-arm polling in case it was stopped.
      tick()
      start()
    }
    if (!document.hidden) start()
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('focus', onFocus)

    return () => {
      cancelled = true
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('focus', onFocus)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, ...deps])

  return { data, error, reload: () => fetcherRef.current().then(setData) }
}
