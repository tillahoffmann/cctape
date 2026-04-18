import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, type SessionDetail, type Turn } from '../lib/api'

interface Block {
  type: string
  text?: string
  name?: string
  input?: unknown
  content?: unknown
  id?: string
  tool_use_id?: string
}

function blocksFromRequestMessage(message: unknown): Block[] {
  if (!message || typeof message !== 'object') return []
  const content = (message as { content?: unknown }).content
  if (typeof content === 'string') return [{ type: 'text', text: content }]
  if (Array.isArray(content)) return content as Block[]
  return []
}

function assistantTextFromSSE(payload: string | null): {
  text: string
  tools: Block[]
} {
  if (!payload) return { text: '', tools: [] }
  let text = ''
  const tools: Block[] = []
  const currentTool: { name?: string; input: string; id?: string } = { input: '' }
  let inToolUse = false

  for (const rawLine of payload.split('\n')) {
    const line = rawLine.trim()
    if (!line.startsWith('data:')) continue
    const data = line.slice(5).trim()
    if (!data || data === '[DONE]') continue
    try {
      const event = JSON.parse(data) as {
        type?: string
        delta?: { type?: string; text?: string; partial_json?: string }
        content_block?: { type?: string; name?: string; id?: string }
      }
      if (event.type === 'content_block_start' && event.content_block?.type === 'tool_use') {
        inToolUse = true
        currentTool.name = event.content_block.name
        currentTool.id = event.content_block.id
        currentTool.input = ''
      } else if (event.type === 'content_block_delta') {
        if (event.delta?.type === 'text_delta' && event.delta.text) {
          text += event.delta.text
        } else if (event.delta?.type === 'input_json_delta' && event.delta.partial_json) {
          currentTool.input += event.delta.partial_json
        }
      } else if (event.type === 'content_block_stop' && inToolUse) {
        let parsed: unknown = currentTool.input
        try {
          parsed = JSON.parse(currentTool.input || '{}')
        } catch {
          // keep raw
        }
        tools.push({
          type: 'tool_use',
          name: currentTool.name,
          id: currentTool.id,
          input: parsed,
        })
        inToolUse = false
        currentTool.input = ''
        currentTool.name = undefined
        currentTool.id = undefined
      }
    } catch {
      // ignore malformed SSE frames
    }
  }
  return { text, tools }
}

function TextBlock({ text }: { text: string }) {
  return (
    <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">{text}</div>
  )
}

function ToolBlock({ block }: { block: Block }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="mt-2 rounded-md border border-zinc-700 bg-zinc-900/60">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-3 py-1.5 text-xs font-mono text-amber-300 hover:bg-zinc-800/60 flex items-center gap-2"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>tool_use: {block.name ?? 'unknown'}</span>
      </button>
      {open && (
        <pre className="px-3 pb-3 pt-1 text-xs overflow-x-auto text-zinc-300">
          {JSON.stringify(block.input, null, 2)}
        </pre>
      )}
    </div>
  )
}

function ToolResultBlock({ block }: { block: Block }) {
  const [open, setOpen] = useState(false)
  const content =
    typeof block.content === 'string'
      ? block.content
      : JSON.stringify(block.content, null, 2)
  return (
    <div className="mt-2 rounded-md border border-zinc-700 bg-zinc-900/60">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-3 py-1.5 text-xs font-mono text-emerald-300 hover:bg-zinc-800/60 flex items-center gap-2"
      >
        <span>{open ? '▾' : '▸'}</span>
        <span>tool_result</span>
      </button>
      {open && (
        <pre className="px-3 pb-3 pt-1 text-xs overflow-x-auto text-zinc-300 whitespace-pre-wrap">
          {content}
        </pre>
      )}
    </div>
  )
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    month: 'short',
    day: 'numeric',
  })
}

function BubbleHeader({
  label,
  timestamp,
  color,
}: {
  label: string
  timestamp: string
  color: string
}) {
  return (
    <div className="flex items-baseline justify-between mb-1 gap-4">
      <div className={`text-xs uppercase tracking-wide ${color}`}>{label}</div>
      <time
        className="text-[10px] text-zinc-500 tabular-nums"
        title={new Date(timestamp).toLocaleString()}
        dateTime={timestamp}
      >
        {formatTimestamp(timestamp)}
      </time>
    </div>
  )
}

function UserBubble({ blocks, timestamp }: { blocks: Block[]; timestamp: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-3xl rounded-lg bg-sky-900/40 border border-sky-800/50 px-4 py-3 text-zinc-100">
        <BubbleHeader label="User" timestamp={timestamp} color="text-sky-300/80" />
        {blocks.map((b, i) => {
          if (b.type === 'text' && b.text) return <TextBlock key={i} text={b.text} />
          if (b.type === 'tool_result') return <ToolResultBlock key={i} block={b} />
          return (
            <pre key={i} className="text-xs text-zinc-400 overflow-x-auto">
              {JSON.stringify(b, null, 2)}
            </pre>
          )
        })}
      </div>
    </div>
  )
}

function AssistantBubble({
  text,
  tools,
  timestamp,
}: {
  text: string
  tools: Block[]
  timestamp: string
}) {
  return (
    <div className="flex justify-start">
      <div className="max-w-3xl rounded-lg bg-zinc-900 border border-zinc-800 px-4 py-3 text-zinc-100">
        <BubbleHeader label="Assistant" timestamp={timestamp} color="text-zinc-400" />
        {text && <TextBlock text={text} />}
        {tools.map((t, i) => (
          <ToolBlock key={i} block={t} />
        ))}
        {!text && tools.length === 0 && (
          <div className="text-zinc-500 text-sm italic">(no content)</div>
        )}
      </div>
    </div>
  )
}

function TurnView({ turn }: { turn: Turn }) {
  const messages = (turn.request.payload?.messages ?? []) as unknown[]
  const lastUser = messages[messages.length - 1]
  const userBlocks = blocksFromRequestMessage(lastUser)
  const { text, tools } = assistantTextFromSSE(turn.response?.payload ?? null)
  return (
    <div className="space-y-3">
      <UserBubble blocks={userBlocks} timestamp={turn.request.timestamp} />
      {turn.response && (
        <AssistantBubble
          text={text}
          tools={tools}
          timestamp={turn.response.timestamp}
        />
      )}
      {turn.response && (
        <div className="text-xs text-zinc-500 text-right">
          {turn.response.input_tokens ?? '—'} in · {turn.response.output_tokens ?? '—'} out
          {turn.response.cache_read_input_tokens != null &&
            ` · ${turn.response.cache_read_input_tokens} cache`}
        </div>
      )}
    </div>
  )
}

export default function Session() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!sessionId) return
    api.session(sessionId).then(setDetail).catch((e) => setError(String(e)))
  }, [sessionId])

  if (error) return <div className="text-red-400">Error: {error}</div>
  if (!detail) return <div className="text-zinc-400">Loading…</div>

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Link to="/sessions" className="text-sm text-sky-400 hover:text-sky-300">
          ← Sessions
        </Link>
        <h2 className="text-lg font-semibold font-mono text-zinc-200">
          {detail.session_id}
        </h2>
        <span className="text-xs text-zinc-500">{detail.turns.length} turns</span>
      </div>
      <div className="space-y-6">
        {detail.turns.map((t) => (
          <TurnView key={t.request.id} turn={t} />
        ))}
      </div>
    </div>
  )
}
