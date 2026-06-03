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
  method: "GET" | "POST" | "DELETE" | "PATCH",
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

  // ---- ClickUp -------------------------------------------------------------
  disconnectClickup: () =>
    api.delete<{ deleted: true; provider: string }>("/v1/connectors/clickup"),

  // ---- HubSpot -------------------------------------------------------------
  disconnectHubspot: () =>
    api.delete<{ deleted: true; provider: string }>("/v1/connectors/hubspot"),
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
      "/v1/connectors/slack/apikey",
      { api_key: apiKey },
    ),
  disconnectSlack: () =>
    api.delete<{ deleted: true; provider: string }>("/v1/connectors/slack"),
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
      "/v1/connectors/fireflies/apikey",
      { api_key: apiKey },
    ),
  disconnectFireflies: () =>
    api.delete<{ deleted: true; provider: string }>("/v1/connectors/fireflies"),
  listGithubRepos: (perPage = 50) =>
    api.get<{ repositories: GitHubRepo[] }>(
      `/v1/connectors/github/repos?per_page=${encodeURIComponent(String(perPage))}`,
    ),

  // ---- Generic test-connection (commit K) ---------------------------------
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

  // ---- Generic start-OAuth (commit F) -------------------------------------
  /**
   * Returns the provider's OAuth authorize URL as JSON. The caller is
   * expected to navigate the browser to it (`window.location.href = url`).
   *
   * Why this exists: the legacy GET /authorize routes 307-redirect to
   * Google/Figma/GitHub, but they require auth — and a browser URL-bar
   * navigation can't attach the Supabase Bearer token. This endpoint
   * runs the auth check via fetch + Bearer, then hands back the URL the
   * browser should navigate to next.
   */
  startOauth: (provider: string, dataset?: string) =>
    api.post<{ authorize_url: string }>(
      `/v1/connectors/${encodeURIComponent(provider)}/start-oauth`,
      dataset ? { dataset } : {},
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

export const designAgentApi = {
  /** Kicks off prototype generation in the background; returns immediately
   *  with a prototype_id. Client should poll designAgentApi.get(id) (via
   *  runDesignAgentGeneration) until status === 'ready'. */
  generate: (body: {
    prd_id: number
    target_platform: "desktop" | "mobile" | "both"
    instructions: string
    figma_file_key?: string | null
    website_url?: string | null  // P5-02: Scenario B fallback source
    manual_design?: { primary_color: string; font_family: string } | null  // P5-02: manual floor
  }) => api.post<PrototypeStartResponse>("/v1/design-agent/generate", body),
  /** Fetch a prototype row by id. bundle_url is filled when status === 'ready'. */
  get: (prototypeId: number) =>
    api.get<PrototypeRecord>(`/v1/design-agent/${prototypeId}`),
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
  createCommentByToken: (token: string, body: { anchor_id: string; body: string }) =>
    api.post<CommentRecord>(
      `/v1/design-agent/by-token/${encodeURIComponent(token)}/comments`,
      body,
    ),
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
