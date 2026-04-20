import { useEffect, useState } from 'react'
import { api, type Config } from '../lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table'

export default function Settings() {
  const [config, setConfig] = useState<Config | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.config().then(setConfig).catch((e) => setError(String(e)))
  }, [])

  if (error) return <div className="text-destructive">Error: {error}</div>
  if (!config) return <div className="text-muted-foreground">Loading…</div>

  const rows: Array<{ label: string; value: string | null }> = [
    { label: 'Version', value: config.version },
    { label: 'Database path', value: config.db_path },
    { label: 'ANTHROPIC_BASE_URL', value: config.anthropic_base_url },
  ]

  return (
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
  )
}
