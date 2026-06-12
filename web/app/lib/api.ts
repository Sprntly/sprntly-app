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
  method: "GET" | "POST" | "PUT" | "DELETE" | "PATCH",
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
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
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
  /** Dataset slug — internal key (db / infra-api only); never render in UI. */
  company: string
  /** Human-readable name (companies.display_name); null for legacy demo
   * datasets that have no companies row. */
  company_name?: string | null
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

export type SkillInfo = {
  id: string
  label: string
  trigger: string
  description: string
}

export const askApi = {
  ask: (question: string, company: string = "asurion") =>
    api.post<AskResponse>("/v1/ask", { question, dataset: company }),
  /** List available skills the chat can route to. */
  skills: () =>
    api.get<{ skills: SkillInfo[] }>("/v1/ask/skills"),
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

// ---- onboarding website analysis -------------------------------------------

/** A suggested success metric: a short name plus a free-text description. */
export type SuggestedMetric = {
  metric: string
  description: string
}

/**
 * Response from POST /v1/onboarding/analyze-website. The endpoint ALWAYS
 * returns HTTP 200; `ok: false` (with a `reason`) means analysis degraded
 * gracefully and the UI should fall back to manual entry. All inferred
 * fields are best-effort and may be null even when `ok` is true.
 */
export type AnalyzeWebsiteResponse = {
  ok: boolean
  reason: string | null
  url: string
  industry: string | null
  sub_vertical: string | null
  business_type: string | null
  stage: string | null
  business_context: string
  suggested_metrics: SuggestedMetric[]
  provenance: string
  business_context_version: number | null
}

export const onboardingApi = {
  /**
   * Analyze a product website to infer industry / business type / stage and
   * draft a business-context blurb + suggested metrics. Best-effort: the
   * backend always answers 200, signalling failure via `ok: false`. Company
   * is taken from the JWT (Depends(require_company)) — no slug needed.
   */
  analyzeWebsite: (url: string) =>
    api.post<AnalyzeWebsiteResponse>("/v1/onboarding/analyze-website", { url }),
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
  config: {
    // Google Drive
    dataset?: string
    folder_id?: string
    folder_name?: string
    // Slack
    channel_id?: string
    channel_name?: string
    // Figma (PAT-vs-OAuth distinction set by backend on save)
    auth_kind?: "pat" | "oauth"
  }
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

export type GitHubInstallation = {
  installation_id: number
  account_login: string
  account_type: "User" | "Organization" | string
  repository_selection: "selected" | "all" | string
  suspended?: boolean
}

export type GitHubInstallRepo = {
  id: number
  name: string
  full_name: string
  private: boolean | null
  html_url: string
  default_branch: string | null
  description: string | null
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

export type SlackChannel = {
  id: string
  name: string
  is_private: boolean
  is_member: boolean
  is_archived: boolean
}

// Multitenant: connector routes resolve the active company entirely
// from the JWT (`Depends(require_company)`) — no client-side workspace
// or company id is sent. Methods below therefore take only the inputs
// that aren't derivable server-side (folder ids, channel ids, etc.).

export const connectorsApi = {
  list: () =>
    api.get<{ connections: ConnectionSummary[] }>(`/v1/connectors`),
  disconnectGoogleDrive: () =>
    api.delete<{ deleted: true; provider: string }>(
      `/v1/connectors/google-drive`,
    ),
  browseGoogleDriveFolders: (parentId = "root") =>
    api.get<DriveFolderBrowse>(
      `/v1/connectors/google-drive/folders?parent_id=${encodeURIComponent(parentId)}`,
    ),
  setGoogleDriveConfig: (
    folderId: string,
    dataset?: string,
    folderName?: string,
  ) =>
    api.post<{ ok: true; config: ConnectionSummary["config"] }>(
      `/v1/connectors/google-drive/config`,
      { folder_id: folderId, folder_name: folderName, dataset },
    ),
  syncGoogleDrive: (dataset?: string, folderId?: string) =>
    api.post<GoogleDriveSyncResult>(`/v1/connectors/google-drive/sync`, {
      dataset,
      folder_id: folderId,
    }),
  /** Full-page navigation — OAuth must not use fetch. */
  googleDriveAuthorizeUrl: (dataset: string) =>
    `${API_URL}/v1/connectors/google-drive/authorize?dataset=${encodeURIComponent(dataset)}`,

  // ---- Figma ---------------------------------------------------------------
  figmaAuthorizeUrl: () => `${API_URL}/v1/connectors/figma/authorize`,
  disconnectFigma: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/figma`),
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
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/github`),
  listGithubRepos: (perPage = 50) =>
    api.get<{ repositories: GitHubRepo[] }>(
      `/v1/connectors/github/repos?per_page=${encodeURIComponent(String(perPage))}`,
    ),
  /** Repos the Sprntly App was granted access to during install,
   * aggregated across every installation owned by the caller's company.
   * Use this (not listGithubRepos) for any picker UI — listGithubRepos
   * uses the OAuth user token + `read:user user:email` scope which can't
   * enumerate private repos and returns empty for users with no public
   * repos under their login. */
  listAccessibleGithubRepos: () =>
    api.get<{ repositories: GitHubRepo[] }>(
      `/v1/connectors/github/accessible-repos`,
    ),
  listGithubInstallations: () =>
    api.get<{ installations: GitHubInstallation[] }>(
      `/v1/connectors/github/installations`,
    ),
  listGithubInstallRepos: (installationId: number) =>
    api.get<{
      installation_id: number
      total: number
      repositories: GitHubInstallRepo[]
    }>(
      `/v1/connectors/github/installations/${installationId}/repositories`,
    ),
  addGithubInstallRepo: (installationId: number, repositoryId: number) =>
    api.put<{ added: true; installation_id: number; repository_id: number }>(
      `/v1/connectors/github/installations/${installationId}/repositories/${repositoryId}`,
    ),
  removeGithubInstallRepo: (installationId: number, repositoryId: number) =>
    api.delete<{
      removed: true
      installation_id: number
      repository_id: number
    }>(
      `/v1/connectors/github/installations/${installationId}/repositories/${repositoryId}`,
    ),

  // ---- ClickUp -------------------------------------------------------------
  disconnectClickup: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/clickup`),

  // ---- HubSpot -------------------------------------------------------------
  disconnectHubspot: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/hubspot`),
  syncHubspot: (dataset: string) =>
    api.post<{
      dataset: string;
      contacts_count: number;
      companies_count: number;
      deals_count: number;
      total_synced: number;
      errors: string[];
    }>("/v1/connectors/hubspot/sync-to-corpus", { dataset }),

  // ---- Slack ---------------------------------------------------------------
  connectSlackWithBotToken: (apiKey: string) =>
    api.post<{ ok: true; provider: string; account_label: string }>(
      `/v1/connectors/slack/apikey`,
      { api_key: apiKey },
    ),
  disconnectSlack: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/slack`),
  listSlackChannels: () =>
    api.get<{ channels: SlackChannel[] }>(`/v1/connectors/slack/channels`),
  setSlackConfig: (channelId: string, channelName?: string) =>
    api.post<{ ok: true; config: ConnectionSummary["config"] }>(
      `/v1/connectors/slack/config`,
      { channel_id: channelId, channel_name: channelName },
    ),
  syncSlack: (dataset: string, historyDays = 90) =>
    api.post<{
      dataset: string
      channels_count: number
      messages_count: number
      threads_count: number
      total_synced: number
      errors: string[]
    }>("/v1/connectors/slack/sync-to-corpus", {
      dataset,
      history_days: historyDays,
    }),

  // ---- Fireflies (API key, not OAuth) --------------------------------------
  connectFirefliesWithApiKey: (apiKey: string) =>
    api.post<{ ok: true; provider: string; account_label: string }>(
      `/v1/connectors/fireflies/apikey`,
      { api_key: apiKey },
    ),
  disconnectFireflies: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/fireflies`),

  // ---- Figma Personal Access Token (PAT, stopgap while OAuth in review) ----
  connectFigmaWithPat: (pat: string) =>
    api.post<{ ok: true; provider: string; account_label: string }>(
      `/v1/connectors/figma/pat`,
      { pat },
    ),

  // ---- Generic test-connection --------------------------------------------
  /**
   * Re-validate a stored connection by re-running the provider's
   * identity lookup with the decrypted token. Backs the "Test
   * connection" button in the Configure drawer.
   *
   * Returns {ok, account_label, tested_at} on success; throws ApiError
   * on 400 (token rejected) / 404 (not connected).
   */
  testConnection: (provider: string) =>
    api.post<{ ok: true; account_label: string; tested_at: string }>(
      `/v1/connectors/${encodeURIComponent(provider)}/test`,
      {},
    ),

  // ---- Generic start-OAuth ------------------------------------------------
  /**
   * Returns the provider's OAuth authorize URL as JSON. The caller is
   * expected to navigate the browser to it (`window.location.href = url`).
   *
   * Why this exists: the legacy GET /authorize routes 307-redirect to
   * Google/Figma/GitHub, but they require auth — and a browser URL-bar
   * navigation can't attach the Supabase Bearer token. This endpoint
   * runs the auth check via fetch + Bearer, then hands back the URL the
   * browser should navigate to next.
   *
   * `returnTo` is an optional relative path (e.g. `/onboarding/connectors`) the
   * backend signs into the OAuth state JWT; the callback then redirects
   * there with `?connected=<provider>` appended. Used by the onboarding
   * connector modal to bounce the user back to the same step instead of
   * the default `/settings?section=connectors`. Backend validates it as
   * a safe relative path (open-redirect guard).
   */
  startOauth: (provider: string, dataset?: string, returnTo?: string) => {
    const body: Record<string, string> = {}
    if (dataset) body.dataset = dataset
    if (returnTo) body.return_to = returnTo
    return api.post<{ authorize_url: string }>(
      `/v1/connectors/${encodeURIComponent(provider)}/start-oauth`,
      body,
    )
  },
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

// ---- pipeline ---------------------------------------------------------------

export type PipelineStageResult = {
  status: "completed" | "failed" | "skipped"
  duration_ms?: number
  error?: string
  [key: string]: unknown
}

export type PipelineRunStatus = {
  id: string
  dataset: string
  trigger: string
  status: "running" | "completed" | "failed"
  stages: Record<string, PipelineStageResult>
  started_at: string
  completed_at: string | null
  error: string | null
}

export const pipelineApi = {
  run: (company: string) =>
    api.post<{ started: boolean; dataset: string; message: string }>(
      `/v1/pipeline/${encodeURIComponent(company)}/run`,
    ),
  status: (company: string) =>
    api.get<PipelineRunStatus>(
      `/v1/pipeline/${encodeURIComponent(company)}/status`,
    ),
}

// ─────────────────────── Agent with live tools ───────────────────────
//
// POST /v1/agent/chat-with-tools — runs an Anthropic tool-use loop so the
// agent can fetch live data from GitHub during the chat (no pre-sync).
// See backend app/agent_tools/github.py for the available tools.

export type AgentChatWithToolsResponse = {
  response: string
  iterations: number
  tool_calls: string[]
  truncated: boolean
}

export const agentChatApi = {
  chatWithTools: (message: string, installationId: number) =>
    api.post<AgentChatWithToolsResponse>(`/v1/agent/chat-with-tools`, {
      message,
      installation_id: installationId,
    }),
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
  /** Fetch the latest ready PRD for a dataset/company slug. 404 if none. */
  latest: (dataset: string) => api.get<PrdRecord>(`/v1/prd/latest?dataset=${encodeURIComponent(dataset)}`),
  /** Old name retained for compatibility. */
  byId: (id: number) => api.get<PrdRecord>(`/v1/prd/${id}`),
  /** Save PRD edits (title + markdown). Auto-creates a version snapshot. */
  update: (id: number, body: { title: string; payload_md: string }) =>
    api.put<PrdRecord>(`/v1/prd/${id}`, body),
  /** List all versions of a PRD, newest first. */
  listVersions: (id: number) =>
    api.get<{ id: number; prd_id: number; version_number: number; title: string; payload_md: string; saved_by: string; saved_at: string }[]>(`/v1/prd/${id}/versions`),
  /** Restore a PRD to a specific version. */
  restoreVersion: (prdId: number, versionId: number) =>
    api.post<PrdRecord>(`/v1/prd/${prdId}/versions/${versionId}/restore`, {}),
}

// ---- Design Agent (P1-09) ---------------------------------------------------
// Append-only block; does not modify any export above. Mirrors prdApi — reuses
// the shared `api` helper so credentials/JSON/${API_URL} handling stays
// centralised (no raw fetch, no reinvented client).

/** F12 (P3-08) — the agent's clarifying question, persisted on the prototype
 *  row as a sidecar. Shape `{question, choices?, context?}`. When non-null the
 *  prototype is in `awaiting_clarification` (`status` stays `ready` — the
 *  question is a sidecar, NOT a status enum value). `choices` present → answer
 *  by picking a button; absent → free-text answer. */
export type PendingQuestion = {
  question: string
  choices?: string[]
  context?: string
}

/** Full prototype row returned by GET /v1/design-agent/{id}. */
export type PrototypeRecord = {
  id: number
  status: "generating" | "ready" | "failed" | "invalidated"
  bundle_url: string | null
  error: string | null
  // ── P2-12 (append-only): F14/F15 + F6 columns added by the P2-06 sharing
  //    migration. GET /v1/design-agent/{id} does `select("*")`, so the row
  //    carries these. Typed OPTIONAL so existing `PrototypeRecord` literals
  //    (e.g. the runDesignAgentGeneration test's `proto()` base) keep
  //    typechecking; consumers default with `?? …` for older/partial rows.
  is_complete?: boolean
  share_mode?: "private" | "public" | "passcode"
  share_token?: string | null
  // ── P3-16 (append-only): F12 `awaiting_clarification` sidecar — the
  //    `pending_question` column added by P3-08. GET /{id} `select("*")` carries
  //    it; typed OPTIONAL/nullable to match the posture above (no api method
  //    added — the existing GET poll surfaces it; the answer routes through the
  //    existing P3-14 `iterate`). Null/absent ⇒ no question pending.
  pending_question?: PendingQuestion | null
  // ── (append-only): optional preview-thumbnail URL captured on generation-
  //    complete. GET /{id} / by-prd both `select("*")`, so the column flows
  //    through automatically — no api method change. Null/absent ⇒ no thumbnail
  //    captured (the preview card falls back to its existing placeholder); typed
  //    OPTIONAL/nullable to match the posture above so existing literals keep
  //    typechecking.
  preview_image_url?: string | null
}

/** 202 kickoff response from POST /v1/design-agent/generate. */
export type PrototypeStartResponse = {
  prototype_id: number
  status: string
}

/** F8 (P3-02/P3-03) — an anchored comment. Wire shape mirrors the backend
 *  `CommentOut` (id/anchor_id/body/author/status/created_at/resolved_at).
 *  `status` is the AD12 lifecycle: `open` (active), `resolved` (internally
 *  closed), `orphaned` (the anchor no longer exists in the current bundle —
 *  set by P3-04, rendered with no pin by the panel). */
export type CommentRecord = {
  id: number
  anchor_id: string
  body: string
  author: string
  status: "open" | "resolved" | "orphaned"
  created_at: string
  resolved_at: string | null
  pin_x_pct?: number | null
  pin_y_pct?: number | null
  resolved_anchor_id?: string | null
}

/** F11 (P3-09/P3-10) — a proposed PRD patch. Wire shape mirrors the backend
 *  `PrdPatchOut` (id/prd_id/prototype_id/rationale/patch_md/status/created_at).
 *  `status` is `pending` (awaiting accept/reject), `applied` (folded into the
 *  rendered PRD on read via apply_patches_to_prd_md), or `rejected`. The banner
 *  only ever lists `pending` rows. */
export type PrdPatchRecord = {
  id: number
  prd_id: number
  prototype_id: number
  rationale: string
  patch_md: string
  status: "pending" | "applied" | "rejected"
  created_at: string
}

/** One listable Figma file for the Generate modal's design-source selector
 *  (`designAgentApi.listFigmaFiles`). */
export type FigmaFile = {
  key: string
  name: string
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
    /** Optional Figma node-id (frame-level targeting); extracted from a pasted
     *  URL's node-id query param. Passed through to the backend so the agent
     *  loop fetches only that specific frame instead of the file's top-5. */
    figma_node_id?: string | null
    website_url?: string | null  // P5-02: Scenario B fallback source
    manual_design?: { primary_color: string; font_family: string } | null  // P5-02: manual floor
    github_repo?: string | null  // connected-repo full_name ("org/repo"); prompt context only
    design_source?: "figma" | "github" | "website" | null  // explicit source selector; null = back-compat implicit precedence
  }) => api.post<PrototypeStartResponse>("/v1/design-agent/generate", body),
  /** Fetch a prototype row by id. bundle_url is filled when status === 'ready'. */
  get: (prototypeId: number) =>
    api.get<PrototypeRecord>(`/v1/design-agent/${prototypeId}`),
  delete: (prototypeId: number) =>
    api.delete<void>(`/v1/design-agent/${prototypeId}`),
  /**
   * READ-ONLY "does this PRD have a ready prototype?" lookup, by PRD id. Powers
   * the PRD-screen preview card and the "View Prototype" vs "Generate Prototype"
   * label / skip-loading decision WITHOUT side effects.
   *
   * Calls `GET /v1/design-agent/by-prd/{prd_id}`, which returns the most-recent
   * ready prototype row for the PRD under the caller's workspace, or 404 when
   * none — a pure read that never kicks off a generation (unlike the dedup
   * short-circuit inside `POST /v1/design-agent/generate`). On any error (404 /
   * not found / transient) the caller swallows it → null → no preview card,
   * label stays "Generate Prototype" (graceful degrade, NEVER faking existence /
   * NEVER kicking a generation). */
  getByPrd: async (prdId: number): Promise<PrototypeRecord | null> => {
    try {
      return await api.get<PrototypeRecord>(
        `/v1/design-agent/by-prd/${encodeURIComponent(String(prdId))}`,
      )
    } catch {
      // 404 (no ready prototype) / not found / transient → degrade to "no
      // existing prototype" so the card hides and the label stays Generate.
      return null
    }
  },
  /** F14 — mark a prototype complete. Empty body. */
  complete: (prototypeId: number) =>
    api.post<{
      prototype_id: number
      is_complete: boolean
      complete_checkpoint_id: number | null
    }>(`/v1/design-agent/${prototypeId}/complete`, {}),
  /** F15 — resume iteration on a completed prototype. Empty body. */
  resume: (prototypeId: number) =>
    api.post<{
      prototype_id: number
      is_complete: boolean
      handoffs_flagged_stale: number
    }>(`/v1/design-agent/${prototypeId}/resume`, {}),
  /** F6 — set the share mode (and, for passcode mode, the passcode). */
  share: (
    prototypeId: number,
    body: { mode: "private" | "public" | "passcode"; passcode?: string },
  ) =>
    api.post<{
      prototype_id: number
      share_mode: string
      share_token: string | null
    }>(`/v1/design-agent/${prototypeId}/share`, body),
  /**
   * F16 — `GET /v1/design-agent/{id}/export` returns `text/markdown`, NOT JSON,
   * so it bypasses the shared JSON-parsing `request<T>` helper and uses `fetch`
   * directly. Same auth path (Bearer via `accessTokenProvider`) + cookie.
   */
  exportMarkdown: async (prototypeId: number): Promise<string> => {
    const token = accessTokenProvider ? await accessTokenProvider() : null
    const headers: Record<string, string> = { Accept: "text/markdown" }
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(
      `${API_URL}/v1/design-agent/${prototypeId}/export`,
      { method: "GET", headers, credentials: "include" },
    )
    if (!res.ok) {
      // 409 = WIP (F17). 404 = wrong workspace / missing. 401 = no auth.
      throw new ApiError(res.status, await res.text())
    }
    return await res.text()
  },
  // ── F8 anchored comments (P3-03) ──────────────────────────────────────────
  /** Public-route comment write (external viewer on `/p/<token>`): the token
   *  is the access primitive (F6), so no auth is required. Hits the P3-02
   *  public route; the backend attributes the comment to the `external` author. */
  createCommentByToken: (token: string, body: {
    anchor_id: string; body: string;
    pin_x_pct?: number; pin_y_pct?: number; resolved_anchor_id?: string | null;
  }) =>
    api.post<CommentRecord>(
      `/v1/design-agent/by-token/${encodeURIComponent(token)}/comments`,
      body,
    ),
  /** Authed comment create for the signed-in canvas (mark-and-comment pin flow).
   *  Hits the authed route `POST /v1/design-agent/{id}/comments` (same-origin/CSRF
   *  gated). Position fields are optional — pin comments include x/y and the
   *  resolved anchor; right-click anchor comments omit them. */
  createComment: (prototypeId: number, body: {
    anchor_id: string; body: string;
    pin_x_pct?: number; pin_y_pct?: number; resolved_anchor_id?: string | null;
  }) =>
    api.post<CommentRecord>(`/v1/design-agent/${prototypeId}/comments`, body),
  /** Public-route comment read: lists every comment for the token's prototype
   *  (all statuses). Same 404 posture as the resolver for missing/private. */
  listCommentsByToken: (token: string) =>
    api.get<CommentRecord[]>(
      `/v1/design-agent/by-token/${encodeURIComponent(token)}/comments`,
    ),
  /** Internal (authed) resolve — external viewers cannot resolve (spec §4
   *  Stage 2). Addressed by prototype id; renders only on the signed-in mount
   *  where a `prototypeId` is supplied. */
  resolveComment: (prototypeId: number, commentId: number) =>
    api.patch<CommentRecord>(
      `/v1/design-agent/${prototypeId}/comments/${commentId}/resolve`,
    ),
  deleteComment: (prototypeId: number, commentId: number) =>
    api.delete<void>(`/v1/design-agent/${prototypeId}/comments/${commentId}`),
  // ── F11 PRD patches (P3-10) ───────────────────────────────────────────────
  /** List the PENDING PRD patches for a PRD (workspace-filtered server-side).
   *  The PrdPatchBanner calls this on mount to decide whether to surface. */
  listPendingPatches: (prdId: number) =>
    api.get<PrdPatchRecord[]>(
      `/v1/design-agent/prd-patches?prd_id=${encodeURIComponent(prdId)}`,
    ),
  /** Accept a proposed PRD patch → flips it to `applied`. The rendered PRD
   *  reflects it on the next load (read path folds applied patches in); this does
   *  NOT mutate the PrdScreen contentEditable. */
  acceptPatch: (patchId: number) =>
    api.post<PrdPatchRecord>(
      `/v1/design-agent/prd-patches/${patchId}/accept`,
      {},
    ),
  /** Reject a proposed PRD patch → flips it to `rejected`. */
  rejectPatch: (patchId: number) =>
    api.post<PrdPatchRecord>(
      `/v1/design-agent/prd-patches/${patchId}/reject`,
      {},
    ),
  // ── AD14 pre-flight cost estimate (P3-11) ─────────────────────────────────
  /** Pre-flight cost estimate for an iterate run (AD14). Deterministic, makes no
   *  Anthropic call server-side — drives the CostEstimateModal's
   *  "~$0.X · Continue / Cancel" gate. The iterate composer itself (`iterate`) is
   *  P3-14; this only estimates. */
  estimateIterate: (
    prototypeId: number,
    body: { prompt: string; applied_comment_id?: number | null },
  ) =>
    api.post<IterateCostEstimate>(
      `/v1/design-agent/${prototypeId}/iterate/estimate`,
      body,
    ),
  // ── F9/F10 iterate (P3-14) ────────────────────────────────────────────────
  /** Kick off an iterate of an existing prototype (F9 re-prompt / F10 Apply).
   *  Owned HERE, not P3-11 (which ships `estimateIterate` only). The IterateComposer
   *  routes Submit through the AD14 `CostEstimateModal` gate and calls this ONLY
   *  from the modal's Continue handler — never directly from a Submit. Defaults
   *  `mode:'execute'` (`'plan'` is P3-07). Returns the background-run handle +
   *  `queue_position` (P3-06's iterate queue). 409 when the prototype is locked
   *  (`is_complete`) or not `ready`; 429 when the queue is full. */
  iterate: (
    prototypeId: number,
    body: {
      prompt: string
      applied_comment_id?: number | null
      mode?: "plan" | "execute"
    },
  ) =>
    api.post<IterateResponse>(`/v1/design-agent/${prototypeId}/iterate`, {
      ...body,
      mode: body.mode ?? "execute",
    }),
  // ── F13 manual edit (P4-01 caller / P4-02 route) ──────────────────────────
  /** F13 (AD13/AD23) — commit a batch of light visual property edits collected
   *  by the ManualEditOverlay. Mirrors `iterate`'s response shape (background-run
   *  handle + queue_position). `body.edits` are de-duplicated
   *  `{anchor_id, property, old_value, new_value}` triples; P4-02's backend route
   *  translates them into source edits via one LLM run. 409 when the prototype is
   *  locked (`is_complete`) or not `ready`; the route returns a clear error when
   *  an anchor_id no longer exists in the current bundle (the overlay surfaces it
   *  as a stale-anchor reload affordance). */
  manualEdit: (prototypeId: number, body: { edits: ManualEditTriple[] }) =>
    api.post<ManualEditResponse>(
      `/v1/design-agent/${prototypeId}/manual-edit`,
      body,
    ),
  /** List the connected company's Figma files for the Generate modal's design
   *  selector (`GET /v1/design-agent/figma-files`). DA-flag gated (404 when off)
   *  and Figma-connection gated (404 when not connected). Returns an honest
   *  empty `files` list when the upstream listing can't be produced -- never
   *  fabricated files; the modal renders that as "Couldn't load designs". */
  listFigmaFiles: () =>
    api.get<{ files: FigmaFile[] }>("/v1/design-agent/figma-files"),
  /** Build the SSE URL for streaming step events during an iterate run.
   *  The bearer token rides as ?token= because EventSource cannot set headers.
   *  Single source of truth for this URL so the token-in-URL construction is
   *  auditable in one place. */
  eventsUrl: (prototypeId: number, token: string): string =>
    `${API_URL}/v1/design-agent/${prototypeId}/events?token=${encodeURIComponent(token)}`,
  /** Ask the LLM for a single clarifying question about a comment body before
   *  the Apply flow commits an iterate. Lightweight Haiku call — resolves in
   *  <1s. Returns { question }. */
  clarifyComment: (prototypeId: number, commentBody: string) =>
    api.post<{ question: string }>(`/v1/design-agent/${prototypeId}/clarify-comment`, { comment_body: commentBody }),
}

/** Shape returned by POST /v1/design-agent/{id}/iterate/estimate (AD14/AD15). */
export type IterateCostEstimate = {
  cached_input_tokens: number
  new_input_tokens: number
  expected_output_tokens: number
  est_cost_usd: number
  soft_cap_usd: number
  exceeds_soft_cap: boolean
  model: string
}

/** Shape returned by POST /v1/design-agent/{id}/iterate (P3-05 route + P3-06 queue). */
export type IterateResponse = {
  prototype_id: number
  status: string
  queue_position: number
}

/** F13 (P4-01) — the closed set of properties the ManualEditOverlay exposes.
 *  Border, animation, gap, margin, etc. are OUT of scope (deferred to v2 per
 *  BUILD-PHASES.md). The wire keeps this typed so the overlay and P4-02 share
 *  one shape end-to-end. */
export type EditableProperty = "text" | "font-size" | "padding" | "color" | "background"

/** F13 (P4-01/P4-02) — one fixed-property visual edit. The SAVED triple keys on
 *  `anchor_id` (AD4 — one id may match N structurally-identical elements; P4-02
 *  applies the edit to ALL N). `old_value` is the pristine value at first
 *  selection; `new_value` is the final value at Save. */
export type ManualEditTriple = {
  anchor_id: string
  property: EditableProperty
  old_value: string
  new_value: string
}

/** Shape returned by POST /v1/design-agent/{id}/manual-edit (P4-02). Mirrors
 *  IterateResponse — a manual edit kicks off the same background-run + queue. */
export type ManualEditResponse = {
  prototype_id: number
  status: string
  queue_position: number
}

// ---- ticket push (ClickUp / Jira) ------------------------------------------

export type ClickUpList = {
  id: string
  name: string
  space: string | null
  folder: string | null
}

export type TicketPushResult = {
  created: { ticket: string; task_id: string; url: string }[]
  errors: { ticket: string; error: string }[]
}

export type TicketDataResponse = {
  description: string | null
  acceptance_criteria: string[] | null
  attachments: { id: number; label: string; sub: string }[]
  comments: { id: number; author: string; body: string; time: string }[]
}

export const ticketDataApi = {
  /** Get all saved overrides for a ticket (description, attachments, comments). */
  getData: (ticketKey: string) =>
    api.get<TicketDataResponse>(`/v1/tickets/${encodeURIComponent(ticketKey)}/data`),
  /** Save description + acceptance criteria. */
  saveDescription: (ticketKey: string, description: string, acceptanceCriteria: string[]) =>
    api.put(`/v1/tickets/${encodeURIComponent(ticketKey)}/description`, {
      description, acceptance_criteria: acceptanceCriteria,
    }),
  /** Add an attachment. */
  addAttachment: (ticketKey: string, label: string, sub: string) =>
    api.post<{ id: number; label: string; sub: string }>(
      `/v1/tickets/${encodeURIComponent(ticketKey)}/attachments`, { label, sub },
    ),
  /** Remove an attachment. */
  removeAttachment: (ticketKey: string, attachmentId: number) =>
    api.delete(`/v1/tickets/${encodeURIComponent(ticketKey)}/attachments/${attachmentId}`),
  /** Add a comment. */
  addComment: (ticketKey: string, author: string, body: string) =>
    api.post<{ id: number; author: string; body: string; time: string }>(
      `/v1/tickets/${encodeURIComponent(ticketKey)}/comments`, { author, body },
    ),
  /** Remove a comment. */
  removeComment: (ticketKey: string, commentId: number) =>
    api.delete(`/v1/tickets/${encodeURIComponent(ticketKey)}/comments/${commentId}`),
}

export const ticketPushApi = {
  /** Fetch ClickUp lists the company can push tickets into. 404 when not connected. */
  listClickUpLists: () =>
    api.post<{ lists: ClickUpList[] }>("/v1/tickets/lists", {}),
  /** Push one or more tickets into a ClickUp list. */
  pushToClickUp: (
    listId: string,
    tickets: { title: string; description: string; priority: string }[],
  ) =>
    api.post<TicketPushResult>("/v1/tickets/push-clickup", {
      list_id: listId,
      tickets,
    }),
}

// ── Conversations (chat history persistence) ──

export type ConversationRecord = {
  id: number
  company_id: string
  title: string
  preview: string
  agent_type: string
  query: string
  reply: string
  pinned: boolean
  created_at: string
  updated_at: string
}

export type ConversationTurn = {
  id: number
  conversation_id: number
  role: "user" | "assistant"
  content: string
  created_at: string
}

export const conversationsApi = {
  list: () =>
    api.get<{ conversations: ConversationRecord[] }>("/v1/conversations"),
  create: (body: { title: string; preview?: string; agent_type?: string; query?: string; reply?: string; pinned?: boolean }) =>
    api.post<ConversationRecord>("/v1/conversations", body),
  update: (id: number, body: { title?: string; preview?: string; query?: string; reply?: string; pinned?: boolean }) =>
    api.patch<ConversationRecord>(`/v1/conversations/${id}`, body),
  remove: (id: number) =>
    api.delete(`/v1/conversations/${id}`),
  /** List all turns (messages) in a conversation, oldest first. */
  listTurns: (conversationId: number) =>
    api.get<{ turns: ConversationTurn[] }>(`/v1/conversations/${conversationId}/turns`),
  /** Add a turn to a conversation. */
  addTurn: (conversationId: number, role: "user" | "assistant", content: string) =>
    api.post<ConversationTurn>(`/v1/conversations/${conversationId}/turns`, { role, content }),
}

// ---- transient-auth resilience (shared primitive) ---------------------------
// Supabase issues short-lived bearer tokens; `accessTokenProvider` refreshes
// them in the background. A request that lands DURING a refresh can come back
// 401 even though the session is healthy — a transient failure, not a real auth
// loss. Today every authed poll / status fetch treats a 401 as terminal, so a
// single mid-refresh blip aborts the work or flips connected rows to "off".
//
// `withAuthRetry` is the one place that handles this: it runs the wrapped call,
// and on a 401 it re-acquires the token (forcing the in-flight refresh to
// settle) and retries the call exactly once after a short backoff. Non-401
// errors propagate untouched, and a 401 that survives the retry is re-thrown so
// a genuine auth failure still surfaces to the caller's own error handling. The
// primitive owns no UI state and never swallows errors — callers wrap any authed
// read that polls or auto-refreshes and decide for themselves what a persistent
// failure means.

/** Retrieve the current access token directly for non-fetch uses (e.g. EventSource URLs). */
export async function getAccessToken(): Promise<string | null> {
  return accessTokenProvider ? await accessTokenProvider() : null
}

export type WithAuthRetryOptions = {
  /** Backoff before the single retry, in milliseconds. Defaults to 250. Tests
   *  pass 0 to keep the retry path instant. */
  backoffMs?: number
}

export async function withAuthRetry<T>(
  fn: () => Promise<T>,
  opts: WithAuthRetryOptions = {},
): Promise<T> {
  try {
    return await fn()
  } catch (err) {
    // Only a 401 is treated as a transient token-refresh race; everything else
    // (including a non-ApiError throw) propagates immediately, no retry.
    if (!(err instanceof ApiError) || err.status !== 401) {
      throw err
    }
    // Re-acquire the token so the retry carries the refreshed bearer, wait out
    // the refresh window, then retry once. A 401 that persists re-throws from
    // this second attempt.
    if (accessTokenProvider) {
      await accessTokenProvider()
    }
    const backoffMs = opts.backoffMs ?? 250
    if (backoffMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, backoffMs))
    }
    return await fn()
  }
}
