import { createClient, type SupabaseClient } from "@supabase/supabase-js"
import { fetchWorkspaceForUser } from "../onboarding/store"
import { slugForStep, ONBOARDING_STEP_SLUGS } from "../onboarding/types"
import { projectPath } from "../routes"

let browserClient: SupabaseClient | null = null

function trimEnv(value: string | undefined): string {
  return (value ?? "").trim()
}

/** Must be https://<project-ref>.supabase.co (no trailing slash). */
export function parseSupabaseUrl(raw: string | undefined): string | null {
  const value = trimEnv(raw)
  if (!value) return null
  try {
    const parsed = new URL(value)
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null
    if (!parsed.hostname) return null
    // Supabase project URL is origin-only; reject stray paths/query.
    if (parsed.pathname !== "/" && parsed.pathname !== "") return null
    if (parsed.search || parsed.hash) return null
    return parsed.origin
  } catch {
    return null
  }
}

export function getSupabasePublicConfig(): {
  url: string
  anonKey: string
} | null {
  const url = parseSupabaseUrl(process.env.NEXT_PUBLIC_SUPABASE_URL)
  const anonKey = trimEnv(process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY)
  if (!url || !anonKey) return null
  return { url, anonKey }
}

export function isSupabaseConfigured(): boolean {
  return getSupabasePublicConfig() !== null
}

/** Whoever was already persisted in this browser BEFORE the Supabase client
 *  initialized — enough to re-establish that session (tokens) and to tell it
 *  apart from a different user (userId/email). */
export type PriorSessionSnapshot = {
  userId: string
  email: string | null
  accessToken: string
  refreshToken: string
}

let priorSnapshot: PriorSessionSnapshot | null = null
let priorSnapshotTaken = false

/**
 * Pull the persisted Supabase session out of a Storage synchronously.
 *
 * supabase-js (browser, localStorage) writes the Session object as a single
 * JSON string under `sb-<ref>-auth-token`; older shapes wrap it in
 * `{ currentSession }`. Returns null when nothing usable is stored. Pure and
 * side-effect free so it can be unit tested against a fake Storage.
 */
export function readPersistedSession(store: Storage): PriorSessionSnapshot | null {
  try {
    for (let i = 0; i < store.length; i++) {
      const key = store.key(i)
      // Match the auth-token slot only — skip the PKCE `-code-verifier` sibling.
      if (!key || !key.startsWith("sb-") || !key.endsWith("-auth-token")) continue
      const raw = store.getItem(key)
      if (!raw) continue
      let obj: Record<string, unknown>
      try {
        obj = JSON.parse(raw) as Record<string, unknown>
      } catch {
        continue
      }
      const s = (
        typeof obj.access_token === "string"
          ? obj
          : (obj.currentSession as Record<string, unknown> | undefined)
      ) as
        | { access_token?: unknown; refresh_token?: unknown; user?: { id?: unknown; email?: unknown } }
        | undefined
      const userId = s?.user?.id
      if (
        typeof s?.access_token === "string" &&
        typeof s?.refresh_token === "string" &&
        typeof userId === "string"
      ) {
        return {
          userId,
          email: typeof s.user?.email === "string" ? s.user.email : null,
          accessToken: s.access_token,
          refreshToken: s.refresh_token,
        }
      }
    }
  } catch {
    /* storage disabled/unavailable — treat as no prior session */
  }
  return null
}

/** The session that was already persisted in this browser BEFORE the Supabase
 *  client initialized — and thus before any invite/magic-link token in the URL
 *  could overwrite it. Null when nobody was signed in. Used by /auth/callback
 *  to keep an invite link from silently hijacking an existing session. */
export function getPriorSessionSnapshot(): PriorSessionSnapshot | null {
  return priorSnapshot
}

/**
 * The invitee session minted by an invite magic link that was opened while
 * another user was already signed in.
 *
 * The link's one-time token is spent the instant it's clicked — reopening it
 * only ever shows "invalid or expired". So rather than discard the (already
 * minted) invitee session and force the user back to a dead link, we hold it
 * so /invite-conflict can offer switching INTO it without re-visiting the link.
 *
 * HELD IN sessionStorage, NOT JUST MEMORY. It used to be a module variable
 * only, which meant one reload of /invite-conflict destroyed the sole route
 * into the invited account while the invite link itself was already spent —
 * a dead end whose most natural exit is signing up again, which is how a
 * company ends up with a duplicate workspace (2026-08-19 incident). The
 * module variable stays as the fast path and as the sole store when storage
 * is unavailable (private mode, storage disabled).
 *
 * sessionStorage rather than localStorage on purpose: it is scoped to the tab
 * and dies with it, so a held invitee session cannot linger on a shared
 * machine. These are the same tokens the Supabase client already persists via
 * `persistSession: true`, so nothing new in kind is being written to storage —
 * only something shorter-lived. Cleared on both exits from /invite-conflict
 * (adopt or stay), so it never outlives the decision.
 */
export type PendingInviteSession = {
  email: string | null
  accessToken: string
  refreshToken: string
}

const PENDING_INVITE_STORAGE_KEY = "sprntly.pendingInviteSession"

let pendingInviteSession: PendingInviteSession | null = null

/** Shape-check a parsed value before trusting it as a session. Storage is
 *  attacker-writable in principle, and handing a malformed object to
 *  `setSession` produces a confusing failure rather than a clean "no held
 *  session" fallback. */
function isPendingInviteSession(value: unknown): value is PendingInviteSession {
  if (!value || typeof value !== "object") return false
  const v = value as Record<string, unknown>
  return (
    typeof v.accessToken === "string" &&
    v.accessToken.length > 0 &&
    typeof v.refreshToken === "string" &&
    v.refreshToken.length > 0 &&
    (v.email === null || typeof v.email === "string")
  )
}

export function setPendingInviteSession(session: PendingInviteSession | null): void {
  pendingInviteSession = session
  if (typeof window === "undefined") return
  try {
    if (session) {
      window.sessionStorage.setItem(
        PENDING_INVITE_STORAGE_KEY,
        JSON.stringify(session),
      )
    } else {
      window.sessionStorage.removeItem(PENDING_INVITE_STORAGE_KEY)
    }
  } catch {
    /* storage disabled/unavailable — the module variable still serves this
       page view, which is exactly the old behaviour. */
  }
}

/** Drop an unusable stored value. Separately guarded: the caller may be here
 *  precisely because storage is misbehaving, and the eviction must not become
 *  a second failure. */
function evictStoredInviteSession(): void {
  try {
    window.sessionStorage.removeItem(PENDING_INVITE_STORAGE_KEY)
  } catch {
    /* nothing further to do — the value is unreadable either way */
  }
}

export function getPendingInviteSession(): PendingInviteSession | null {
  if (pendingInviteSession) return pendingInviteSession
  if (typeof window === "undefined") return null

  let raw: string | null
  try {
    raw = window.sessionStorage.getItem(PENDING_INVITE_STORAGE_KEY)
  } catch {
    // Storage unavailable entirely — there is nothing stored to evict.
    return null
  }
  if (!raw) return null

  let parsed: unknown = null
  try {
    parsed = JSON.parse(raw)
  } catch {
    // Malformed JSON falls through to the shape check below, which evicts it.
    parsed = null
  }
  if (!isPendingInviteSession(parsed)) {
    // Junk in storage is a dead end of its own — drop it rather than repeating
    // the same failed restore on every read.
    evictStoredInviteSession()
    return null
  }

  pendingInviteSession = parsed
  return parsed
}

export function clearPendingInviteSession(): void {
  setPendingInviteSession(null)
}

export function getSupabase(): SupabaseClient {
  if (browserClient) return browserClient

  const config = getSupabasePublicConfig()
  if (!config) {
    throw new Error(
      "Supabase is not configured. Set NEXT_PUBLIC_SUPABASE_URL (https://YOUR_REF.supabase.co) and NEXT_PUBLIC_SUPABASE_ANON_KEY at build time, then redeploy.",
    )
  }

  // Snapshot the already-signed-in user BEFORE createClient runs its
  // detectSessionInUrl pass. An invite magic link in the URL overwrites the
  // persisted session the moment the client initializes; capturing it here —
  // synchronously, pre-init — lets /auth/callback tell "opened an invite while
  // already signed in as someone else" from a normal fresh sign-in and refuse
  // to hijack the existing account. See getPriorSessionSnapshot().
  if (!priorSnapshotTaken) {
    priorSnapshotTaken = true
    if (typeof window !== "undefined") {
      priorSnapshot = readPersistedSession(window.localStorage)
    }
  }

  browserClient = createClient(config.url, config.anonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
    },
  })
  return browserClient
}

/** Email-confirmation redirect (static export — client-side callback). */
export function authCallbackUrl(): string {
  if (typeof window === "undefined") return "/auth/callback"
  return `${window.location.origin}/auth/callback`
}

/** Where to send the user after a successful sign-in. */
export async function postLoginPath(): Promise<string> {
  const supabase = getSupabase()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return "/sign-in"

  if (!user.email_confirmed_at) return "/verify-email"

  const workspace = await fetchWorkspaceForUser(user.id)

  // Auto-accept-on-sign-in (CEO 2-A): if the user has no workspace yet,
  // check the backend for a pending invite that matches their verified
  // email. On success the backend creates their company_members row, so
  // the next workspace fetch resolves to a real company. Best-effort —
  // any failure (404 = no invite, network glitch) falls through to
  // onboarding without surfacing an error here.
  if (!workspace) {
    const accept = await tryAcceptInvite()
    if (accept.outcome === "accepted") {
      const fresh = await fetchWorkspaceForUser(user.id)
      if (fresh) {
        if (fresh.onboarding_completed_at) {
          // Project invite (AD-TNM3): the accept already landed them in the
          // project's `project_members` — send them straight to its private
          // chat rather than the dashboard. A plain workspace/org invite
          // carries no project_id and keeps the existing "/" landing.
          if (accept.projectId != null) {
            return projectPath(accept.projectId, { chat: "individual" })
          }
          return "/"
        }
        // slugForStep clamps the (possibly stale 7-step) index into range and
        // maps it to its semantic slug.
        return `/onboarding/${slugForStep(fresh.onboarding_step)}`
      }
    }
    // Guest account state: a user with zero company memberships who signed
    // up via a shared-artifact link carries the token in user_metadata (set
    // server-side at signUp() time — see lib/auth.tsx's SignUpInput — so it
    // survives the verify-email hop even across devices). Resolve it before
    // falling through to onboarding, which would otherwise create a brand
    // new company for someone who only meant to view/join an existing one.
    const pendingToken = user.user_metadata?.pending_share_token
    if (typeof pendingToken === "string" && pendingToken) {
      const { resolveArtifactShare, tryAutoJoinCompanyOnDomainMatch } = await import(
        "../artifactShareApi"
      )
      // One-shot, best-effort: right after email verification succeeds,
      // grant COMPANY (never workspace) membership when the verified email
      // domain matches the share's owning company. No-op if the caller
      // already has a company, the domain doesn't match, or the token is
      // bad — the /resolve call right below is the real gate either way, and
      // its existing same_company branch picks up a successful auto-join
      // naturally on this very next call.
      await tryAutoJoinCompanyOnDomainMatch(pendingToken)
      const outcome = await resolveArtifactShare(pendingToken)
      if (outcome?.outcome === "member") {
        // The auto-join above left them able to act in the artifact's
        // workspace already (an unbound legacy dataset, or a company whose
        // membership implies it) — send them to the plain deep link, NOT
        // the `share=` one, so AuthGate hands them the real editable app
        // instead of the read-only guest viewer.
        const prdParam = outcome.public_id ?? String(outcome.artifact_id)
        return `/?prd=${encodeURIComponent(prdParam)}`
      }
      if (outcome?.outcome === "guest_view") {
        // public_id (never artifact_id, the raw sequential id) is what this
        // guest's own landing URL should carry — the fallback to the int
        // only covers the (currently unreachable) case of a PRD row with no
        // public_id at all, same defensive-fallback shape useArtifactUrlSync
        // uses for its own reflect effect.
        const prdParam = outcome.public_id ?? String(outcome.artifact_id)
        return `/?prd=${encodeURIComponent(prdParam)}&share=${pendingToken}`
      }
      if (outcome?.outcome === "blocked") {
        return `/not-authorized?share=${pendingToken}&reason=${outcome.reason}`
      }
      // outcome undefined (network/other error, or a stale/malformed token
      // server-side has no row for) — fail OPEN to onboarding, never to a
      // stuck screen. The real security gate (view/join) is enforced
      // server-side by resolve/join regardless of how the user got here.
    }

    // Bare-link ("full parity") guest account state — the token-less
    // sibling of the pendingToken branch above, same rationale: a user who
    // signed up via a bare `?prd=` visit (no share row) carries the PRD's
    // opaque public_id in user_metadata (never the raw sequential id) so it
    // survives the verify-email hop on any device. `access=guest` on the
    // redirect target is what lets AuthGate keep routing THIS guest's own
    // future visits/refreshes through the guest pipeline without affecting
    // a real member's ordinary bare `?prd=` navigation (see AuthGate.tsx's
    // prdOnlyGuestMode). The redirect itself carries the public_id, NOT
    // outcome.artifact_id (the real int) — reflecting the int here would
    // reintroduce, on this guest's very first landing URL, exactly the
    // blind-enumeration exposure this scope exists to close.
    const pendingPrdPublicId = user.user_metadata?.pending_prd_public_id
    if (typeof pendingPrdPublicId === "string" && pendingPrdPublicId) {
      const { resolvePrdAccess, tryAutoJoinCompanyOnDomainMatchForPrd } = await import(
        "../prdAccessApi"
      )
      await tryAutoJoinCompanyOnDomainMatchForPrd(pendingPrdPublicId)
      const outcome = await resolvePrdAccess(pendingPrdPublicId)
      if (outcome?.outcome === "member") {
        // No `access=guest` marker — the real app, editable. Same rationale
        // as the pendingToken branch's own `member` case above.
        return `/?prd=${encodeURIComponent(pendingPrdPublicId)}`
      }
      if (outcome?.outcome === "guest_view") {
        return `/?prd=${encodeURIComponent(pendingPrdPublicId)}&access=guest`
      }
      if (outcome?.outcome === "blocked") {
        return `/not-authorized?reason=${outcome.reason}`
      }
      // fail OPEN to onboarding — same rationale as the pendingToken branch.
    }

    // Pre-onboarding profile gate: a brand-new user whose profile is missing
    // a first name OR the company-vs-personal account type goes to the
    // unnumbered `your-name` gate first. Google sign-ups always miss the
    // account type (the choice only exists on the email sign-up form) and may
    // miss the name; email/password users provide both at sign-up and skip
    // straight to the first numbered step. A missing profile row is treated
    // as missing both → show the gate.
    if (!(await hasCompleteSignupProfile(user.id))) {
      return "/onboarding/your-name"
    }
    return `/onboarding/${ONBOARDING_STEP_SLUGS[0]}`
  }
  // The user already belongs to a company. A pending invite for their email
  // still needs resolving at sign-in:
  //  - same company, more workspaces → the backend accept grants them
  //    (idempotent "second invite" semantics), then continue in normally;
  //  - a DIFFERENT company → the one-user-one-company invariant means they
  //    can never accept it, and silently ignoring the invite leaves both
  //    sides confused — route to the explanatory blocked-invite page instead.
  //  - no invite (404) / transient error → normal flow.
  const accept = await tryAcceptInvite()
  if (accept.outcome === "conflict") return "/invite-conflict"

  if (workspace.onboarding_completed_at) {
    // Project invite (AD-TNM3): an existing-company member accepting a
    // project-carrying invite lands directly in that project's private chat.
    // Plain workspace/org invites (no project_id) keep the "/" landing.
    if (accept.outcome === "accepted" && accept.projectId != null) {
      return projectPath(accept.projectId, { chat: "individual" })
    }
    return "/"
  }
  return `/onboarding/${slugForStep(workspace.onboarding_step)}`
}

/**
 * True when the user's profile already has BOTH a non-empty first name and an
 * account type (the company-vs-personal signup choice). Minimal query; a
 * missing row or any error is treated as incomplete so the gate shows rather
 * than silently skipping it.
 */
async function hasCompleteSignupProfile(userId: string): Promise<boolean> {
  try {
    const supabase = getSupabase()
    const { data, error } = await supabase
      .from("profiles")
      .select("first_name, account_type")
      .eq("id", userId)
      .maybeSingle()
    if (error || !data) return false
    const row = data as { first_name?: unknown; account_type?: unknown }
    return (
      String(row.first_name ?? "").trim().length > 0 &&
      (row.account_type === "company" || row.account_type === "personal")
    )
  } catch {
    return false
  }
}

/**
 * Where NotAuthorizedScreen's "Continue to your workspace" action should
 * send the currently signed-in user — their own account's real home, NEVER
 * a re-resolution of the share token that just blocked them (so this can
 * never loop back to /not-authorized: unlike postLoginPath()'s guest
 * branch, this never looks at pending_share_token at all).
 *
 * "Has a real workspace" mirrors the backend's own require_workspace /
 * _resolve_workspace invariant exactly (backend/app/auth.py): an owner/admin
 * company role implicitly administers every workspace in their company (no
 * workspace_members row needed), while a plain member/viewer needs a real
 * workspace_members row or every workspace-scoped read 403s. This is
 * deliberately NOT the same check postLoginPath() uses elsewhere
 * (fetchWorkspaceForUser only checks company membership) — a company member
 * with zero workspace_members rows would otherwise be sent to "/" and hit a
 * wall of 403s.
 *
 *  - has one → "/" (their home; safe, no loop).
 *  - has a company but no workspace → their OWN company's onboarding step
 *    (the exact path shape postLoginPath()'s existing-company branch uses —
 *    never a fresh "create a company" flow, they already have one).
 *  - has no company at all → the same zero-company onboarding entry
 *    postLoginPath()'s guest branch uses for a brand-new user.
 */
export async function notAuthorizedContinuePath(): Promise<string> {
  const supabase = getSupabase()
  const {
    data: { user },
  } = await supabase.auth.getUser()
  if (!user) return "/sign-in"

  const { data: membership } = await supabase
    .from("company_members")
    .select("role")
    .eq("user_id", user.id)
    .limit(1)
    .maybeSingle()

  if (!membership) {
    if (!(await hasCompleteSignupProfile(user.id))) return "/onboarding/your-name"
    return `/onboarding/${ONBOARDING_STEP_SLUGS[0]}`
  }

  const role = String((membership as { role?: unknown }).role ?? "")
  if (role === "owner" || role === "admin") return "/"

  const { data: workspaceMember } = await supabase
    .from("workspace_members")
    .select("id")
    .eq("user_id", user.id)
    .limit(1)
    .maybeSingle()
  if (workspaceMember) return "/"

  // A company member with no workspace_members row yet — continue THEIR
  // company's own onboarding at its real step.
  const workspace = await fetchWorkspaceForUser(user.id)
  if (workspace) return `/onboarding/${slugForStep(workspace.onboarding_step)}`
  return `/onboarding/${ONBOARDING_STEP_SLUGS[0]}`
}

/** Outcome of the sign-in invite-accept attempt:
 *  - accepted — the backend materialised the invite (membership/workspaces)
 *  - none     — no pending invite for this email (404)
 *  - conflict — the invite is from ANOTHER company; the one-user-one-company
 *               invariant blocks acceptance (409)
 *  - error    — network/other failure; treated as best-effort no-op */
type InviteAcceptOutcome = "accepted" | "none" | "conflict" | "error"

/** The accept outcome plus, on "accepted", the project the invite carried
 *  (AD-TNM3) — null for a plain workspace/org invite. postLoginPath uses a
 *  non-null projectId to land the accepter in that project's private chat. */
type InviteAcceptResult = {
  outcome: InviteAcceptOutcome
  projectId: number | null
}

async function tryAcceptInvite(): Promise<InviteAcceptResult> {
  try {
    // Lazy import keeps the api module out of the cold-start path of
    // postLoginPath (teamApi now lives in lib/teamApi, not TeamSettings).
    const { teamApi } = await import("../teamApi")
    const res = await teamApi.acceptInvite()
    const pid = res?.project_id
    return {
      outcome: "accepted",
      projectId: typeof pid === "number" && Number.isFinite(pid) ? pid : null,
    }
  } catch (err) {
    const { ApiError } = await import("../api")
    if (err instanceof ApiError) {
      if (err.status === 409) return { outcome: "conflict", projectId: null }
      if (err.status === 404) return { outcome: "none", projectId: null }
    }
    return { outcome: "error", projectId: null }
  }
}
