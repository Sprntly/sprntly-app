/** Email and password rules.
 *
 * Pre-launch (CEO 2026-06-06): consumer-domain emails (gmail, outlook,
 * yahoo, etc.) are accepted on signup. Once billing ships, signups from
 * a personal domain will gate on an active Sprntly subscription — see
 * isPersonalDomain() for the helper that flags those addresses so the
 * UI can show subscription-required copy at that time.
 *
 * For now this module only validates the *shape* of the email (plus a
 * basic regex). The old work-only block is gone.
 */

const CONSUMER_DOMAINS = new Set([
  "gmail.com",
  "googlemail.com",
  "yahoo.com",
  "yahoo.co.uk",
  "hotmail.com",
  "outlook.com",
  "live.com",
  "msn.com",
  "icloud.com",
  "me.com",
  "mac.com",
  "aol.com",
  "proton.me",
  "protonmail.com",
  "pm.me",
  "mail.com",
  "yandex.com",
  "gmx.com",
  "zoho.com",
])

/** Normalize an email for auth: trim surrounding whitespace and lowercase so
 *  sign-in / sign-up / reset are case-insensitive (matches GoTrue's own
 *  server-side normalization and any app-side email lookups). */
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase()
}

export function emailDomain(email: string): string | null {
  const trimmed = email.trim().toLowerCase()
  const at = trimmed.lastIndexOf("@")
  if (at < 1) return null
  return trimmed.slice(at + 1)
}

/** Team addresses that predate a company domain — always permitted. */
const BUILTIN_AUTH_EMAIL_ALLOWLIST = ["sprntly@gmail.com"] as const

/** Comma-separated full addresses (build-time env), merged with builtin list. */
export function authEmailAllowlist(): Set<string> {
  const raw = process.env.NEXT_PUBLIC_AUTH_EMAIL_ALLOWLIST ?? ""
  const fromEnv = raw
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean)
  return new Set([...BUILTIN_AUTH_EMAIL_ALLOWLIST, ...fromEnv])
}

export function isAllowlistedEmail(email: string): boolean {
  return authEmailAllowlist().has(email.trim().toLowerCase())
}

/** True iff this email is on a known consumer/personal-email domain.
 * Used by the UI (post-billing) to flag the "personal email — subscription
 * required" copy. Does NOT block signup on its own. */
export function isPersonalDomain(email: string): boolean {
  if (isAllowlistedEmail(email)) return false
  const domain = emailDomain(email)
  if (!domain) return false
  return CONSUMER_DOMAINS.has(domain)
}

/** Kept for compatibility (callers from before the 2026-06-06 unblock).
 * Now returns true for any well-formed email (no consumer-domain block).
 * Use isPersonalDomain() when you specifically need the "personal email"
 * signal. */
export function isWorkEmail(email: string): boolean {
  return validateWorkEmail(email) === null
}

/** Validate the email used at signup. Returns null on success, or an
 * error message string. Today only the shape is checked. Once billing
 * ships, callers should additionally gate personal-domain signups on
 * an active subscription (see isPersonalDomain). */
export function validateWorkEmail(email: string): string | null {
  const trimmed = email.trim()
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
    return "Enter a valid email address."
  }
  return null
}

export type PasswordStrength = "weak" | "fair" | "good" | "strong"

export function passwordStrength(password: string): PasswordStrength {
  if (password.length < 8) return "weak"
  let score = 0
  if (/[A-Z]/.test(password)) score++
  if (/[0-9]/.test(password)) score++
  if (/[^A-Za-z0-9]/.test(password)) score++
  if (password.length >= 12) score++
  if (score <= 1) return "weak"
  if (score === 2) return "fair"
  if (score === 3) return "good"
  return "strong"
}

export function validatePassword(password: string): string | null {
  if (password.length < 8) return "Password must be at least 8 characters."
  if (!/[A-Z]/.test(password)) return "Include at least one uppercase letter."
  if (!/[0-9]/.test(password)) return "Include at least one number."
  if (!/[^A-Za-z0-9]/.test(password)) return "Include at least one symbol."
  return null
}

/** Client-side failed-attempt lockout (5 tries / 15 min). */
const LOCKOUT_KEY = "sprntly_auth_lockout"
const MAX_ATTEMPTS = 5
const LOCKOUT_MS = 15 * 60 * 1000

type LockoutState = { attempts: number; lockedUntil: number | null }

function readLockout(): LockoutState {
  if (typeof window === "undefined") return { attempts: 0, lockedUntil: null }
  try {
    const raw = localStorage.getItem(LOCKOUT_KEY)
    if (!raw) return { attempts: 0, lockedUntil: null }
    return JSON.parse(raw) as LockoutState
  } catch {
    return { attempts: 0, lockedUntil: null }
  }
}

function writeLockout(state: LockoutState) {
  if (typeof window === "undefined") return
  localStorage.setItem(LOCKOUT_KEY, JSON.stringify(state))
}

export function authLockoutRemainingMs(): number {
  const { lockedUntil } = readLockout()
  if (!lockedUntil) return 0
  const remaining = lockedUntil - Date.now()
  if (remaining <= 0) {
    writeLockout({ attempts: 0, lockedUntil: null })
    return 0
  }
  return remaining
}

export function recordFailedSignIn(): void {
  const state = readLockout()
  const attempts = state.attempts + 1
  if (attempts >= MAX_ATTEMPTS) {
    writeLockout({ attempts, lockedUntil: Date.now() + LOCKOUT_MS })
  } else {
    writeLockout({ attempts, lockedUntil: null })
  }
}

export function clearSignInAttempts(): void {
  writeLockout({ attempts: 0, lockedUntil: null })
}

export type SignInErrorKind = "unconfirmed" | "invalid_credentials" | "unknown"

/** Map a Supabase sign-in failure to the UI message and whether it should count
 *  toward the failed-attempt lockout.
 *
 *  - "unconfirmed": credentials were valid, the email just isn't confirmed yet —
 *    surface the real cause and do NOT count it as a failed attempt.
 *  - "invalid_credentials": wrong password AND no-such-account both land here.
 *    Supabase returns one generic error for both on purpose, to prevent account
 *    enumeration, so we keep them merged.
 *
 *  Duck-types `code`/`message` so it stays a pure helper (no supabase import). */
export function describeSignInError(error: unknown): {
  kind: SignInErrorKind
  message: string
  countsAsFailedAttempt: boolean
} {
  const code = (error as { code?: unknown } | null)?.code
  const message = (error as { message?: unknown } | null)?.message
  if (code === "email_not_confirmed" || message === "Email not confirmed") {
    return {
      kind: "unconfirmed",
      message:
        "Please confirm your email first — check your inbox for the verification link.",
      countsAsFailedAttempt: false,
    }
  }
  if (message === "Invalid login credentials") {
    return {
      kind: "invalid_credentials",
      message: "Email or password incorrect.",
      countsAsFailedAttempt: true,
    }
  }
  return {
    kind: "unknown",
    message: "Couldn't sign in. Try again in a moment.",
    countsAsFailedAttempt: true,
  }
}
