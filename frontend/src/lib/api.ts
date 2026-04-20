export interface UsageRecord {
  timestamp: string
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  unified_5h_utilization: number
  unified_7d_utilization: number
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
  first_message_preview: string | null
  peak_context_tokens: number | null
  cwd: string | null
  git_branch: string | null
  is_sidechain: boolean | null
  started_at: string | null
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
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

export const api = {
  sessions: () => getJSON<SessionSummary[]>('/api/sessions'),
  session: (id: string) => getJSON<SessionDetail>(`/api/sessions/${encodeURIComponent(id)}`),
  usage: (days = 7) => getJSON<UsageRecord[]>(`/api/usage?days=${days}`),
}
