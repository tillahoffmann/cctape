import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts'
import { scaleTime } from 'd3-scale'
import { api, type UsageRecord } from '../lib/api'
import { useNow } from '../lib/useNow'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

interface Point {
  t: number
  unified_5h_utilization: number
  unified_7d_utilization: number
}

const RANGES = [1, 3, 7, 14, 30] as const

export default function Usage() {
  const [records, setRecords] = useState<UsageRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState<number>(7)
  const now = useNow()

  useEffect(() => {
    api.usage(days).then(setRecords).catch((e) => setError(String(e)))
  }, [days])

  if (error) return <div className="text-destructive">Error: {error}</div>
  if (!records) return <div className="text-muted-foreground">Loading…</div>

  const data: Point[] = records.map((r) => ({
    t: new Date(r.timestamp).getTime(),
    unified_5h_utilization: r.unified_5h_utilization,
    unified_7d_utilization: r.unified_7d_utilization,
  }))

  const resets5h = Array.from(
    new Set(
      records
        .map((r) => (r.unified_5h_reset ? new Date(r.unified_5h_reset).getTime() : null))
        .filter((v): v is number => v !== null),
    ),
  ).sort((a, b) => a - b)
  const resets7d = Array.from(
    new Set(
      records
        .map((r) => (r.unified_7d_reset ? new Date(r.unified_7d_reset).getTime() : null))
        .filter((v): v is number => v !== null),
    ),
  ).sort((a, b) => a - b)
  const nextReset5h = resets5h.find((t) => t > now) ?? null
  const nextReset7d = resets7d.find((t) => t > now) ?? null

  const tMin = data.length ? Math.min(...data.map((d) => d.t)) : 0
  const tMax = data.length ? Math.max(...data.map((d) => d.t)) : 0
  const pastResets5h = resets5h.filter((t) => t >= tMin && t <= tMax && t <= now)
  const pastResets7d = resets7d.filter((t) => t >= tMin && t <= tMax && t <= now)
  const fmtAbs = (t: number) => {
    const d = new Date(t)
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
  }
  const tickDates = data.length
    ? scaleTime().domain([tMin, tMax]).ticks(7)
    : []
  const ticks = tickDates.map((d) => d.getTime())
  const allOnDateBoundary = tickDates.every(
    (d) =>
      d.getHours() === 0 &&
      d.getMinutes() === 0 &&
      d.getSeconds() === 0 &&
      d.getMilliseconds() === 0,
  )

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <Tabs value={String(days)} onValueChange={(v) => setDays(Number(v))}>
          <TabsList>
            {RANGES.map((d) => (
              <TabsTrigger key={d} value={String(d)}>
                {d}d
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="text-muted-foreground flex flex-col items-end text-xs leading-tight">
          <div>
            Next 5h reset:{' '}
            <span className="text-foreground">
              {nextReset5h ? fmtAbs(nextReset5h) : '—'}
            </span>
          </div>
          <div>
            Next 7d reset:{' '}
            <span className="text-foreground">
              {nextReset7d ? fmtAbs(nextReset7d) : '—'}
            </span>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {data.length === 0 ? (
          <div className="text-muted-foreground py-12 text-center">
            No usage data in the last {days} day{days === 1 ? '' : 's'}.
          </div>
        ) : (
          <div style={{ width: '100%', height: 420 }}>
            <ResponsiveContainer>
              <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={[tMin, tMax]}
                  scale="time"
                  ticks={ticks}
                  tickFormatter={(v) => {
                    const d = new Date(v)
                    return allOnDateBoundary
                      ? d.toLocaleDateString()
                      : `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
                  }}
                />
                <YAxis domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="unified_5h_utilization"
                  name="5h"
                  stroke="var(--color-chart-1)"
                  dot={true}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="unified_7d_utilization"
                  name="7d"
                  stroke="var(--color-chart-2)"
                  dot={true}
                  isAnimationActive={false}
                />
                {pastResets5h.map((t) => (
                  <ReferenceLine
                    key={`r5-${t}`}
                    x={t}
                    stroke="var(--color-chart-1)"
                    strokeDasharray="2 2"
                    strokeOpacity={0.5}
                  />
                ))}
                {pastResets7d.map((t) => (
                  <ReferenceLine
                    key={`r7-${t}`}
                    x={t}
                    stroke="var(--color-chart-2)"
                    strokeDasharray="2 2"
                    strokeOpacity={0.5}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
