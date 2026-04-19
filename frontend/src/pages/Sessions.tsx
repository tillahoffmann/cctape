import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SessionSummary } from '../lib/api'
import { formatTimestamp } from '../lib/time'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

function formatTokens(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

function basename(path: string | null): string {
  if (!path) return '—'
  const parts = path.replace(/\/$/, '').split('/')
  return parts[parts.length - 1] || path
}

export default function Sessions() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.sessions().then(setSessions).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="text-destructive">Error loading sessions: {error}</div>
  if (!sessions) return <div className="text-muted-foreground">Loading…</div>
  if (sessions.length === 0)
    return <div className="text-muted-foreground">No sessions recorded yet.</div>

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4">Sessions</h2>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Session</TableHead>
            <TableHead>Project</TableHead>
            <TableHead>Entry</TableHead>
            <TableHead>Last</TableHead>
            <TableHead className="text-right">Turns</TableHead>
            <TableHead className="text-right">In</TableHead>
            <TableHead className="text-right">Out</TableHead>
            <TableHead className="text-right">Peak Ctx</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {sessions.map((s) => (
            <TableRow key={s.session_id}>
              <TableCell className="font-mono">
                <Link to={`/sessions/${encodeURIComponent(s.session_id)}`} className="underline">
                  {s.session_id.slice(0, 8)}…
                </Link>
              </TableCell>
              <TableCell className="font-mono text-xs" title={s.cwd ?? ''}>
                {basename(s.cwd)}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                {s.entrypoint ?? '—'}
              </TableCell>
              <TableCell>{formatTimestamp(s.last_timestamp)}</TableCell>
              <TableCell className="text-right tabular-nums">{s.turn_count}</TableCell>
              <TableCell className="text-right tabular-nums">{formatTokens(s.input_tokens)}</TableCell>
              <TableCell className="text-right tabular-nums">{formatTokens(s.output_tokens)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {formatTokens(s.peak_context_tokens)}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
