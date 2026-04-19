import { useState } from 'react'
import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api, type SessionDetail, type Turn } from '../lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
  text: string
  tools: Block[]
  thinking: string
  notice: string | null
  raw: string | null
}

function parseResponse(payload: string | null): ParsedResponse {
  if (!payload) {
    return { text: '', tools: [], thinking: '', notice: 'Empty response body.', raw: null }
  }

  const trimmed = payload.trimStart()

  // Non-streaming JSON response (error or non-stream request)
  if (trimmed.startsWith('{')) {
    try {
      const obj = JSON.parse(trimmed) as {
        type?: string
        error?: { type?: string; message?: string }
        content?: Block[]
      }
      if (obj.type === 'error' || obj.error) {
        return {
          text: '',
          tools: [],
          thinking: '',
          notice: `API error: ${obj.error?.type ?? 'unknown'} — ${obj.error?.message ?? ''}`,
          raw: payload,
        }
      }
      if (Array.isArray(obj.content)) {
        const text = obj.content
          .filter((b) => b.type === 'text' && b.text)
          .map((b) => b.text as string)
          .join('')
        const tools = obj.content.filter((b) => b.type === 'tool_use')
        const thinking = obj.content
          .filter((b) => b.type === 'thinking')
          .map((b) => (b as { thinking?: string }).thinking ?? '')
          .join('')
        return {
          text,
          tools,
          thinking,
          notice: 'Non-streaming JSON response.',
          raw: payload,
        }
      }
      return { text: '', tools: [], thinking: '', notice: 'Unrecognized JSON response.', raw: payload }
    } catch (e) {
      return {
        text: '',
        tools: [],
        thinking: '',
        notice: `Malformed JSON response: ${String(e)}`,
        raw: payload,
      }
    }
  }

  // SSE response
  let text = ''
  let thinking = ''
  const tools: Block[] = []
  const currentTool: { name?: string; input: string; id?: string } = { input: '' }
  let inToolUse = false
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
        } else if (event.delta?.type === 'thinking_delta' && event.delta.thinking) {
          thinking += event.delta.thinking
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

  let notice: string | null = null
  if (!sawAnyEvent) {
    notice = 'Response body is neither SSE nor JSON.'
  } else if (!text && tools.length === 0 && thinking) {
    notice = 'Response contained only a thinking block (no text or tool use).'
  } else if (!text && tools.length === 0) {
    notice = 'SSE stream had no text or tool use blocks.'
  }

  return { text, tools, thinking, notice, raw: notice ? payload : null }
}

function ToolBlock({ label, body }: { label: string; body: string }) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-2">
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

function MarkdownText({ children }: { children: string }) {
  return (
    <div className="text-sm leading-relaxed break-words [&_p]:my-2 [&_ul]:list-disc [&_ul]:ml-5 [&_ol]:list-decimal [&_ol]:ml-5 [&_code]:px-1 [&_code]:py-0.5 [&_code]:bg-muted [&_code]:rounded [&_pre]:p-3 [&_pre]:bg-muted [&_pre]:rounded-md [&_pre]:overflow-x-auto [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_h1]:text-lg [&_h1]:font-semibold [&_h1]:mt-3 [&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-3 [&_h3]:font-semibold [&_h3]:mt-2 [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  )
}

function renderBlock(block: Block, key: number, markdown = false) {
  if (block.type === 'text' && block.text) {
    if (markdown) {
      return <MarkdownText key={key}>{block.text}</MarkdownText>
    }
    return (
      <div key={key} className="whitespace-pre-wrap break-words text-sm leading-relaxed">
        {block.text}
      </div>
    )
  }
  if (block.type === 'tool_use') {
    return (
      <ToolBlock
        key={key}
        label={`tool_use: ${block.name ?? 'unknown'}`}
        body={JSON.stringify(block.input, null, 2)}
      />
    )
  }
  if (block.type === 'tool_result') {
    const content =
      typeof block.content === 'string' ? block.content : JSON.stringify(block.content, null, 2)
    return <ToolBlock key={key} label="tool_result" body={content} />
  }
  return (
    <pre key={key} className="text-xs overflow-x-auto">
      {JSON.stringify(block, null, 2)}
    </pre>
  )
}

function formatTimestamp(iso: string): string {
  return new Date(iso).toLocaleString()
}

function TurnView({ turn }: { turn: Turn }) {
  const messages = (turn.request.payload?.messages ?? []) as unknown[]
  const lastUser = messages[messages.length - 1]
  const userBlocks = blocksFromRequestMessage(lastUser)
  const parsed = parseResponse(turn.response?.payload ?? null)
  const assistantBlocks: Block[] = [
    ...(parsed.text ? [{ type: 'text', text: parsed.text } as Block] : []),
    ...parsed.tools,
  ]
  const hasContent = assistantBlocks.length > 0

  return (
    <div className="space-y-3">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">User · {formatTimestamp(turn.request.timestamp)}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {userBlocks.map((b, i) => renderBlock(b, i))}
        </CardContent>
      </Card>
      {turn.response && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">
              Assistant · {formatTimestamp(turn.response.timestamp)}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {assistantBlocks.map((b, i) => renderBlock(b, i, true))}
            {parsed.thinking && (
              <ToolBlock label="thinking" body={parsed.thinking} />
            )}
            {parsed.notice && !hasContent && (
              <div className="rounded-md border border-destructive/50 bg-destructive/5 text-destructive px-3 py-2 text-sm">
                <div className="font-medium">No renderable content</div>
                <div className="text-xs mt-1">{parsed.notice}</div>
              </div>
            )}
            {parsed.raw && !hasContent && (
              <ToolBlock label="raw response body" body={parsed.raw} />
            )}
            <div className="text-xs text-muted-foreground pt-2">
              {turn.response.input_tokens ?? '—'} in · {turn.response.output_tokens ?? '—'} out
              {turn.response.cache_read_input_tokens != null &&
                ` · ${turn.response.cache_read_input_tokens} cache`}
            </div>
          </CardContent>
        </Card>
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

  if (error) return <div className="text-destructive">Error: {error}</div>
  if (!detail) return <div className="text-muted-foreground">Loading…</div>

  return (
    <div>
      <div className="mb-4 flex items-center gap-3">
        <Link to="/sessions" className="underline text-sm">
          ← Sessions
        </Link>
        <h2 className="text-lg font-semibold font-mono">{detail.session_id}</h2>
        <span className="text-xs text-muted-foreground">{detail.turns.length} turns</span>
      </div>
      <div className="space-y-6">
        {detail.turns.map((t) => (
          <TurnView key={t.request.id} turn={t} />
        ))}
      </div>
    </div>
  )
}
