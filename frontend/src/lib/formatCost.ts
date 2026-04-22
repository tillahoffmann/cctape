export function formatCost(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n === 0) return '$0.00'
  if (n < 0.01) return '<$0.01'
  if (n < 100) return `$${n.toFixed(2)}`
  return `$${n.toFixed(0)}`
}
