// Shared number formatters for UI display. The DB always stores raw values;
// these helpers only shape what the user sees.

export function formatCost(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n === 0) return '$0.00'
  if (n > 0 && n < 0.01) return '<$0.01'
  if (n < 0 && n > -0.01) return '>-$0.01'
  const sign = n < 0 ? '-' : ''
  const abs = Math.abs(n)
  return `${sign}$${abs.toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`
}

// Whole counts (turns, hits, messages). Always full precision with thousands
// separators.
export function formatCount(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toLocaleString()
}

// Token counts. Compact (k/M/B) for large values to keep meta rows from
// wrapping; full with thousands separators for small values where precision
// matters.
export function formatTokens(n: number | null | undefined): string {
  if (n == null) return '—'
  const abs = Math.abs(n)
  if (abs >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (abs >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (abs >= 10_000) return `${(n / 1e3).toFixed(1)}k`
  return n.toLocaleString()
}

// Fractions in [0, 1] rendered as integer percentages (e.g. 0.0734 → "7%").
export function formatPct(n: number | null | undefined): string {
  if (n == null) return '—'
  return `${Math.round(n * 100).toLocaleString()}%`
}
