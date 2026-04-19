import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts'
import { scaleTime } from 'd3-scale'
import { api, type UsageRecord } from '../lib/api'
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

  const tMin = data.length ? Math.min(...data.map((d) => d.t)) : 0
  const tMax = data.length ? Math.max(...data.map((d) => d.t)) : 0
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
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
