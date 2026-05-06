export interface UsageRecord {
  timestamp: string
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  unified_5h_utilization: number
  unified_7d_utilization: number
  unified_5h_reset: string | null
  unified_7d_reset: string | null
}

export interface SessionSummary {
  session_id: string
  first_timestamp: string
  last_timestamp: string
  turn_count: number
  input_tokens: number | null
  output_tokens: number | null
  cache_creation_input_tokens: number | null
  cache_read_input_tokens: number | null
  cost_usd: number | null
  first_message_preview: string | null
  peak_context_tokens: number | null
  cwd: string | null
  git_branch: string | null
  is_sidechain: boolean | null
  started_at: string | null
  title: string | null
}

export interface RequestRecord {
  id: number
  timestamp: string
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  payload: any
}

export interface ResponseRecord {
  status_code: number
  timestamp: string
  payload: string | null
  input_tokens: number | null
  output_tokens: number | null
  cache_creation_input_tokens: number | null
  cache_read_input_tokens: number | null
  unified_5h_utilization: number | null
  unified_7d_utilization: number | null
  model: string | null
  cost_usd: number | null
}

export interface Turn {
  request: RequestRecord
  response: ResponseRecord | null
}

export interface SessionDetail {
  session_id: string
  turns: Turn[]
  cwd: string | null
  git_branch: string | null
  is_sidechain: boolean | null
  started_at: string | null
  title: string | null
}

// Per-URL ETag cache. Paired with the server's ETag middleware: we send
// If-None-Match with the last ETag we saw for this URL; the server returns
// 304 with no body when nothing's changed, and we serve the stored data.
const etagCache = new Map<string, { etag: string; data: unknown }>()

async function getJSON<T>(url: string): Promise<T> {
  const cached = etagCache.get(url)
  const res = await fetch(url, {
    headers: cached ? { 'if-none-match': cached.etag } : {},
  })
  if (res.status === 304 && cached) return cached.data as T
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  const data = (await res.json()) as T
  const etag = res.headers.get('etag')
  if (etag) etagCache.set(url, { etag, data })
  return data
}

async function sendJSON<T>(url: string, method: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export interface SearchHit {
  session_id: string
  snippet: string
  rank: number
  hit_count: number
  title: string | null
  cwd: string | null
  git_branch: string | null
}

export interface Config {
  version: string | null
  db_path: string
  anthropic_base_url: string
}

export interface AccountSummary {
  account_id: string
  message_count: number
  first_timestamp: string
  last_timestamp: string
  input_tokens: number | null
  output_tokens: number | null
  cache_creation_input_tokens: number | null
  cache_read_input_tokens: number | null
  cost_usd: number | null
}

export type Pricing = Record<string, Record<string, number>>

export const api = {
  config: () => getJSON<Config>('/api/config'),
  pricing: () => getJSON<Pricing>('/api/pricing'),
  sessions: (limit?: number) =>
    getJSON<SessionSummary[]>(
      limit ? `/api/sessions?limit=${limit}` : '/api/sessions',
    ),
  session: (id: string) => getJSON<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`),
  usage: (days = 7, accountId?: string) => {
    const qs = accountId
      ? `?days=${days}&account_id=${encodeURIComponent(accountId)}`
      : `?days=${days}`
    return getJSON<UsageRecord[]>(`/api/usage${qs}`)
  },
  accounts: () => getJSON<AccountSummary[]>('/api/accounts'),
  search: (q: string, limit = 50) =>
    getJSON<SearchHit[]>(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  updateSessionTitle: (id: string, title: string | null) =>
    sendJSON<{ session_id: string; title: string | null }>(
      `/api/sessions/${encodeURIComponent(id)}`,
      'PATCH',
      { title },
    ),
}
