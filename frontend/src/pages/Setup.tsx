import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CassetteTape } from 'lucide-react'
import { api, type Config } from '../lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CodeBlock } from '@/components/CodeBlock'

function Step({
  n,
  title,
  children,
}: {
  n: number
  title: string
  children: React.ReactNode
}) {
  return (
    <div className="flex gap-4">
      <div className="shrink-0 h-8 w-8 rounded-full bg-primary text-primary-foreground text-sm font-semibold flex items-center justify-center">
        {n}
      </div>
      <div className="flex-1 flex flex-col gap-3 pt-1 min-w-0">
        <div className="text-base font-semibold">{title}</div>
        {children}
      </div>
    </div>
  )
}

export default function Setup() {
  const [config, setConfig] = useState<Config | null>(null)
  const [hasSessions, setHasSessions] = useState<boolean | null>(null)

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null))
    api
      .sessions()
      .then((list) => setHasSessions(list.length > 0))
      .catch(() => setHasSessions(null))
  }, [])

  const proxyUrl =
    config?.anthropic_base_url ?? `${window.location.origin}/proxy`
  const envExport = `export ANTHROPIC_BASE_URL=${proxyUrl}`
  const vscodeSnippet = `"claudeCode.environmentVariables": [
    {"name": "ANTHROPIC_BASE_URL", "value": "${proxyUrl}"}
]`

  return (
    <div className="flex flex-col gap-6 max-w-3xl mx-auto py-4">
      <div className="flex flex-col items-center gap-3 text-center">
        <div className="h-12 w-12 rounded-lg bg-primary/10 flex items-center justify-center">
          <CassetteTape className="h-6 w-6 text-primary" />
        </div>
        <h1 className="text-2xl font-bold tracking-tight">Welcome to cctape</h1>
        <p className="text-muted-foreground max-w-lg">
          cctape is a local proxy that records every Claude Code request and
          response to a SQLite database, so you can browse sessions, search
          transcripts, and track token usage and cost.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Get started</CardTitle>
          <p className="text-muted-foreground text-xs">
            Point Claude Code at this proxy. Pick whichever setup matches how
            you run Claude Code.
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-6 pt-2">
          <Step n={1} title="Shell (claude CLI)">
            <p className="text-muted-foreground text-sm">
              Add to <span className="font-mono">~/.zshrc</span> or{' '}
              <span className="font-mono">~/.bashrc</span>, then start a new
              shell.
            </p>
            <CodeBlock code={envExport} />
          </Step>

          <Step n={2} title="Claude Code VS Code extension">
            <p className="text-muted-foreground text-sm">
              Add to user <span className="font-mono">settings.json</span>.
            </p>
            <CodeBlock code={vscodeSnippet} />
          </Step>

          <Step n={3} title="Verify">
            <p className="text-muted-foreground text-sm">
              Run <span className="font-mono">claude</span> and ask it
              something. A new session should appear on{' '}
              <Link to="/sessions" className="underline">
                the sessions page
              </Link>{' '}
              within a few seconds.
            </p>
            {hasSessions === true && (
              <div className="text-sm rounded-md border border-primary/30 bg-primary/5 px-3 py-2">
                ✓ cctape is already receiving traffic — you're all set.
              </div>
            )}
            {hasSessions === false && (
              <div className="text-sm rounded-md border border-muted-foreground/20 bg-muted px-3 py-2 text-muted-foreground">
                No sessions recorded yet.
              </div>
            )}
          </Step>
        </CardContent>
      </Card>
    </div>
  )
}
