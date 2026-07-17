/**
 * Detect whether a Supabase auth-callback URL is a password-recovery
 * landing.
 *
 * Supabase appends `type=recovery` to the redirect URL when the user
 * arrives from a reset-password email. Depending on SDK version + flow
 * (PKCE vs implicit), the param can live in either the query string or
 * the URL fragment, so we check both.
 *
 * Used by /auth/callback to route the user to /reset-password instead
 * of postLoginPath when a recovery is detected.
 */
/** The Supabase auth-flow `type` param from a callback URL ("recovery",
 *  "invite", "magiclink", "signup", …) — query string or fragment, whichever
 *  carries it — or null. Capture it BEFORE detectSessionInUrl strips the hash. */
export function authFlowType(href: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(href)
  } catch {
    return null
  }
  const fromQuery = parsed.searchParams.get("type")
  if (fromQuery) return fromQuery
  const hash = parsed.hash.startsWith("#") ? parsed.hash.slice(1) : parsed.hash
  if (!hash) return null
  return new URLSearchParams(hash).get("type")
}

export function isRecoveryFlow(href: string): boolean {
  return authFlowType(href) === "recovery"
}

/** True for a workspace-invite landing (admin invite_user_by_email link).
 *  /auth/callback routes these to /set-password — a brand-new invitee must
 *  create a password before entering the app. */
export function isInviteFlow(href: string): boolean {
  return authFlowType(href) === "invite"
}
