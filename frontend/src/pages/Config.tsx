import { useEffect, useState } from 'react'
import { api, type Config as ConfigData, type Pricing } from '../lib/api'
import { Terminal, Code2, Plug, DollarSign, Info } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CodeBlock } from '@/components/CodeBlock'

const PRICE_COLUMNS: Array<{ key: string; label: string }> = [
  { key: 'input', label: 'Input' },
  { key: 'output', label: 'Output' },
  { key: 'cache_write_5m', label: 'Cache write (5m)' },
  { key: 'cache_write_1h', label: 'Cache write (1h)' },
  { key: 'cache_read', label: 'Cache read' },
]

function formatRate(n: number | undefined): string {
  if (n === undefined) return '—'
  return `$${n.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

function SectionTitle({
  icon: Icon,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>
  children: React.ReactNode
}) {
  return (
    <CardTitle className="flex items-center gap-2">
      <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-primary/10 text-primary shrink-0">
        <Icon className="h-4 w-4" />
      </span>
      {children}
    </CardTitle>
  )
}

export default function Config() {
  const [config, setConfig] = useState<ConfigData | null>(null)
  const [pricing, setPricing] = useState<Pricing | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setError(String(e)))
    api.pricing().then(setPricing).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="text-destructive">Error: {error}</div>
  if (!config || !pricing)
    return <div className="text-muted-foreground">Loading…</div>

  const proxyUrl =
    config.anthropic_base_url ?? `${window.location.origin}/proxy`
  const origin = window.location.origin
  const mcpUrl = `${origin}/mcp`

  const envExport = `export ANTHROPIC_BASE_URL=${proxyUrl}`
  const vscodeSnippet = `"claudeCode.environmentVariables": [
    {"name": "ANTHROPIC_BASE_URL", "value": "${proxyUrl}"}
]`
  const mcpAddCmd = `claude mcp add --transport http cctape ${mcpUrl}`

  const aboutRows: Array<{ label: string; value: string | null }> = [
    { label: 'Version', value: config.version },
    { label: 'Database path', value: config.db_path },
    { label: 'ANTHROPIC_BASE_URL', value: config.anthropic_base_url },
  ]

  const models = Object.keys(pricing).sort()

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <SectionTitle icon={Terminal}>Terminal setup</SectionTitle>
          <p className="text-muted-foreground text-sm">
            Record sessions from the{' '}
            <span className="font-mono">claude</span> CLI — add the env var
            to <span className="font-mono">~/.zshrc</span> or{' '}
            <span className="font-mono">~/.bashrc</span>, then start a new
            shell.
          </p>
        </CardHeader>
        <CardContent>
          <CodeBlock code={envExport} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <SectionTitle icon={Code2}>VS Code setup</SectionTitle>
          <p className="text-muted-foreground text-sm">
            Record sessions from the Claude Code VS Code extension — add the
            env var to user <span className="font-mono">settings.json</span>.
          </p>
        </CardHeader>
        <CardContent>
          <CodeBlock code={vscodeSnippet} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <SectionTitle icon={Plug}>MCP setup</SectionTitle>
          <p className="text-muted-foreground text-sm">
            Let Claude Code search its own archive — register the MCP server
            at <span className="font-mono">{mcpUrl}</span> with the{' '}
            <span className="font-mono">claude</span> CLI. Exposes{' '}
            <span className="font-mono">search_transcripts</span> and{' '}
            <span className="font-mono">get_session_window</span>.
          </p>
        </CardHeader>
        <CardContent>
          <CodeBlock code={mcpAddCmd} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <SectionTitle icon={DollarSign}>Pricing</SectionTitle>
          <p className="text-muted-foreground text-sm">
            Rates used to compute session costs — USD per million tokens,
            hardcoded and updated manually from anthropic.com/pricing.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Model</TableHead>
                {PRICE_COLUMNS.map((c) => (
                  <TableHead key={c.key} className="text-right">
                    {c.label}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {models.map((model) => (
                <TableRow key={model}>
                  <TableCell className="font-mono text-sm">{model}</TableCell>
                  {PRICE_COLUMNS.map((c) => (
                    <TableCell
                      key={c.key}
                      className="text-right font-mono text-sm tabular-nums"
                    >
                      {formatRate(pricing[model][c.key])}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <SectionTitle icon={Info}>About</SectionTitle>
          <p className="text-muted-foreground text-sm">
            Runtime details for this cctape instance — version and paths.
          </p>
        </CardHeader>
        <CardContent>
          <Table>
            <TableBody>
              {aboutRows.map(({ label, value }) => (
                <TableRow key={label}>
                  <TableCell className="text-muted-foreground w-1/3 font-medium">
                    {label}
                  </TableCell>
                  <TableCell className="font-mono text-sm break-all">
                    {value ?? '—'}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  )
}
