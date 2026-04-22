import { useEffect, useState } from 'react'
import { api, type Config, type Pricing } from '../lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

const PRICE_COLUMNS: Array<{ key: string; label: string }> = [
  { key: 'input', label: 'Input' },
  { key: 'output', label: 'Output' },
  { key: 'cache_write_5m', label: 'Cache write (5m)' },
  { key: 'cache_write_1h', label: 'Cache write (1h)' },
  { key: 'cache_read', label: 'Cache read' },
]

function formatRate(n: number | undefined): string {
  if (n === undefined) return '—'
  return `$${n.toFixed(2)}`
}

export default function Settings() {
  const [config, setConfig] = useState<Config | null>(null)
  const [pricing, setPricing] = useState<Pricing | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setError(String(e)))
    api.pricing().then(setPricing).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="text-destructive">Error: {error}</div>
  if (!config || !pricing)
    return <div className="text-muted-foreground">Loading…</div>

  const rows: Array<{ label: string; value: string | null }> = [
    { label: 'Version', value: config.version },
    { label: 'Database path', value: config.db_path },
    { label: 'ANTHROPIC_BASE_URL', value: config.anthropic_base_url },
  ]

  const models = Object.keys(pricing).sort()

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Settings</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableBody>
              {rows.map(({ label, value }) => (
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

      <Card>
        <CardHeader>
          <CardTitle>Pricing</CardTitle>
          <p className="text-muted-foreground text-xs">
            USD per million tokens. Rates are hardcoded and updated manually
            from anthropic.com/pricing.
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
    </div>
  )
}
