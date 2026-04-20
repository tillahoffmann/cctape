import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Clock,
  Folder,
  Gauge,
  GitBranch,
  MessagesSquare,
  Search,
} from 'lucide-react'
import { api, type SearchHit, type SessionSummary } from '../lib/api'
import { LiveTimestamp } from '../lib/LiveTimestamp'
import { EditableTitle } from '../lib/EditableTitle'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

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
          <mark key={i} className="bg-primary/80 text-foreground rounded px-0.5">
            {p.text}
          </mark>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </>
  )
}

function MetaItem({
  icon: Icon,
  children,
  title,
  className = '',
}: {
  icon: React.ComponentType<{ className?: string }>
  children: React.ReactNode
  title?: string
  className?: string
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 text-sm text-muted-foreground ${className}`}
      title={title}
    >
      <Icon className="h-4 w-4 shrink-0" />
      <span className="truncate">{children}</span>
    </span>
  )
}

function SessionCard({
  sessionId,
  title,
  cwd,
  gitBranch,
  lastTimestamp,
  turnCount,
  inputTokens,
  outputTokens,
  peakContextTokens,
  snippet,
  onTitleChange,
}: {
  sessionId: string
  title: string | null
  cwd: string | null
  gitBranch: string | null
  lastTimestamp?: string
  turnCount?: number
  inputTokens?: number | null
  outputTokens?: number | null
  peakContextTokens?: number | null
  snippet?: string
  onTitleChange: (next: string | null) => Promise<void>
}) {
  return (
    <Link
      to={`/sessions/${encodeURIComponent(sessionId)}`}
      className="group block rounded-xl focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
    <Card className="transition-all group-hover:border-ring group-hover:shadow-md group-hover:-translate-y-0.5">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <CardTitle className="text-base flex-1 min-w-0">
          <EditableTitle
            value={title}
            onSave={onTitleChange}
            className="hover:bg-transparent hover:underline px-0"
          />
        </CardTitle>
        <span className="font-mono text-xs text-muted-foreground shrink-0 group-hover:text-foreground">
          {sessionId.slice(0, 8)}…
        </span>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          <MetaItem icon={Folder} title={cwd ?? ''} className="font-mono text-xs">
            {basename(cwd)}
          </MetaItem>
          <MetaItem icon={GitBranch} className="font-mono text-xs">
            {gitBranch ?? '—'}
          </MetaItem>
          {lastTimestamp && (
            <MetaItem icon={Clock}>
              <LiveTimestamp iso={lastTimestamp} />
            </MetaItem>
          )}
          {turnCount !== undefined && (
            <MetaItem icon={MessagesSquare}>
              <span className="tabular-nums">{turnCount}</span> turns
            </MetaItem>
          )}
          {inputTokens !== undefined && (
            <MetaItem icon={ArrowDownToLine}>
              <span className="tabular-nums">{formatTokens(inputTokens)}</span> in
            </MetaItem>
          )}
          {outputTokens !== undefined && (
            <MetaItem icon={ArrowUpFromLine}>
              <span className="tabular-nums">{formatTokens(outputTokens)}</span> out
            </MetaItem>
          )}
          {peakContextTokens !== undefined && (
            <MetaItem icon={Gauge}>
              <span className="tabular-nums">{formatTokens(peakContextTokens)}</span> peak
            </MetaItem>
          )}
        </div>
        {snippet && (
          <div className="mt-3 flex items-start gap-2 text-sm">
            <Search className="h-4 w-4 mt-0.5 shrink-0 text-muted-foreground" />
            <div className="min-w-0">
              <Snippet html={snippet} />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
    </Link>
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

  async function updateTitle(id: string, next: string | null) {
    await api.updateSessionTitle(id, next)
    setSessions((prev) =>
      prev ? prev.map((s) => (s.session_id === id ? { ...s, title: next } : s)) : prev,
    )
    setSearchResult((prev) => ({
      ...prev,
      hits: prev.hits
        ? prev.hits.map((h) => (h.session_id === id ? { ...h, title: next } : h))
        : prev.hits,
    }))
  }

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
          <div className="flex flex-col gap-3">
            {hits.map((h) => (
              <SessionCard
                key={h.session_id}
                sessionId={h.session_id}
                title={h.title}
                cwd={h.cwd}
                gitBranch={h.git_branch}
                snippet={h.snippet}
                onTitleChange={(next) => updateTitle(h.session_id, next)}
              />
            ))}
          </div>
        )
      ) : sessions.length === 0 ? (
        <div className="text-muted-foreground">No sessions recorded yet.</div>
      ) : (
        <div className="flex flex-col gap-3">
          {sessions.map((s) => (
            <SessionCard
              key={s.session_id}
              sessionId={s.session_id}
              title={s.title}
              cwd={s.cwd}
              gitBranch={s.git_branch}
              lastTimestamp={s.last_timestamp}
              turnCount={s.turn_count}
              inputTokens={s.input_tokens}
              outputTokens={s.output_tokens}
              peakContextTokens={s.peak_context_tokens}
              onTitleChange={(next) => updateTitle(s.session_id, next)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
