import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type SearchHit, type SessionSummary } from '../lib/api'
import { LiveTimestamp } from '../lib/LiveTimestamp'
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

// Render a snippet containing <mark>...</mark> tags from the FTS response.
// We split on the tags rather than using dangerouslySetInnerHTML to keep
// arbitrary matched text from injecting markup into the page.
function Snippet({ html }: { html: string }) {
  const parts = useMemo(() => {
    const out: Array<{ text: string; hit: boolean }> = []
    const re = /<mark>([\s\S]*?)<\/mark>/g
    let last = 0
    let m: RegExpExecArray | null
    while ((m = re.exec(html)) !== null) {
      if (m.index > last) out.push({ text: html.slice(last, m.index), hit: false })
      out.push({ text: m[1], hit: true })
      last = m.index + m[0].length
    }
    if (last < html.length) out.push({ text: html.slice(last), hit: false })
    return out
  }, [html])
  return (
    <>
      {parts.map((p, i) =>
        p.hit ? (
          <mark key={i} className="bg-yellow-200 dark:bg-yellow-800/50 rounded px-0.5">
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  )
}

export default function Sessions() {
  const [sessions, setSessions] = useState<SessionSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Search state is keyed by the query it was fetched for, so a stale query's
  // results are discarded synchronously by comparing against the current input.
  const [query, setQuery] = useState('')
  const [searchResult, setSearchResult] = useState<{
    query: string
    hits: SearchHit[] | null
    error: string | null
  }>({ query: '', hits: null, error: null })

  useEffect(() => {
    api.sessions().then(setSessions).catch((e) => setError(String(e)))
  }, [])

  useEffect(() => {
    const q = query.trim()
    if (!q) return
    let cancelled = false
    const handle = window.setTimeout(() => {
      api
        .search(q)
        .then((hits) => {
          if (!cancelled) setSearchResult({ query: q, hits, error: null })
        })
        .catch((e) => {
          if (!cancelled)
            setSearchResult({ query: q, hits: null, error: String(e) })
        })
    }, 200)
    return () => {
      cancelled = true
      window.clearTimeout(handle)
    }
  }, [query])

  const trimmed = query.trim()
  const searchActive = trimmed.length > 0
  const resultsFresh = searchResult.query === trimmed
  const hits = resultsFresh ? searchResult.hits : null
  const searchError = resultsFresh ? searchResult.error : null
  const searching = searchActive && !resultsFresh

  if (error) return <div className="text-destructive">Error loading sessions: {error}</div>
  if (!sessions) return <div className="text-muted-foreground">Loading…</div>

  return (
    <div>
      <div className="mb-4 flex items-center gap-2">
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search messages, tool calls, results…"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm
            placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
        {searching && <span className="text-xs text-muted-foreground">searching…</span>}
      </div>

      {searchActive ? (
        searchError ? (
          <div className="text-destructive text-sm">Search error: {searchError}</div>
        ) : hits === null ? (
          <div className="text-muted-foreground text-sm">Searching…</div>
        ) : hits.length === 0 ? (
          <div className="text-muted-foreground text-sm">No matches.</div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Session</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Project</TableHead>
                <TableHead>Match</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {hits.map((h) => (
                <TableRow key={h.session_id}>
                  <TableCell className="font-mono">
                    <Link
                      to={`/sessions/${encodeURIComponent(h.session_id)}`}
                      className="underline"
                    >
                      {h.session_id.slice(0, 8)}…
                    </Link>
                  </TableCell>
                  <TableCell className="text-sm">{h.title ?? '—'}</TableCell>
                  <TableCell className="font-mono text-xs" title={h.cwd ?? ''}>
                    {basename(h.cwd)}
                  </TableCell>
                  <TableCell className="text-sm">
                    <Snippet html={h.snippet} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )
      ) : sessions.length === 0 ? (
        <div className="text-muted-foreground">No sessions recorded yet.</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Session</TableHead>
              <TableHead>Title</TableHead>
              <TableHead>Project</TableHead>
              <TableHead>Branch</TableHead>
              <TableHead>Updated</TableHead>
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
                <TableCell className="text-sm">{s.title ?? '—'}</TableCell>
                <TableCell className="font-mono text-xs" title={s.cwd ?? ''}>
                  {basename(s.cwd)}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {s.git_branch ?? '—'}
                </TableCell>
                <TableCell><LiveTimestamp iso={s.last_timestamp} /></TableCell>
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
      )}
    </div>
  )
}
