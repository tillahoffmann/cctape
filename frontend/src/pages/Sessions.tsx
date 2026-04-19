import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SessionSummary } from '../lib/api'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString()
}

function formatTokens(n: number | null): string {
  if (n == null) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
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
            <TableHead>First</TableHead>
            <TableHead>Last</TableHead>
            <TableHead className="text-right">Turns</TableHead>
            <TableHead className="text-right">In</TableHead>
            <TableHead className="text-right">Out</TableHead>
            <TableHead className="text-right">Cache R</TableHead>
            <TableHead>Preview</TableHead>
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
              <TableCell>{formatDate(s.first_timestamp)}</TableCell>
              <TableCell>{formatDate(s.last_timestamp)}</TableCell>
              <TableCell className="text-right tabular-nums">{s.turn_count}</TableCell>
              <TableCell className="text-right tabular-nums">{formatTokens(s.input_tokens)}</TableCell>
              <TableCell className="text-right tabular-nums">{formatTokens(s.output_tokens)}</TableCell>
              <TableCell className="text-right tabular-nums">
                {formatTokens(s.cache_read_input_tokens)}
              </TableCell>
              <TableCell className="max-w-md truncate">{s.first_message_preview ?? '—'}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
