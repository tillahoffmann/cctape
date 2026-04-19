import { useState, useEffect, useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { api, type SessionDetail, type Turn } from '../lib/api'
import { formatTimestamp } from '../lib/time'
import { Card, CardContent } from '@/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Button } from '@/components/ui/button'

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

interface ParsedResponse {
  blocks: Block[]
  thinking: string
  notice: string | null
  raw: string | null
}

function parseResponse(payload: string | null): ParsedResponse {
  if (!payload) {
    return { blocks: [], thinking: '', notice: 'Empty response body.', raw: null }
  }

  const trimmed = payload.trimStart()

  if (trimmed.startsWith('{')) {
    try {
      const obj = JSON.parse(trimmed) as {
        type?: string
        error?: { type?: string; message?: string }
        content?: Block[]
      }
      if (obj.type === 'error' || obj.error) {
        return {
          blocks: [],
          thinking: '',
          notice: `API error: ${obj.error?.type ?? 'unknown'} — ${obj.error?.message ?? ''}`,
          raw: payload,
        }
      }
      if (Array.isArray(obj.content)) {
        const blocks = obj.content.filter((b) => b.type === 'text' || b.type === 'tool_use')
        const thinking = obj.content
          .filter((b) => b.type === 'thinking')
          .map((b) => (b as { thinking?: string }).thinking ?? '')
          .join('')
        return { blocks, thinking, notice: 'Non-streaming JSON response.', raw: payload }
      }
      return { blocks: [], thinking: '', notice: 'Unrecognized JSON response.', raw: payload }
    } catch (e) {
      return {
        blocks: [],
        thinking: '',
        notice: `Malformed JSON response: ${String(e)}`,
        raw: payload,
      }
    }
  }

  const blocks: Block[] = []
  let thinking = ''
  let currentBlock: Block | null = null
  let currentInputBuffer = ''
  let sawAnyEvent = false

  for (const rawLine of payload.split('\n')) {
    const line = rawLine.trim()
    if (!line.startsWith('data:')) continue
    const data = line.slice(5).trim()
    if (!data || data === '[DONE]') continue
    try {
      const event = JSON.parse(data) as {
        type?: string
        delta?: { type?: string; text?: string; partial_json?: string; thinking?: string }
        content_block?: { type?: string; name?: string; id?: string }
      }
      sawAnyEvent = true
      if (event.type === 'content_block_start') {
        const cb = event.content_block
        if (cb?.type === 'text') {
          currentBlock = { type: 'text', text: '' }
          blocks.push(currentBlock)
        } else if (cb?.type === 'tool_use') {
          currentBlock = { type: 'tool_use', name: cb.name, id: cb.id, input: {} }
          currentInputBuffer = ''
          blocks.push(currentBlock)
        } else {
          currentBlock = null
        }
      } else if (event.type === 'content_block_delta') {
        const d = event.delta
        if (d?.type === 'text_delta' && d.text && currentBlock?.type === 'text') {
          currentBlock.text = (currentBlock.text ?? '') + d.text
        } else if (d?.type === 'input_json_delta' && d.partial_json) {
          currentInputBuffer += d.partial_json
        } else if (d?.type === 'thinking_delta' && d.thinking) {
          thinking += d.thinking
        }
      } else if (event.type === 'content_block_stop') {
        if (currentBlock?.type === 'tool_use') {
          try {
            currentBlock.input = JSON.parse(currentInputBuffer || '{}')
          } catch {
            currentBlock.input = currentInputBuffer
          }
        }
        currentBlock = null
        currentInputBuffer = ''
      }
    } catch {
      // ignore malformed SSE frames
    }
  }

  const hasText = blocks.some((b) => b.type === 'text' && b.text)
  const hasTool = blocks.some((b) => b.type === 'tool_use')
  let notice: string | null = null
  if (!sawAnyEvent) {
    notice = 'Response body is neither SSE nor JSON.'
  } else if (!hasText && !hasTool && thinking) {
    notice = 'Response contained only a thinking block (no text or tool use).'
  } else if (!hasText && !hasTool) {
    notice = 'SSE stream had no text or tool use blocks.'
  }

  return { blocks, thinking, notice, raw: notice ? payload : null }
}

function CollapsibleBlock({ label, body }: { label: string; body: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="my-2">
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" className="font-mono">
          {open ? '▾' : '▸'} {label}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <pre className="mt-2 p-3 text-xs overflow-x-auto border rounded-md whitespace-pre-wrap">
          {body}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  )
}

function ToolUseBlock({ toolUse, result }: { toolUse: Block; result?: Block }) {
  const [open, setOpen] = useState(false)
  const pending = !result
  const resultContent = result
    ? typeof result.content === 'string'
      ? result.content
      : JSON.stringify(result.content, null, 2)
    : null
  const inputJson =
    typeof toolUse.input === 'string' ? toolUse.input : JSON.stringify(toolUse.input, null, 2)
  const isError = !!(result && (result as { is_error?: boolean }).is_error)

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="my-2" data-tool-use-id={toolUse.id}>
      <CollapsibleTrigger asChild>
        <Button variant="outline" size="sm" className="font-mono gap-2">
          <span>{open ? '▾' : '▸'}</span>
          <span>{toolUse.name ?? 'tool'}</span>
          {pending && <span className="text-xs text-muted-foreground">(pending)</span>}
          {isError && <span className="text-xs text-destructive">(error)</span>}
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2">
        <div>
          <div className="text-xs font-semibold text-muted-foreground mb-1">Input</div>
          <pre className="p-3 text-xs overflow-x-auto border rounded-md whitespace-pre-wrap">
            {inputJson}
          </pre>
        </div>
        <div>
          <div className="text-xs font-semibold text-muted-foreground mb-1">Response</div>
          {pending ? (
            <div className="p-3 text-xs text-muted-foreground border rounded-md italic">
              No response yet.
            </div>
          ) : (
            <pre
              className={`p-3 text-xs overflow-x-auto border rounded-md whitespace-pre-wrap ${
                isError ? 'border-destructive/50' : ''
              }`}
            >
              {resultContent}
            </pre>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}

function normalizeMathDelimiters(src: string): string {
  return src
    .replace(/\\\[([\s\S]+?)\\\]/g, (_m, inner) => `$$${inner}$$`)
    .replace(/\\\(([\s\S]+?)\\\)/g, (_m, inner) => `$${inner}$`)
}

function MarkdownText({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed break-words [&_p]:my-2 [&_ul]:list-disc [&_ul]:ml-5 [&_ol]:list-decimal [&_ol]:ml-5 [&_code]:px-1 [&_code]:py-0.5 [&_code]:bg-muted [&_code]:rounded [&_pre]:p-3 [&_pre]:bg-muted [&_pre]:rounded-md [&_pre]:overflow-x-auto [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:mt-3 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-3 [&_h3]:font-semibold [&_h3]:mt-2 [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath]} rehypePlugins={[rehypeKatex]}>
        {normalizeMathDelimiters(children)}
      </ReactMarkdown>
    </div>
  )
}

type ViewMode = 'rendered' | 'raw'

function renderTextBlock(block: Block, key: number | string) {
  if (block.type !== 'text' || !block.text) return null
  return <MarkdownText key={key}>{block.text}</MarkdownText>
}

function renderUserBlock(block: Block, key: number) {
  if (block.type === 'text' && block.text) {
    return (
      <div key={key} className="whitespace-pre-wrap break-words text-sm leading-relaxed">
        {block.text}
      </div>
    )
  }
  return (
    <pre key={key} className="text-xs overflow-x-auto">
      {JSON.stringify(block, null, 2)}
    </pre>
  )
}

function fmtNum(n: number | null | undefined): string {
  return n == null ? '—' : n.toLocaleString()
}

function ViewModeToggle({ mode, onChange }: { mode: ViewMode; onChange: (m: ViewMode) => void }) {
  const options: { value: ViewMode; label: string }[] = [
    { value: 'rendered', label: 'Rendered' },
    { value: 'raw', label: 'Raw' },
  ]
  return (
    <span className="inline-flex gap-2 text-xs">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={
            mode === o.value
              ? 'underline text-foreground'
              : 'text-muted-foreground hover:text-foreground'
          }
        >
          {o.label.toLowerCase()}
        </button>
      ))}
    </span>
  )
}

function buildToolResultMap(turns: Turn[]): Map<string, Block> {
  const map = new Map<string, Block>()
  for (const turn of turns) {
    const messages = (turn.request.payload?.messages ?? []) as unknown[]
    for (const msg of messages) {
      const blocks = blocksFromRequestMessage(msg)
      for (const b of blocks) {
        if (b.type === 'tool_result' && b.tool_use_id && !map.has(b.tool_use_id)) {
          map.set(b.tool_use_id, b)
        }
      }
    }
  }
  return map
}

function TurnView({ turn, toolResults }: { turn: Turn; toolResults: Map<string, Block> }) {
  const [mode, setMode] = useState<ViewMode>('rendered')
  const messages = (turn.request.payload?.messages ?? []) as unknown[]
  const lastUser = messages[messages.length - 1]
  const userBlocks = blocksFromRequestMessage(lastUser)
  const visibleUserBlocks = userBlocks.filter((b) => b.type !== 'tool_result')
  const showUser = visibleUserBlocks.length > 0
  const parsed = parseResponse(turn.response?.payload ?? null)
  const hasAssistantContent = parsed.blocks.length > 0

  return (
    <div id={`msg-${turn.request.id}`} data-turn-id={turn.request.id} className="space-y-3 scroll-mt-20">
      {showUser && (
        <div className="flex justify-end">
          <div className="max-w-[80%] space-y-2">
            <div className="text-xs text-muted-foreground">
              {formatTimestamp(turn.request.timestamp)}
            </div>
            <Card>
              <CardContent className="space-y-2">
                {visibleUserBlocks.map((b, i) => renderUserBlock(b, i))}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
      {turn.response && (
        <div className="space-y-2 max-w-[80%] mr-auto">
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>{formatTimestamp(turn.response.timestamp)}</span>
            <ViewModeToggle mode={mode} onChange={setMode} />
          </div>
          {mode === 'raw' ? (
            <pre className="text-xs overflow-x-auto p-3 bg-muted rounded-md whitespace-pre-wrap break-words">
              {turn.response.payload ?? '(empty response body)'}
            </pre>
          ) : (
            <div className="space-y-2">
              {parsed.blocks.map((b, i) => {
                if (b.type === 'tool_use') {
                  const result = b.id ? toolResults.get(b.id) : undefined
                  return <ToolUseBlock key={b.id ?? `tu-${i}`} toolUse={b} result={result} />
                }
                return renderTextBlock(b, i)
              })}
              {parsed.thinking && <CollapsibleBlock label="thinking" body={parsed.thinking} />}
              {parsed.notice && !hasAssistantContent && (
                <div className="rounded-md border border-destructive/50 bg-destructive/5 text-destructive px-3 py-2 text-sm">
                  <div className="font-medium">No renderable content</div>
                  <div className="text-xs mt-1">{parsed.notice}</div>
                </div>
              )}
              {parsed.raw && !hasAssistantContent && (
                <CollapsibleBlock label="raw response body" body={parsed.raw} />
              )}
            </div>
          )}
          <div className="text-xs text-muted-foreground">
            {fmtNum(turn.response.input_tokens)} in · {fmtNum(turn.response.output_tokens)} out
            {turn.response.cache_read_input_tokens != null &&
              ` · ${fmtNum(turn.response.cache_read_input_tokens)} cache`}
          </div>
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

  const toolResults = useMemo(
    () => (detail ? buildToolResultMap(detail.turns) : new Map<string, Block>()),
    [detail],
  )

  useEffect(() => {
    if (!detail) return
    const hash = window.location.hash.slice(1)
    if (hash) {
      const el = document.getElementById(hash)
      if (el) {
        requestAnimationFrame(() => el.scrollIntoView({ block: 'start' }))
      }
    }
  }, [detail])

  useEffect(() => {
    if (!detail) return
    const headerOffset = 56
    let frame = 0
    const onScroll = () => {
      if (frame) return
      frame = requestAnimationFrame(() => {
        frame = 0
        const nodes = document.querySelectorAll<HTMLElement>('[data-turn-id]')
        let topmost: HTMLElement | null = null
        for (const node of nodes) {
          const rect = node.getBoundingClientRect()
          if (rect.bottom > headerOffset) {
            topmost = node
            break
          }
        }
        if (topmost) {
          const id = `msg-${topmost.dataset.turnId}`
          if (window.location.hash.slice(1) !== id) {
            window.history.replaceState(null, '', `#${id}`)
          }
        }
      })
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [detail])

  if (error) return <div className="text-destructive">Error: {error}</div>
  if (!detail) return <div className="text-muted-foreground">Loading…</div>

  return (
    <div>
      <div className="mb-4 space-y-1">
        <div className="flex items-center gap-3">
          <Link to="/sessions" className="underline text-sm">
            ← Sessions
          </Link>
          <h2 className="text-lg font-semibold font-mono">{detail.session_id}</h2>
          <span className="text-xs text-muted-foreground">{detail.turns.length} turns</span>
        </div>
        {(detail.cwd || detail.git_branch || detail.started_at || detail.is_sidechain) && (
          <div className="text-xs text-muted-foreground flex flex-wrap gap-x-4">
            {detail.cwd && <span className="font-mono">{detail.cwd}</span>}
            {detail.git_branch && <span className="font-mono">branch: {detail.git_branch}</span>}
            {detail.is_sidechain && <span>sidechain</span>}
            {detail.started_at && <span>started {formatTimestamp(detail.started_at)}</span>}
          </div>
        )}
      </div>
      <div className="space-y-6">
        {detail.turns.map((t) => (
          <TurnView key={t.request.id} turn={t} toolResults={toolResults} />
        ))}
      </div>
    </div>
  )
}
