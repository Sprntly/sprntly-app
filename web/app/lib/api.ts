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
    super(message || apiErrorMessage(status, body))
    this.status = status
    this.body = body
  }
}

/** FastAPI `detail` (string or validation list) for failed requests. */
export function apiErrorMessage(status: number, body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === "string" && detail.trim()) return detail
    if (Array.isArray(detail)) {
      const parts = detail
        .map((x) => {
          if (typeof x === "object" && x && "msg" in x) {
            return String((x as { msg: string }).msg)
          }
          return String(x)
        })
        .filter(Boolean)
      if (parts.length) return parts.join(" · ")
    }
  }
  if (typeof body === "string" && body.trim()) return body
  return `Request failed (${status})`
}

let accessTokenProvider: (() => Promise<string | null>) | null = null

/** Registered by AuthProvider — attaches Supabase JWT to backend requests. */
export function setAccessTokenProvider(fn: () => Promise<string | null>) {
  accessTokenProvider = fn
}

async function request<T>(
  method: "GET" | "POST" | "DELETE",
  path: string,
  body?: unknown,
): Promise<T> {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData
  const headers: Record<string, string> = isForm
    ? { Accept: "application/json" }
    : body
    ? { "Content-Type": "application/json", Accept: "application/json" }
    : { Accept: "application/json" }

  if (accessTokenProvider) {
    const token = await accessTokenProvider()
    if (token) headers.Authorization = `Bearer ${token}`
  }

  const res = await fetch(`${API_URL}${path}`, {
    method,
    credentials: "include",
    headers,
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

export type Audience = "app" | "demo"

// Per-audience session presence reported by /v1/auth/me. Either field
// may be null if no live session exists for that audience.
export type AuthMe = {
  app: { expires_at: string } | null
  demo: { expires_at: string } | null
}

// Pick the audience from the hostname. app.sprntly.ai → "app";
// anything else (demo.sprntly.ai, localhost, sprntly.ai/demo) → "demo".
// SSR-safe: falls back to "demo" when window is undefined.
function inferAudience(): Audience {
  if (typeof window === "undefined") return "demo"
  return window.location.hostname.startsWith("app.") ? "app" : "demo"
}

/** Legacy demo-password auth — kept for demo.sprntly.ai compatibility. */
export const demoAuth = {
  login: (password: string, audience: Audience = inferAudience()) =>
    api.post<{ ok: true; audience: Audience }>("/v1/auth/login", { password, audience }),
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
  /** v4 schema: LLM marks exactly one insight as the hero finding for the
   *  Brief v2 render. Older briefs omit it; frontend falls back to
   *  highest-confidence selection in that case. */
  is_headline?: boolean
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
  /** Storage variant — `v2` for new rows; historical `v1` rows in prod
   *  remain readable. Implementation detail; UI shouldn't switch on it. */
  variant?: string
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
  variant?: string
}

export type EvidenceStartResponse = {
  evidence_id: number
  status: "generating" | "ready" | "failed"
  title: string
  /** Storage variant — `v2` for new rows; historical `v1` rows in prod
   *  remain readable. Implementation detail; UI shouldn't switch on it. */
  variant?: string
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
  variant?: string
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

// ---- connectors -------------------------------------------------------------

export type ConnectionSummary = {
  id: string
  provider: "google_drive" | "figma" | "github" | string
  status: "active" | "error" | "revoked" | string
  google_email: string | null
  account_label?: string | null
  scopes: string
  config: { dataset?: string; folder_id?: string; folder_name?: string }
  last_sync_at: string | null
  last_sync_error: string | null
  created_at: string
  updated_at: string
}

export type GitHubRepo = {
  full_name: string
  name: string
  private: boolean
  html_url: string
  default_branch: string
  description: string | null
  updated_at: string
  stargazers_count: number
}

export type GoogleDriveSyncResult = {
  dataset: string
  folder_id: string
  synced: { filename: string; md_path: string; md_chars: number }[]
  skipped: { name: string; reason: string }[]
  errors: { name: string; error: string }[]
}

export type DriveFolderBrowse = {
  current: { id: string; name: string }
  parent: { id: string; name: string } | null
  folders: { id: string; name: string }[]
}

export const connectorsApi = {
  list: () => api.get<{ connections: ConnectionSummary[] }>("/v1/connectors"),
  disconnectGoogleDrive: () =>
    api.delete<{ deleted: true; provider: string }>("/v1/connectors/google-drive"),
  browseGoogleDriveFolders: (parentId = "root") =>
    api.get<DriveFolderBrowse>(
      `/v1/connectors/google-drive/folders?parent_id=${encodeURIComponent(parentId)}`,
    ),
  setGoogleDriveConfig: (folderId: string, dataset?: string, folderName?: string) =>
    api.post<{ ok: true; config: ConnectionSummary["config"] }>(
      "/v1/connectors/google-drive/config",
      { folder_id: folderId, folder_name: folderName, dataset },
    ),
  syncGoogleDrive: (dataset?: string, folderId?: string) =>
    api.post<GoogleDriveSyncResult>("/v1/connectors/google-drive/sync", {
      dataset,
      folder_id: folderId,
    }),
  /** Full-page navigation — OAuth must not use fetch. */
  googleDriveAuthorizeUrl: (dataset: string) =>
    `${API_URL}/v1/connectors/google-drive/authorize?dataset=${encodeURIComponent(dataset)}`,

  // ---- Figma ---------------------------------------------------------------
  figmaAuthorizeUrl: () => `${API_URL}/v1/connectors/figma/authorize`,
  disconnectFigma: () =>
    api.delete<{ deleted: true; provider: string }>("/v1/connectors/figma"),
  getFigmaFile: (key: string, depth = 2) =>
    api.get<Record<string, unknown>>(
      `/v1/connectors/figma/files/${encodeURIComponent(key)}?depth=${encodeURIComponent(String(depth))}`,
    ),
  getFigmaFileStyles: (key: string) =>
    api.get<Record<string, unknown>>(
      `/v1/connectors/figma/files/${encodeURIComponent(key)}/styles`,
    ),

  // ---- GitHub --------------------------------------------------------------
  githubAuthorizeUrl: () => `${API_URL}/v1/connectors/github/authorize`,
  disconnectGithub: () =>
    api.delete<{ deleted: true; provider: string }>("/v1/connectors/github"),
  listGithubRepos: (perPage = 50) =>
    api.get<{ repositories: GitHubRepo[] }>(
      `/v1/connectors/github/repos?per_page=${encodeURIComponent(String(perPage))}`,
    ),
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
   *  prd_id; client should poll prdApi.get(id) until status === 'ready'.
   *  Backend emits the canonical semantic-block (v2) format. */
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

// ---- Design Agent (P1-09) ---------------------------------------------------
// Append-only block; does not modify any export above. Mirrors prdApi — reuses
// the shared `api` helper so credentials/JSON/${API_URL} handling stays
// centralised (no raw fetch, no reinvented client).

/** Full prototype row returned by GET /v1/design-agent/{id}. */
export type PrototypeRecord = {
  id: number
  status: "generating" | "ready" | "failed" | "invalidated"
  bundle_url: string | null
  error: string | null
}

/** 202 kickoff response from POST /v1/design-agent/generate. */
export type PrototypeStartResponse = {
  prototype_id: number
  status: string
}

export const designAgentApi = {
  /** Kicks off prototype generation in the background; returns immediately
   *  with a prototype_id. Client should poll designAgentApi.get(id) (via
   *  runDesignAgentGeneration) until status === 'ready'. */
  generate: (body: {
    prd_id: number
    target_platform: "desktop" | "mobile" | "both"
    instructions: string
    figma_file_key?: string | null
  }) => api.post<PrototypeStartResponse>("/v1/design-agent/generate", body),
  /** Fetch a prototype row by id. bundle_url is filled when status === 'ready'. */
  get: (prototypeId: number) =>
    api.get<PrototypeRecord>(`/v1/design-agent/${prototypeId}`),
}
