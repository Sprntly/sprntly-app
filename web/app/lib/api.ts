/**
 * Thin client for the Sprntly backend at api.sprntly.ai.
 * All requests include the session cookie via credentials: 'include'.
 *
 * Backend wire format still uses `dataset` (the DB column name) — these
 * wrappers expose `company` to the rest of the app and translate at the
 * request/response boundary.
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
  company: string
  generated_at: string
  week_label: string
  summary_headline: string
  insights: Insight[]
}

export type BriefStatus = {
  company: string
  status: "ready" | "generating" | "failed" | "empty"
  error?: string
}

// Wire shapes from the backend — kept around so we can map cleanly.
type WireBrief = Omit<Brief, "company"> & { dataset: string }
type WireBriefStatus = Omit<BriefStatus, "company"> & { dataset: string }

function briefFromWire(b: WireBrief): Brief {
  const { dataset, ...rest } = b
  return { ...rest, company: dataset }
}

function briefStatusFromWire(s: WireBriefStatus): BriefStatus {
  const { dataset, ...rest } = s
  return { ...rest, company: dataset }
}

export const briefApi = {
  current: (company: string = "asurion") =>
    api
      .get<WireBrief>(`/v1/brief/current?dataset=${encodeURIComponent(company)}`)
      .then(briefFromWire),
  byId: (id: number) => api.get<WireBrief>(`/v1/brief/${id}`).then(briefFromWire),
  status: (company: string = "asurion") =>
    api
      .get<WireBriefStatus>(`/v1/brief/status?dataset=${encodeURIComponent(company)}`)
      .then(briefStatusFromWire),
  regenerate: (company: string = "asurion") =>
    api
      .post<{ started: boolean; dataset: string }>(
        `/v1/brief/regenerate?dataset=${encodeURIComponent(company)}`,
      )
      .then((r) => ({ started: r.started, company: r.dataset })),
  generate: () =>
    api
      .post<WireBrief & { brief_id: number }>("/v1/brief/generate")
      .then((b) => ({ ...briefFromWire(b), brief_id: b.brief_id })),
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
  ask: (question: string, company: string = "asurion") =>
    api.post<AskResponse>("/v1/ask", { question, dataset: company }),
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

/** Sample-build v2 evidence format. Backend stores v2 rows in the same
 *  `evidences` table with `variant: "v2"`; v1 and v2 ids do not overlap
 *  but they don't dedupe against each other either. */
export type EvidenceV2StartResponse = EvidenceStartResponse & {
  variant: "v2"
}
export type EvidenceV2Record = EvidenceRecord & { variant: "v2" }

export const evidenceV2Api = {
  generate: (briefId: number, insightIndex: number, force = false) =>
    api.post<EvidenceV2StartResponse>("/v1/evidence/v2/generate", {
      brief_id: briefId,
      insight_index: insightIndex,
      force,
    }),
  get: (id: number) => api.get<EvidenceV2Record>(`/v1/evidence/v2/${id}`),
}

// ---- companies --------------------------------------------------------------

export type CompanySummary = {
  slug: string
  display_name: string
  created_at: string
  has_brief: boolean
  brief_id: number | null
  raw_file_count: number
  md_file_count: number
}

export type CreateCompanyResponse = {
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

export const companiesApi = {
  list: () =>
    api
      .get<{ datasets: CompanySummary[] }>("/v1/datasets")
      .then((r) => ({ companies: r.datasets })),
  create: (slug: string, displayName: string) =>
    api.post<CreateCompanyResponse>("/v1/datasets", {
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
    api
      .post<{ started: boolean; dataset: string }>(
        `/v1/datasets/${encodeURIComponent(slug)}/generate`,
      )
      .then((r) => ({ started: r.started, company: r.dataset })),
  remove: (slug: string) =>
    api.delete<{ deleted: true; slug: string }>(
      `/v1/datasets/${encodeURIComponent(slug)}`,
    ),
}

// ---- sources ----------------------------------------------------------------

export type SourceFile = {
  filename: string
  kind: string
  size_bytes: number
  md_chars: number
  added_at: string
}
export type ListSourcesResponse = { slug: string; files: SourceFile[] }
export type DeleteSourceResponse = {
  slug: string
  filename: string
  removed: { raw: boolean; md: boolean }
}

export const sourcesApi = {
  list: (slug: string) =>
    api.get<ListSourcesResponse>(
      `/v1/datasets/${encodeURIComponent(slug)}/files`,
    ),
  remove: (slug: string, filename: string) =>
    api.delete<DeleteSourceResponse>(
      `/v1/datasets/${encodeURIComponent(slug)}/files/${encodeURIComponent(filename)}`,
    ),
  // upload/regen reuse companiesApi.uploadFiles + companiesApi.generate.
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
