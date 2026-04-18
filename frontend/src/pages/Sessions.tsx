import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SessionSummary } from '../lib/api'

function formatDate(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleString()
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

  if (error) return <div className="text-red-400">Error loading sessions: {error}</div>
  if (!sessions) return <div className="text-zinc-400">Loading…</div>
  if (sessions.length === 0)
    return <div className="text-zinc-400">No sessions recorded yet.</div>

  return (
    <div>
      <h2 className="text-xl font-semibold mb-4 text-zinc-100">Sessions</h2>
      <div className="rounded-lg border border-zinc-800 overflow-hidden bg-zinc-900/40">
        <table className="w-full text-sm">
          <thead className="bg-zinc-900 text-zinc-400 text-xs uppercase tracking-wide">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Session</th>
              <th className="text-left px-4 py-2 font-medium">First</th>
              <th className="text-left px-4 py-2 font-medium">Last</th>
              <th className="text-right px-4 py-2 font-medium">Turns</th>
              <th className="text-right px-4 py-2 font-medium">In</th>
              <th className="text-right px-4 py-2 font-medium">Out</th>
              <th className="text-right px-4 py-2 font-medium">Cache R</th>
              <th className="text-left px-4 py-2 font-medium">Preview</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800">
            {sessions.map((s) => (
              <tr key={s.session_id} className="hover:bg-zinc-900/60">
                <td className="px-4 py-2 font-mono text-xs">
                  <Link
                    to={`/sessions/${encodeURIComponent(s.session_id)}`}
                    className="text-sky-400 hover:text-sky-300"
                  >
                    {s.session_id.slice(0, 8)}…
                  </Link>
                </td>
                <td className="px-4 py-2 text-zinc-400 whitespace-nowrap">
                  {formatDate(s.first_timestamp)}
                </td>
                <td className="px-4 py-2 text-zinc-400 whitespace-nowrap">
                  {formatDate(s.last_timestamp)}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{s.turn_count}</td>
                <td className="px-4 py-2 text-right tabular-nums text-zinc-300">
                  {formatTokens(s.input_tokens)}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-zinc-300">
                  {formatTokens(s.output_tokens)}
                </td>
                <td className="px-4 py-2 text-right tabular-nums text-zinc-400">
                  {formatTokens(s.cache_read_input_tokens)}
                </td>
                <td className="px-4 py-2 text-zinc-400 max-w-md truncate">
                  {s.first_message_preview ?? <span className="text-zinc-600">—</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
