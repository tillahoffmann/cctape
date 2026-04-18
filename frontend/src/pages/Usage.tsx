import { useEffect, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api, type UsageRecord } from '../lib/api'

interface Point {
  t: number
  label: string
  unified_5h_utilization: number
  unified_7d_utilization: number
}

export default function Usage() {
  const [records, setRecords] = useState<UsageRecord[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(7)

  useEffect(() => {
    api.usage(days).then(setRecords).catch((e) => setError(String(e)))
  }, [days])

  if (error) return <div className="text-red-400">Error: {error}</div>
  if (!records) return <div className="text-zinc-400">Loading…</div>

  const data: Point[] = records.map((r) => {
    const t = new Date(r.timestamp).getTime()
    return {
      t,
      label: new Date(r.timestamp).toLocaleString(),
      unified_5h_utilization: r.unified_5h_utilization,
      unified_7d_utilization: r.unified_7d_utilization,
    }
  })

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-xl font-semibold text-zinc-100">Utilization</h2>
        <div className="flex items-center gap-1 text-xs">
          {[1, 3, 7, 14, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`px-2.5 py-1 rounded-md transition-colors ${
                days === d
                  ? 'bg-zinc-800 text-zinc-100'
                  : 'text-zinc-400 hover:text-zinc-100 hover:bg-zinc-900'
              }`}
            >
              {d}d
            </button>
          ))}
        </div>
      </div>
      <div className="rounded-lg border border-zinc-800 bg-zinc-900/40 p-4">
        {data.length === 0 ? (
          <div className="text-zinc-400 py-12 text-center">
            No usage data in the last {days} day{days === 1 ? '' : 's'}.
          </div>
        ) : (
          <div style={{ width: '100%', height: 420 }}>
            <ResponsiveContainer>
              <LineChart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid stroke="rgb(39 39 42)" strokeDasharray="3 3" />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={['dataMin', 'dataMax']}
                  scale="time"
                  tickFormatter={(v) => new Date(v).toLocaleDateString()}
                  stroke="rgb(113 113 122)"
                  fontSize={11}
                />
                <YAxis
                  domain={[0, 1]}
                  tickFormatter={(v) => `${Math.round(v * 100)}%`}
                  stroke="rgb(113 113 122)"
                  fontSize={11}
                />
                <Tooltip
                  contentStyle={{
                    background: 'rgb(24 24 27)',
                    border: '1px solid rgb(39 39 42)',
                    borderRadius: 6,
                    color: 'rgb(244 244 245)',
                    fontSize: 12,
                  }}
                  labelFormatter={(v) => new Date(v as number).toLocaleString()}
                  formatter={(value) => `${(Number(value) * 100).toFixed(1)}%`}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Line
                  type="monotone"
                  dataKey="unified_5h_utilization"
                  name="5h"
                  stroke="rgb(56 189 248)"
                  dot={false}
                  strokeWidth={2}
                  isAnimationActive={false}
                />
                <Line
                  type="monotone"
                  dataKey="unified_7d_utilization"
                  name="7d"
                  stroke="rgb(251 191 36)"
                  dot={false}
                  strokeWidth={2}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  )
}
