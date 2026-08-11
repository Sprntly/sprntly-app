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

// The ACTIVE workspace, set by WorkspaceContext when the user picks one from
// the switcher (mirrors the accessTokenProvider pattern). Injected as the
// X-Workspace-Id header on every backend request in ONE place; the backend
// falls back to the company's default workspace when absent.
let activeWorkspaceId: string | null = null

export function setActiveWorkspaceId(id: string | null) {
  activeWorkspaceId = id
}

export function getActiveWorkspaceId(): string | null {
  return activeWorkspaceId
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
  if (activeWorkspaceId) headers["X-Workspace-Id"] = activeWorkspaceId

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
/** The top-insights skill's closed type taxonomy (drives accent + the category
 *  pill). See backend/skills/top-insights/SKILL.md step 3. */
export type BriefSkillType =
  | "reliability"
  | "retention"
  | "competitive"
  | "growth"
  | "demand"
  | "engagement"
  | "compliance"
  | "momentum"

export type BriefSkillCta = {
  label:
    | "View the evidence"
    | "View the full report"
    | "Generate PRD"
    | "View PRD"
    // pre-rename labels, still present on persisted briefs
    | "Draft PRD"
    | "View prototype"
    | "Generate prototype"
    | string
  style: "primary" | "ghost" | string
}

/** The skill's native card, attached to each insight by the backend as `_card`
 *  (top_insights_skill.cards_to_insights). The render layer prefers this over
 *  the legacy tag fields. `accent` may be mismatched to `type` by the model —
 *  derive accent from `type` instead (see lib/brief-skill-taxonomy). */
export type BriefSkillCard = {
  type?: BriefSkillType | string
  accent?: string
  title?: string
  body?: string
  sources?: string[]
  ctas?: BriefSkillCta[]
  /** Freshness state from the ledger ('new' | 'updated'). An updated card's
   *  body opens with what changed; the render shows a quiet "Updated" chip.
   *  Absent on briefs composed before the ledger wiring. */
  state?: "new" | "updated" | string
  /** The finding this card phrases (== the insight theme_id). Cards persisted
   *  before the top-insights rename spell it `signal_id`. */
  finding_id?: string
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
  /** The user-facing insight-type categories this finding belongs to (1–2 of
   *  the canonical slugs in lib/insight-types). Set by the backend so each
   *  reader can filter the pool to the types they picked. Older briefs omit it
   *  → the finding matches no specific filter and shows only in the default
   *  (unfiltered) view. */
  insight_types?: string[]
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
  /** The render-only FILTER pool: the full ranked set (top POOL_SIZE findings),
   *  each classified into `insight_types`. `insights` above is the canonical
   *  top-3 brief; the frontend renders from `_pool` when the reader has an
   *  insight-type filter, falling back to `insights`. Absent on briefs generated
   *  before the pool existed — treat `insights` as the pool in that case. */
  _pool?: Insight[]
  /** Phase 2A ledger: candidates held back from this brief with a reason
   *  (carried | dismissed | deferred | in_progress | rotation_exhausted).
   *  `deferred_until` accompanies reason 'deferred'. Feeds the quiet
   *  "held back this cycle" line under the cards — "what am I not seeing"
   *  always has an answer. Absent on pre-ledger briefs. */
  _backlog?: {
    theme_id: string
    theme_label: string
    reason: "carried" | "dismissed" | "deferred" | "in_progress" | "rotation_exhausted" | "sibling_deferred" | "sibling_dismissed" | string
    deferred_until?: string | null
  }[]
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
   * sources/connectors/uploads → top-insights synthesis → PRD generation →
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
  /** Record a card dismissal in the server ledger ("not interested" — stays
   *  out unless the issue materially worsens). The card UI still greys out via
   *  localStorage instantly; this makes the action durable + theme-keyed. */
  dismiss: (briefId: number, insightIndex: number) =>
    api.post<{ dismissed: boolean; theme_id: string }>("/v1/brief/dismiss", {
      brief_id: briefId,
      insight_index: insightIndex,
    }),
  /** Record a card deferral ("not now" — interested, wrong moment). The theme
   *  is suppressed until deferred_until, then re-enters the next brief at full
   *  rank even if unchanged. Never counts toward dismissal streaks. */
  defer: (briefId: number, insightIndex: number) =>
    api.post<{ deferred: boolean; theme_id: string; deferred_until: string }>(
      "/v1/brief/defer",
      { brief_id: briefId, insight_index: insightIndex },
    ),
  /** Undo a dismiss/defer server-side (action back to 'surfaced'), so a card
   *  the reader visibly restored isn't suppressed again next run. */
  restore: (briefId: number, insightIndex: number) =>
    api.post<{ restored: boolean; theme_id: string }>("/v1/brief/restore", {
      brief_id: briefId,
      insight_index: insightIndex,
    }),
}

// ---- ideation ---------------------------------------------------------------
//
// The ideation pool is the REMAINDER of the same weekly-analysis ranking that
// feeds the brief: the top 3 ranked insights go into the brief, ranks 4..N are
// sequenced into the pool, and a weekly prioritization pass shortlists the
// 25-30 ideas worth showing — the list route returns ONLY that visible set.
// The backend gates the list on a brief existing, so a company that has never
// had a brief returns an empty list.
//
// The route is tenant-scoped via the session (no company param) — the backend
// resolves the company from the authenticated user.

export type IdeationTag = "something_new" | "something_better" | "something_broken"
// 'backlog' is the legacy spelling of 'proposed' (rows written by pre-rename
// prod until cutover) — treat the two as the same landing state.
export type IdeationStatus = "proposed" | "backlog" | "in_progress" | "done" | "dismissed"

export type IdeationItem = {
  id: string
  theme_id: string
  title: string
  tag: IdeationTag | null
  rank: number
  score: number
  status: IdeationStatus
  /** Picked by the weekly prioritization pass (manual ideas are born true). */
  shortlisted?: boolean
  reasoning: string | null
  updated_at?: string
}

export type IdeationList = { items: IdeationItem[]; count: number }

/** A completed brief finding — a theme whose action is prd_created or done. */
export type CompletedItem = {
  theme_id: string
  title: string
  action: "prd_created" | "done"
  last_surfaced_at: string | null
}

export type CompletedList = { items: CompletedItem[]; count: number }

/** One supporting excerpt behind an idea, pulled from the knowledge graph.
 *  `content` is the extractor-distilled signal text — the closest thing we
 *  persist to a customer's own words (raw transcripts are never stored). */
export type IdeationEvidence = {
  signal_id: string
  content: string
  kind: string | null
  source_type: string | null
  provenance: Record<string, unknown>
  confidence: number | null
}

/** An idea plus the evidence behind it — backs the Ideation detail popup.
 *  `evidence` is capped to the strongest few; `evidence_count` is the true
 *  total, and `sources` spans the whole trail (breadth, not just the head). */
export type IdeationDetail = {
  id: string
  theme_id: string
  title: string
  tag: IdeationTag | null
  rank: number
  score: number
  status: IdeationStatus
  reasoning: string | null
  evidence: IdeationEvidence[]
  evidence_count: number
  sources: string[]
  is_manual: boolean
}

export const ideationApi = {
  /** The visible ideas (rank-ascending): the weekly shortlist + user-pinned
   *  rows. Empty when no brief exists. */
  list: () => api.get<IdeationList>("/v1/ideation"),
  /** Completed findings (prd_created | done) for the Completed tab. */
  completed: () => api.get<CompletedList>("/v1/ideation/completed"),
  /** One idea + the KG evidence behind it (the detail popup). Manual ideas
   *  have no theme, so they come back with an empty trail. */
  detail: (itemId: string) =>
    api.get<IdeationDetail>(`/v1/ideation/${encodeURIComponent(itemId)}/detail`),
  /** Move one item to a new status (in_progress | done | dismissed). */
  setStatus: (itemId: string, status: "in_progress" | "done" | "dismissed") =>
    api.patch<IdeationItem>(`/v1/ideation/${encodeURIComponent(itemId)}`, { status }),
  /** Create a user-added idea ("+ Add idea"). `tag` is an optional
   *  IdeationTag when the idea-type maps cleanly, else null. Returns the row. */
  create: (title: string, tag: IdeationTag | null = null) =>
    api.post<IdeationItem>("/v1/ideation", { title, tag }),
  /** Persist a new rank order (drag-to-rerank / Re-sequence). `orderedIds` is
   *  the full visible order; each item's rank becomes its position. */
  reorder: (orderedIds: string[]) =>
    api.post<IdeationList>("/v1/ideation/reorder", { ordered_ids: orderedIds }),
}

export type AskCitation = { source: string; evidence: string }
/** A Jira change the agent has PROPOSED and the user has not yet confirmed.
 *  Rides on the ask answer; the chat renders it as a confirm card. Nothing is
 *  written until the user acts on it — see jiraApi.applyChange. */
export type PendingJiraChange = {
  issue_key: string
  summary: string
  /** Jira field ids → values, already validated against the issue's editmeta. */
  fields: Record<string, unknown>
  /** Target workflow status name; status moves via a transition, not a field. */
  to_status: string
  comment: string
  /** Human "Field: before → after" lines, rendered verbatim on the card. */
  preview: string[]
}

/** A PRD-grounded rewrite of one ticket's description, PROPOSED and not yet
 *  applied. Rides on the ask answer; the chat renders it as a confirm card.
 *
 *  `target` says which surface owns the ticket, because a chat says "the
 *  ticket" for both: "sprntly" is one generated from a PRD (applied through
 *  ticketDataApi.saveDescription), "jira" is a real Jira issue (applied through
 *  jiraApi.applyChange). The backend resolved this by looking the ticket up —
 *  the card just routes the write. */
export type PendingTicketChange = {
  target: "sprntly" | "jira"
  ticket_key: string
  /** The ticket's current title, for the card header. */
  title: string
  /** The full proposed description, markdown. Replaces what is there. */
  description: string
  /** For `sprntly`, the EXACT criteria list to write — the ticket's current
   *  ones when the agent didn't rewrite them, because PUT /description replaces
   *  whatever it is sent and an omitted list would blank them. Always null for
   *  `jira`, which has no separate criteria field: there they are folded into
   *  `description` by the backend. */
  acceptance_criteria: string[] | null
  /** Human "what will change" lines, rendered verbatim on the card. */
  preview: string[]
}

export type AskResponse = {
  answer: string
  key_points: string[]
  citations: AskCitation[]
  confidence: number
  unanswered: string
  /** Skill id the backend attributed the answer to (e.g. voice-of-customer-report). */
  _skill?: string | null
  /** Present only when the Jira agent proposed a change awaiting confirmation. */
  _pending_jira_change?: PendingJiraChange
  /** Present only when the ticket-update agent proposed a rewrite awaiting
   *  confirmation. */
  _pending_ticket_change?: PendingTicketChange
}

/** What POST /v1/jira/write reports back. Each part is independent: a request
 *  can set fields, move status and comment, and any one of them can fail on its
 *  own, so the UI reports exactly what landed rather than one boolean. */
export type JiraWriteResult = {
  ok: boolean
  issue_key: string
  applied: string[]
  failed: string[]
  fields?: { ok: boolean; updated?: string[]; rejected?: string[]; error?: string }
  status?: { ok: boolean; status?: string; error?: string }
  comment?: { ok: boolean; comment_id?: string; error?: string }
}

export const jiraApi = {
  /** Apply a change the user CONFIRMED in the chat. This is the only call in
   *  the app that mutates Jira from a conversation — the agent can only
   *  propose, so this must be triggered by a person clicking Confirm. */
  applyChange: (change: {
    issue_key: string
    fields?: Record<string, unknown>
    to_status?: string
    comment?: string
  }) => api.post<JiraWriteResult>("/v1/jira/write", change),
}

/** One entry the chat composer's skill palette may offer.
 *
 *  This used to be the vendored BUILT-IN catalog. It is now the company's own
 *  uploaded skills: chat no longer selects a built-in method for a turn, so a
 *  palette of built-ins would have offered triggers nothing would honour.
 *  `category` is therefore always "Custom". The shape is unchanged so the
 *  composer, the Skills screen and the command palette need no new contract. */
export type SkillInfo = {
  id: string
  label: string
  trigger: string
  description: string
  category: string
}

/** POST /v1/ask is fire-and-forget: it returns an ask_id immediately and the
 *  answer keeps generating server-side (blur/remount-safe). The client polls
 *  askApi.get(id) until status leaves 'generating'. */
export type AskStartResponse = {
  ask_id: number
  status: "generating" | "ready" | "error" | "cancelled"
}

/** GET /v1/ask/{id} status + result. Once status === 'ready' the answer /
 *  key_points / citations / confidence / unanswered fields carry the SAME
 *  citation-stripped shape the old synchronous POST returned, so downstream
 *  rendering is unchanged. `error` is set when status === 'error'. */
export type AskStatusResponse = AskResponse & {
  status: "generating" | "ready" | "error" | "cancelled"
  error?: string | null
  /** The skill `qa_agent.route()` chose, or null when it answered directly with
   *  no skill. Backed by the `ask_jobs.routed_skill` column and returned at
   *  EVERY status — including `generating` — so it is known while the answer is
   *  still being written, not only once it lands.
   *
   *  Declared explicitly rather than left to the index signature below: these
   *  two are a real part of the contract (routes/ask.py excludes them from the
   *  payload passthrough so the columns stay authoritative), and typing them as
   *  `unknown` is what let them sit unread here since they shipped. */
  routed_skill?: string | null
  routed_skill_action?: string | null
  /** Extra qa_agent metadata passed through verbatim. */
  [extra: string]: unknown
}

export const askApi = {
  /** Kick off an Ask in the background. Returns immediately with an ask_id;
   *  poll askApi.get(ask_id) until status !== 'generating'. */
  start: (
    question: string,
    company: string = "asurion",
    opts?: { conversation_id?: number; pinned_skill?: string; prd_id?: number },
  ) =>
    api.post<AskStartResponse>("/v1/ask", {
      question,
      dataset: company,
      ...(opts?.conversation_id != null ? { conversation_id: opts.conversation_id } : {}),
      ...(opts?.pinned_skill != null ? { pinned_skill: opts.pinned_skill } : {}),
      // PRD-tab chat: ground the answer on the PRD open beside this chat
      // (+ its insight, evidence, tickets, prototype).
      ...(opts?.prd_id != null ? { prd_id: opts.prd_id } : {}),
    }),
  /** Read the status + result of an Ask job. */
  get: (askId: number) => api.get<AskStatusResponse>(`/v1/ask/${askId}`),
  /** SSE URL to token-stream an answer as it generates. The bearer rides as
   *  ?token= (EventSource can't set headers). Frames: an optional
   *  {kind:'replay',text} catch-up, {kind:'delta',text} carrying answer
   *  markdown, then a terminal {kind:'done'|'error'}. Progressive display
   *  only — askApi.get(id) stays the authoritative finished answer (and the
   *  only carrier of key_points / confidence / skill metadata). */
  streamUrl: (askId: number, token: string): string =>
    `${API_URL}/v1/ask/${askId}/stream?token=${encodeURIComponent(token)}` +
    (activeWorkspaceId
      ? `&workspace_id=${encodeURIComponent(activeWorkspaceId)}`
      : ""),
  /** Stop an in-flight Ask (the user hit Stop). Flips the job to `cancelled`
   *  so the worker aborts before the next LLM step and a late answer is
   *  discarded. Idempotent — returns the job's resulting status. */
  cancel: (askId: number) =>
    api.post<{ ask_id: number; status: "generating" | "ready" | "error" | "cancelled" }>(
      `/v1/ask/${askId}/cancel`,
    ),
  /** The skills the chat composer may offer — the company's own uploads.
   *  Company-scoped and authenticated (it serves one customer's library now,
   *  not a global catalog). */
  skills: () =>
    api.get<{ skills: SkillInfo[] }>("/v1/ask/skills"),
  /** Parse a binary document attachment (pptx/pdf/docx/…) to markdown so the
   *  composer can inline it as [Attached files] context. Server-side, no LLM. */
  extractFile: (file: File) => {
    const form = new FormData()
    form.append("file", file, file.name)
    return api.post<{ name: string; markdown: string }>("/v1/ask/extract-file", form)
  },
}

/** A user-uploaded custom skill (PRD 1854) — COMPANY-scoped: every workspace
 *  in the company shares one library. The MANAGEMENT view of the same skills
 *  `askApi.skills()` returns for the composer: this one carries uploader
 *  attribution, the file, and the id the delete/download routes take. */
export type CustomSkillInfo = {
  id: string
  slug: string
  trigger: string
  name: string
  description: string
  uploader_name: string
  created_at: string | null
  has_file: boolean
  /** A BUILT-IN skill's name was already taken when this skill was uploaded,
   *  so its trigger was disambiguated away from the name's plain slug
   *  (`/prd-author-2` for a skill named "PRD Author"). Nothing was replaced —
   *  the skill that owned the name keeps its own trigger and both are
   *  invocable. */
  name_conflict: boolean
  /** POST only: this upload REPLACED the company's existing skill of the same
   *  name (same id, same trigger, new content) rather than adding a new one.
   *  Absent on list items, where it would mean nothing. */
  replaced?: boolean
  /** This skill comes from a SYNCED GitHub folder, so the repository owns its
   *  text: it is re-imported on every sweep, the edit form is closed to it, and
   *  deleting it is refused (both 409 server-side). Stopping the folder's sync
   *  releases it and this goes false. */
  synced?: boolean
}

/** One custom skill WITH its method text — GET /v1/skills/{id}, the source the
 *  edit form pre-fills from. Split from the list because a method can run to
 *  50,000 characters and the library grid needs none of it.
 *
 *  `modules`/`references` are FILENAMES only: editing swaps the main method
 *  and leaves a .zip's supporting files attached untouched, so the form
 *  reports them rather than editing them. `attached_chars` is what those files
 *  contribute toward the 50,000-character cap, which is measured over the
 *  whole parsed skill — without it a client-side check would be wrong for
 *  every skill uploaded as an archive. */
export type CustomSkillDetail = CustomSkillInfo & {
  method: string
  modules: string[]
  references: string[]
  attached_chars: number
}

/** The PATCH result: the edited skill, plus the id of the company's OTHER
 *  skill this edit absorbed (a rename onto a name they already used replaces
 *  that skill). `null` means nothing else changed. */
export type CustomSkillEditResult = CustomSkillDetail & {
  replaced_skill_id: string | null
}

/** A skill folder inside a multi-skill archive that could NOT be imported.
 *  `path` is the folder it sat in ("" for the archive root), `name` whatever
 *  name we could derive, and `reason` is written for a person to act on
 *  (a missing `description:`, an over-cap method). */
export type SkippedSkill = {
  path: string
  name: string
  reason: string
}

/** POST /v1/skills when the uploaded .zip held SEVERAL skills — one folder per
 *  skill, the layout a zipped `skills/` directory has. Each one became its own
 *  row with its own trigger, named from its own SKILL.md frontmatter rather
 *  than from the form (which can only name one), so the answer is a LIST
 *  instead of the single object. Folders that couldn't be imported are in
 *  `skipped` with a reason and cost the others nothing; an archive that
 *  yielded no skills at all fails the request instead. */
export type MultiSkillUploadResult = {
  skills: CustomSkillInfo[]
  skipped: SkippedSkill[]
}

/** What an upload answers: one skill, or the multi-skill archive result. */
export type SkillUploadResult = CustomSkillInfo | MultiSkillUploadResult

/** One skill found in a connected GitHub repo, as the picker needs it.
 *
 *  `status` is the server's verdict, computed against the company's own
 *  library with the same rules the write path uses — never guessed here:
 *    - `new`      → imports as a new skill at `trigger_preview`
 *    - `replaces` → the company already has this name; importing updates that
 *                   skill in place and keeps its trigger
 *    - `invalid`  → cannot be imported; `reason` says why (no description, a
 *                   file over GitHub's 1 MB text ceiling, over the 50k cap) */
export type GithubSkillPreview = {
  path: string
  name: string
  description: string
  slug_preview: string
  trigger_preview: string
  file_count: number
  char_count: number
  status: "new" | "replaces" | "invalid"
  reason: string
}

/** GET /v1/skills/github/discover — read-only; writes nothing.
 *  `truncated` + `notes` report anything the repo was too big to show. */
export type GithubSkillDiscovery = {
  repo: string
  ref: string
  commit_sha: string
  truncated: boolean
  notes: string[]
  skills: GithubSkillPreview[]
}

/** POST /v1/skills/github/import — the same per-skill payloads an upload
 *  returns (each with `replaced`), plus what it couldn't import. */
export type GithubSkillImportResult = {
  imported: CustomSkillInfo[]
  skipped: SkippedSkill[]
  commit_sha: string
  ref: string
  /** The folder was registered to keep syncing, so the imported skills are
   *  read-only and the folder's later contents will arrive on their own. */
  synced: boolean
}

/** One folder a company keeps synced — GET /v1/skills/sources.
 *
 *  `ref` empty means "whatever the repo's default branch is", deliberately not
 *  resolved to a name: the folder follows the default branch rather than being
 *  pinned to whatever it was called at import time. `last_error` is the last
 *  failure verbatim and is cleared by the next clean sync, so a non-empty value
 *  is a live problem rather than history. */
export type SyncedFolder = {
  id: string
  repo: string
  ref: string
  path: string
  active: boolean
  last_synced_at: string | null
  last_commit_sha: string
  last_error: string
}

/** POST /v1/skills/sources/{id}/sync — what one forced re-read did. `error`
 *  non-empty means the sync itself failed; the call still answers 200, because
 *  a GitHub outage is something the panel shows, not an exception it can't
 *  explain. */
export type SyncedFolderSyncResult = {
  source: SyncedFolder
  imported: number
  replaced: number
  skipped: string[]
  error: string
}

/** Discriminates the two upload bodies by shape (the multi one has no `id`).
 *  Exported because every caller has to branch on it. */
export function isMultiSkillUpload(
  result: SkillUploadResult,
): result is MultiSkillUploadResult {
  return Array.isArray((result as MultiSkillUploadResult).skills)
}

export const skillsApi = {
  /** The company's custom skills, newest first (metadata only). */
  list: () => api.get<{ skills: CustomSkillInfo[] }>("/v1/skills"),
  /** One skill with its method text (the edit form's source). 404s on a
   *  foreign or unknown id, indistinguishably. */
  get: (id: string) =>
    api.get<CustomSkillDetail>(`/v1/skills/${encodeURIComponent(id)}`),
  /** Edit a skill's name, description, and method in place — same row, same
   *  id. All three are always sent: the form owns the complete set it
   *  rendered, so a partial write could revert a field.
   *
   *  Two consequences the caller has to handle. RENAMING re-derives the
   *  trigger (the response's `slug`/`trigger` are authoritative — a name
   *  shared with a built-in lands on the `-2` series, and the old `/slug`
   *  stops working). Renaming onto one of the company's OWN skill names
   *  REPLACES that skill: it is deleted and its id comes back as
   *  `replaced_skill_id`, so the caller must drop that card. That is
   *  destructive — confirm it with the user before calling. */
  update: (
    id: string,
    patch: { name: string; description: string; method: string },
  ) =>
    api.patch<CustomSkillEditResult>(
      `/v1/skills/${encodeURIComponent(id)}`,
      patch,
    ),
  /** Upload a .md/.zip skill file (≤ 20 MB) with its name + description.
   *  Server is the authoritative validator (422/400/413 with readable
   *  `detail`); the modal mirrors the cheap checks client-side. A name shared
   *  with a BUILT-IN skill is accepted (the 201's `trigger`/`name_conflict`
   *  report the disambiguated trigger); a name already used by one of the
   *  company's OWN custom skills REPLACES that skill in place — same id, same
   *  trigger, new content — and the 201 comes back with `replaced: true`.
   *
   *  A .zip holding SEVERAL SKILL.md files imports as several skills and
   *  answers `{skills, skipped}` instead of the single object — branch with
   *  `isMultiSkillUpload`. The name and description sent here apply to a
   *  single skill only; a multi-skill archive names each skill from its own
   *  SKILL.md, and the per-skill collision rules (replace-in-place, the `-2`
   *  built-in series) apply to each of them independently. */
  upload: (file: File, name: string, description: string) => {
    const form = new FormData()
    form.append("file", file, file.name)
    form.append("name", name)
    form.append("description", description)
    return api.post<SkillUploadResult>("/v1/skills", form)
  },
  /** The skills a CONNECTED GitHub repo holds, at `ref` (default branch when
   *  omitted), optionally scoped to one folder. Read-only — it writes nothing,
   *  so it is safe to call as the user types a branch.
   *
   *  The repo's installation is resolved server-side from the caller's company;
   *  a repo this company hasn't connected 404s (never 403 — that would confirm
   *  someone else connected it). A GitHub outage is a 502, a missing branch a
   *  404, both with a readable `detail`. */
  discoverGithub: (repo: string, opts?: { ref?: string; path?: string }) => {
    const params = new URLSearchParams({ repo })
    if (opts?.ref) params.set("ref", opts.ref)
    if (opts?.path) params.set("path", opts.path)
    return api.get<GithubSkillDiscovery>(`/v1/skills/github/discover?${params}`)
  },
  /** Import the selected skills from that repo. `paths` FILTER the server's
   *  own re-run of discovery — they are never fetch targets, so a path that
   *  isn't a skill in that repo imports nothing rather than reading a file.
   *  Each imported skill follows the upload rules: a name the company already
   *  used replaces that skill in place, a built-in's name takes the next free
   *  trigger. Per-skill failures come back in `skipped`.
   *
   *  `sync: true` also REGISTERS the folder: the half-hourly sweep re-imports
   *  whatever markdown it holds from then on, so the ticked list stops being
   *  the unit — a file added to that folder later arrives on its own, whether
   *  or not anyone would have ticked it. The skills it produces become
   *  read-only (edit and delete both 409). Requires a non-empty `path`; the
   *  server 422s a synced repo ROOT, since a whole repository's markdown is not
   *  a skill library. */
  importGithub: (body: {
    repo: string
    ref?: string
    path?: string
    paths: string[]
    sync?: boolean
  }) => api.post<GithubSkillImportResult>("/v1/skills/github/import", body),
  /** The company's synced folders, active and stopped alike — a stopped folder
   *  is still where its skills came from. */
  listSources: () => api.get<{ sources: SyncedFolder[] }>("/v1/skills/sources"),
  /** Re-read one folder now instead of waiting for the sweep. Forced, so it
   *  re-imports even when the commit hasn't moved — the button exists for the
   *  case where the library visibly doesn't match the folder. */
  syncSource: (id: string) =>
    api.post<SyncedFolderSyncResult>(
      `/v1/skills/sources/${encodeURIComponent(id)}/sync`,
      {},
    ),
  /** Stop syncing a folder. Its skills STAY in the library and become editable
   *  again — `released` is how many were handed back. */
  stopSyncingSource: (id: string) =>
    api.delete<{ stopped: true; id: string; released: number }>(
      `/v1/skills/sources/${encodeURIComponent(id)}`,
    ),
  /** Fresh signed view/download URLs for the ORIGINAL uploaded file. */
  fileLinks: (id: string) =>
    api.get<{ name: string; view_url: string; download_url: string }>(
      `/v1/skills/${encodeURIComponent(id)}/file`,
    ),
  /** Delete a skill for the WHOLE company (row + original file). 404s on a
   *  foreign or unknown id. */
  remove: (id: string) =>
    api.delete<{ deleted: true; id: string }>(
      `/v1/skills/${encodeURIComponent(id)}`,
    ),
}

/** The action envelope from POST /v1/chat/intent — the backend's history-aware
 *  verdict on what ONE chat message asks for. Shaped like a one-iteration
 *  tool-use turn: `intent` names the executor, the other fields are its
 *  arguments, synthesized from the whole conversation (not the surface words
 *  of the newest message). `prd_id`/`prd_title` echo the resolved TARGET (the
 *  tab-sent PRD, or the one the conversation is bound to) so the reducer acts
 *  on the same document the decision was grounded on. */
export type ChatIntentEnvelope = {
  intent:
    | "answer"
    | "generate_prd"
    | "edit_prd"
    | "generate_tickets"
    | "generate_prototype"
    | "open_artifact"
  confidence: number
  /** generate_prd: self-contained task brief composed from the thread. */
  task: string | null
  /** edit_prd: the change to apply, self-contained. */
  instruction: string | null
  /** open_artifact: which existing artifact kind to bring up. */
  artifact_type: OpenArtifactKind | null
  /** open_artifact: the subject the user named the document by. */
  artifact_query: string | null
  reason: string
  /** "llm" | "fallback" | "low_confidence" | "no_target_prd" | "no_instruction"
   *  | "no_artifact_query" */
  source: string
  prd_id: number | null
  prd_title: string | null
  /** open_artifact ONLY — the backend's lookup of `artifact_query` against this
   *  company's artifact library. Absent for every other intent. */
  open?: OpenArtifactResult
}

/** Artifact kinds an OPEN request can name. Both have an existing right-panel
 *  view in the chat; prototypes/reports deliberately do not appear here. */
export type OpenArtifactKind = "prd" | "evidence"

/** One artifact the user's phrase could have meant — carrying the IDS needed to
 *  open it, so a disambiguation chip is a real action and never a re-sent
 *  message. `prd_id` for the PRD panel; `brief_id` + `insight_index` for the
 *  Evidence panel, which is scoped by the insight rather than by an evidence
 *  row id (matching ChatScreen's `kind: "evidence"` open). */
export type OpenArtifactCandidate = {
  type: OpenArtifactKind
  id: number
  title: string
  status: string
  prd_id: number | null
  brief_id: number | null
  insight_index: number | null
  /** Whether `insight_index` names a REAL brief finding rather than the storage
   *  sentinel a chat/ideation/uploaded PRD carries (always `0`). Only pass the
   *  pair to the panel as `meta` when this is true — the panel's Evidence tab
   *  loads by (briefId, insightIndex), so a sentinel would render the brief's
   *  first finding underneath an unrelated document. */
  brief_anchored: boolean
  week_label: string | null
}

/** The 0/1/many verdict for an open request. All three outcomes are part of the
 *  contract: `not_found` must open NOTHING and say so (it never degrades into
 *  generating a new document — see backend/app/artifact_open.py), `ambiguous`
 *  must ask, `resolved` opens directly. */
export type OpenArtifactResult = {
  /** `unsupported_type` = the user named a real artifact kind this panel cannot
   *  show (a prototype, a report). It is NOT coerced into a PRD — say where the
   *  thing actually lives instead. */
  status: "resolved" | "ambiguous" | "not_found" | "unsupported_type"
  /** What the user NAMED — may be a kind outside OpenArtifactKind when the
   *  status is `unsupported_type`. */
  artifact_type: OpenArtifactKind | string
  query: string
  artifact: OpenArtifactCandidate | null
  candidates: OpenArtifactCandidate[]
}

export const chatIntentApi = {
  /** Decide the action for one chat message (flag: chat_intent_envelope).
   *  Backend loads the conversation history itself; the client only ships the
   *  light tab context. Fail-open BY THE CALLER: any network/HTTP failure →
   *  fall back to the legacy regex ladder, never block the send. */
  resolve: (
    message: string,
    opts?: {
      conversationId?: number | null
      prdId?: number | null
      hasAttachments?: boolean
    },
  ) =>
    api.post<ChatIntentEnvelope>("/v1/chat/intent", {
      message,
      ...(opts?.conversationId != null ? { conversation_id: opts.conversationId } : {}),
      ...(opts?.prdId != null ? { prd_id: opts.prdId } : {}),
      ...(opts?.hasAttachments ? { has_attachments: true } : {}),
    }),
}

/** Next-prompt suggestions for a chat thread — `POST /v1/chat/suggestions`.
 *
 *  Fetched AFTER an answer has rendered, never before or during: it is a
 *  separate round trip so it cannot delay, block or fail the answer stream.
 *  An empty array is the ORDINARY result, not a failure — the backend abstains
 *  whenever the conversation doesn't point at a specific next step (see
 *  app/chat_suggestions.py). Callers must treat `[]` and a rejected promise
 *  identically: render nothing. */
export const chatSuggestionsApi = {
  next: (conversationId: number, opts?: { prdId?: number | null }) =>
    api.post<{ suggestions: string[] }>("/v1/chat/suggestions", {
      conversation_id: conversationId,
      ...(opts?.prdId != null ? { prd_id: opts.prdId } : {}),
    }),
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
  /** Opaque, unguessable external identifier — returned by the GET routes'
   *  `select("*")` (prds.public_id). What `useArtifactUrlSync` puts in the
   *  `?prd=` URL going forward, instead of the sequential `id`. */
  public_id?: string
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
  /** How this PRD was created — returned by the GET routes' `select("*")`.
   *  Only `'brief'` PRDs carry their own research Evidence (keyed at
   *  `(brief_id, insight_index)`); `'ideation'` and `'upload'` PRDs have none.
   *  Absent on legacy rows — treat missing as `'brief'` (the DB default). */
  source?: "brief" | "ideation" | "backlog" | "upload" | "chat"
  /** The originating chat question, when this PRD was generated via the
   *  "generate a PRD for X" chat command (routes/prd.py's generate-from-task).
   *  Null/absent for every other generation path (brief insight, ideation,
   *  import) and for rows generated before this column existed. */
  question?: string | null
  /** The originating ask_jobs row, when one exists. Currently always null in
   *  practice — the chat-task PRD command runs outside the ask pipeline — kept
   *  for shape parity with db/reports.py's identical column. */
  ask_id?: number | null
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
  /** Set when status === "failed": the prior run's error. The backend no
   *  longer silently re-generates a failed insight on open — the client shows
   *  this and offers an explicit retry (generate with force=true). */
  error?: string | null
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
  /** The originating chat question — same shape/contract as PrdRecord.question
   *  (see there). Set only on the chat-task Evidence path. */
  question?: string | null
  /** Kept for shape parity with PrdRecord.ask_id — currently always null. */
  ask_id?: number | null
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
  /** SSE URL to token-stream an evidence doc's generation as it's written.
   *  Mirrors prdApi.streamUrl: the bearer rides as ?token= (EventSource can't
   *  set headers). Frames: {kind:'delta',text} then a terminal
   *  {kind:'done'|'error'}. Progressive display only — evidenceApi.get(id)
   *  stays the authoritative finished doc. */
  streamUrl: (evidenceId: number, token: string): string =>
    `${API_URL}/v1/evidence/${evidenceId}/stream?token=${encodeURIComponent(token)}` +
    (activeWorkspaceId
      ? `&workspace_id=${encodeURIComponent(activeWorkspaceId)}`
      : ""),
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

export const signupApi = {
  /**
   * PUBLIC (pre-auth): does an account already exist for this email? Called
   * from sign-up step 1 so returning users are stopped with "already
   * registered — sign in" before filling the about-you step. The backend
   * fails open (exists: false) — the end-of-signup already_registered check
   * remains the backstop.
   */
  emailExists: (email: string) =>
    api.post<{ exists: boolean }>("/v1/auth/email-exists", { email }),
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
  /**
   * Names the default workspace (renames the company's default `workspaces`
   * row — never creates a second — grants the caller workspace-admin, and
   * binds the company dataset). No longer an onboarding step since v6; kept
   * for Settings-side callers.
   */
  createWorkspace: (
    name: string,
    fields: {
      team_scope?: string | null
      team_strategy?: string | null
      team_roadmap?: string | null
      sizing_methodology?: string | null
      additional_context?: string | null
    } = {},
  ) =>
    api.post<{ id: string; name: string; slug: string; is_default: boolean }>(
      "/v1/onboarding/workspace",
      { name, ...fields },
    ),
  /**
   * Step 9 "Here's what we learned": draft the business-context prose from
   * everything collected (company/product/metrics rows + website analysis +
   * connected sources). Synchronous — a spinner-length call; re-request on
   * remount if lost. Fully editable client-side before accept.
   */
  draftBusinessContext: () =>
    api.post<{ draft: string }>("/v1/onboarding/business-context-draft", {}),
  /**
   * Define-metrics sub-flow: AI-draft a plain-English definition + analytics
   * event mapping (+ best-effort current value) for each picked metric.
   */
  draftMetricDefinitions: (metrics: string[]) =>
    api.post<{
      definitions: {
        metric: string
        definition: string
        mapping: string
        baseline: string | null
      }[]
    }>("/v1/onboarding/metric-definitions", { metrics }),
  /**
   * Signal that onboarding is complete so the backend fires the one-time
   * "welcome to Sprntly, your workspace is ready" email. Company/user are
   * taken from the JWT (Depends(require_company)); de-duplicated server-side,
   * so a double call never double-sends. Best-effort — callers fire-and-forget
   * and must never block entering the app on it.
   */
  complete: () =>
    api.post<{ ok: true; sent: boolean; reason?: string }>(
      "/v1/onboarding/complete",
      {},
    ),
}

// ── Workspaces (multi-workspace 2026-07) ────────────────────────────────────

/** One workspace as the switcher sees it: the caller's effective role plus
 *  the dataset slug every dataset-keyed call feeds on. */
export type WorkspaceSummary = {
  id: string
  name: string
  slug: string
  is_default: boolean
  product_id: string | null
  dataset: string | null
  role: "admin" | "member" | "viewer"
  // Workspace-owned "Your workspace" fields (2026-07-22 — moved off the
  // companies row). Present on the default workspace; optional on the summary.
  team_scope?: string | null
  team_strategy?: string | null
  team_roadmap?: string | null
  sizing_methodology?: string | null
  additional_context?: string | null
}

/** Partial update for PATCH /v1/workspaces/{id} — any subset of the name +
 *  the five workspace-owned fields. */
export type WorkspacePatch = {
  name?: string
  team_scope?: string | null
  team_strategy?: string | null
  team_roadmap?: string | null
  sizing_methodology?: string | null
  additional_context?: string | null
}

export type WorkspaceMemberRecord = {
  id: string | null
  user_id: string
  role: "admin" | "member" | "viewer"
  created_at: string | null
  display_name: string | null
  email: string | null
  avatar_url: string | null
}

export const workspacesApi = {
  // org_role: the caller's COMPANY-level role (owner/admin/member/viewer) —
  // workspace creation is org-admin gated, unlike the per-workspace `role`
  // each summary row carries.
  list: () =>
    api.get<{ workspaces: WorkspaceSummary[]; org_role?: string | null }>(
      "/v1/workspaces",
    ),
  create: (name: string) =>
    api.post<WorkspaceSummary>("/v1/workspaces", { name }),
  /** PATCH any subset of the name + the five workspace-owned fields. */
  update: (id: string, patch: WorkspacePatch) =>
    api.patch<WorkspaceSummary>(
      `/v1/workspaces/${encodeURIComponent(id)}`,
      patch,
    ),
  rename: (id: string, name: string) =>
    workspacesApi.update(id, { name }),
  remove: (id: string) =>
    api.delete<void>(`/v1/workspaces/${encodeURIComponent(id)}`),
  members: (id: string) =>
    api.get<{ members: WorkspaceMemberRecord[] }>(
      `/v1/team/workspaces/${encodeURIComponent(id)}/members`,
    ),
  setMemberRole: (id: string, userId: string, role: "admin" | "member" | "viewer") =>
    api.put<{ user_id: string; role: string }>(
      `/v1/team/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`,
      { role },
    ),
  removeMember: (id: string, userId: string) =>
    api.delete<void>(
      `/v1/team/workspaces/${encodeURIComponent(id)}/members/${encodeURIComponent(userId)}`,
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
  uploadFiles: (slug: string, files: File[], category = "") => {
    const form = new FormData()
    for (const f of files) form.append("files", f, f.name)
    if (category) form.append("category", category)
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

/** `GET /v1/company/business-context/refresh-status` — polled after
 *  `refresh()` kicks off the async job. status stays 'idle' for a company
 *  that has never triggered a refresh. */
export type BusinessContextRefreshStatus = {
  status: "idle" | "generating" | "done" | "error"
  error: string | null
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
  /** POST refresh (admin-only) — kicks off the Business Context agent as a
   *  background job and returns immediately (`status: "generating"`, or
   *  `"done"`/`"error"` under the test harness's synchronous inline path).
   *  `already_running: true` means a refresh was already live for this
   *  tenant and this call was a no-op, not a new run. Poll
   *  `refreshStatus()` (see lib/runBusinessContextRefresh.ts) for
   *  completion — the doc itself only updates once status leaves
   *  'generating'. */
  refresh: () =>
    api.post<
      BusinessContextRefreshStatus & { ok: true; already_running?: boolean }
    >("/v1/company/business-context/refresh"),
  /** GET refresh-status (any member) — the current async refresh job's
   *  state for this tenant. */
  refreshStatus: () =>
    api.get<BusinessContextRefreshStatus>(
      "/v1/company/business-context/refresh-status",
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
 * stores the doc + its extracted text against the company so the Top Insights brief
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

// ---- artifact format templates ----------------------------------------------
//
// The company's own PRD / ticket / engineering-spec FORMS. Distinct from
// `templatesApi` directly above, which is the "what good looks like" exemplar
// library — additive prose the prd-author skill reads as voice guidance. These
// are a GOVERNING skeleton: at most one active per artifact type, and every
// document of that type gets written into it. The two coexist.
//
// Mirrors backend/app/routes/artifact_templates.py. `X-Workspace-Id` is
// injected centrally in this file's `request()` — never add it per call.

/** Which generator a format governs. `impl_spec` is the implementation-spec
 *  skill's Part B (the markdown the ticket generator consumes). */
export type ArtifactTemplateType = "prd" | "tickets" | "impl_spec"

/** Where a format is in the checking pipeline.
 *    pending      — queued, nothing has checked it yet
 *    compiling    — being checked right now
 *    ready        — checked clean; can be activated
 *    needs_review — checked, but something in it has no home; see compile_notes
 *    failed       — could not be read at all */
export type CompileStatus =
  | "pending"
  | "compiling"
  | "ready"
  | "needs_review"
  | "failed"

/** One problem found while checking a format. NEVER rendered raw — `code` keys
 *  the translation table in lib/compileNotes.ts, because the backend's own
 *  wording names CSS classes (`ul.ev`) that must never reach a screen.
 *  `message` is plain-language and is the fallback for an unknown code. */
export type CompileNote = {
  code: string
  message: string
}

/** How a section of the customer's format is written. Closed set, validated
 *  server-side at compile time so one concept never gets two labels. */
export type SectionForm = "prose" | "bullets" | "table" | "stories"

/** One row of "how we mapped your format": the customer's section name, what
 *  Sprntly writes into it, and the shape it takes. */
export type SectionMapEntry = {
  id: string
  /** The Sprntly concept that lands here (plain words). */
  house: string
  /** The customer's own name for the section. */
  customer: string
  order: number
  form: SectionForm
}

export type SectionMap = {
  sections: SectionMapEntry[]
  /** Sprntly elements the format has no section for — placed where they fit. */
  unmapped_house: string[]
  /** Sections that are the customer's alone, filled from their evidence. */
  extra_sections: string[]
}

/** One library row. Carries everything the list screen renders, so no row ever
 *  needs a detail fetch to display. */
export type ArtifactTemplate = {
  id: string
  name: string
  artifact_type: ArtifactTemplateType
  uploader_name: string
  created_at: string | null
  updated_at: string | null
  compile_status: CompileStatus
  is_active: boolean
  /** Characters in the uploaded markdown. */
  source_chars: number
  /** The FIRST compile note's message, or null. */
  compile_summary: string | null
  /** How many notes there are — the "See all 3" affordance needs the count,
   *  and it cannot be derived from `compile_summary`. */
  compile_note_count: number
}

/** A row PLUS its uploaded source and full mapping — the edit form's source. */
export type ArtifactTemplateDetail = ArtifactTemplate & {
  source_md: string
  content_hash: string
  compile_notes: CompileNote[]
  section_map: SectionMap
}

/** What the preview modal renders. `format` is an EXPLICIT discriminator —
 *  never sniff a leading `<`, which is guesswork on model output. `body` is ""
 *  until a compiler has run, which renders as "we couldn't build a preview
 *  from this format yet", not as an error. */
export type ArtifactTemplatePreview = {
  id: string
  name: string
  artifact_type: ArtifactTemplateType
  compile_status: CompileStatus
  compile_notes: CompileNote[]
  format: "html" | "markdown"
  body: string
  section_map: SectionMap
}

/** Which generators actually honour a custom format yet — TOP-LEVEL on the list
 *  response, not per row, because the common state is zero rows and the screen
 *  still renders all three group headers. Never hardcode this client-side. */
export type GenerationEnabled = Record<ArtifactTemplateType, boolean>

export type ArtifactTemplateList = {
  templates: ArtifactTemplate[]
  generation_enabled: GenerationEnabled
}

export type ArtifactTemplateDeleteResult = {
  deleted: true
  id: string
  artifact_type: ArtifactTemplateType
  /** True when this delete dropped the company back to the built-in format. */
  fell_back_to_builtin: boolean
}

export const artifactTemplatesApi = {
  /** The company's format library, newest first, optionally one type only.
   *  Poll THIS (not each row) while anything is compiling — one request covers
   *  every in-flight row. */
  list: (type?: ArtifactTemplateType) => {
    const qs = type ? `?type=${encodeURIComponent(type)}` : ""
    return api.get<ArtifactTemplateList>(`/v1/artifact-templates${qs}`)
  },
  /** One format WITH its markdown source and mapping. 404s on a foreign or
   *  unknown id, indistinguishably. */
  get: (id: string) =>
    api.get<ArtifactTemplateDetail>(
      `/v1/artifact-templates/${encodeURIComponent(id)}`,
    ),
  /** Add a format from PASTED markdown. The server is the authoritative
   *  validator (422 name/type, 400 empty, 413 over 50,000 characters); the
   *  modal mirrors the cheap checks. Names are free text and are NOT
   *  deconflicted — two formats may share a name and neither replaces the
   *  other. */
  create: (body: {
    name: string
    artifact_type: ArtifactTemplateType
    source_md: string
  }) => api.post<ArtifactTemplateDetail>("/v1/artifact-templates", body),
  /** Add a format from an uploaded `.md` (≤ 2 MB). Same route as `create`,
   *  multipart instead of JSON; the name defaults to the filename when
   *  omitted. */
  upload: (file: File, artifactType: ArtifactTemplateType, name?: string) => {
    const form = new FormData()
    form.append("file", file, file.name)
    form.append("artifact_type", artifactType)
    if (name) form.append("name", name)
    return api.post<ArtifactTemplateDetail>("/v1/artifact-templates", form)
  },
  /** Rename a format and/or replace its markdown. Send only what changed — an
   *  omitted field is left alone, so the rename modal never blanks a source it
   *  didn't render. Replacing the source re-queues the check; an ACTIVE format
   *  stays active and keeps serving its last good skeleton meanwhile. */
  update: (id: string, patch: { name?: string; source_md?: string }) =>
    api.patch<ArtifactTemplateDetail>(
      `/v1/artifact-templates/${encodeURIComponent(id)}`,
      patch,
    ),
  /** Queue a (re)check of this format. Answers the preview shape with the row's
   *  new status, so the caller can restart polling from the response. */
  compile: (id: string) =>
    api.post<ArtifactTemplatePreview>(
      `/v1/artifact-templates/${encodeURIComponent(id)}/compile`,
    ),
  /** The compiled skeleton + how we mapped the format onto it. Available at
   *  every status — it is the diagnostic for a format that didn't map
   *  cleanly. */
  preview: (id: string) =>
    api.get<ArtifactTemplatePreview>(
      `/v1/artifact-templates/${encodeURIComponent(id)}/preview`,
    ),
  /** Make this THE format for its type, company-wide. Admin only (403
   *  otherwise). 409 with a `{message, code, notes}` detail when the format
   *  hasn't compiled clean — translate those notes, never print them. */
  activate: (id: string) =>
    api.post<ArtifactTemplateDetail>(
      `/v1/artifact-templates/${encodeURIComponent(id)}/activate`,
    ),
  /** Go back to Sprntly's built-in format for this type. The format stays in
   *  the library. Admin only (403 otherwise); idempotent. */
  deactivate: (id: string) =>
    api.post<ArtifactTemplateDetail>(
      `/v1/artifact-templates/${encodeURIComponent(id)}/deactivate`,
    ),
  /** Remove a format for the whole company. Deleting the ACTIVE one is admin
   *  only (403) — it falls back to the built-in, which is what deactivating
   *  does, and that is admin-gated. `fell_back_to_builtin` says whether it did,
   *  so the toast can name it. */
  remove: (id: string) =>
    api.delete<ArtifactTemplateDeleteResult>(
      `/v1/artifact-templates/${encodeURIComponent(id)}`,
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
  // Onboarding workspace-step upload-or-type blocks.
  | "team_strategy"
  | "team_roadmap"
  | "decision_process"
  | "additional_context"
  // The workspace step's "Attach a previous sizing doc" affordance.
  | "sizing_doc"

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

// ---- LLM context import -----------------------------------------------------

/** The onboarding fields an import could prefill. Every key is optional: an
 *  export only carries what the user actually told their assistant, and the
 *  backend deliberately omits anything it could not read rather than filling a
 *  gap with a guess (see backend/app/llm_context.py). */
export type LlmContextFields = {
  company_name?: string
  company_website?: string
  mission?: string
  strategy?: string
  portfolio?: string
  planning_cycle?: string
  product_name?: string
  product_website?: string
  surfaces?: string[]
  monetization?: string
  users_description?: string
  competitors?: string[]
  metrics?: string[]
  prioritization_framework?: string
  /** The workspace step's mandatory name (contract v2). */
  team_name?: string
  team_scope?: string
  sizing_methodology?: string
  notes?: string
}

export type LlmContextImportResponse = {
  /** False when the read produced no fields — the caller must surface `note`
   *  rather than claiming a successful import. On the UPLOAD response this is
   *  always false: the LLM extraction is the only reader and it has not run
   *  yet, so a false here with a live `job_id` is not a failed import. On a
   *  job result it is the verdict. */
  ok: boolean
  fields: LlmContextFields
  /** Kept for shape compatibility; always empty since the v3 prompt. The whole
   *  .md is filed as a document source, so nothing needs a second home. */
  unmapped: Record<string, string>
  format_version: string | null
  note: string | null
  /** The background LLM extraction kicked off by this upload, or null when it
   *  couldn't start — in which case the upload prefills nothing at all, since
   *  this pass is the only read of the file. */
  job_id?: number | null
  /** True when the raw .md was actually filed as a document source AND handed
   *  to the knowledge-graph ingest. Distinct from `ok` (whether the extraction
   *  read structured fields): a caller that only cares about grounding the
   *  agents — e.g. the Business Context import, which never prefills — keys its
   *  success message off this, not `ok`. Absent on background-job results. */
  filed?: boolean
}

export type LlmContextJobStatus = {
  status: "generating" | "ready" | "error"
  /** Populated once `status === "ready"` — the same shape as the upload
   *  response, so one apply path handles both reads. */
  result: LlmContextImportResponse | null
  error: string | null
}

export const llmContextApi = {
  /** The prompt the user pastes into Claude / ChatGPT / Gemini. Fetched rather
   *  than duplicated in the UI so the copy can never drift from what the
   *  backend extraction expects to read back.
   *
   *  Pass what you already know about the company and the backend writes it
   *  into the prompt's confirmed-values block, so the assistant starts with the
   *  entity locked instead of inferring it. Onboarding always has both by this
   *  point — the company step runs first. Omitting them serves the prompt with
   *  that block empty for the user to fill in by hand. */
  prompt: (about?: { companyName?: string; companyWebsite?: string }) => {
    const q = new URLSearchParams()
    if (about?.companyName) q.set("company_name", about.companyName)
    if (about?.companyWebsite) q.set("company_website", about.companyWebsite)
    const qs = q.toString()
    return api.get<{ prompt: string; format_version: string }>(
      `/v1/connectors/llm-context/prompt${qs ? `?${qs}` : ""}`,
    )
  },
  /** Upload the .md the assistant produced (multipart). */
  importFile: (file: File) => {
    const form = new FormData()
    form.append("file", file, file.name)
    return api.post<LlmContextImportResponse>(
      "/v1/connectors/llm-context/import",
      form,
    )
  },
  /** Poll the background LLM extraction the upload kicked off. */
  importStatus: (jobId: number) =>
    api.get<LlmContextJobStatus>(
      `/v1/connectors/llm-context/import/${jobId}`,
    ),
}

// ---- sources ----------------------------------------------------------------

export type SourceFile = {
  filename: string
  kind: string
  size_bytes: number
  md_chars: number
  added_at: string
  /** Connector category the file was uploaded under. "" = legacy/uncategorized
   *  (uploaded before per-category attribution existed). */
  category?: string
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
  /** What this provider IS — one type for now, list-shaped for the future
   *  (e.g. ["task-management"]). Mirrors
   *  backend/app/connectors/catalog.py; features derive availability from
   *  these rather than hardcoding provider ids. */
  types?: string[]
  google_email: string | null
  account_label?: string | null
  scopes: string
  config: {
    // Google Drive
    dataset?: string
    folder_id?: string
    folder_name?: string
    // Google Drive — files picked via the Google Picker (drive.file scope).
    // An entry may be a FOLDER: only Drive metadata says which, so the shape is
    // identical either way.
    files?: GoogleDrivePickedFile[]
    // Written by the sync: folder id -> the SUBTREE (sub-folders and files,
    // each parented by parentId) that folder expanded to on the last run.
    // Present (possibly empty) for every picked entry that turned out to be a
    // folder, which is also how the UI knows an entry IS one.
    folder_contents?: Record<string, GoogleDriveTreeNode[]>
    // Slack — brief-delivery target…
    target_type?: "channel" | "dm"
    channel_id?: string
    channel_name?: string
    // …and the corpus-sync pull-channel selection (empty/absent = every
    // channel the bot is a member of). The selection is COMPANY-wide.
    sync_channel_ids?: string[]
    sync_channel_names?: Record<string, string>
    // True when this row is the company's SHARED Slack connection surfaced
    // to a member who has no install of their own (voice-of-customer view,
    // sanitized server-side). Delivery UIs must ignore such rows — the
    // member has no personal delivery target until they connect their own.
    company_connection?: boolean
    // Confluence — the Atlassian site id, cached at connect, plus the
    // space selection the KG ingest pulls from. Empty/absent = every space
    // the connected account can read. COMPANY-wide, admin-only to change.
    cloud_id?: string
    sync_space_ids?: string[]
    sync_space_keys?: Record<string, string>
    // Zoom — which hosts' cloud recordings the KG ingest reads. Empty/absent =
    // every licensed host on the account. COMPANY-wide, admin-only to change.
    // Names are stored alongside the ids so a host who has since been
    // deactivated (and so is absent from the live listing) can still be shown
    // by name rather than as an opaque Zoom user id.
    sync_user_ids?: string[]
    sync_user_names?: Record<string, string>
    // …and the last run's counters. The GAP between them is the signal: a
    // sync that found meetings but read no transcripts almost always means
    // Audio transcript is switched off in the customer's Zoom account, which
    // is a setting they can fix. Absent (undefined) means "never synced" —
    // which is NOT the same as zero and must not be rendered as one.
    last_sync_meetings?: number
    last_sync_transcripts?: number
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
  /** Files handed to the knowledge-graph extractor this run (doc names).
   *  Extraction runs in the background — presence here means "queued". */
  kg_queued?: string[]
  kg_signals?: number
}

/** A file the user picked via the Google Picker (drive.file scope). */
export type GoogleDrivePickedFile = {
  id: string
  name?: string
}

/** One node in a picked folder's stored subtree (see backend
 *  `google_drive_sync.expand_folder`): a sub-folder or a file, parented to
 *  the folder it was found in (`parentId` — the picked root's own id for a
 *  direct child). Superset of GoogleDrivePickedFile, so legacy flat data (no
 *  `mimeType`/`parentId`) still satisfies this shape. */
export type GoogleDriveTreeNode = GoogleDrivePickedFile & {
  mimeType?: string | null
  parentId?: string | null
}

/** Service-account mode state for the Drive connector: the per-company SA email
 *  the customer shares folders with, the enumerated top-level shared roots, and
 *  the walked subtree keyed by folder id (same shape as OAuth folder_contents). */
export type GoogleDriveServiceAccountState = {
  service_account_email?: string | null
  shared_roots?: GoogleDriveTreeNode[]
  folder_contents?: Record<string, GoogleDriveTreeNode[]>
}

/** Short-lived, drive.file-scoped access token for the browser Google Picker. */
export type GoogleDrivePickerToken = {
  access_token: string
  expires_in: number
  app_id?: string
}

/** One document inside a named upload source (never the extracted text
 *  itself — `extracted_chars` says how much we parsed out of it). */
export type UploadSourceFile = {
  id: string
  filename: string
  content_type: string | null
  size_bytes: number
  extracted_chars: number
  uploaded_at: string | null
}

/** A named bundle of the user's own documents — the `uploads` connector's
 *  unit of data. `description` is the optional "what are these documents"
 *  the user supplied; it travels into the knowledge graph with the content. */
export type UploadSource = {
  id: string
  name: string
  description: string
  created_at: string | null
  file_count: number
  files: UploadSourceFile[]
}

export type UploadSourceMutationResponse = {
  ok: boolean
  source: UploadSource
  /** Per-file failures (oversized, empty, unreadable) — partial success is
   *  expected, so these are reported rather than failing the whole batch. */
  errors: { filename: string; error: string }[]
}

export type SlackChannel = {
  id: string
  name: string
  is_private: boolean
  is_member: boolean
  is_archived: boolean
}

export type ConfluenceSpace = {
  id: string
  key: string | null
  name: string | null
  /** "global" | "personal" — personal spaces are filtered out server-side. */
  type: string | null
}

export type ZoomUser = {
  id: string
  email: string
  display_name: string
  /** Only Licensed Zoom accounts can record to the cloud, so an unlicensed
   *  host has nothing to sync. Surfaced rather than filtered so a user looking
   *  for a colleague finds them with an explanation instead of an absence. */
  licensed: boolean
  /** How many recordings this host has. ALWAYS PRESENT, and null until the
   *  count is cheap to compute — it needs one windowed recordings call per
   *  host. Declared null rather than omitted so the UI renders its degraded
   *  line from day one instead of gaining a field later. */
  recording_count: number | null
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
  /** Which Drive access route is active ("oauth" | "service_account"). */
  getGoogleDriveMode: () =>
    api.get<{ mode: string; service_account_configured: boolean }>(`/v1/connectors/google-drive/mode`),
  /** SA mode: provision (idempotent) this company's service account; returns its email + any scanned tree. */
  provisionGoogleDriveServiceAccount: (dataset?: string) =>
    api.get<GoogleDriveServiceAccountState>(`/v1/connectors/google-drive/service-account${dataset ? `?dataset=${encodeURIComponent(dataset)}` : ""}`),
  /** SA mode: enumerate + walk + ingest everything shared with the SA. */
  scanGoogleDriveServiceAccount: (dataset?: string) =>
    api.post<GoogleDriveSyncResult & GoogleDriveServiceAccountState>(`/v1/connectors/google-drive/service-account/scan`, { dataset }),
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

  // ---- Jira ----------------------------------------------------------------
  disconnectJira: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/jira`),

  // ---- Confluence ----------------------------------------------------------
  disconnectConfluence: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/confluence`),

  /** Spaces the connected Confluence account can read (personal ones
   *  excluded server-side), plus the currently persisted selection. */
  listConfluenceSpaces: () =>
    api.get<{ spaces: ConfluenceSpace[]; selected_ids: string[] }>(
      `/v1/connectors/confluence/spaces`,
    ),

  /** Choose which spaces the KG ingest pulls from (stored on the company's
   *  Confluence connection config as sync_space_ids / sync_space_keys). An
   *  empty list clears the selection back to every readable space.
   *  Admin-only — a member gets 403 with the admin-gate message. */
  setConfluenceSyncSpaces: (spaces: { id: string; key?: string | null }[]) =>
    api.post<{ ok: true; config: ConnectionSummary["config"] }>(
      `/v1/connectors/confluence/spaces`,
      { spaces },
    ),

  // ---- Google Meet ---------------------------------------------------------
  /** Drops the company's Google Meet connection, revoking the grant at Google
   *  first. Admin-only — a member gets 403 with the admin-gate message.
   *
   *  There is no `listGoogleMeet…` picker counterpart on purpose: coverage is
   *  fixed to the connecting account's own meetings (Google exposes nothing
   *  else) and the 30-day window is Google's, so there is nothing to choose. */
  disconnectGoogleMeet: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/google-meet`),

  // ---- Zoom ----------------------------------------------------------------
  disconnectZoom: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/zoom`),

  /** The active hosts on the connected Zoom account, plus the persisted
   *  selection.
   *
   *  `total` is HOW MANY WE FETCHED, not how many exist on the account, and
   *  `fetch_capped` says Zoom still had pages when the listing budget ran out
   *  — the two together are what let the UI say "the first N" honestly instead
   *  of asserting a number it cannot know.
   *
   *  `selected_names` matters because the listing is ACTIVE-ONLY: a selected
   *  host who has since been deactivated is absent from `users`, and without
   *  their stored name the picker could only show a bare id. */
  listZoomUsers: () =>
    api.get<{
      users: ZoomUser[]
      selected_ids: string[]
      selected_names: Record<string, string>
      total: number
      fetch_capped: boolean
      truncated: boolean
    }>(`/v1/connectors/zoom/users`),

  /** Choose which hosts' recordings the KG ingest pulls from (stored on the
   *  company's Zoom connection config as sync_user_ids / sync_user_names). An
   *  empty list clears the selection back to every licensed host.
   *  Admin-only — a member gets 403 with the admin-gate message. */
  setZoomSyncUsers: (users: { id: string; email?: string | null }[]) =>
    api.post<{ ok: true; config: ConnectionSummary["config"] }>(
      `/v1/connectors/zoom/users`,
      { users },
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
  /** Save which channels the Slack corpus sync pulls from (stored on the
   * connection config as sync_channel_ids / sync_channel_names). An empty
   * list clears the selection — the sync reverts to every channel the bot
   * is a member of. `joined` echoes the public channels the bot could
   * self-join right away.
   *
   * Unticking is the reverse of ticking, so the response also reports the
   * teardown: `left` are the channels the bot walked out of, `leave_failed`
   * the ones Slack refused (today that is every one of them until the app
   * gains the `channels:manage` scope — the bot only holds `channels:join`),
   * `delivery_skipped` the ones deliberately kept because somebody delivers
   * briefs there, and `purged` how much synced content was removed. All four
   * are advisory: the selection itself always saves. */
  setSlackSyncChannels: (channels: { id: string; name?: string }[]) =>
    api.post<{
      ok: true
      config: ConnectionSummary["config"]
      joined: string[]
      left: string[]
      leave_failed: string[]
      delivery_skipped: string[]
      purged: {
        datasets: string[]
        sections_removed: number
        reseeded: string[]
      }
    }>(`/v1/connectors/slack/sync-channels`, { channels }),

  // ---- Sprinklr ------------------------------------------------------------
  disconnectSprinklr: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/sprinklr`),

  // ---- Asana ---------------------------------------------------------------
  disconnectAsana: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/asana`),

  // ---- Fireflies (API key, not OAuth) --------------------------------------
  connectFirefliesWithApiKey: (apiKey: string) =>
    api.post<{ ok: true; provider: string; account_label: string }>(
      `/v1/connectors/fireflies/apikey`,
      { api_key: apiKey },
    ),
  disconnectFireflies: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/fireflies`),

  // ---- Superset (self-hosted; instance URL + service-account login) --------
  connectSupersetWithCredentials: (
    baseUrl: string, username: string, password: string,
  ) =>
    api.post<{ ok: true; provider: string; account_label: string }>(
      `/v1/connectors/superset/connect`,
      { base_url: baseUrl, username, password },
    ),
  disconnectSuperset: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/superset`),

  // ---- Uploaded documents (no third party — the files ARE the credential) --
  /** Every named document source for the workspace, newest first. */
  listUploadSources: () =>
    api.get<{ sources: UploadSource[] }>(`/v1/connectors/uploads/sources`),
  /**
   * Create a named source from one or more files of ANY type. `description`
   * is the optional "what are these documents" step — it's carried into the
   * knowledge graph with the content, so it's real context, not a label.
   */
  createUploadSource: (name: string, description: string, files: File[]) => {
    const form = new FormData()
    form.append("name", name)
    form.append("description", description)
    for (const f of files) form.append("files", f)
    return api.post<UploadSourceMutationResponse>(
      `/v1/connectors/uploads/sources`,
      form,
    )
  },
  /** Add more documents to an existing source. */
  addUploadSourceFiles: (sourceId: string, files: File[]) => {
    const form = new FormData()
    for (const f of files) form.append("files", f)
    return api.post<UploadSourceMutationResponse>(
      `/v1/connectors/uploads/sources/${encodeURIComponent(sourceId)}/files`,
      form,
    )
  },
  removeUploadSource: (sourceId: string) =>
    api.delete<{ deleted: true; id: string }>(
      `/v1/connectors/uploads/sources/${encodeURIComponent(sourceId)}`,
    ),
  disconnectUploads: () =>
    api.delete<{ deleted: true; provider: string }>(`/v1/connectors/uploads`),

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
  /** SSE URL to token-stream a PRD's generation as it's written. The bearer
   *  rides as ?token= (EventSource can't set headers). Frames:
   *  {kind:'delta',text} then a terminal {kind:'done'|'error'}. Progressive
   *  display only — prdApi.get(id) stays the authoritative finished PRD. */
  streamUrl: (prdId: number, token: string): string =>
    `${API_URL}/v1/prd/${prdId}/stream?token=${encodeURIComponent(token)}` +
    (activeWorkspaceId
      ? `&workspace_id=${encodeURIComponent(activeWorkspaceId)}`
      : ""),
  /** Kick off PRD generation for an IDEATION item (a theme ranked ≥ 4, not in
   *  the brief's top-3). Same fire-and-forget contract as `generate`: returns a
   *  prd_id to poll via prdApi.get(id). The backend synthesizes the insight from
   *  the ideation row and grounds it on the company's current brief. */
  generateFromIdeation: (ideationItemId: string, force = false) =>
    api.post<PrdStartResponse>("/v1/prd/generate-from-ideation", {
      ideation_item_id: ideationItemId,
      force,
    }),
  /** Kick off PRD generation for a SPECIFIC TASK the user described in chat
   *  ("generate a PRD for dark mode"). The backend synthesizes the insight from
   *  the task text (find-or-create keyed on it) and grounds on the company's
   *  data. Same fire-and-forget contract as `generate`: returns a prd_id to
   *  poll via prdApi.get(id) until status === 'ready'. */
  generateFromTask: (
    task: string,
    force = false,
    sourceDocs?: TurnAttachment[],
    /** The chat conversation this command came from. The backend binds it to the
     *  new PRD immediately, so navigating away mid-generation can't leave the
     *  chat orphaned (reopened from history with no PRD and no View PRD button). */
    conversationId?: number | null,
  ) =>
    api.post<PrdStartResponse>("/v1/prd/generate-from-task", {
      task,
      force,
      // Documents attached earlier in the chat thread — the backend grounds the
      // PRD on them (they used to be silently forgotten by this command).
      ...(sourceDocs && sourceDocs.length ? { source_docs: sourceDocs } : {}),
      ...(conversationId != null ? { conversation_id: conversationId } : {}),
    }),
  /** Clarify-first sufficiency gate (runs on EVERY chat-PRD command before
   *  generation): does the task + attached documents carry the ingredients a
   *  grounded PRD needs? sufficient=false comes with 3–5 targeted questions
   *  the chat asks first. Backend fails open to sufficient, so this can never
   *  block generation. */
  clarifyTask: (task: string, sourceDocs?: TurnAttachment[]) =>
    api.post<{
      sufficient: boolean
      questions: { prompt: string; options: string[]; skip_default?: string | null }[]
      missing: string[]
    }>("/v1/prd/clarify-task", {
      task,
      ...(sourceDocs && sourceDocs.length ? { source_docs: sourceDocs } : {}),
    }),
  /** LLM fallback for the chat command decision (tier 2): does this message ask
   *  us to CREATE a PRD? Called only when the message names a PRD but the regex
   *  tier (isPrdCommand) didn't match — novel phrasings. `task` echoes the
   *  user's topic + requirement details verbatim (null → caller falls back to
   *  the raw message). Backend fails open to not-a-command. */
  classifyCommand: (text: string) =>
    api.post<{ is_prd_command: boolean; task: string | null; confidence: number }>(
      "/v1/prd/classify-command",
      { text },
    ),
  /** The Evidence artifact behind a chat-task PRD (generated in parallel with
   *  the PRD from semantic KG retrieval over the task). Resolves null when the
   *  PRD isn't chat-sourced OR retrieval found no backing signals and the doc
   *  was skipped — the Evidence tab stays hidden in either case. May return a
   *  `generating` row; poll evidenceApi.get(id) until terminal. */
  evidenceForPrd: async (prdId: number): Promise<EvidenceRecord | null> => {
    try {
      return await api.get<EvidenceRecord>(`/v1/prd/${prdId}/evidence`)
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null
      throw e
    }
  },
  /** Import an existing PRD from an uploaded file (PDF/PPT/DOCX/…). The backend
   *  parses it to text and re-lays-it-out into our format via the prd-author
   *  skill. Same fire-and-forget contract as `generate`: returns a prd_id to
   *  poll via prdApi.get(id) until status === 'ready'. `dataset` is the company
   *  slug the PRD belongs to. */
  importDoc: (file: File, dataset: string, conversationId?: number | null) => {
    const form = new FormData()
    form.append("file", file, file.name)
    form.append("dataset", dataset)
    // See generateFromTask: binds the commanding chat to the PRD server-side so
    // leaving the page mid-import can't orphan it.
    if (conversationId != null) form.append("conversation_id", String(conversationId))
    return api.post<PrdStartResponse>("/v1/prd/import", form)
  },
  /** Fetch a PRD by id. payload_md is only filled when status === 'ready'. */
  get: (id: number) => api.get<PrdRecord>(`/v1/prd/${id}`),
  /** Resolve a PRD's opaque public_id (the `?prd=` URL's canonical form) to
   *  its real internal id — same ownership check `get(id)` already enforces.
   *  The one call `useArtifactUrlSync` needs to open a `?prd={public_id}`
   *  deep-link with the existing, unchanged internal open path. */
  resolveIdByPublicId: (publicId: string) =>
    api.get<{ id: number }>(`/v1/prd/by-public-id/${encodeURIComponent(publicId)}`),
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
  /** The PRD's structured "User input needed" questions. Rendered in the PRD's
   *  chat as messages with answer buttons. Returns every question; the client
   *  shows pending ones as actionable. `extracting: true` means the backend just
   *  scheduled the extraction for this PRD (a pre-feature PRD opened from
   *  Artifacts, or a just-generated one whose extraction is still running) —
   *  poll until it flips false and the questions arrive. */
  listInputQuestions: (id: number) =>
    api.get<PrdInputQuestionsList>(`/v1/prd/${id}/input-questions`),
  /** Answer one "User input needed" question. The backend folds the answer into
   *  only the affected PRD sections (a scoped edit — NOT a full regeneration),
   *  saves an undoable version, and returns the updated PRD + which sections
   *  changed so the panel can refresh live and the chat can confirm. */
  answerInputQuestion: (prdId: number, questionId: number, answer: string) =>
    api.post<PrdInputAnswerResponse>(
      `/v1/prd/${prdId}/input-questions/${questionId}/answer`,
      { answer },
    ),
  /** Apply a free-form chat edit instruction to the PRD ("make this PRD
   *  shorter"). Same scoped-editor contract as answerInputQuestion — only the
   *  affected sections change, saved as an undoable version — driven by the
   *  user's own instruction. Empty `sections_changed` means the editor judged
   *  the message wasn't an edit and left the document untouched. */
  chatEdit: (prdId: number, instruction: string) =>
    api.post<{ prd: PrdRecord; sections_changed: string[]; summary: string }>(
      `/v1/prd/${prdId}/chat-edit`,
      { instruction },
    ),
}

/** One structured "User input needed" item lifted out of the PRD document.
 *  `tag` is 'escalate' (a product decision → answered by picking an `options`
 *  button) or 'need' (missing data → answered as free text, `options` empty).
 *  `status` walks pending → answered (or dismissed). */
export type PrdInputQuestion = {
  id: number
  prd_id: number
  ordinal: number
  tag: "escalate" | "need"
  prompt: string
  owner?: string | null
  options: PendingQuestionChoice[]
  status: "pending" | "answered" | "dismissed"
  answer?: string | null
}

/** Response from GET /v1/prd/{id}/input-questions — the stored questions plus
 *  whether a background extraction is currently producing them (poll while
 *  true). */
export type PrdInputQuestionsList = {
  questions: PrdInputQuestion[]
  extracting?: boolean
}

/** Response from POST /v1/prd/{id}/input-questions/{qid}/answer — the updated PRD
 *  (with the scoped edit folded in), the now-answered question, and the
 *  human-readable section names the edit touched (for the chat confirmation). */
export type PrdInputAnswerResponse = {
  prd: PrdRecord
  question: PrdInputQuestion
  sections_changed: string[]
  summary: string
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
  // ── (append-only): the owning PRD id. GET /{id} / by-prd both `select("*")`,
  //    so the column flows through at runtime; typed OPTIONAL so existing
  //    `PrototypeRecord` literals keep typechecking.
  prd_id?: number
  // ── (append-only): the checkpoint id a completed iterate/generation last
  //    advanced to. GET /{id} does `select("*")`, so the column flows through
  //    automatically — no api method change. Typed OPTIONAL/nullable (older
  //    rows predating the checkpoint concept may carry null) so existing
  //    `PrototypeRecord` literals keep typechecking. Consumed by
  //    PostGenerationResult's useViewGrant call so the view-grant re-mints the
  //    moment the checkpoint advances, not just on a bundle_url change.
  current_checkpoint_id?: number | null
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
  /** Comment provenance: 'internal' (authed team surface) or 'public'
   *  (anonymous share-link viewer). Optional for back-compat — absent means
   *  internal. The public by-token list only ever returns 'public' rows. */
  origin?: "internal" | "public"
  /** Public by-token list only: true when this visitor created the row
   *  (HttpOnly visitor-cookie match, computed server-side). Null on the
   *  authed list — internal users act by role, not visitor identity. */
  mine?: boolean | null
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

export type BriefPrototypeReadiness = {
  ready: boolean
  preview_image_url: string | null
  /** The PRD the ready prototype is actually attached to — open the prototype
   *  via THIS id. Usually equals the entry's prd_id, but after a PRD
   *  regeneration it points at the older PRD the prototype was built against. */
  prd_id?: number | null
}
export type BriefPrototypeMapEntry = {
  insight_index: number
  prd_id: number
  prd_title: string
  prototype: BriefPrototypeReadiness | null
}
export type BriefPrototypeMap = { brief_id: number; entries: BriefPrototypeMapEntry[] }

/** Bound on the view-grant mint POST. If the request stalls (observed: Safari's
 *  stricter cookie/ITP handling can hang a same-origin credentialed POST
 *  indefinitely), the AbortController fires and the fetch rejects, letting
 *  useViewGrant's existing mint() catch block surface its clean error state
 *  instead of leaving the caller's promise pending forever. */
export const VIEW_GRANT_FETCH_TIMEOUT_MS = 10_000

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
    design_source?: "figma" | "github" | "website" | "screenshot" | null  // explicit source selector; null = back-compat implicit precedence
    /** Staged upload keys returned by `uploadScreenshot`, one call per slot,
     *  in upload (= prompt) order (screenshot source only). Absent/omitted
     *  for every other source. */
    screenshot_keys?: string[] | null
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
    /** The PM-confirmed external-entry-point description from the locate
     *  gate's `external_surface` signal (codebase generation only, no chosen
     *  screen). Free text, e.g. "a confirmation email sent to the customer" —
     *  never a closed enum. Absent/null = no signal / old client. */
    external_surface_hint?: string | null
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
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), VIEW_GRANT_FETCH_TIMEOUT_MS)
    try {
      const res = await fetch(viewGrantUrl, {
        method: "POST",
        headers,
        credentials: "include",
        signal: controller.signal,
      })
      if (!res.ok) throw new ApiError(res.status, null, "view-grant failed")
    } finally {
      clearTimeout(timeoutId)
    }
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
    /** null = a general (unpinned) comment -- prototype-level feedback with no
     *  element anchor. Pinned callers keep passing a non-empty string. */
    anchor_id: string | null; body: string;
    pin_x_pct?: number | null; pin_y_pct?: number | null; resolved_anchor_id?: string | null;
    viewer_name?: string;
  }) =>
    api.post<CommentRecord>(
      `/v1/design-agent/by-token/${encodeURIComponent(token)}/comments`,
      body,
    ),
  /** Authed comment create for the signed-in canvas (mark-and-comment pin flow).
   *  Hits the authed route `POST /v1/design-agent/{id}/comments` (same-origin/CSRF
   *  gated). Position fields are optional — pin comments include x/y and the
   *  resolved anchor; right-click anchor comments omit them. `anchor_id: null`
   *  is the authed General composer's general (unpinned, prototype-level)
   *  comment — same null representation as createCommentByToken's general case. */
  createComment: (prototypeId: number, body: {
    anchor_id: string | null; body: string;
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
  /** Stage a user-uploaded screenshot as generate-time design context.
   *  Multipart POST via the shared helper's FormData branch (the runtime sets
   *  the multipart boundary; no manual Content-Type), credentialed like every
   *  other authed mutation. Returns the storage key the generate body threads
   *  as `screenshot_key`. The server sniffs the REAL media type from the
   *  bytes — the response's `media_type` is authoritative, not the filename. */
  uploadScreenshot: (
    file: File | Blob,
  ): Promise<{ screenshot_key: string; media_type: string }> => {
    const form = new FormData()
    form.append(
      "file",
      file,
      typeof File !== "undefined" && file instanceof File ? file.name : "screenshot",
    )
    return api.post<{ screenshot_key: string; media_type: string }>(
      "/v1/design-agent/uploads/screenshot",
      form,
    )
  },
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
  /** Whether the SAME locate call's own read of the PRD flagged the entry
   *  point as genuinely external (an email, an SMS, a third-party partner UI,
   *  anything — never a closed set of channels). Present ONLY on a
   *  ranked_confirm outcome where no strong in-app match was found; undefined
   *  / null on every other decision (a real in-app match always wins) and on
   *  the unmapped fail-open path (no locate call ran). Optional/additive. */
  external_surface?: {
    detected: boolean
    /** Free text describing WHAT the external surface is, e.g. "a
     *  confirmation email sent to the customer" — never a fixed category. */
    surface_description: string
    confidence: number
  } | null
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

/** A Jira project the company can push into (target picker). */
export type JiraProject = {
  id: string
  key: string
  name: string
}

/** A user assignable to issues in a Jira project (assignee picker). `accountId`
 *  is the Atlassian id passed back on push to set the issue's assignee. */
export type JiraMember = {
  accountId: string
  displayName: string | null
  email: string | null
  active: boolean
  avatarUrl: string | null
}

export type JiraTicketPushResult = {
  ok: boolean
  created: { task_id: string; jira_issue_key: string; url: string | null; title: string }[]
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
  /** Atlassian accountId (from listJiraMembers) to assign the issue to on a Jira
   *  push. Omit/null = unassigned. Ignored for ClickUp. */
  assignee_account_id?: string | null
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
/** A normalized tracker custom-field value (see backend tracker_meta.py):
 *  scalars as themselves, select/user → {id, name}, multiselect/users →
 *  [{id, name}], labels → string[]. */
export type TrackerFieldValue =
  | string | number | boolean
  | { id: string | null; name: string | null }
  | { id: string | null; name: string | null }[]
  | string[]
  | null

export type TicketFields = {
  title?: string | null
  priority?: string | null
  status?: string | null
  sprint?: string | null
  assignee?: TicketAssignee | null
  /** Child issues override. Omit = keep generated; a list (incl. []) replaces. */
  subtasks?: string[] | null
  /** Tracker custom-field overrides keyed by field id — MERGED server-side
   *  (send only the fields being changed; null clears one override). */
  custom_fields?: Record<string, TrackerFieldValue> | null
  /** Tracker issue type (Jira Task/Story/… — the destination's real types).
   *  Pushed on create; changes sync best-effort. */
  issue_type?: string | null
}

/** Whether a ticket is live, held back from the PM tool, or deleted.
 *  Non-active tickets do not exist in the tracker — that is the whole point of
 *  both non-active states; they differ only in whether Sprntly still shows the
 *  ticket. */
export type TicketLifecycle = "active" | "excluded" | "deleted"

export type TicketDataResponse = {
  description: string | null
  acceptance_criteria: string[] | null
  title: string | null
  priority: string | null
  status: string | null
  sprint: string | null
  assignee: TicketAssignee | null
  subtasks: string[] | null
  custom_fields: Record<string, TrackerFieldValue> | null
  issue_type: string | null
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
  /** Add a comment. The author is resolved SERVER-SIDE from the signed-in
   *  session (profile name → email); the optional `author` here is only
   *  honored for the "Sprntly" system notes — anything else is ignored. */
  addComment: (ticketKey: string, body: string, author = "user") =>
    api.post<{ id: number; author: string; body: string; time: string }>(
      `/v1/tickets/${encodeURIComponent(ticketKey)}/comments`, { author, body },
    ),
  /** Remove a comment. When the comment had been pushed to the bound tracker,
   *  the tracker's copy is deleted too. */
  removeComment: (ticketKey: string, commentId: number) =>
    api.delete(`/v1/tickets/${encodeURIComponent(ticketKey)}/comments/${commentId}`),
  /** Exclude / delete / restore a ticket.
   *
   *  `excluded` keeps it in Sprntly but holds it back from the PM tool;
   *  `deleted` removes it from Sprntly. BOTH also delete the Jira/ClickUp/
   *  Asana issue if the ticket had been pushed (closed instead where the
   *  tracker refuses on permissions). `active` restores it, and the next sync
   *  re-creates it in the tracker. */
  setLifecycle: (ticketKey: string, lifecycle: TicketLifecycle) =>
    api.put<{ ok: boolean; lifecycle: TicketLifecycle; tracker_sync_started: boolean }>(
      `/v1/tickets/${encodeURIComponent(ticketKey)}/lifecycle`, { lifecycle },
    ),
  /** Delete a ticket — from Sprntly and from the bound PM tool. Shorthand for
   *  setLifecycle(key, "deleted"). */
  remove: (ticketKey: string) =>
    api.delete<{ ok: boolean; lifecycle: TicketLifecycle; tracker_sync_started: boolean }>(
      `/v1/tickets/${encodeURIComponent(ticketKey)}`,
    ),
  /** AI summary of the comment thread. `summary` is null when there's too little
   *  to summarize (< 2 comments) or the LLM call failed (best-effort). */
  summarizeComments: (ticketKey: string) =>
    api.get<{ summary: string | null; proposed_criterion?: string | null }>(
      `/v1/tickets/${encodeURIComponent(ticketKey)}/comments/summary`,
    ),
  /** The status moves LEGAL for this ticket right now (tracker-bound tickets'
   *  status dropdown). 404 when the PRD is unbound or the ticket was never
   *  pushed — callers fall back to the default status options. */
  getTransitions: (ticketKey: string) =>
    api.get<{ provider: TrackerProvider; transitions: TrackerTransition[] }>(
      `/v1/tickets/${encodeURIComponent(ticketKey)}/transitions`,
    ),
  /** A destination's vocabulary BEFORE any PRD is bound to it (the create
   *  drawer's pickers right after the user picks a project/list). 404 when
   *  nothing can be fetched — callers keep their default pickers. */
  trackerMetaForDestination: (provider: TrackerProvider, destinationId: string) =>
    api.post<{ provider: TrackerProvider; destination_id: string; meta: TrackerMeta }>(
      "/v1/tickets/tracker-meta", { provider, destination_id: destinationId },
    ),
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
  /** Fetch Jira projects the company can push tickets into. 404 when not connected. */
  listJiraProjects: () =>
    api.post<{ projects: JiraProject[] }>("/v1/tickets/jira/projects", {}),
  /** List users assignable to issues in a Jira project (assignee picker). `query`
   *  narrows by name/email for type-ahead. 404 when Jira isn't connected. */
  listJiraMembers: (projectKey: string, query?: string) =>
    api.post<{ members: JiraMember[] }>("/v1/tickets/jira/members", {
      project_key: projectKey,
      ...(query ? { query } : {}),
    }),
  /** Push the selected tasks into a Jira project as issues. Same override-merge
   *  behavior as pushToClickUp; each task's assignee_account_id (if set) assigns
   *  the issue. Returns the created issue keys + URLs. */
  pushToJira: (projectKey: string, tasks: TicketPushTask[], issueType = "Task") =>
    api.post<JiraTicketPushResult>("/v1/tickets/push-jira", {
      project_key: projectKey,
      tasks,
      issue_type: issueType,
    }),
}

// ── User stories: real PRD→tickets generation + ClickUp push ────────────────
// Backend: app/routes/stories.py. Generation is LLM-backed (the vendored
// user-stories skill) and writes nothing; push is the explicit ClickUp write.
// This is the REAL path behind "Create ticket" (vs the mock ticket fixtures).
// The canonical ticket the `ticket` skill emits. `title`/`body`/
// `acceptance_criteria` are the legacy core; the rest is the structured
// contract (five-section description, trace spine, delivery structure, and the
// decision/spike variants). Structured fields are optional — a set cached
// before the v2 contract, or a ticket the model left thin, omits them.
export type TicketAC = string // "Given… When… Then…", may be prefixed "[failure]"/"[edge]"

export type GeneratedStory = {
  /** Content-derived stable id (hash of title+body) stamped at generation.
   *  Keys per-ticket edit overrides. Optional for sets cached before it existed. */
  id?: string
  /** build (deliverable) · decision ([ESCALATE]) · spike ([ASSUMPTION → T0]). */
  ticket_type?: "build" | "decision" | "spike"
  title: string
  body: string
  acceptance_criteria: TicketAC[]
  priority: string | null
  route: string | null
  // ── Five-section structured description ──
  what?: string
  why_now?: string
  user_story?: string
  scope?: string[]
  out_of_scope?: string
  // ── Provenance / trace spine ──
  prd_section?: string // "Part A §5 R3"
  ears_ids?: string[]
  signals?: string[]
  ac_inherited?: boolean
  // ── Delivery structure ──
  subtasks?: string[] // may be prefixed "[P]" for parallel-safe
  blocked_by?: string[]
  blocks?: string[]
  story_points?: number | null
  labels?: string[]
  data_gaps?: string[] // [NEED] markers, never filled
  // ── Story-map placement (Jeff Patton); empty for a flat/unsized set ──
  activity?: string // backbone step (Part A §4) this ticket serves
  release?: string // release slice; "Release 1" = walking skeleton
  // ── Decision-ticket fields ──
  decision?: string | null
  owner?: string | null
  decide_by?: string | null
  // ── Spike fields ──
  timebox?: string | null
  exit_condition?: string | null
  // ── Push-time only (Jira): Atlassian accountId to assign this ticket to.
  // Set by the per-ticket assignee picker just before a Jira push; not a
  // generated property (backend omits it from the cache). null = unassigned. ──
  assignee_account_id?: string | null
  // ── Lifecycle. Absent for the ordinary "active" case; "excluded" means the
  // user is holding this ticket back from the PM tool. Deleted tickets are
  // filtered out server-side, so this never arrives as "deleted". ──
  lifecycle?: TicketLifecycle
}

export type StoryPushResult = {
  created: { story: string; task_id: string; url: string }[]
  errors: { story: string; error: string }[]
}

// One planned-but-not-yet-written ticket from the fan-out's plan leg — enough
// to render a skeleton row (~20-35s in) while the full tickets generate.
export type TicketStub = {
  title: string
  summary?: string
  prd_section?: string
}

export type StoryJob = {
  job_id: number
  status: "generating" | "ready" | "failed"
  stories?: GeneratedStory[]
  // Fan-out streams tickets batch-by-batch: while `generating`, `stories` may
  // hold the partial set landed so far and `progress` the batch counter.
  // `stubs` arrives first — the planned roster, before any full ticket exists.
  stubs?: TicketStub[]
  progress?: { done: number; total: number }
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

/** What POST /v1/stories/generate hands back on both paths.
 *
 *  `ticket_set_id` is present ONLY on the insight (no-PRD) path: the backend
 *  creates the durable `ticket_sets` row at kick-off, BEFORE scheduling the
 *  job, and the client never posts stories back — which is what makes a
 *  double-poll or a StrictMode double-effect unable to produce two sets. A
 *  re-attaching call (the in-flight dedupe) returns the SAME set id. */
export type StoryGenerateStart = {
  job_id: number
  status: string
  ticket_set_id?: number
}

export const storiesApi = {
  /** Persisted tickets for a PRD + whether they're still fresh. Read this first;
   *  only regenerate when missing/stale (`fresh` false). No LLM call. */
  getForPrd: (prdId: number) =>
    api.get<StoryCache>(`/v1/stories/for-prd/${prdId}`),
  /** Kick off breaking a PRD into user-story tickets (fire-and-forget). Returns
   *  a job id immediately; poll `getJob` until ready/failed. Persists on ready. */
  generate: (prdId: number) =>
    api.post<StoryGenerateStart>("/v1/stories/generate", { prd_id: prdId }),
  /** Kick off tickets from a free-form INSIGHT instead of a PRD — the chat's
   *  "generate tickets" ask in a thread that has no PRD. Same job contract as
   *  `generate` (poll `getJob`), plus a `ticket_set_id` for the durable
   *  `ticket_sets` row the run fills. `conversationId` stamps the thread the
   *  set was born in so it can be reopened from there; the backend 404s a
   *  conversation that isn't the caller's company's.
   *
   *  Callers should go through `lib/runTicketSetGeneration.ts` rather than
   *  calling this directly — it owns the kick-off/poll/publish arc, and one
   *  owner is what keeps the panel from starting a second run of its own. */
  generateFromInsight: (insight: string, conversationId?: number | null) =>
    api.post<StoryGenerateStart>("/v1/stories/generate", {
      insight,
      ...(conversationId != null ? { conversation_id: conversationId } : {}),
    }),
  /** Poll a story-generation job. 404 once it's unknown / not the caller's. */
  getJob: (jobId: number) =>
    api.get<StoryJob>(`/v1/stories/jobs/${jobId}`),
  /** ClickUp lists the company can push into (target picker). 404 if ClickUp
   *  isn't connected. */
  listClickUpLists: () =>
    api.post<{ lists: ClickUpList[] }>("/v1/stories/lists", {}),
  /** Asana projects the company can push into (target picker). Shaped like the
   *  ClickUp list picker (project gid as `id`) so the same picker is reused.
   *  404 if Asana isn't connected. */
  listAsanaProjects: () =>
    api.post<{ lists: ClickUpList[] }>("/v1/stories/asana/projects", {}),
  /** Create the reviewed stories as tasks in a ClickUp list (explicit write). */
  pushToClickUp: (listId: string, stories: GeneratedStory[]) =>
    api.post<StoryPushResult>("/v1/stories/push", { list_id: listId, stories }),
  /** Jira projects the company can push into (target picker). 404 if Jira
   *  isn't connected. */
  listJiraProjects: () =>
    api.post<{ projects: JiraProject[] }>("/v1/stories/jira/projects", {}),
  /** List users assignable to issues in a Jira project (assignee picker). 404 if
   *  Jira isn't connected. */
  listJiraMembers: (projectKey: string, query?: string) =>
    api.post<{ members: JiraMember[] }>("/v1/stories/jira/members", {
      project_key: projectKey,
      ...(query ? { query } : {}),
    }),
  /** Create the reviewed stories as issues in a Jira project (explicit write). */
  pushToJira: (projectKey: string, stories: GeneratedStory[], issueType = "Task") =>
    api.post<StoryPushResult>("/v1/stories/jira/push", {
      project_key: projectKey,
      stories,
      issue_type: issueType,
    }),
  /** Bidirectional read: current ClickUp state (status/assignee/url) for tickets
   *  already synced to a list, keyed by ticket id. Unsynced tickets are absent. */
  pullClickUpStatus: (listId: string, ticketIds: string[]) =>
    api.post<{ statuses: Record<string, ClickUpTicketState> }>(
      "/v1/stories/pull-status", { list_id: listId, ticket_ids: ticketIds },
    ),
  /** This PRD's tracker-sync state: destination, whether a sync is running,
   *  last-synced time, and the pulled per-ticket tracker statuses.
   *  `configured: false` = tickets were never pushed anywhere. */
  getSyncState: (prdId: number) =>
    api.get<TicketSyncState>(`/v1/stories/sync/${prdId}`),
  /** Run a two-way sync pass in the background (push local edits + pull tracker
   *  status). Pass `dest` on the FIRST push (or to switch tool/destination);
   *  omit it to re-sync the configured destination. Poll getSyncState after. */
  triggerSync: (prdId: number, dest?: {
    provider: TrackerProvider; destination_id: string; destination_name?: string
  }) =>
    api.post<{ status: "syncing" }>(`/v1/stories/sync/${prdId}`, dest ?? {}),
  /** The bound destination's vocabulary (statuses/priorities/issue types/
   *  custom fields) — what bound tickets render instead of the canned lists.
   *  `configured: false` (never an error) when the PRD has no destination;
   *  `meta` may be null when metadata couldn't be fetched yet. */
  getTrackerMeta: (prdId: number, refresh = false) =>
    api.get<{
      configured: boolean
      provider: TrackerProvider | null
      destination_id: string | null
      meta: TrackerMeta | null
    }>(`/v1/stories/sync/${prdId}/tracker-meta${refresh ? "?refresh=1" : ""}`),
}

// ── Standalone ticket sets ───────────────────────────────────────────────────
// Tickets generated from a chat with NO PRD behind them (backend:
// app/routes/ticket_sets.py). A set is a durable artifact, not a chat bubble:
// it is created by POST /v1/stories/generate's insight path (see
// `storiesApi.generateFromInsight`), and these routes are the read + tracker
// surface over the result.
//
// The tracker routes MIRROR the per-PRD ones exactly and share the backend
// implementation (app/stories/sync_control.py), so a set syncs two-way with
// Jira / ClickUp / Asana on identical terms — same first-push binding, same
// interval auto-sync, same last-writer-wins reconciliation. The shapes below
// are therefore the same shapes the per-PRD calls return.

/** One ticket set with its tickets (GET /v1/ticket-sets/{id}).
 *  `title` and `source_text` come back even when empty — the API does not
 *  decide that a blank line should disappear; the panel renders its own
 *  fallback copy. DELETED tickets are already dropped and EXCLUDED ones tagged,
 *  exactly as `getForPrd` does. */
export type TicketSetRecord = {
  id: number
  title: string
  /** "generating" | "ready" | "failed" */
  status: string
  stories: GeneratedStory[]
  ticket_count: number
  conversation_id: number | null
  source_text: string
  created_at?: string | null
}

/** One row of a thread's ticket-set list (GET /v1/ticket-sets/by-conversation/
 *  {id}) — the same set as `TicketSetRecord` minus `stories`, which the listing
 *  deliberately never carries (it is the resume read, not a document fetch). */
export type TicketSetSummary = {
  id: number
  title: string
  /** "generating" | "ready" | "failed" */
  status: string
  created_at: string | null
}

export const ticketSetsApi = {
  /** One ticket set with its tickets. 404 for an unknown id AND for another
   *  tenant's — deliberately indistinguishable, so never surface the
   *  difference in copy. */
  get: (setId: number) => api.get<TicketSetRecord>(`/v1/ticket-sets/${setId}`),
  /** The sets born in one chat, newest first — the THREAD-RESUME read.
   *
   *  Reopening a chat asks this so it can put the panel back on what that chat
   *  produced: a `generating` set reopens on the live run, a finished one on its
   *  tickets. Company-scoped in the backend query, so a conversation id that
   *  isn't the caller's company's comes back as an empty list rather than a 403
   *  (routes/ticket_sets.py::sets_for_conversation). */
  byConversation: (conversationId: number) =>
    api.get<{ ticket_sets: TicketSetSummary[] }>(
      `/v1/ticket-sets/by-conversation/${conversationId}`,
    ),
  /** This set's tracker-sync state. Same shape as `storiesApi.getSyncState`. */
  getSyncState: (setId: number) =>
    api.get<TicketSyncState>(`/v1/ticket-sets/${setId}/sync`),
  /** Run a two-way sync pass in the background. Pass `dest` on the FIRST push
   *  (or to switch tool/destination); omit it to re-sync the configured one. */
  triggerSync: (setId: number, dest?: {
    provider: TrackerProvider; destination_id: string; destination_name?: string
  }) =>
    api.post<{ status: "syncing" }>(`/v1/ticket-sets/${setId}/sync`, dest ?? {}),
  /** The destination's vocabulary (statuses/priorities/issue types/custom
   *  fields). Same three-way contract as the PRD route: bound → the bound
   *  destination's meta, unbound-but-connected → the connect-time warm cache,
   *  no tracker → all-null and the web keeps its defaults. */
  getTrackerMeta: (setId: number, refresh = false) =>
    api.get<{
      configured: boolean
      provider: TrackerProvider | null
      destination_id: string | null
      meta: TrackerMeta | null
    }>(`/v1/ticket-sets/${setId}/tracker-meta${refresh ? "?refresh=1" : ""}`),
}

export type ClickUpTicketState = {
  status: string | null
  assignee: string | null
  url: string | null
  /** The tracker's priority name for this ticket, pulled each sync pass. */
  priority?: string | null
  /** Canonical open/in_progress/done projection of `status` (from tracker
   *  metadata) — vocabulary-independent completion semantics. */
  status_category?: TrackerStatusCategory | null
  /** Pulled custom-field values (normalized, keyed by field id) — the detail
   *  screen's read-side value when there's no local override. */
  custom_fields?: Record<string, TrackerFieldValue> | null
  /** The tracker-side issue type (Jira), pulled each sync pass. */
  issue_type?: string | null
}

// ── Tracker metadata (tracker-native vocabulary) ────────────────────────────
// The bound destination's REAL statuses / priorities / issue types / custom
// fields, normalized by the backend (app/connectors/tracker_meta.py) and
// cached per destination. Bound tickets render THESE instead of Sprntly's
// canned lists; unbound tickets keep the defaults.

export type TrackerStatusCategory = "open" | "in_progress" | "done"

export type TrackerStatus = {
  id: string | null
  name: string
  color: string | null
  category: TrackerStatusCategory
}

export type TrackerPriority = { id: string; name: string; color: string | null }

export type TrackerIssueType = { id: string; name: string; subtask: boolean }

export type TrackerFieldDef = {
  id: string
  name: string
  /** Editor type (text/select/user/…), or "unsupported" — render read-only. */
  type: string
  raw_type: string
  required: boolean
  editable: boolean
  options: { id: string | null; name: string; color: string | null }[] | null
}

export type TrackerMeta = {
  provider: TrackerProvider
  destination_id: string
  fetched_at?: string
  statuses: TrackerStatus[]
  priorities: TrackerPriority[]
  issue_types: TrackerIssueType[] | null
  fields: TrackerFieldDef[]
}

/** One legal status move for a ticket (GET /v1/tickets/{key}/transitions).
 *  Jira: the issue's live workflow transitions; ClickUp: every list status
 *  (no workflow restrictions) in the same shape. */
export type TrackerTransition = {
  id: string | null
  name: string
  to_status_id: string | null
  to_status_name: string
  category: TrackerStatusCategory | null
}

// ── Ticket tracker sync (per-PRD) ────────────────────────────────────────────

export type TrackerProvider = "clickup" | "jira" | "asana"

/** Per-PRD tracker-sync state (GET /v1/stories/sync/{prd_id}). After the first
 *  push registers a destination, the backend auto-syncs on an interval; the
 *  sync button reads/triggers the same state. */
export type TicketSyncState = {
  configured: boolean
  provider?: TrackerProvider
  destination_id?: string
  destination_name?: string | null
  auto_sync?: boolean
  sync_status?: "idle" | "syncing"
  last_synced_at?: string | null
  last_error?: string | null
  statuses?: Record<string, ClickUpTicketState>
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

// ── Admin (owner/admin only): LLM provider + per-company API keys ──
// A workspace runs on Claude (Anthropic) or OpenAI. When a key is configured
// for the ACTIVE provider, ALL of the company's LLM calls use THAT key instead
// of the platform key. The full key is never returned — reads carry a masked
// preview only.
//
// The two keys are independent of the provider switch: a workspace can hold
// both and flip between them without re-entering either.

/** The providers the backend can run on. Mirrors app/llm_providers.py. */
export const LLM_PROVIDERS = ["anthropic", "openai"] as const
export type LlmProvider = (typeof LLM_PROVIDERS)[number]

export type LlmKeyStatus = { configured: boolean; masked: string | null }

/** Which provider is live plus the key status of BOTH, in one request — so the
 *  Admin pane can show "Claude key saved" on an inactive card without a second
 *  round trip. */
export type LlmConfig = {
  provider: LlmProvider
  providers: Record<LlmProvider, LlmKeyStatus>
}

/** `?provider=` on every key route. Omitted for Anthropic so the requests the
 *  onboarding step and older clients send stay byte-identical. */
function providerQuery(provider: LlmProvider): string {
  return provider === "anthropic" ? "" : `?provider=${encodeURIComponent(provider)}`
}

export const adminApi = {
  /** Active provider + both key statuses. */
  getLlmConfig: () => api.get<LlmConfig>("/v1/admin/llm-config"),
  /** Switch which provider this workspace's LLM calls run on. Allowed with no
   *  key stored for the target — it then runs on Sprntly's key for that
   *  provider, the same posture a keyless Claude workspace has always had. */
  setLlmProvider: (provider: LlmProvider) =>
    api.put<LlmConfig>("/v1/admin/llm-config", { provider }),
  /** Current key status for one provider (masked preview, never the full key). */
  getLlmKey: (provider: LlmProvider = "anthropic") =>
    api.get<LlmKeyStatus>(`/v1/admin/llm-key${providerQuery(provider)}`),
  /** Store / replace the company key for one provider. */
  setLlmKey: (apiKey: string, provider: LlmProvider = "anthropic") =>
    api.put<LlmKeyStatus>(`/v1/admin/llm-key${providerQuery(provider)}`, {
      api_key: apiKey,
    }),
  /** Remove the key (revert to the platform key). Does not change the provider. */
  deleteLlmKey: (provider: LlmProvider = "anthropic") =>
    api.delete<LlmKeyStatus>(`/v1/admin/llm-key${providerQuery(provider)}`),
  /** Explicit, opt-in live validation of the stored key. */
  testLlmKey: (provider: LlmProvider = "anthropic") =>
    api.post<{ ok: true }>(`/v1/admin/llm-key/test${providerQuery(provider)}`),
}

// ── Usage (owner/admin only): LLM spend + token usage for this workspace ──
// Every `est_cost_usd` here is ESTIMATED — the provider APIs return token counts,
// never dollars, so the backend prices tokens against the published rate card.
// Surface it as "estimated" in the UI; it will not match an Anthropic invoice to
// the cent. `cost_basis` carries that provenance from the API.

/** The numeric columns every usage rollup shares. */
export type UsageBucket = {
  calls: number
  failed_calls: number
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  est_cost_usd: number
}

export type UsageSummary = {
  range: { start: string; end: string; days: number; tz: string }
  cost_basis: string
  /** Always "customer_key": only calls billed to the company's OWN provider key
   *  are counted. Usage on Sprntly's platform key is spend we absorb and is
   *  deliberately excluded — it is not the customer's to see or pay. */
  scope: string
  /** Which provider this payload covers, echoed back by the server; null when
   *  the request was un-scoped and the figures span every provider. Read this
   *  rather than the requested value — a chart must be captioned with the scope
   *  it was actually served. */
  provider: LlmProvider | null
  totals: UsageBucket
  /** One entry per calendar day in the range — empty days included. */
  daily: (UsageBucket & { day: string })[]
  by_feature: (UsageBucket & { feature: string })[]
  by_model: (UsageBucket & { model: string })[]
  by_provider: (UsageBucket & { provider: string })[]
  by_operation: (UsageBucket & { operation: string })[]
}

/** Guess the viewer's IANA zone so "today" on the chart is their calendar day. */
function localTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
}

/** `&provider=…` when scoping to one provider, empty when spanning all of them.
 *  Omitting the param is what asks the server for every provider, so an absent
 *  value must not be sent as the empty string. */
function usageProviderQuery(provider?: LlmProvider | null): string {
  return provider ? `&provider=${encodeURIComponent(provider)}` : ""
}

export const usageApi = {
  /** `provider` scopes every figure in the response — totals, daily series and
   *  all breakdowns — to that provider alone. The Admin pane always passes the
   *  one it is running on: Claude and OpenAI bill separately, so a blended
   *  number reconciles against neither invoice. */
  summary: (
    days: number,
    provider?: LlmProvider | null,
    tz: string = localTimeZone(),
  ) =>
    api.get<UsageSummary>(
      `/v1/admin/usage/summary?days=${days}&tz=${encodeURIComponent(
        tz,
      )}${usageProviderQuery(provider)}`,
    ),
  /** The same rollup as CSV text (the request helper returns non-JSON as-is). */
  exportCsv: (
    days: number,
    provider?: LlmProvider | null,
    tz: string = localTimeZone(),
  ) =>
    api.get<string>(
      `/v1/admin/usage/export.csv?days=${days}&tz=${encodeURIComponent(
        tz,
      )}${usageProviderQuery(provider)}`,
    ),
}

// ── Staff admin panel (dedicated owner-only credential) ──
// Org invites + per-company entitlements. Auth is fully separate from the
// normal app session: POST /v1/staff/login (id + password from env on the
// backend) mints a short-lived staff JWT which we keep in sessionStorage and
// send as the Bearer on every staff call — the Supabase token provider is
// deliberately NOT used here. Anything but a live staff token gets 404 on
// every route (the surface is invisible); the /admin page treats 401/404 as
// "signed out" and drops back to its standalone login form.

export const STAFF_TOKEN_KEY = "sprntly_staff_token"

export function getStaffToken(): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.sessionStorage.getItem(STAFF_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setStaffToken(token: string): void {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.setItem(STAFF_TOKEN_KEY, token)
  } catch {
    // Storage unavailable (e.g. blocked) — the panel just won't stay signed in.
  }
}

export function clearStaffToken(): void {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.removeItem(STAFF_TOKEN_KEY)
  } catch {
    // ignore
  }
}

/** Like `request`, but authed with an explicit internal-surface JWT instead of
 *  the app session (no cookies, no Supabase token provider). Shared by the
 *  staff panel and the transcript viewer, which hold separate credentials. */
async function bearerRequest<T>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  token: string | null,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = body
    ? { "Content-Type": "application/json", Accept: "application/json" }
    : { Accept: "application/json" }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
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

const staffRequest = <T,>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
) => bearerRequest<T>(method, path, getStaffToken(), body)

export const staffAuth = {
  /** Dedicated staff login. Stores the returned token on success. */
  login: async (id: string, password: string) => {
    const out = await staffRequest<{
      token: string
      token_type: "bearer"
      expires_in: number
    }>("POST", "/v1/staff/login", { id, password })
    setStaffToken(out.token)
    return out
  },
  logout: () => clearStaffToken(),
  hasToken: () => getStaffToken() != null,
}

export type StaffCompany = {
  id: string
  slug: string
  display_name: string
  created_at: string | null
  /** Max members incl. pending invites; null = unlimited. */
  seat_limit: number | null
  prototype_enabled: boolean
  /** true ⇒ runs on Sprntly's platform Claude key; false ⇒ must bring their own. */
  use_platform_key: boolean
  feature_flags: Record<string, boolean>
  /** Whether a BYOK key is stored (never the key itself). */
  llm_key_configured: boolean
  member_count: number
  pending_invite_count: number
}

export type StaffEntitlementsPatch = {
  seat_limit?: number | null
  prototype_enabled?: boolean
  use_platform_key?: boolean
  /** Partial merge — only the keys sent change. */
  feature_flags?: Record<string, boolean>
}

export type OrgInvite = {
  id: string
  email: string
  company_name: string
  seat_limit: number | null
  prototype_enabled: boolean
  use_platform_key: boolean
  feature_flags: Record<string, boolean>
  status: "pending" | "accepted" | "revoked"
  company_id: string | null
  created_at: string | null
  accepted_at: string | null
  email_sent?: boolean
}

export type OrgInviteIn = {
  email: string
  company_name: string
  seat_limit?: number | null
  prototype_enabled?: boolean
  use_platform_key?: boolean
  feature_flags?: Record<string, boolean>
}

export const staffApi = {
  listCompanies: () =>
    staffRequest<{ companies: StaffCompany[] }>("GET", "/v1/staff/companies"),
  updateCompany: (companyId: string, patch: StaffEntitlementsPatch) =>
    staffRequest<StaffCompany>(
      "PATCH",
      `/v1/staff/companies/${companyId}`,
      patch,
    ),
  listInvites: () =>
    staffRequest<{ invites: OrgInvite[] }>("GET", "/v1/staff/invites"),
  createInvite: (body: OrgInviteIn) =>
    staffRequest<OrgInvite>("POST", "/v1/staff/invites", body),
  revokeInvite: (inviteId: string) =>
    staffRequest<void>("DELETE", `/v1/staff/invites/${inviteId}`),
  resendInvite: (inviteId: string) =>
    staffRequest<OrgInvite>("POST", `/v1/staff/invites/${inviteId}/resend`),
}

// ── Internal transcript viewer (/v1/transcripts) ──
// Read-only cross-tenant chat review, gated by its own SHARED ACCESS CODE —
// deliberately a different credential from the staff panel above, so reading
// transcripts doesn't also grant entitlement edits. The page lives at an
// obscure URL, but that path is cosmetic: this is a static export, so the
// backend code check is the only real gate. Same 401/404 ⇒ signed-out posture
// as the staff panel.

export const TRANSCRIPTS_TOKEN_KEY = "sprntly_transcripts_token"

export function getTranscriptsToken(): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.sessionStorage.getItem(TRANSCRIPTS_TOKEN_KEY)
  } catch {
    return null
  }
}

export function setTranscriptsToken(token: string): void {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.setItem(TRANSCRIPTS_TOKEN_KEY, token)
  } catch {
    // Storage unavailable (e.g. blocked) — the viewer just won't stay signed in.
  }
}

export function clearTranscriptsToken(): void {
  if (typeof window === "undefined") return
  try {
    window.sessionStorage.removeItem(TRANSCRIPTS_TOKEN_KEY)
  } catch {
    // ignore
  }
}

const transcriptsRequest = <T,>(
  method: "GET" | "POST" | "PATCH" | "DELETE",
  path: string,
  body?: unknown,
) => bearerRequest<T>(method, path, getTranscriptsToken(), body)

export const transcriptsAuth = {
  /** Shared-access-code login. Stores the returned token on success. */
  login: async (code: string) => {
    const out = await transcriptsRequest<{
      token: string
      token_type: "bearer"
      expires_in: number
    }>("POST", "/v1/transcripts/login", { code })
    setTranscriptsToken(out.token)
    return out
  },
  logout: () => clearTranscriptsToken(),
  hasToken: () => getTranscriptsToken() != null,
}

export type TranscriptSummary = {
  id: number
  company_id: string
  /** Resolved display name, falling back to the raw id. */
  company_name: string
  user_id: string | null
  /** Full name / email of the member who had the chat; null for legacy rows. */
  user_label: string | null
  title: string
  preview: string
  agent_type: string
  prd_id: number | null
  turn_count: number
  created_at: string | null
  updated_at: string | null
}

export type TranscriptTurn = {
  id: number
  /** A turn is ONE message, not a user+AI pair. */
  role: "user" | "assistant"
  content: string
  created_at: string | null
}

export type TranscriptDetail = {
  conversation: TranscriptSummary & {
    /** Legacy single-shot shape — populated on old rows that have no turns. */
    query: string
    reply: string
  }
  turns: TranscriptTurn[]
}

export type TranscriptFilters = {
  /** Inclusive, YYYY-MM-DD (UTC). */
  date_from?: string
  /** Inclusive, YYYY-MM-DD (UTC). */
  date_to?: string
  company_id?: string
  limit?: number
}

export const transcriptsApi = {
  listCompanies: () =>
    transcriptsRequest<{ companies: { id: string; display_name: string }[] }>(
      "GET",
      "/v1/transcripts/companies",
    ),
  listConversations: (filters: TranscriptFilters = {}) => {
    const qs = new URLSearchParams()
    if (filters.date_from) qs.set("date_from", filters.date_from)
    if (filters.date_to) qs.set("date_to", filters.date_to)
    if (filters.company_id) qs.set("company_id", filters.company_id)
    if (filters.limit != null) qs.set("limit", String(filters.limit))
    const q = qs.toString()
    return transcriptsRequest<{
      conversations: TranscriptSummary[]
      has_more: boolean
    }>("GET", `/v1/transcripts/conversations${q ? `?${q}` : ""}`)
  },
  getConversation: (id: number) =>
    transcriptsRequest<TranscriptDetail>(
      "GET",
      `/v1/transcripts/conversations/${id}`,
    ),
}

export const orgInviteApi = {
  /** Apply the signed-in owner's pending org invite to their new company.
   *  404 ⇒ no pending invite (the normal self-serve case) — callers ignore it. */
  claim: () => api.post<{ applied: boolean }>("/v1/org-invites/claim"),
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
  /** The member who created (and exclusively owns) this chat. Chats are
   *  per-user within a workspace — the backend only ever returns the
   *  caller's own rows, so this always equals the logged-in user's id. */
  user_id: string | null
  title: string
  preview: string
  agent_type: string
  query: string
  reply: string
  pinned: boolean
  /** The PRD this conversation is about, when opened from a PRD tab (else null). */
  prd_id?: number | null
  created_at: string
  updated_at: string
}

/** Extracted text of a file attached to a chat turn — persisted with the turn
 *  so reloaded threads (and the chat→PRD flow) still see earlier documents.
 *  `key`/`mime` point at the ORIGINAL uploaded file in storage so a reopened chat
 *  can render the real document (PDF/image inline, everything downloadable) — not
 *  just the extracted text. Null on legacy turns / text pasted without an upload. */
export type TurnAttachment = {
  name: string
  content: string
  key?: string | null
  mime?: string | null
  size?: number | null
}

export const attachmentsApi = {
  /** Stash the ORIGINAL uploaded file so a reopened chat can render it back.
   *  Returns the storage key + sniffed metadata to persist on the turn. */
  upload: (file: File) => {
    const form = new FormData()
    form.append("file", file, file.name)
    return api.post<{ key: string; name: string; mime: string; size: number }>(
      "/v1/conversations/attachments",
      form,
    )
  },
  /** Fresh short-lived signed URLs (view inline + download) for a stored key.
   *  Bearer-authed here; the returned URLs are public so an <iframe>/<img> can
   *  load them directly. Re-minted on every viewer open (the URLs expire). */
  sign: (key: string, name?: string) =>
    api.get<{ view_url: string; download_url: string; mime: string }>(
      `/v1/conversations/attachments/sign?key=${encodeURIComponent(key)}${name ? `&name=${encodeURIComponent(name)}` : ""}`,
    ),
}

export type ConversationTurn = {
  id: number
  conversation_id: number
  role: "user" | "assistant"
  content: string
  created_at: string
  attachments?: TurnAttachment[] | null
}

export const conversationsApi = {
  list: () =>
    api.get<{ conversations: ConversationRecord[] }>("/v1/conversations"),
  create: (body: { title: string; preview?: string; agent_type?: string; query?: string; reply?: string; pinned?: boolean; prd_id?: number }) =>
    api.post<ConversationRecord>("/v1/conversations", body),
  /** Most recent conversation (with its turns) for a PRD, so reopening the PRD
   *  tab can rehydrate the earlier chat. `conversation` is null when none exists. */
  byPrd: (prdId: number) =>
    api.get<{ conversation: ConversationRecord | null; turns: ConversationTurn[] }>(`/v1/conversations/by-prd/${prdId}`),
  /** Evidence mirror of byPrd — most recent conversation (with turns) bound to
   *  an Evidence doc via `conversations.evidence_id`. `conversation` is null
   *  when the caller has none (never generated it, or it predates this
   *  linkage) — never 404. */
  byEvidence: (evidenceId: number) =>
    api.get<{ conversation: ConversationRecord | null; turns: ConversationTurn[] }>(`/v1/conversations/by-evidence/${evidenceId}`),
  update: (id: number, body: { title?: string; preview?: string; query?: string; reply?: string; pinned?: boolean; prd_id?: number }) =>
    api.patch<ConversationRecord>(`/v1/conversations/${id}`, body),
  remove: (id: number) =>
    api.delete(`/v1/conversations/${id}`),
  /** List all turns (messages) in a conversation, oldest first. */
  listTurns: (conversationId: number) =>
    api.get<{ turns: ConversationTurn[] }>(`/v1/conversations/${conversationId}/turns`),
  /** Add a turn to a conversation. `attachments` carries the extracted text of
   *  files attached to this turn (persisted so a reloaded thread and the
   *  chat→PRD flow can still ground on documents attached earlier). */
  addTurn: (
    conversationId: number,
    role: "user" | "assistant",
    content: string,
    attachments?: TurnAttachment[],
  ) =>
    api.post<ConversationTurn>(`/v1/conversations/${conversationId}/turns`, {
      role,
      content,
      ...(attachments && attachments.length ? { attachments } : {}),
    }),
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
  | {
      type: "report"
      id: number
      title: string
      /** Always "" — a report is complete the moment it is captured, so there is
       *  no lifecycle for the row to render (contrast prototype's status). */
      status: string
      created_at: string
      /** The report KIND: the skill id that produced it, e.g.
       *  "voice-of-customer-report". Drives the badge sub-label. */
      skill: string
      /** Whether a share link exists. The TOKEN is never in the listing — only
       *  the share dialog fetches it. */
      share_mode: "private" | "public" | "passcode"
      /** `conversation_*` / `prd_*` are the report's ATTACHMENT — the chat room
       *  and PRD it was generated in. Either pair may be null (the run carried no
       *  such context); a non-null id with a null title means that chat/PRD has
       *  since been deleted, so the row shows no "from" label rather than a
       *  fabricated one. */
      source: {
        skill: string
        question: string
        conversation_id: number | null
        conversation_title: string | null
        prd_id: number | null
        prd_title: string | null
      }
      /** The listing carries no `html` — the body is fetched by id on open. */
      open: { report_id: number }
    }
  | {
      /** A STANDALONE ticket set — tickets generated from a chat with no PRD
       *  behind them (`ticket_sets`). A PRD's tickets are NOT in this library:
       *  they belong to the PRD row, which is already here. */
      type: "ticket_set"
      id: number
      /** The set's LLM-derived name, or "" before the naming leg ran. The row
       *  renders its own fallback rather than a fabricated title. */
      title: string
      /** Lifecycle. Aggregation filters to generating|ready — a `failed` run
       *  produced nothing and is not an artifact (db/artifacts.py). */
      status: "generating" | "ready"
      created_at: string
      /** How many tickets the set holds. Counted server-side from `stories`,
       *  which is never shipped to the client on this listing. */
      ticket_count: number
      /** The chat the set was born in. `conversation_id` with a null
       *  `conversation_title` means that chat was deleted (`on delete set
       *  null` leaves the id): the row then omits the "from <chat>" clause
       *  rather than inventing a label. `question` is the original request
       *  (`ticket_sets.source_text`). */
      source: {
        conversation_id: number | null
        conversation_title: string | null
        question: string
      }
      open: { ticket_set_id: number }
    }

/** One captured report, body included (GET /v1/reports/{id}). */
export type ReportDoc = {
  id: number
  skill: string
  title: string
  question: string
  /** The self-contained HTML document, rendered verbatim in a sandboxed iframe. */
  html: string
  created_at: string
  conversation_id: number | null
  prd_id: number | null
  share_mode: "private" | "public" | "passcode"
  /** Null while private — the link is only revealed once sharing is on. */
  share_token: string | null
}

export const artifactsApi = {
  /** Unified artifact list for a company slug, newest first. */
  list: (company: string) =>
    api
      .get<{ artifacts: ArtifactItem[] }>(
        `/v1/artifacts?dataset=${encodeURIComponent(company)}`,
      )
      .then((r) => r.artifacts),
  /** LLM chat summary of a freshly generated artifact. Best-effort by
   *  contract: the backend returns {summary: null} on any summarizer failure
   *  (never an error), and callers skip posting in that case. */
  chatSummary: (kind: "prd" | "evidence" | "prototype" | "ticket_set", id: number) =>
    api.post<{ summary: string | null }>("/v1/artifacts/chat-summary", { kind, id }),
}

/** One row in a thread's report list (GET /v1/reports?conversation_id=…) — the
 *  same document as `ReportDoc` minus the body, which the list never carries. */
export type ReportSummary = Omit<ReportDoc, "html" | "share_token">

export const reportsApi = {
  /** One captured report including its HTML body. The artifact listing omits the
   *  body (it would carry N full documents), so the viewer fetches it on open. */
  get: (reportId: number) => api.get<ReportDoc>(`/v1/reports/${reportId}`),

  /** Every report captured in one chat thread, newest first — what the chat
   *  panel's Reports tab lists. Bodies are omitted; opening a row fetches that
   *  one document via `get`. */
  listForConversation: (conversationId: number) =>
    api
      .get<{ reports: ReportSummary[] }>(`/v1/reports?conversation_id=${conversationId}`)
      .then((r) => r.reports),

  /**
   * Download the report as a PDF. Rendered SERVER-side (headless Chromium over
   * the document's own `@media print` rules), so every download is identical
   * regardless of the viewer's browser — hence a blob fetch rather than
   * `window.print()`. Returns `application/pdf`, not JSON, so it bypasses the
   * shared `request<T>` helper while keeping the same auth path.
   *
   * 503 means the renderer was unavailable; the caller should say so rather than
   * saving a broken file.
   */
  downloadPdf: async (reportId: number): Promise<{ blob: Blob; filename: string }> => {
    const token = accessTokenProvider ? await accessTokenProvider() : null
    const headers: Record<string, string> = { Accept: "application/pdf" }
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(`${API_URL}/v1/reports/${reportId}/pdf`, {
      method: "GET", headers, credentials: "include",
    })
    if (!res.ok) throw new ApiError(res.status, await res.text())
    return { blob: await res.blob(), filename: filenameFromDisposition(res.headers) }
  },

  /** Turn link sharing on/off. Passcode is required iff share_mode==="passcode".
   *  The returned token is null while private. */
  share: (
    reportId: number,
    body: { share_mode: "private" | "public" | "passcode"; passcode?: string },
  ) =>
    api.post<{ share_mode: string; share_token: string | null }>(
      `/v1/reports/${reportId}/share`, body,
    ),
}

/** Parse `attachment; filename="x.pdf"` → `x.pdf`, falling back to `report.pdf`. */
function filenameFromDisposition(headers: Headers): string {
  const match = /filename="([^"]+)"/.exec(headers.get("content-disposition") ?? "")
  return match?.[1] || "report.pdf"
}

export const documentsApi = {
  /**
   * Render an assembled HTML document (PRD, Evidence, or the two combined) to
   * PDF server-side and get the file back.
   *
   * Same renderer as `reportsApi.downloadPdf` — headless Chromium over the
   * document's own `@media print` rules — so a PRD download is byte-identical
   * across browsers and carries the same watermark and sprntly.ai footer as a
   * report. This replaced a `window.print()` dialog, which produced a different
   * file per browser and could not be marked.
   *
   * The HTML is sent rather than read server-side by id because these panels are
   * editable: see backend/app/routes/documents.py.
   *
   * 503 means the renderer was unavailable; the caller should say so rather than
   * saving a broken file.
   */
  downloadPdf: async (
    html: string,
    filename: string,
  ): Promise<{ blob: Blob; filename: string }> => {
    const token = accessTokenProvider ? await accessTokenProvider() : null
    const headers: Record<string, string> = {
      Accept: "application/pdf",
      "Content-Type": "application/json",
    }
    if (token) headers["Authorization"] = `Bearer ${token}`
    const res = await fetch(`${API_URL}/v1/documents/pdf`, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify({ html, filename }),
    })
    if (!res.ok) throw new ApiError(res.status, await res.text())
    return { blob: await res.blob(), filename: filenameFromDisposition(res.headers) }
  },
}

/** What an anonymous visitor on `/r/<token>` can see — four fields, enforced
 *  server-side by a response_model (routes/reports_public.py). */
export type PublicReport = {
  title: string
  /** Humanised report kind, e.g. "Voice of customer report". */
  kind: string
  html: string
  created_at: string | null
}

/**
 * The no-auth share surface. These calls deliberately send NO credentials: the
 * token in the URL is the access primitive, and a signed-in viewer must see
 * exactly what a stranger sees.
 */
export const publicReportsApi = {
  /** 401 `passcode_required` when the link is passcode-gated; 404 when the token
   *  is unknown OR sharing was revoked (the two are indistinguishable by design). */
  get: async (token: string): Promise<PublicReport> => {
    const res = await fetch(`${API_URL}/v1/public/reports/${encodeURIComponent(token)}`)
    if (!res.ok) throw new ApiError(res.status, await res.text())
    return (await res.json()) as PublicReport
  },

  /** Exchange a passcode for the document. 401 on a wrong passcode, 429 once the
   *  per-token attempt budget is spent. */
  unlock: async (token: string, passcode: string): Promise<PublicReport> => {
    const res = await fetch(
      `${API_URL}/v1/public/reports/${encodeURIComponent(token)}/unlock`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passcode }),
      },
    )
    if (!res.ok) throw new ApiError(res.status, await res.text())
    return (await res.json()) as PublicReport
  },

  /** PDF of a shared report. POST so a passcode-gated link can carry its passcode
   *  in the body instead of a URL that would land in access logs and history. */
  downloadPdf: async (
    token: string, passcode?: string,
  ): Promise<{ blob: Blob; filename: string }> => {
    const res = await fetch(
      `${API_URL}/v1/public/reports/${encodeURIComponent(token)}/pdf`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/pdf" },
        body: JSON.stringify(passcode ? { passcode } : {}),
      },
    )
    if (!res.ok) throw new ApiError(res.status, await res.text())
    return { blob: await res.blob(), filename: filenameFromDisposition(res.headers) }
  },
}

// ── MCP tokens (customer-facing Model Context Protocol access) ──

/**
 * What the token was minted for — picked at creation, immutable after.
 * developer = ticket + PRD tools only; pm = the full MCP tool set
 * (adds datasets, backlog, Top Insights brief).
 */
export type McpTokenRole = "developer" | "pm"

export type McpToken = {
  id: string
  name: string
  token_role: McpTokenRole
  token_prefix: string
  created_at: string
  last_used_at: string | null
  revoked_at: string | null
}

export type McpTokenCreated = {
  id: string
  name: string
  /** Raw bearer token — present ONLY in the create response, never again. */
  token: string
  token_role: McpTokenRole
  token_prefix: string
  created_at: string
}

export const mcpTokensApi = {
  list: () => api.get<{ tokens: McpToken[] }>("/v1/mcp-tokens"),
  create: (name: string, token_role: McpTokenRole) =>
    api.post<McpTokenCreated>("/v1/mcp-tokens", { name, token_role }),
  revoke: (id: string) =>
    api.delete<{ ok: true }>(`/v1/mcp-tokens/${encodeURIComponent(id)}`),
}

// ── Projects (shared container + collaboration context, build spec §5) ──

/** Artifact type keys a project can hold (`project_artifacts.artifact_type`). */
export type ProjectArtifactType = "prd" | "evidence" | "prototype" | "report" | "ticket_set"

/** One row from `GET /v1/projects` — recency-ordered, MEMBER-scoped by the
 *  backend (AD-P11: a workspace project the caller isn't a member of never
 *  appears here). Counts are derived at read time, never stored. Note: this
 *  list row carries `member_count` only (no per-member identity/avatar data —
 *  that lives on `GET /v1/projects/{id}`), so a project card cannot render
 *  real member initials from this endpoint alone. */
export type ProjectListItem = {
  id: number
  company_id: string
  workspace_id: string
  name: string
  origin: "manual" | "prd_auto" | "artifact"
  created_by: string
  created_at: string
  updated_at: string
  artifact_counts: Partial<Record<ProjectArtifactType, number>>
  member_count: number
  has_group_chat: boolean
  memory_count: number
}

/** A project member row from `GET /v1/projects/{id}` — either a real
 *  `project_members` row (`kind: "human"`) or the virtual "Sprntly" agent
 *  member the backend prepends to every response (`kind: "agent"`,
 *  AD-P6 — never a stored row, always this shape). */
export type ProjectMember =
  | {
      kind: "human"
      user_id: string
      name: string | null
      email: string | null
      avatar_url: string | null
      job_role: string | null
      added_at: string | null
    }
  | {
      kind: "agent"
      user_id: null
      name: string
      role_label: string
      status: string
    }

/** `GET /v1/projects/{id}` — the project row plus its member roster
 *  (human members + the prepended virtual agent member, AD-P6) and the
 *  project's single group-chat id (`null` until a group chat has been
 *  created for this project). Membership-gated server-side: a same-tenant
 *  non-member gets 403, a foreign-tenant project id 404s
 *  (`ApiError.status`, never a crash). */
export type ProjectDetail = {
  id: number
  company_id: string
  workspace_id: string
  name: string
  origin: "manual" | "prd_auto" | "artifact"
  created_by: string
  created_at: string
  updated_at: string
  members: ProjectMember[]
  group_chat_id: number | null
}

/** `GET /v1/projects/{id}/memory/summary` — the cached synthesized
 *  "what this project knows" summary, read-only (never triggers an LLM
 *  call). `summary_md` is `null` until a summary has been generated;
 *  `entry_count` always reflects the current discrete-entry count. */
export type ProjectMemorySummary = {
  project_id?: number
  summary_md: string | null
  entry_count: number
  generated_at?: string
  stale: boolean
}

export const projectsApi = {
  /** Projects in the caller's active workspace, recency-ordered, scoped to
   *  the caller's memberships by the backend — no `dataset`/company arg
   *  (tenancy rides the `X-Workspace-Id` header, `ask.py`'s pattern). */
  list: () => api.get<{ projects: ProjectListItem[] }>("/v1/projects").then((r) => r.projects),
  /** Create a project — manual (blank) or from-artifact/PRD-auto (`origin`). */
  create: (payload: { name: string; origin?: "manual" | "prd_auto" | "artifact" }) =>
    api.post<ProjectListItem>("/v1/projects", payload),
  /** Project detail: members (incl. the virtual agent member) + group-chat
   *  id. Throws `ApiError` with `.status` 403 (same-tenant non-member) or
   *  404 (foreign-tenant/absent) — callers must handle both without
   *  crashing (design-spec AC — membership-gated detail view). */
  get: (id: number | string) =>
    api.get<ProjectDetail>(`/v1/projects/${encodeURIComponent(String(id))}`),
  /** The project's artifacts, in the same unified shape `GET /v1/artifacts`
   *  returns (AD-P1/AD-P12), filtered to this project's refs. */
  artifacts: (id: number | string) =>
    api
      .get<{ artifacts: ArtifactItem[] }>(`/v1/projects/${encodeURIComponent(String(id))}/artifacts`)
      .then((r) => r.artifacts),
  /** The cached project-memory summary — read-only, no LLM call. */
  memorySummary: (id: number | string) =>
    api.get<ProjectMemorySummary>(`/v1/projects/${encodeURIComponent(String(id))}/memory/summary`),
}
