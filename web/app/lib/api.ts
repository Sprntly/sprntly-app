/**
 * Thin client for the Sprntly backend at api.sprntly.ai.
 * All requests include the session cookie via credentials: 'include'.
 */

// Default to the deployed backend so `npm run dev` works out of the box
// without a local FastAPI. To run against a local backend, set
// `NEXT_PUBLIC_API_URL=http://localhost:8000` in `web/.env.local`.
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "https://api.sprntly.ai"

export class ApiError extends Error {
  status: number
  body: unknown
  constructor(status: number, body: unknown, message?: string) {
    super(message || `API ${status}`)
    this.status = status
    this.body = body
  }
}

async function request<T>(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData
  const res = await fetch(`${API_URL}${path}`, {
    method,
    credentials: "include",
    headers: isForm
      ? { Accept: "application/json" }
      : body
      ? { "Content-Type": "application/json", Accept: "application/json" }
      : { Accept: "application/json" },
    body: isForm
      ? (body as FormData)
      : body
      ? JSON.stringify(body)
      : undefined,
  })
  let parsed: unknown = null
  const text = await res.text()
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = text
    }
  }
  if (!res.ok) {
    throw new ApiError(res.status, parsed)
  }
  return parsed as T
}

export const api = {
  get: <T>(path: string) => request<T>("GET", path),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
}

// ---- typed wrappers ---------------------------------------------------------

export type AuthMe = { scope: string; expires_at: string }

export const auth = {
  login: (password: string) => api.post<{ ok: true }>("/v1/auth/login", { password }),
  logout: () => api.post<{ ok: true }>("/v1/auth/logout"),
  me: () => api.get<AuthMe>("/v1/auth/me"),
}

export type BriefMetric = { label: string; value: string }
export type ConvergenceItem = { source: string; signal: string; strength: string }
export type UserQuote = { quote: string; source: string }
export type ChartHint = {
  kind: "bar" | "line" | "stat"
  title: string
  data: { label: string; value: number }[]
}
export type Insight = {
  tag: "something_new" | "something_better" | "something_broken"
  title: string
  subtitle: string
  metrics: BriefMetric[]
  domain: string
  subdomain: string
  confidence: number
  headline: string
  why_this_ranks: string[]
  why_alternatives_dont_hold: string[]
  recommendation: string
  impact_math: string[]
  verification_metrics: string[]
  convergence: ConvergenceItem[]
  user_quotes: UserQuote[]
  chart_hints: ChartHint[]
}
export type Brief = {
  id: number
  dataset: string
  generated_at: string
  week_label: string
  summary_headline: string
  insights: Insight[]
}

export type BriefStatus = {
  dataset: string
  status: "ready" | "generating" | "failed" | "empty"
  error?: string
}

export const briefApi = {
  current: (dataset: string = "asurion") =>
    api.get<Brief>(`/v1/brief/current?dataset=${encodeURIComponent(dataset)}`),
  byId: (id: number) => api.get<Brief>(`/v1/brief/${id}`),
  status: (dataset: string = "asurion") =>
    api.get<BriefStatus>(`/v1/brief/status?dataset=${encodeURIComponent(dataset)}`),
  regenerate: (dataset: string = "asurion") =>
    api.post<{ started: boolean; dataset: string }>(
      `/v1/brief/regenerate?dataset=${encodeURIComponent(dataset)}`,
    ),
  generate: () => api.post<Brief & { brief_id: number }>("/v1/brief/generate"),
}

export type AskCitation = { source: string; evidence: string }
export type AskResponse = {
  answer: string
  key_points: string[]
  citations: AskCitation[]
  confidence: number
  unanswered: string
}

export const askApi = {
  ask: (question: string, dataset: string = "asurion") =>
    api.post<AskResponse>("/v1/ask", { question, dataset }),
}

export type PrdStartResponse = {
  prd_id: number
  status: "generating" | "ready" | "failed"
  title: string
}

export type PrdRecord = {
  id: number
  brief_id: number
  insight_index: number
  generated_at: string
  title: string
  payload_md: string
  status: "generating" | "ready" | "failed"
  error?: string | null
}

export type EvidenceStartResponse = {
  evidence_id: number
  status: "generating" | "ready" | "failed"
  title: string
}

export type EvidenceRecord = {
  id: number
  brief_id: number
  insight_index: number
  generated_at: string
  title: string
  payload_md: string
  status: "generating" | "ready" | "failed"
  error?: string | null
}

export const evidenceApi = {
  /** Kicks off Evidence Page generation in the background. Returns
   *  immediately with an evidence_id; client should poll
   *  evidenceApi.get(id) until status === 'ready'. */
  generate: (briefId: number, insightIndex: number, force = false) =>
    api.post<EvidenceStartResponse>("/v1/evidence/generate", {
      brief_id: briefId,
      insight_index: insightIndex,
      force,
    }),
  get: (id: number) => api.get<EvidenceRecord>(`/v1/evidence/${id}`),
}

// ---- datasets ---------------------------------------------------------------

export type DatasetSummary = {
  slug: string
  display_name: string
  created_at: string
  has_brief: boolean
  brief_id: number | null
  raw_file_count: number
  md_file_count: number
}

export type CreateDatasetResponse = {
  slug: string
  display_name: string
  data_dir: string
}

export type IngestedFile = {
  filename: string
  md_path: string
  md_chars: number
}

export type UploadFilesResponse = {
  slug: string
  ingested: IngestedFile[]
  errors: { filename: string; error: string }[]
}

export const datasetsApi = {
  list: () => api.get<{ datasets: DatasetSummary[] }>("/v1/datasets"),
  create: (slug: string, displayName: string) =>
    api.post<CreateDatasetResponse>("/v1/datasets", {
      slug,
      display_name: displayName,
    }),
  uploadFiles: (slug: string, files: File[]) => {
    const form = new FormData()
    for (const f of files) form.append("files", f, f.name)
    return api.post<UploadFilesResponse>(
      `/v1/datasets/${encodeURIComponent(slug)}/files`,
      form,
    )
  },
  generate: (slug: string) =>
    api.post<{ started: boolean; dataset: string }>(
      `/v1/datasets/${encodeURIComponent(slug)}/generate`,
    ),
  remove: (slug: string) =>
    api.delete<{ deleted: true; slug: string }>(
      `/v1/datasets/${encodeURIComponent(slug)}`,
    ),
}

export const prdApi = {
  /** Kicks off PRD generation in the background. Returns immediately with a
   *  prd_id; client should poll prdApi.get(id) until status === 'ready'. */
  generate: (briefId: number, insightIndex: number, force = false) =>
    api.post<PrdStartResponse>("/v1/prd/generate", {
      brief_id: briefId,
      insight_index: insightIndex,
      force,
    }),
  /** Fetch a PRD by id. payload_md is only filled when status === 'ready'. */
  get: (id: number) => api.get<PrdRecord>(`/v1/prd/${id}`),
  /** Old name retained for compatibility. */
  byId: (id: number) => api.get<PrdRecord>(`/v1/prd/${id}`),
}
