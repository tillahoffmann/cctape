import { formatTimestamp } from './time'
import { useNow } from './useNow'

export function LiveTimestamp({ iso }: { iso: string | Date }) {
  const now = useNow()
  return <>{formatTimestamp(iso, new Date(now))}</>
}
