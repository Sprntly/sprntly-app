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
export function isRecoveryFlow(href: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(href)
  } catch {
    return false
  }
  if (parsed.searchParams.get("type") === "recovery") return true
  const hash = parsed.hash.startsWith("#") ? parsed.hash.slice(1) : parsed.hash
  if (!hash) return false
  const hashParams = new URLSearchParams(hash)
  return hashParams.get("type") === "recovery"
}
