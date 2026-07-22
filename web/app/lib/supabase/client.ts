import { createClient, type SupabaseClient } from "@supabase/supabase-js"
import { fetchWorkspaceForUser } from "../onboarding/store"
import { slugForStep, ONBOARDING_STEP_SLUGS } from "../onboarding/types"

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
 * minted) invitee session and force the user back to a dead link, we hold it in
 * memory: /invite-conflict can then offer switching INTO it without re-visiting
 * the link. Memory only (never the URL) and one-shot — lost on a full page
 * reload, at which point /invite-conflict falls back to "ask for a fresh
 * invite".
 */
export type PendingInviteSession = {
  email: string | null
  accessToken: string
  refreshToken: string
}

let pendingInviteSession: PendingInviteSession | null = null

export function setPendingInviteSession(session: PendingInviteSession | null): void {
  pendingInviteSession = session
}

export function getPendingInviteSession(): PendingInviteSession | null {
  return pendingInviteSession
}

export function clearPendingInviteSession(): void {
  pendingInviteSession = null
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
    const accepted = (await tryAcceptInvite()) === "accepted"
    if (accepted) {
      const fresh = await fetchWorkspaceForUser(user.id)
      if (fresh) {
        if (fresh.onboarding_completed_at) return "/"
        // slugForStep clamps the (possibly stale 7-step) index into range and
        // maps it to its semantic slug.
        return `/onboarding/${slugForStep(fresh.onboarding_step)}`
      }
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
  if ((await tryAcceptInvite()) === "conflict") return "/invite-conflict"

  if (workspace.onboarding_completed_at) return "/"
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

/** Outcome of the sign-in invite-accept attempt:
 *  - accepted — the backend materialised the invite (membership/workspaces)
 *  - none     — no pending invite for this email (404)
 *  - conflict — the invite is from ANOTHER company; the one-user-one-company
 *               invariant blocks acceptance (409)
 *  - error    — network/other failure; treated as best-effort no-op */
type InviteAcceptOutcome = "accepted" | "none" | "conflict" | "error"

async function tryAcceptInvite(): Promise<InviteAcceptOutcome> {
  try {
    // Lazy import keeps the api module out of the cold-start path of
    // postLoginPath (teamApi now lives in lib/teamApi, not TeamSettings).
    const { teamApi } = await import("../teamApi")
    await teamApi.acceptInvite()
    return "accepted"
  } catch (err) {
    const { ApiError } = await import("../api")
    if (err instanceof ApiError) {
      if (err.status === 409) return "conflict"
      if (err.status === 404) return "none"
    }
    return "error"
  }
}
