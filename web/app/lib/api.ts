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
/** The weekly-brief skill's closed type taxonomy (drives accent + the category
 *  pill). See backend/skills/weekly-brief/SKILL.md step 3. */
export type BriefSkillType =
  | "reliability"
  | "retention"
  | "competitive"
  | "growth"
  | "demand"
  | "engagement"
  | "compliance"

export type BriefSkillCta = {
  label: "View PRD" | "Draft PRD" | "View prototype" | "Generate prototype" | string
  style: "primary" | "ghost" | string
}

/** The skill's native card, attached to each insight by the backend as `_card`
 *  (weekly_brief_skill.cards_to_insights). The render layer prefers this over
 *  the legacy tag fields. `accent` may be mismatched to `type` by the model —
 *  derive accent from `type` instead (see lib/brief-skill-taxonomy). */
export type BriefSkillCard = {
  type?: BriefSkillType | string
  accent?: string
  title?: string
  body?: string
  sources?: string[]
  ctas?: BriefSkillCta[]
  signal_id?: string
}

export type Insight = {
  tag: "something_new" | "something_better" | "something_broken"
  /** Skill taxonomy type, hoisted to the insight top level by newer backends.
   *  Older briefs carry it only inside `_card`. */
  type?: BriefSkillType | string
  /** Skill accent hex (may be model-mismatched — prefer deriving from `type`). */
  accent?: string
  /** The skill's native card (type/accent/body/sources/ctas), attached by the
   *  backend. Present on briefs generated since the skill sweep. */
  _card?: BriefSkillCard
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
  /** v5 schema: LLM marks whether this finding's fix can be visualized as a
   *  UI prototype (a screen/flow change), vs. a backend/data/pricing/ops
   *  change that has nothing to render. Gates the "Generate prototype"
   *  option. Older briefs omit it → treated as prototypeable (shown). */
  prototypeable?: boolean
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
  /** Backend evidence-gate flag: set when the brief was saved EMPTY because the
   *  KG lacked enough connected-source evidence (vs. a brand-new account with no
   *  data at all). Lets the UI tell "we got your upload, but need more connected
   *  evidence" apart from "add your first source". Older/normal briefs omit it. */
  _insufficient_evidence?: boolean
  /** Human-readable reason that accompanies `_insufficient_evidence` (set by the
   *  backend). Optional and may carry internal phrasing — the UI prefers its own
   *  static copy unless this is clearly user-friendly. */
  _empty_reason?: string
}

export type BriefStatus = {
  company: string
  status: "ready" | "generating" | "failed" | "empty"
  error?: string
  /** A fresh brief is being built over a still-cached one. `status` stays
   *  "ready" (the current brief keeps rendering) while this is true. */
  regenerating?: boolean
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
  /**
   * Kick off the FULL regeneration pipeline: KG ingestion of the latest
   * sources/connectors/uploads → weekly-brief synthesis → PRD generation →
   * evidence generation. Fire-and-forget; poll `status()` for the brief stage.
   * Backs the "Regenerate brief" button on the Connectors settings page.
   */
  regenerateAll: (company: string = "asurion") =>
    api
      .post<{ started: boolean; dataset: string }>(
        `/v1/brief/regenerate-all?dataset=${encodeURIComponent(company)}`,
      )
      .then((r) => ({ started: r.started, company: r.dataset })),
  generate: () =>
    api
      .post<WireBrief & { brief_id: number }>("/v1/brief/generate")
      .then((b) => ({ ...briefFromWire(b), brief_id: b.brief_id })),
}

// ---- backlog ----------------------------------------------------------------
//
// The backlog is the REMAINDER of the same weekly-analysis ranking that feeds
// the brief: the top 3 ranked insights go into the brief, ranks 4..N are
// sequenced into the backlog. The backend gates the list on a brief existing,
// so a company that has never had a brief returns an empty backlog.
//
// The route is tenant-scoped via the session (no company param) — the backend
// resolves the company from the authenticated user.

export type BacklogTag = "something_new" | "something_better" | "something_broken"
export type BacklogStatus = "backlog" | "in_progress" | "done" | "dismissed"

export type BacklogItem = {
  id: string
  theme_id: string
  title: string
  tag: BacklogTag | null
  rank: number
  score: number
  status: BacklogStatus
  reasoning: string | null
}

export type BacklogList = { items: BacklogItem[]; count: number }

/** A completed brief finding — a theme whose action is prd_created or done. */
export type CompletedItem = {
  theme_id: string
  title: string
  action: "prd_created" | "done"
  last_surfaced_at: string | null
}

export type CompletedList = { items: CompletedItem[]; count: number }

export const backlogApi = {
  /** Ranked backlog items (rank-ascending). Empty when no brief exists. */
  list: () => api.get<BacklogList>("/v1/backlog"),
  /** Completed findings (prd_created | done) for the Completed tab. */
  completed: () => api.get<CompletedList>("/v1/backlog/completed"),
  /** Move one item to a new status (in_progress | done | dismissed). */
  setStatus: (itemId: string, status: Exclude<BacklogStatus, "backlog">) =>
    api.patch<BacklogItem>(`/v1/backlog/${encodeURIComponent(itemId)}`, { status }),
  /** Create a user-added backlog item ("+ Add idea"). `tag` is an optional
   *  BacklogTag when the idea-type maps cleanly, else null. Returns the row. */
  create: (title: string, tag: BacklogTag | null = null) =>
    api.post<BacklogItem>("/v1/backlog", { title, tag }),
  /** Persist a new rank order (drag-to-rerank / Re-sequence). `orderedIds` is
   *  the full visible order; each item's rank becomes its position. */
  reorder: (orderedIds: string[]) =>
    api.post<BacklogList>("/v1/backlog/reorder", { ordered_ids: orderedIds }),
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

/** POST /v1/ask is fire-and-forget: it returns an ask_id immediately and the
 *  answer keeps generating server-side (blur/remount-safe). The client polls
 *  askApi.get(id) until status leaves 'generating'. */
export type AskStartResponse = {
  ask_id: number
  status: "generating" | "ready" | "error"
}

/** GET /v1/ask/{id} status + result. Once status === 'ready' the answer /
 *  key_points / citations / confidence / unanswered fields carry the SAME
 *  citation-stripped shape the old synchronous POST returned, so downstream
 *  rendering is unchanged. `error` is set when status === 'error'. */
export type AskStatusResponse = AskResponse & {
  status: "generating" | "ready" | "error"
  error?: string | null
  /** Extra qa_agent metadata (e.g. routed skill) passed through verbatim. */
  [extra: string]: unknown
}

export const askApi = {
  /** Kick off an Ask in the background. Returns immediately with an ask_id;
   *  poll askApi.get(ask_id) until status !== 'generating'. */
  start: (
    question: string,
    company: string = "asurion",
    opts?: { conversation_id?: number; pinned_skill?: string },
  ) =>
    api.post<AskStartResponse>("/v1/ask", {
      question,
      dataset: company,
      ...(opts?.conversation_id != null ? { conversation_id: opts.conversation_id } : {}),
      ...(opts?.pinned_skill != null ? { pinned_skill: opts.pinned_skill } : {}),
    }),
  /** Read the status + result of an Ask job. */
  get: (askId: number) => api.get<AskStatusResponse>(`/v1/ask/${askId}`),
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
  /** Part B: the implementation-spec skill output (EARS requirements, design/
   *  contracts, dependency-ordered tasks, acceptance tests, Definition of Done,
   *  verification report), stored as faithful markdown. Returned by the
   *  GET routes' `select("*")`. Optional — absent on legacy rows / when Part B
   *  generation failed. */
  llm_part?: string
  status: "generating" | "ready" | "failed"
  error?: string | null
  variant?: string
}

/** Response from POST /v1/prd/{id}/impl-spec — the on-demand machine-readable
 *  Implementation Spec produced when a PRD is sent to Claude Code. `cached` is
 *  true when an unchanged PRD reused a previously-generated spec. */
export type ImplSpecResponse = {
  llm_part: string
  cached: boolean
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
  /** Read the latest evidence for a brief insight (ready or in-flight), or null.
   *  Lets the Evidence tab populate for the insight whose PRD is being viewed /
   *  generated — a pure read, never kicks off generation. Swallows 404→null. */
  byInsight: async (
    briefId: number,
    insightIndex: number,
  ): Promise<EvidenceRecord | null> => {
    try {
      return await api.get<EvidenceRecord>(
        `/v1/evidence/by-insight/${encodeURIComponent(String(briefId))}/${encodeURIComponent(String(insightIndex))}`,
      )
    } catch {
      return null
    }
  },
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

/** POST /v1/onboarding/analyze-website is fire-and-forget: it returns a job_id
 *  immediately and the analysis keeps running server-side (blur/remount-safe).
 *  The client polls onboardingApi.analyzeWebsiteStatus(job_id) until the status
 *  leaves 'generating'. */
export type AnalyzeWebsiteStartResponse = {
  job_id: number
  status: "generating" | "ready" | "error"
}

/** GET /v1/onboarding/analyze-website/{job_id} status + result. Once
 *  status === 'ready' the `result` field carries the SAME AnalyzeWebsiteResponse
 *  dict the old synchronous POST returned, so setWebsiteAnalysis(result) is
 *  unchanged. `result` is null while generating / on error. */
export type AnalyzeWebsiteStatusResponse = {
  status: "generating" | "ready" | "error"
  result: AnalyzeWebsiteResponse | null
  error: string | null
}

export const onboardingApi = {
  /**
   * Kick off a website analysis to infer industry / business type / stage and
   * draft a business-context blurb + suggested metrics. Fire-and-forget: returns
   * a job_id immediately and the analysis runs server-side; poll
   * analyzeWebsiteStatus(job_id) until status !== 'generating'. Company is taken
   * from the JWT (Depends(require_company)) — no slug needed.
   */
  analyzeWebsite: (url: string) =>
    api.post<AnalyzeWebsiteStartResponse>(
      "/v1/onboarding/analyze-website",
      { url },
    ),
  /** Read the status + result of a website-analysis job. */
  analyzeWebsiteStatus: (jobId: number) =>
    api.get<AnalyzeWebsiteStatusResponse>(
      `/v1/onboarding/analyze-website/${jobId}`,
    ),
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

// ---- business context -------------------------------------------------------
//
// The company's structured, provenance-tracked "lens" (8 layers). Mirrors
// backend/app/business_context.py: every leaf is wrapped in a provenance
// envelope (value + src + conf + as_of + evidence). Stored in
// companies.business_context (JSONB). The doc tolerates partials — only
// identity is guaranteed present, and its leaves may still be unknown.

/** Per-leaf provenance envelope. `value` is whatever the leaf holds
 *  (string | string[] | boolean | null). */
export type BcSrc = "given" | "user" | "inferred" | "web" | "unknown"
export type BcConf = "high" | "med" | "low" | null
export type BcLeaf<T = unknown> = {
  value: T
  src: BcSrc
  conf: BcConf
  as_of: string | null
  evidence: string | null
}

export type BcIdentity = {
  legal_name: BcLeaf
  also_known_as: BcLeaf
  website: BcLeaf
  one_liner: BcLeaf
  industry: BcLeaf
  sub_vertical: BcLeaf
  company_size: BcLeaf
  stage: BcLeaf
  hq_geography: BcLeaf
  markets_served: BcLeaf
}

export type BcBusinessModel = {
  model_type: BcLeaf
  revenue_model: BcLeaf
  pricing_model: BcLeaf
  who_pays: BcLeaf
  who_uses: BcLeaf
  monetization_unit: BcLeaf
  unit_economics_shape: BcLeaf
  good_outcome: BcLeaf
}

export type BcSegment = {
  name: BcLeaf
  description: BcLeaf
  jtbd: BcLeaf
  is_buyer: BcLeaf
  is_user: BcLeaf
  is_champion: BcLeaf
  relative_size: BcLeaf
}

export type BcUsersSegments = {
  segments: BcSegment[]
  primary_segment: BcLeaf
}

export type BcProductValue = {
  what_it_does: BcLeaf
  core_value_moments: BcLeaf
  activation_definition: BcLeaf
  key_features: BcLeaf
  platforms: BcLeaf
}

export type BcMarketCompetition = {
  category: BcLeaf
  main_alternatives: BcLeaf
  positioning_angle: BcLeaf
}

export type BcGoalsStrategy = {
  stated_goal: BcLeaf
  north_star: BcLeaf
  current_priorities: BcLeaf
  known_constraints: BcLeaf
}

export type BcVocabTerm = {
  term: BcLeaf
  their_meaning: BcLeaf
  sprntly_default: BcLeaf
  note: BcLeaf
}

export type BcVocabulary = {
  terms: BcVocabTerm[]
}

export type BcSourceRef = { url: string | null; as_of: string | null }

export type BcDocMeta = {
  created: BcLeaf
  last_refreshed: BcLeaf
  refresh_trigger: BcLeaf
  overall_confidence: BcLeaf
  sources: BcSourceRef[]
}

/** The full 8-layer document (+ version). Mirrors the pydantic
 *  `BusinessContext` model. */
export type BusinessContextDoc = {
  identity: BcIdentity
  business_model: BcBusinessModel
  users_segments: BcUsersSegments
  product_value: BcProductValue
  market_competition: BcMarketCompetition
  goals_strategy: BcGoalsStrategy
  vocabulary: BcVocabulary
  meta: BcDocMeta
  version: number
}

export const businessContextApi = {
  /**
   * GET the current business-context doc (any member). Returns `null` when
   * the backend answers 404 — i.e. the doc hasn't been generated yet
   * (onboarding incomplete / never refreshed). Other errors propagate.
   */
  get: async (): Promise<BusinessContextDoc | null> => {
    try {
      return await api.get<BusinessContextDoc>("/v1/company/business-context")
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  },
  /** PUT human edits (admin-only). Known leaves are stamped src="user"
   *  server-side. Returns the new version. */
  update: (doc: BusinessContextDoc) =>
    api.put<{ ok: true; version: number }>(
      "/v1/company/business-context",
      doc,
    ),
  /** POST refresh — re-runs the Business Context agent (admin-only). */
  refresh: () =>
    api.post<{ ok: true; [k: string]: unknown }>(
      "/v1/company/business-context/refresh",
    ),
}

// ---- roadmap doc (onboarding strategy step) ---------------------------------

export type RoadmapDocUploadResponse = {
  ok: true
  filename: string
  /** Number of characters extracted from the upload. */
  extracted_chars: number
  version: number
  [k: string]: unknown
}

/** The stored roadmap, as the `roadmapdoc` artifact view reads it. */
export type RoadmapDoc = {
  filename: string
  content_type: string | null
  /** Markdown text extracted from the upload — what the read-only view renders. */
  extracted_text: string
  uploaded_at: string | null
  version: number
}

/**
 * Roadmap-doc API for the onboarding strategy step (design scene onbstrat) +
 * the read-only `roadmapdoc` artifact view.
 *
 * `upload` POSTs the multipart file to `POST /v1/company/roadmap-doc`, which
 * stores the doc + its extracted text against the company so the weekly brief
 * can pressure-test findings against the roadmap. `get` reads the stored
 * roadmap (404 → null) for the artifact view.
 */
export const roadmapDocApi = {
  upload: (file: File) => {
    const form = new FormData()
    form.append("file", file, file.name)
    return api.post<RoadmapDocUploadResponse>("/v1/company/roadmap-doc", form)
  },
  /** Fetch the stored roadmap; resolves to null when none uploaded yet (404). */
  get: async (): Promise<RoadmapDoc | null> => {
    try {
      return await api.get<RoadmapDoc>("/v1/company/roadmap-doc")
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  },
}

// ---- templates ("what good looks like") -------------------------------------
//
// The company's uploaded gold-standard PRD examples. Sibling of the roadmap doc
// above, but MANY per company: each is its own listed, individually-deletable
// row. The extracted text is fed to the prd-author skill as a FORMAT/STYLE
// EXEMPLAR so generated PRDs match the team's structure & voice. Mirrors
// backend/app/company_template.py + the /v1/company/templates routes.

/** One stored gold-standard template, as the list view reads it. Never carries
 *  the raw file bytes — only metadata + the extracted-char count. */
export type CompanyTemplate = {
  id: string
  label: string | null
  type: string
  filename: string
  content_type: string | null
  /** Characters extracted from the upload (the text fed to prd-author). */
  extracted_chars: number
  uploaded_at: string | null
}

export type TemplateUploadResponse = { ok: true } & CompanyTemplate

export const templatesApi = {
  /** All gold-standard templates for the company, newest first. Optionally
   *  filtered by `type` (defaults to all). */
  list: (type?: string) => {
    const qs = type ? `?type=${encodeURIComponent(type)}` : ""
    return api
      .get<{ templates: CompanyTemplate[] }>(`/v1/company/templates${qs}`)
      .then((r) => r.templates)
  },
  /** Upload a gold-standard PRD example (multipart). Optional `label` names it
   *  in the list; `type` defaults to "prd" server-side. */
  upload: (file: File, opts?: { label?: string; type?: string }) => {
    const form = new FormData()
    form.append("file", file, file.name)
    if (opts?.label) form.append("label", opts.label)
    if (opts?.type) form.append("type", opts.type)
    return api.post<TemplateUploadResponse>("/v1/company/templates", form)
  },
  /** Remove one template by id. */
  remove: (id: string) =>
    api.delete<{ ok: true; id: string }>(
      `/v1/company/templates/${encodeURIComponent(id)}`,
    ),
}

// ---- company documents (onboarding strategy step — scene onbstrat) ----------
//
// The strategy/context files a PM uploads on the FINAL onboarding step: a typed
// grid of upload cards. Generalized sibling of the roadmap doc + templates: a
// single store with a `doc_type` discriminator. MANY per company. Mirrors
// backend/app/company_document.py + the /v1/company/documents routes. STORED
// only for now (feeding the text into agent context is a follow-up).

/** The strategy-step upload cards. Mirrors company_document.DOC_TYPES. */
export type CompanyDocType =
  | "ceo_memo"
  | "team_priorities"
  | "research"
  | "company_strategy"

/** One stored company document, as the list view reads it. Never carries the
 *  raw file bytes — only metadata + the extracted-char count. */
export type CompanyDocument = {
  id: string
  doc_type: CompanyDocType
  filename: string
  content_type: string | null
  /** Characters extracted from the upload. */
  extracted_chars: number
  uploaded_at: string | null
}

export type CompanyDocUploadResponse = { ok: true } & CompanyDocument

export const companyDocsApi = {
  /** All strategy/context documents for the company, newest first. Optionally
   *  filtered by `doc_type`. */
  list: (docType?: CompanyDocType) => {
    const qs = docType ? `?doc_type=${encodeURIComponent(docType)}` : ""
    return api
      .get<{ documents: CompanyDocument[] }>(`/v1/company/documents${qs}`)
      .then((r) => r.documents)
  },
  /** Upload a strategy/context document under one of the onbstrat cards
   *  (multipart). */
  upload: (file: File, docType: CompanyDocType) => {
    const form = new FormData()
    form.append("file", file, file.name)
    form.append("doc_type", docType)
    return api.post<CompanyDocUploadResponse>("/v1/company/documents", form)
  },
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
    // Google Drive — files picked via the Google Picker (drive.file scope)
    files?: GoogleDrivePickedFile[]
    // Slack
    target_type?: "channel" | "dm"
    channel_id?: string
    channel_name?: string
    // Figma (PAT-vs-OAuth distinction set by backend on save)
    auth_kind?: "pat" | "oauth"
  }
  last_sync_at: string | null
  last_sync_error: string | null
  // Token-health set by the scheduled connector health monitor (and the on-open
  // test). "connected" | "disconnected"; null/undefined = never checked.
  health?: string | null
  last_health_error?: string | null
  last_health_check_at?: string | null
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
  synced: { filename: string; md_path: string; md_chars: number }[]
  skipped: { name: string; reason: string }[]
  errors: { name: string; error: string }[]
}

/** A file the user picked via the Google Picker (drive.file scope). */
export type GoogleDrivePickedFile = {
  id: string
  name?: string
}

/** Short-lived, drive.file-scoped access token for the browser Google Picker. */
export type GoogleDrivePickerToken = {
  access_token: string
  expires_in: number
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
  /** Mint a short-lived, drive.file-scoped access token for the browser
   * Google Picker. The Picker widget runs in the user's own browser and
   * needs an OAuth token to render their Drive. */
  getGoogleDrivePickerToken: () =>
    api.get<GoogleDrivePickerToken>(
      `/v1/connectors/google-drive/picker-token`,
    ),
  /** Persist the files the user selected in the Google Picker and run a
   * sync so they land in the corpus. Replaces the whole stored list. */
  saveGoogleDriveFiles: (body: { files: GoogleDrivePickedFile[] }) =>
    api.post<GoogleDriveSyncResult>(
      `/v1/connectors/google-drive/files`,
      body,
    ),
  syncGoogleDrive: (dataset?: string) =>
    api.post<GoogleDriveSyncResult>(`/v1/connectors/google-drive/sync`, {
      dataset,
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
  disconnectSlack: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/slack`),
  listSlackChannels: () =>
    api.get<{ channels: SlackChannel[] }>(`/v1/connectors/slack/channels`),
  setSlackConfig: (
    target: { targetType: "channel"; channelId: string; channelName?: string }
      | { targetType: "dm" },
  ) =>
    api.post<{ ok: true; config: ConnectionSummary["config"]; joined: boolean }>(
      `/v1/connectors/slack/config`,
      target.targetType === "dm"
        ? { target_type: "dm" }
        : {
            target_type: "channel",
            channel_id: target.channelId,
            channel_name: target.channelName,
          },
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
   * Returns the provider's OAuth authorize URL as JSON. The caller opens it
   * in a new browser tab (see `openOauthTab` in lib/connectorsOauth) so the
   * user isn't navigated out of onboarding / settings to authorize.
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
  /** Kick off PRD generation for a BACKLOG item (a theme ranked ≥ 4, not in the
   *  brief's top-3). Same fire-and-forget contract as `generate`: returns a
   *  prd_id to poll via prdApi.get(id). The backend synthesizes the insight from
   *  the backlog row and grounds it on the company's current brief. */
  generateFromBacklog: (backlogItemId: string, force = false) =>
    api.post<PrdStartResponse>("/v1/prd/generate-from-backlog", {
      backlog_item_id: backlogItemId,
      force,
    }),
  /** Fetch a PRD by id. payload_md is only filled when status === 'ready'. */
  get: (id: number) => api.get<PrdRecord>(`/v1/prd/${id}`),
  /** Fetch the latest ready PRD for a dataset/company slug. 404 if none. */
  latest: (dataset: string) => api.get<PrdRecord>(`/v1/prd/latest?dataset=${encodeURIComponent(dataset)}`),
  /** Old name retained for compatibility. */
  byId: (id: number) => api.get<PrdRecord>(`/v1/prd/${id}`),
  /** Generate (or reuse the cached) machine-readable Implementation Spec for a
   *  PRD — backs the "Send to Claude Code" action. Synchronous: resolves with the
   *  agent-ready spec markdown to paste into Claude Code. The spec is cached on
   *  the PRD until its human content changes. */
  sendToClaudeCode: (id: number) =>
    api.post<ImplSpecResponse>(`/v1/prd/${id}/impl-spec`, {}),
  /** Save PRD edits (title + markdown). Auto-creates a version snapshot. */
  update: (id: number, body: { title: string; payload_md: string }) =>
    api.put<PrdRecord>(`/v1/prd/${id}`, body),
  /** List all versions of a PRD, newest first. */
  listVersions: (id: number) =>
    api.get<{ id: number; prd_id: number; version_number: number; title: string; payload_md: string; saved_by: string; saved_at: string }[]>(`/v1/prd/${id}/versions`),
  /** Restore a PRD to a specific version. */
  restoreVersion: (prdId: number, versionId: number) =>
    api.post<PrdRecord>(`/v1/prd/${prdId}/versions/${versionId}/restore`, {}),
  /** Prior generations of this PRD (regenerations sharing brief+insight), newest first. */
  listGenerations: (id: number) =>
    api
      .get<{ generations: { id: number; title: string; status: string; generated_at: string; insight_index: number | null }[] }>(
        `/v1/prd/${id}/generations`,
      )
      .then((r) => r.generations),
}

// ---- Design Agent ---------------------------------------------------
// Append-only block; does not modify any export above. Mirrors prdApi — reuses
// the shared `api` helper so credentials/JSON/${API_URL} handling stays
// centralised (no raw fetch, no reinvented client).

/** The agent's clarifying question, persisted on the prototype
 *  row as a sidecar. Shape `{question, choices?, context?}`. When non-null the
 *  prototype is in `awaiting_clarification` (`status` stays `ready` — the
 *  question is a sidecar, NOT a status enum value). `choices` present → answer
 *  by picking a button; absent → free-text answer. */
export type PendingQuestionChoice = { label: string; description?: string | null }
export type PendingQuestion = {
  question: string
  /** Each choice is `{label, description?}`. Legacy rows may still ship plain
   *  `string[]`; consumers normalize a bare string to `{label}` (graceful). */
  choices?: Array<PendingQuestionChoice | string>
  context?: string
}

/** Normalize a `PendingQuestion.choices` entry (object or legacy string) into the
 *  object shape. A bare string becomes `{label}` with no description (graceful
 *  degrade for old in-flight rows). */
export function normalizeChoice(
  choice: PendingQuestionChoice | string,
): PendingQuestionChoice {
  return typeof choice === "string" ? { label: choice } : choice
}

/** Full prototype row returned by GET /v1/design-agent/{id}. */
export type PrototypeRecord = {
  id: number
  status: "generating" | "ready" | "failed" | "invalidated"
  bundle_url: string | null
  error: string | null
  // ── (append-only): mark-complete/resume + share columns added by the sharing
  //    migration. GET /v1/design-agent/{id} does `select("*")`, so the row
  //    carries these. Typed OPTIONAL so existing `PrototypeRecord` literals
  //    (e.g. the runDesignAgentGeneration test's `proto()` base) keep
  //    typechecking; consumers default with `?? …` for older/partial rows.
  is_complete?: boolean
  share_mode?: "private" | "public" | "passcode"
  share_token?: string | null
  // ── (append-only): `awaiting_clarification` sidecar — the
  //    `pending_question` column. GET /{id} `select("*")` carries
  //    it; typed OPTIONAL/nullable to match the posture above (no api method
  //    added — the existing GET poll surfaces it; the answer routes through the
  //    existing `iterate`). Null/absent ⇒ no question pending.
  pending_question?: PendingQuestion | null
  // ── (append-only): optional preview-thumbnail URL captured on generation-
  //    complete. GET /{id} / by-prd both `select("*")`, so the column flows
  //    through automatically — no api method change. Null/absent ⇒ no thumbnail
  //    captured (the preview card falls back to its existing placeholder); typed
  //    OPTIONAL/nullable to match the posture above so existing literals keep
  //    typechecking.
  preview_image_url?: string | null
  // ── (append-only): the form factor chosen in the Generate flow
  //    ("desktop" | "mobile" | "both"). GET /{id} / by-prd both `select("*")`,
  //    so the column flows through automatically — no api method change. Typed
  //    OPTIONAL/permissive (the `| string` tail covers legacy `web` rows) so
  //    existing literals keep typechecking and the viewer defaults to showing
  //    both device toggles for any absent/unrecognised value.
  target_platform?: "desktop" | "mobile" | "both" | string | null
}

/** 202 kickoff response from POST /v1/design-agent/generate. */
export type PrototypeStartResponse = {
  prototype_id: number
  status: string
}

/** An anchored comment. Wire shape mirrors the backend
 *  `CommentOut` (id/anchor_id/body/author/status/created_at/resolved_at).
 *  `status` is the lifecycle: `open` (active), `resolved` (internally
 *  closed), `orphaned` (the anchor no longer exists in the current bundle —
 *  set by the backend, rendered with no pin by the panel). */
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

/** A proposed PRD patch. Wire shape mirrors the backend
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

export type BriefPrototypeReadiness = { ready: boolean; preview_image_url: string | null }
export type BriefPrototypeMapEntry = {
  insight_index: number
  prd_id: number
  prd_title: string
  prototype: BriefPrototypeReadiness | null
}
export type BriefPrototypeMap = { brief_id: number; entries: BriefPrototypeMapEntry[] }

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
    website_url?: string | null  // Scenario B fallback source
    manual_design?: { primary_color: string; font_family: string } | null  // manual floor
    github_repo?: string | null  // connected-repo full_name ("org/repo"); prompt context only
    design_source?: "figma" | "github" | "website" | null  // explicit source selector; null = back-compat implicit precedence
    /** The screen route the PM confirmed in the locate UX. Sent only on the
     *  codebase generation path so the backend can resolve it into a recreate
     *  pre-seed. Absent / null = blank-canvas generation. */
    chosen_screen_route?: string | null
    /** The stable node id the PM confirmed in the locate UX. This is the
     *  resolution key the backend uses first: a non-route host (the app shell,
     *  an in-page section) has a non-route id and an empty/shared route, so the
     *  id is what lets it reach the recreate pre-seed. chosen_screen_route still
     *  travels as the human label + cache pin; absent id falls back to route. */
    chosen_screen_id?: string | null
    /** The snapshot SHA the route was confirmed against. Pins the backend's
     *  build_map at read time so the recreate reads the same bytes the PM
     *  confirmed against (and lands a cache hit). */
    map_commit_sha?: string | null
  }) => api.post<PrototypeStartResponse>("/v1/design-agent/generate", body),
  /** Fetch a prototype row by id. bundle_url is filled when status === 'ready'. */
  get: (prototypeId: number) =>
    api.get<PrototypeRecord>(`/v1/design-agent/${prototypeId}`),
  /** Clear the prototype's pending clarifying question ("Skip this change").
   *  POSTs the dismiss endpoint; backend clears `pending_question`. Returns the
   *  `{ok}` body. Same `api` helper as the other authed mutations. */
  dismissQuestion: (prototypeId: number) =>
    api.post<{ ok: boolean }>(`/v1/design-agent/${prototypeId}/dismiss-question`),
  delete: (prototypeId: number) =>
    api.delete<void>(`/v1/design-agent/${prototypeId}`),
  /** True abort of an in-flight generation: deletes the prototype row, resets
   *  the PRD to draft, and best-effort cancels the running generation task so it
   *  stops spending on further LLM turns. Same-origin + credentialed like the
   *  other DA mutations. Returns 204 / void. */
  cancel: (prototypeId: number) =>
    api.post<void>(`/v1/design-agent/${prototypeId}/cancel`),
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
  /** Resume lookup: the most-recent READY-or-GENERATING prototype for a PRD, or
   *  null. Unlike getByPrd (ready only), this also returns an in-flight row so
   *  the prototype route can re-attach to a generation in progress on a (re)load
   *  and poll it to ready — instead of stranding the finished bundle during the
   *  readiness lag. Swallows 404→null like getByPrd. */
  getActiveByPrd: async (prdId: number): Promise<PrototypeRecord | null> => {
    try {
      return await api.get<PrototypeRecord>(
        `/v1/design-agent/by-prd/${encodeURIComponent(String(prdId))}/active`,
      )
    } catch {
      return null
    }
  },
  /** Failed-state lookup: the most-recent prototype for a PRD of ANY status
   *  (incl 'failed'), or null. Unlike getActiveByPrd (ready-or-generating only),
   *  this surfaces a FAILED latest row so the prototype route shows an
   *  error+retry surface instead of the bare generate CTA. The route calls it
   *  only on the none-branch (no ready/generating row). Swallows 404→null like
   *  getActiveByPrd. */
  getLatestByPrd: async (prdId: number): Promise<PrototypeRecord | null> => {
    try {
      return await api.get<PrototypeRecord>(
        `/v1/design-agent/by-prd/${encodeURIComponent(String(prdId))}/latest`,
      )
    } catch {
      return null
    }
  },
  /** Mark a prototype complete. Empty body. */
  complete: (prototypeId: number) =>
    api.post<{
      prototype_id: number
      is_complete: boolean
      complete_checkpoint_id: number | null
    }>(`/v1/design-agent/${prototypeId}/complete`, {}),
  /** Bundle-proxy view-grant (Option B). Mints the short-lived, HttpOnly,
   *  path-scoped `da_view_grant` cookie that the SAME-ORIGIN bundle iframe's
   *  asset GETs carry automatically (the iframe cannot send a bearer). This
   *  bearer-authed POST (`require_company` server-side) MUST precede setting the
   *  authed iframe `src` to the opaque proxy bundle URL. The backend returns 204
   *  with the cookie as the payload — there is no body. 404 if the workspace
   *  doesn't own the prototype, 401 if unauthenticated, 429 if rate-limited.
   *  ONLY the authed surface calls this; the public `/p/<token>` path is
   *  token-in-URL and never mints a grant. */
  viewGrant: async (viewGrantUrl: string): Promise<void> => {
    // Option A (approved v3 §1.6): mint via the APP-ORIGIN /_da-bundle/ path
    // (viewGrantUrl, derived from the proxy bundle URL) — NOT api.post(API_URL).
    // This sets da_view_grant HOST-ONLY first-party to the app origin (no Domain
    // attr ⇒ no cookie_domain dependency) so the same-origin iframe's asset GETs
    // carry it. Bearer-authed (require_company server-side); credentials:'include'
    // so the Set-Cookie is stored. 204/no body; throws on 401/404/429.
    const headers: Record<string, string> = {}
    if (accessTokenProvider) {
      const token = await accessTokenProvider()
      if (token) headers.Authorization = `Bearer ${token}`
    }
    const res = await fetch(viewGrantUrl, { method: "POST", headers, credentials: "include" })
    if (!res.ok) throw new ApiError(res.status, null, "view-grant failed")
  },
  /** Resume iteration on a completed prototype. Empty body. */
  resume: (prototypeId: number) =>
    api.post<{
      prototype_id: number
      is_complete: boolean
      handoffs_flagged_stale: number
    }>(`/v1/design-agent/${prototypeId}/resume`, {}),
  /** Set the share mode (and, for passcode mode, the passcode). */
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
   * `GET /v1/design-agent/{id}/export` returns `text/markdown`, NOT JSON,
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
      // 409 = WIP. 404 = wrong workspace / missing. 401 = no auth.
      throw new ApiError(res.status, await res.text())
    }
    return await res.text()
  },
  // ── anchored comments ──────────────────────────────────────────
  /** Public-route comment write (external viewer on `/p/<token>`): the token
   *  is the access primitive, so no auth is required. Hits the
   *  public route. An optional `viewer_name` is the viewer's self-
   *  supplied display name; the backend maps it onto the comment author (falling
   *  back to "Anonymous"). Omitted on the signed-in surface. Additive field. */
  createCommentByToken: (token: string, body: {
    anchor_id: string; body: string;
    pin_x_pct?: number; pin_y_pct?: number; resolved_anchor_id?: string | null;
    viewer_name?: string;
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
  /** Authed comment read for the signed-in editor: lists every comment for the
   *  prototype (all statuses). Hits the authed route `GET /v1/design-agent/{id}/comments`
   *  — the by-token route 404s in the editor context where there is no share token. */
  listComments: (prototypeId: number) =>
    api.get<CommentRecord[]>(`/v1/design-agent/${prototypeId}/comments`),
  /** Internal (authed) resolve — external viewers cannot resolve (spec §4
   *  Stage 2). Addressed by prototype id; renders only on the signed-in mount
   *  where a `prototypeId` is supplied. */
  resolveComment: (prototypeId: number, commentId: number) =>
    api.patch<CommentRecord>(
      `/v1/design-agent/${prototypeId}/comments/${commentId}/resolve`,
    ),
  deleteComment: (prototypeId: number, commentId: number) =>
    api.delete<void>(`/v1/design-agent/${prototypeId}/comments/${commentId}`),
  // ── PRD patches ───────────────────────────────────────────────
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
  // ── pre-flight cost estimate ─────────────────────────────────
  /** Pre-flight cost estimate for an iterate run. Deterministic, makes no
   *  Anthropic call server-side — drives the CostEstimateModal's
   *  "~$0.X · Continue / Cancel" gate. The iterate composer itself (`iterate`)
   *  only estimates here. */
  estimateIterate: (
    prototypeId: number,
    body: { prompt: string; applied_comment_id?: number | null },
  ) =>
    api.post<IterateCostEstimate>(
      `/v1/design-agent/${prototypeId}/iterate/estimate`,
      body,
    ),
  // ── iterate ────────────────────────────────────────────────
  /** Kick off an iterate of an existing prototype (re-prompt / Apply).
   *  The IterateComposer
   *  routes Submit through the `CostEstimateModal` gate and calls this ONLY
   *  from the modal's Continue handler — never directly from a Submit. Defaults
   *  `mode:'execute'`. Returns the background-run handle +
   *  `queue_position` (the iterate queue). 409 when the prototype is locked
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
  // ── manual edit ──────────────────────────
  /** Commit a batch of light visual property edits collected
   *  by the ManualEditOverlay. Mirrors `iterate`'s response shape (background-run
   *  handle + queue_position). `body.edits` are de-duplicated
   *  `{anchor_id, property, old_value, new_value}` triples; the backend route
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
  /** Kick off the map → locate-LLM → gate pipeline for a PRD + connected repo.
   *  ASYNC contract: the POST returns 202 with a `job_id` immediately;
   *  the gate decision is produced in the background and read back by polling
   *  `locateJob(job_id)`. Inline failures still surface on the POST itself —
   *  notably 404 (feature off / PRD not owned / cross-workspace) — so callers
   *  must catch the POST as well as the poll. Use `locateJob` to drive the loop. */
  locate: (body: {
    prd_id: number
    github_repo: string
    ref?: string | null
    /** Optional "search again" steer — a free-text direction (e.g. "the
     *  settings page") that re-ranks locate toward the surface the PM means.
     *  Omitted/blank = today's unsteered locate. */
    hint?: string | null
    /** Optional image-as-steer — a client-downscaled base64 image
     *  data URL ("data:image/<png|jpeg|webp>;base64,…") of the screen the PM
     *  wants. The server reads its on-screen text/route cues and re-ranks; falls
     *  open to text-only on an oversized/undecodable image. Omitted = no image. */
    image?: string | null
  }) => api.post<LocateJobHandle>("/v1/design-agent/locate", body),
  /** Poll a locate job by id. Returns the job status; when `status` is
   *  "done" the existing `LocateResponse` rides in `result`, and when "error"
   *  the failure reason rides in `error`. A 404 from this endpoint means the
   *  job is unknown / TTL-swept / cross-workspace — a TERMINAL error, distinct
   *  from a transient 5xx the caller should retry. Reuses LocateResponse as the
   *  result shape (do not redefine). */
  locateJob: (jobId: string) =>
    api.get<LocateJobStatus>(
      `/v1/design-agent/locate/jobs/${encodeURIComponent(jobId)}`,
    ),
  briefPrototypeMap: (briefId: number): Promise<BriefPrototypeMap> =>
    api.get<BriefPrototypeMap>(
      `/v1/design-agent/brief-prototype-map?brief_id=${encodeURIComponent(String(briefId))}`,
    ),
}

/** One ranked screen candidate from the locate pipeline (map → LLM → gate). */
export type LocateCandidate = {
  /** Stable node id. "app-shell" for the shell host, the section id for an
   *  in-page section, the route for a routed screen. The picker forwards this
   *  as chosen_screen_id so a non-route host survives the click → generate hop. */
  id: string
  route: string
  entry_component: string
  confidence: number
  rationale: string
  ambiguous: boolean
  component_count: number
}

/** Handle returned by POST /v1/design-agent/locate (HTTP 202). The job
 *  runs in the background; poll `locateJob(job_id)` until it is "done"/"error". */
export type LocateJobHandle = {
  job_id: string
  status: "running"
}

/** Snapshot returned by GET /v1/design-agent/locate/jobs/{job_id}.
 *  `result` is the unchanged LocateResponse, present only when status is
 *  "done"; `error` carries the failure reason when status is "error". */
export type LocateJobStatus = {
  status: "running" | "done" | "error"
  result?: LocateResponse
  error?: string
}

/** Shape returned by POST /v1/design-agent/locate. */
export type LocateResponse = {
  decision: "auto_proceed" | "proceed_with_note" | "ranked_confirm"
  chosen: LocateCandidate[]
  ranked: LocateCandidate[]
  top_confidence: number
  threshold: number
  repo: string
  posture: "CLEAN" | "PARTIAL"
  unmapped: boolean
  /** Snapshot SHA the locate result was resolved against. Empty string on the
   *  unmapped path. The generate body sends this back as `map_commit_sha` so
   *  the recreate reads the same snapshot. */
  commit_sha: string
  /** Image-as-steer. Cues the model read off an attached screenshot
   *  (URL/route, nav labels, headings), for the recovery chip. Always `[]`
   *  unless `image_status === "applied"` (backend-enforced). Optional/additive. */
  read_cues?: string[]
  /** Image-as-steer. Tells the UI whether an attached screenshot was
   *  used: "absent" (no image sent), "applied" (re-ranked toward it),
   *  "ignored_oversize" / "ignored_decode" (fell open to text-only — the UI must
   *  NOT claim the image was used). Optional/additive; defaults to "absent". */
  image_status?: "absent" | "applied" | "ignored_oversize" | "ignored_decode"
}

/** Shape returned by POST /v1/design-agent/{id}/iterate/estimate. */
export type IterateCostEstimate = {
  cached_input_tokens: number
  new_input_tokens: number
  expected_output_tokens: number
  est_cost_usd: number
  soft_cap_usd: number
  exceeds_soft_cap: boolean
  model: string
}

/** Shape returned by POST /v1/design-agent/{id}/iterate (route + queue). */
export type IterateResponse = {
  prototype_id: number
  status: string
  queue_position: number
}

/** The closed set of properties the ManualEditOverlay exposes.
 *  Border, animation, gap, margin, etc. are OUT of scope (deferred to v2).
 *  The wire keeps this typed so the overlay and the backend share
 *  one shape end-to-end. */
export type EditableProperty = "text" | "font-size" | "padding" | "color" | "background"

/** One fixed-property visual edit. The SAVED triple keys on
 *  `anchor_id` (one id may match N structurally-identical elements; the backend
 *  applies the edit to ALL N). `old_value` is the pristine value at first
 *  selection; `new_value` is the final value at Save. */
export type ManualEditTriple = {
  anchor_id: string
  property: EditableProperty
  old_value: string
  new_value: string
}

/** Shape returned by POST /v1/design-agent/{id}/manual-edit. Mirrors
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
  ok: boolean
  created: { task_id: string; clickup_task_id: string; url: string; title: string }[]
  errors: { task_id: string; title: string; error: string }[]
}

/** One task to push into ClickUp. `task_id` is the stable ticket key the user
 *  selected; the backend merges its saved edits/comments over these base
 *  fields before creating the ClickUp task. */
export type TicketPushTask = {
  task_id: string
  title: string
  description?: string
  acceptance_criteria?: string[]
  priority?: string
}

/** The team member picked as a ticket's assignee (subset of TeamMemberRecord). */
export type TicketAssignee = {
  user_id: string
  display_name: string | null
  email: string | null
  role: string | null
  avatar_url: string | null
}

/** Editable ticket metadata. All optional — a partial save only writes what's set. */
export type TicketFields = {
  title?: string | null
  priority?: string | null
  status?: string | null
  sprint?: string | null
  assignee?: TicketAssignee | null
}

export type TicketDataResponse = {
  description: string | null
  acceptance_criteria: string[] | null
  title: string | null
  priority: string | null
  status: string | null
  sprint: string | null
  assignee: TicketAssignee | null
  attachments: { id: number; label: string; sub: string }[]
  comments: { id: number; author: string; body: string; time: string }[]
}

export const ticketDataApi = {
  /** Get all saved overrides for a ticket (fields, description, attachments, comments). */
  getData: (ticketKey: string) =>
    api.get<TicketDataResponse>(`/v1/tickets/${encodeURIComponent(ticketKey)}/data`),
  /** Save description + acceptance criteria. */
  saveDescription: (ticketKey: string, description: string, acceptanceCriteria: string[]) =>
    api.put(`/v1/tickets/${encodeURIComponent(ticketKey)}/description`, {
      description, acceptance_criteria: acceptanceCriteria,
    }),
  /** Save title/priority/status/sprint/assignee. Only the keys present are
   *  written, so a partial save never clobbers the description or other fields. */
  saveFields: (ticketKey: string, fields: TicketFields) =>
    api.put(`/v1/tickets/${encodeURIComponent(ticketKey)}/fields`, fields),
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
  /** AI summary of the comment thread. `summary` is null when there's too little
   *  to summarize (< 2 comments) or the LLM call failed (best-effort). */
  summarizeComments: (ticketKey: string) =>
    api.get<{ summary: string | null }>(`/v1/tickets/${encodeURIComponent(ticketKey)}/comments/summary`),
}

export const ticketPushApi = {
  /** Fetch ClickUp lists the company can push tickets into. 404 when not connected. */
  listClickUpLists: () =>
    api.post<{ lists: ClickUpList[] }>("/v1/tickets/lists", {}),
  /** Push the selected tasks into a ClickUp list. The backend merges each
   *  task's saved edits/comments over the supplied base fields, then creates
   *  the ClickUp tasks and returns their ids + URLs so the UI can confirm. */
  pushToClickUp: (listId: string, tasks: TicketPushTask[]) =>
    api.post<TicketPushResult>("/v1/tickets/push-clickup", {
      list_id: listId,
      tasks,
    }),
}

// ── User stories: real PRD→tickets generation + ClickUp push ────────────────
// Backend: app/routes/stories.py. Generation is LLM-backed (the vendored
// user-stories skill) and writes nothing; push is the explicit ClickUp write.
// This is the REAL path behind "Create ticket" (vs the mock ticket fixtures).
export type GeneratedStory = {
  /** Content-derived stable id (hash of title+body) stamped at generation.
   *  Keys per-ticket edit overrides. Optional for sets cached before it existed. */
  id?: string
  title: string
  body: string
  acceptance_criteria: string[]
  priority: string | null
  route: string | null
}

export type StoryPushResult = {
  created: { story: string; task_id: string; url: string }[]
  errors: { story: string; error: string }[]
}

export type StoryJob = {
  job_id: number
  status: "generating" | "ready" | "failed"
  stories?: GeneratedStory[]
  error?: string
}

// Persisted tickets for a PRD. `fresh` is true when the stored stories were
// generated from the PRD's CURRENT rendered content (content-hash match) — the
// tab renders them with no LLM call. Otherwise the tab regenerates.
export type StoryCache = {
  status: "none" | "ready" | "generating" | "failed"
  fresh: boolean
  stories: GeneratedStory[]
  generated_at?: string
}

export const storiesApi = {
  /** Persisted tickets for a PRD + whether they're still fresh. Read this first;
   *  only regenerate when missing/stale (`fresh` false). No LLM call. */
  getForPrd: (prdId: number) =>
    api.get<StoryCache>(`/v1/stories/for-prd/${prdId}`),
  /** Kick off breaking a PRD into user-story tickets (fire-and-forget). Returns
   *  a job id immediately; poll `getJob` until ready/failed. Persists on ready. */
  generate: (prdId: number) =>
    api.post<{ job_id: number; status: string }>("/v1/stories/generate", { prd_id: prdId }),
  /** Poll a story-generation job. 404 once it's unknown / not the caller's. */
  getJob: (jobId: number) =>
    api.get<StoryJob>(`/v1/stories/jobs/${jobId}`),
  /** ClickUp lists the company can push into (target picker). 404 if ClickUp
   *  isn't connected. */
  listClickUpLists: () =>
    api.post<{ lists: ClickUpList[] }>("/v1/stories/lists", {}),
  /** Create the reviewed stories as tasks in a ClickUp list (explicit write). */
  pushToClickUp: (listId: string, stories: GeneratedStory[]) =>
    api.post<StoryPushResult>("/v1/stories/push", { list_id: listId, stories }),
}

// ── Team members ──────────────────────────────────────────────────────────

export type TeamMemberRecord = {
  user_id: string
  role: string
  display_name: string | null
  email: string | null
  avatar_url: string | null
}

export const teamApi = {
  /** Fetch all company members enriched with profile data. */
  list: () => api.get<{ members: TeamMemberRecord[] }>("/v1/team/members"),
}

// ── Feedback / feature-request (June 20 #13 + #A) ──
// Users submit a short message + an optional type from the left nav. The
// backend stores it and emails it to the team. type defaults to "other".

export type FeedbackType = "bug" | "feature_request" | "connector_request" | "other"

export type FeedbackResult = {
  id: string
  type: FeedbackType
  email_sent: boolean
}

export const feedbackApi = {
  /** Submit in-app feedback / a feature or connector request. */
  submit: (body: { message: string; type?: FeedbackType }) =>
    api.post<FeedbackResult>("/v1/feedback", body),
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


// ── Multi-Agent API ─────────────────────────────────────────────────────

export type MultiAgentMode = "standard" | "aggressive"

export interface MultiAgentGenerateResponse {
  run_id: string
  status: string
  mode: MultiAgentMode
  brief_id: number
  insight_index: number
}

export interface MultiAgentDocStatus {
  id: number
  status: string
  title: string
}

export interface MultiAgentRunStatus {
  run_id: string
  status: "generating" | "ready" | "partial" | "unknown"
  docs: Record<string, MultiAgentDocStatus>
}

export interface MultiAgentDoc {
  id: number
  doc_type: string
  title: string
  status: string
  payload_md: string
  error?: string
}

export interface MultiAgentDocsResponse {
  run_id: string
  docs: MultiAgentDoc[]
}

export const multiAgentApi = {
  /** Kick off multi-agent generation. Returns immediately with run_id. */
  generate: (
    briefId: number,
    insightIndex: number,
    mode: MultiAgentMode = "aggressive",
    force = false,
  ) =>
    api.post<MultiAgentGenerateResponse>("/v1/multi-agent/generate", {
      brief_id: briefId,
      insight_index: insightIndex,
      mode,
      force,
    }),

  /** Poll run status until all docs are ready/partial. */
  getStatus: (runId: string) =>
    api.get<MultiAgentRunStatus>(`/v1/multi-agent/${runId}`),

  /** Fetch all generated docs for a run (full markdown). */
  getDocs: (runId: string) =>
    api.get<MultiAgentDocsResponse>(`/v1/multi-agent/${runId}/docs`),

  /** Fetch a single doc by id. */
  getDoc: (docId: number) =>
    api.get<MultiAgentDoc>(`/v1/multi-agent/doc/${docId}`),

  /** Read the generated QA test-scenarios doc for a brief insight. Returns
   *  `{ doc: null }` when none exists; the doc's `payload_md` carries the
   *  `:::qa-scenarios` semantic block. */
  getQaScenarios: (briefId: number, insightIndex = 0) =>
    api.get<{ doc: { id: number; title: string; status: string; payload_md: string } | null }>(
      `/v1/multi-agent/qa-scenarios?brief_id=${briefId}&insight_index=${insightIndex}`,
    ),
}

// ---- Artifacts (All-Chats "Artifacts" tab) ---------------------------------
// Append-only block. A unified, recency-sorted list of every generated PRD,
// prototype, and evidence for the active company — backs the Artifacts tab.
// Reuses the shared `api` helper (credentials/JSON/${API_URL} centralised).

/** The brief/parent-PRD context shown on an artifact row's meta line, plus the
 *  ids the existing viewer needs to OPEN it. Discriminated by `type`. */
export type ArtifactItem =
  | {
      type: "prd"
      id: number
      title: string
      status: string
      created_at: string
      source: { brief_id: number; week_label: string | null; insight_index: number | null }
      open: { brief_id: number; insight_index: number | null; prd_id: number }
    }
  | {
      type: "evidence"
      id: number
      title: string
      status: string
      created_at: string
      source: { brief_id: number; week_label: string | null; insight_index: number | null }
      open: { brief_id: number; insight_index: number | null; evidence_id: number }
    }
  | {
      type: "prototype"
      id: number
      title: string
      // Lifecycle. Aggregation filters to generating|ready; failed/invalidated
      // never arrive. (Widened to `string` is avoided — the surface keys UI off
      // these two values; an unknown value falls through to the ready branch.)
      status: "generating" | "ready"
      created_at: string
      source: { prd_id: number | null; prd_title: string }
      open: { prototype_id: number; prd_id: number | null }
      is_complete: boolean
      preview_image_url: string | null
    }

export const artifactsApi = {
  /** Unified artifact list for a company slug, newest first. */
  list: (company: string) =>
    api
      .get<{ artifacts: ArtifactItem[] }>(
        `/v1/artifacts?dataset=${encodeURIComponent(company)}`,
      )
      .then((r) => r.artifacts),
}
