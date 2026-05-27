/** Work-email and password rules from Onboarding Flow Spec v1. */

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

export function isWorkEmail(email: string): boolean {
  if (isAllowlistedEmail(email)) return true
  const domain = emailDomain(email)
  if (!domain) return false
  return !CONSUMER_DOMAINS.has(domain)
}

export function validateWorkEmail(email: string): string | null {
  const trimmed = email.trim()
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
    return "Enter a valid email address."
  }
  if (!isWorkEmail(trimmed)) {
    return "Use your work email (e.g. you@company.com). Personal Gmail and similar addresses are not supported."
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
