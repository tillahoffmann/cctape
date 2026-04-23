import { useEffect, useMemo, useState } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { TooltipContentProps } from 'recharts'
import { scaleTime } from 'd3-scale'
import { Popover as PopoverPrimitive } from 'radix-ui'
import {
  ArrowDownToLine,
  ArrowUpFromLine,
  Check,
  ChevronsUpDown,
  Clock,
  DollarSign,
  MessagesSquare,
} from 'lucide-react'
import { api, type AccountSummary, type UsageRecord } from '../lib/api'
import { formatCost } from '../lib/formatCost'
import { useAutoRefresh } from '../lib/useAutoRefresh'
import { useNow } from '../lib/useNow'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

interface Point {
  t: number
  unified_5h_utilization: number | null
  unified_7d_utilization: number | null
}

const RANGES = [1, 3, 7, 14, 30] as const
const STORAGE_KEY = 'usage.selectedAccountId'

function formatDate(s: string): string {
  return new Date(s).toLocaleDateString()
}

function formatCompact(n: number): string {
  if (n >= 1e9) return `${(n / 1e9).toFixed(1)}B`
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return String(n)
}

function totalTokens(a: AccountSummary): number {
  return (
    (a.input_tokens ?? 0) +
    (a.output_tokens ?? 0) +
    (a.cache_creation_input_tokens ?? 0) +
    (a.cache_read_input_tokens ?? 0)
  )
}

function shortId(id: string): string {
  const dash = id.indexOf('-')
  return (dash > 0 ? id.slice(0, dash) : id.slice(0, 8)) + '…'
}

function AccountOption({
  account,
  selected,
  onSelect,
}: {
  account: AccountSummary
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className="hover:bg-accent data-[selected=true]:border-ring flex w-full flex-col gap-1 rounded-lg border bg-card px-3 py-2 text-left transition-colors"
      data-selected={selected}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-sm">{account.account_id}</span>
        {selected && <Check className="text-foreground h-4 w-4 shrink-0" />}
      </div>
      <div className="text-muted-foreground flex flex-wrap gap-x-3 gap-y-0.5 text-xs">
        <span className="inline-flex items-center gap-1">
          <MessagesSquare className="h-3.5 w-3.5" />
          <span className="tabular-nums">{account.message_count}</span> msgs
        </span>
        <span className="inline-flex items-center gap-1">
          <ArrowDownToLine className="h-3.5 w-3.5" />
          <span className="tabular-nums">
            {formatCompact(account.input_tokens ?? 0)}
          </span>{' '}
          in
        </span>
        <span className="inline-flex items-center gap-1">
          <ArrowUpFromLine className="h-3.5 w-3.5" />
          <span className="tabular-nums">
            {formatCompact(account.output_tokens ?? 0)}
          </span>{' '}
          out
        </span>
        <span className="inline-flex items-center gap-1">
          <DollarSign className="h-3.5 w-3.5" />
          <span className="tabular-nums">{formatCost(account.cost_usd)}</span>
        </span>
        <span className="inline-flex items-center gap-1">
          <Clock className="h-3.5 w-3.5" />
          {formatDate(account.first_timestamp)}–
          {formatDate(account.last_timestamp)}
        </span>
      </div>
    </button>
  )
}

function AccountPicker({
  accounts,
  selectedAccountId,
  onChange,
}: {
  accounts: AccountSummary[]
  selectedAccountId: string
  onChange: (id: string) => void
}) {
  const [open, setOpen] = useState(false)
  const selected = accounts.find((a) => a.account_id === selectedAccountId)
  return (
    <PopoverPrimitive.Root open={open} onOpenChange={setOpen}>
      <PopoverPrimitive.Trigger
        className="border-input bg-background hover:bg-accent inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm transition-colors"
      >
        {selected ? (
          <>
            <span className="font-mono">{shortId(selected.account_id)}</span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground tabular-nums">
              {selected.message_count} msgs
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground tabular-nums">
              {formatCompact(totalTokens(selected))} tok
            </span>
            <span className="text-muted-foreground">·</span>
            <span className="text-muted-foreground tabular-nums">
              {formatCost(selected.cost_usd)}
            </span>
          </>
        ) : (
          <span className="text-muted-foreground">Select account…</span>
        )}
        <ChevronsUpDown className="text-muted-foreground ml-1 h-4 w-4" />
      </PopoverPrimitive.Trigger>
      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Content
          align="start"
          sideOffset={6}
          className="bg-popover text-popover-foreground z-50 w-[min(32rem,calc(100vw-2rem))] rounded-xl border p-2 shadow-md outline-none"
        >
          <div className="flex max-h-[70vh] flex-col gap-1.5 overflow-y-auto">
            {accounts.map((a) => (
              <AccountOption
                key={a.account_id}
                account={a}
                selected={a.account_id === selectedAccountId}
                onSelect={() => {
                  onChange(a.account_id)
                  setOpen(false)
                }}
              />
            ))}
          </div>
        </PopoverPrimitive.Content>
      </PopoverPrimitive.Portal>
    </PopoverPrimitive.Root>
  )
}

export default function Usage() {
  const [days, setDays] = useState<number>(7)
  const [selectedAccountId, setSelectedAccountId] = useState<string | null>(null)
  const now = useNow()

  const { data: accounts, error: accountsError } = useAutoRefresh<AccountSummary[]>(
    () => api.accounts(),
  )

  // Pick an initial account once the list first arrives. Subsequent refreshes
  // leave the user's selection alone. Linter flags setState-in-effect in
  // general, but here it's a one-shot initialization gated by
  // selectedAccountId === null, which is correct.
  useEffect(() => {
    if (accounts === null || selectedAccountId !== null) return
    if (accounts.length === 0) return
    const stored = localStorage.getItem(STORAGE_KEY)
    const match = stored && accounts.find((a) => a.account_id === stored)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelectedAccountId(match ? match.account_id : accounts[0].account_id)
  }, [accounts, selectedAccountId])

  useEffect(() => {
    if (selectedAccountId) localStorage.setItem(STORAGE_KEY, selectedAccountId)
  }, [selectedAccountId])

  const { data: records, error: recordsError } = useAutoRefresh<
    UsageRecord[] | null
  >(
    // Match pre-refactor behavior: wait for an account selection before
    // fetching. Returning the same null preserves object identity across ticks.
    () => (selectedAccountId ? api.usage(days, selectedAccountId) : Promise.resolve(null)),
    [days, selectedAccountId],
  )

  const error = accountsError ?? recordsError

  const derived = useMemo(() => {
    if (!records) return null
    const points: Point[] = new Array(records.length)
    let tMin = Infinity
    let tMax = -Infinity
    for (let i = 0; i < records.length; i++) {
      const r = records[i]
      const t = new Date(r.timestamp).getTime()
      if (t < tMin) tMin = t
      if (t > tMax) tMax = t
      points[i] = {
        t,
        unified_5h_utilization: r.unified_5h_utilization,
        unified_7d_utilization: r.unified_7d_utilization,
      }
    }
    if (!records.length) {
      tMin = 0
      tMax = 0
    }

    const uniqueSorted = (getter: (r: UsageRecord) => string | null): number[] => {
      const set = new Set<number>()
      for (const r of records) {
        const v = getter(r)
        if (v) set.add(new Date(v).getTime())
      }
      return Array.from(set).sort((a, b) => a - b)
    }
    const resets5h = uniqueSorted((r) => r.unified_5h_reset)
    const resets7d = uniqueSorted((r) => r.unified_7d_reset)

    // Split each line at reset boundaries by inserting null entries. Recharts
    // treats null y-values as gaps, breaking the stroke without connecting
    // across the reset. Only resets inside the data range are inserted so the
    // line's data doesn't widen the x-axis past the real points.
    const buildSeries = (resets: number[]): Point[] => {
      const merged: Point[] = points.slice()
      for (const t of resets) {
        if (t >= tMin && t <= tMax) {
          merged.push({ t, unified_5h_utilization: null, unified_7d_utilization: null })
        }
      }
      merged.sort((a, b) => a.t - b.t)
      return merged
    }
    const data5h = buildSeries(resets5h)
    const data7d = buildSeries(resets7d)

    const tickDates = records.length
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

    return {
      points,
      resets5h,
      resets7d,
      data5h,
      data7d,
      tMin,
      tMax,
      ticks,
      allOnDateBoundary,
    }
  }, [records])

  if (error) return <div className="text-destructive">Error: {error}</div>
  if (!accounts) return <div className="text-muted-foreground">Loading…</div>
  if (accounts.length === 0)
    return (
      <div className="text-muted-foreground">No account data recorded yet.</div>
    )
  if (!records || !derived)
    return <div className="text-muted-foreground">Loading…</div>

  const {
    points,
    resets5h,
    resets7d,
    data5h,
    data7d,
    tMin,
    tMax,
    ticks,
    allOnDateBoundary,
  } = derived

  const nextReset5h = resets5h.find((t) => t > now) ?? null
  const nextReset7d = resets7d.find((t) => t > now) ?? null
  const pastResets5h = resets5h.filter((t) => t >= tMin && t <= tMax && t <= now)
  const pastResets7d = resets7d.filter((t) => t >= tMin && t <= tMax && t <= now)
  const fmtAbs = (t: number) => {
    const d = new Date(t)
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
  }

  const fmtPct = (v: number | null | undefined) =>
    v === null || v === undefined ? '—' : `${Math.round(v * 100)}%`

  const UsageTooltip = ({ active, payload }: TooltipContentProps) => {
    if (!active || !payload || payload.length === 0) return null
    const first = payload[0] as { payload?: { t?: number } } | undefined
    const t = first?.payload?.t
    if (t === undefined) return null
    const v5 =
      payload.find((p) => p.dataKey === 'unified_5h_utilization')?.value ?? null
    const v7 =
      payload.find((p) => p.dataKey === 'unified_7d_utilization')?.value ?? null
    return (
      <div className="bg-popover text-popover-foreground animate-in fade-in-0 rounded-md border px-2.5 py-1.5 text-xs shadow-md duration-100">
        <div className="text-muted-foreground mb-1 tabular-nums">{fmtAbs(t)}</div>
        <div className="flex items-center gap-2">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: 'var(--color-chart-1)' }}
          />
          <span className="text-muted-foreground">5h</span>
          <span className="ml-auto tabular-nums">{fmtPct(v5 as number | null)}</span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="h-2 w-2 rounded-full"
            style={{ backgroundColor: 'var(--color-chart-2)' }}
          />
          <span className="text-muted-foreground">7d</span>
          <span className="ml-auto tabular-nums">{fmtPct(v7 as number | null)}</span>
        </div>
      </div>
    )
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div className="flex flex-row items-center gap-3">
          <Tabs value={String(days)} onValueChange={(v) => setDays(Number(v))}>
            <TabsList>
              {RANGES.map((d) => (
                <TabsTrigger key={d} value={String(d)}>
                  {d}d
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <AccountPicker
            accounts={accounts}
            selectedAccountId={selectedAccountId!}
            onChange={setSelectedAccountId}
          />
        </div>
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
        {points.length === 0 ? (
          <div className="text-muted-foreground py-12 text-center">
            No usage data in the last {days} day{days === 1 ? '' : 's'}.
          </div>
        ) : (
          <div style={{ width: '100%', height: 420 }}>
            <ResponsiveContainer>
              <LineChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
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
                <Tooltip content={UsageTooltip} isAnimationActive={false} />
                <Legend />
                <Line
                  data={data5h}
                  type="stepAfter"
                  dataKey="unified_5h_utilization"
                  name="5h"
                  stroke="var(--color-chart-1)"
                  dot={false}
                  connectNulls={false}
                  isAnimationActive={false}
                />
                <Line
                  data={data7d}
                  type="stepAfter"
                  dataKey="unified_7d_utilization"
                  name="7d"
                  stroke="var(--color-chart-2)"
                  dot={false}
                  connectNulls={false}
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
