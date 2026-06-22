import { describe, expect, it, vi, afterEach } from "vitest"
import {
  describeSignInError,
  isPersonalDomain,
  isWorkEmail,
  isAllowlistedEmail,
  normalizeEmail,
  validateWorkEmail,
} from "../auth-validation"

describe("email signup validation (post-gmail-unblock, 2026-06-06)", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  // Gmail / consumer domains are no longer blocked. Per CEO direction
  // 2026-06-06: pre-launch we accept any well-formed email; once billing
  // ships, personal-domain signups will gate on an active subscription.
  // For now this is purely informational — see isPersonalDomain().

  it("accepts a consumer-domain email (gmail)", () => {
    expect(validateWorkEmail("anyone@gmail.com")).toBeNull()
    expect(isWorkEmail("anyone@gmail.com")).toBe(true)
  })

  it("accepts other consumer domains (outlook, yahoo, icloud)", () => {
    expect(validateWorkEmail("a@outlook.com")).toBeNull()
    expect(validateWorkEmail("b@yahoo.com")).toBeNull()
    expect(validateWorkEmail("c@icloud.com")).toBeNull()
  })

  it("still accepts enterprise emails", () => {
    expect(validateWorkEmail("alice@acme.io")).toBeNull()
    expect(validateWorkEmail("bob@meridian.health")).toBeNull()
  })

  it("rejects malformed addresses", () => {
    expect(validateWorkEmail("not-an-email")).toMatch(/valid email/i)
    expect(validateWorkEmail("missing-at.com")).toMatch(/valid email/i)
    expect(validateWorkEmail("@no-local.com")).toMatch(/valid email/i)
  })

  // The informational helper is still useful for UI copy (e.g. "Personal
  // email — Sprntly subscription required" inline note once billing ships).
  it("flags consumer-domain emails via isPersonalDomain", () => {
    expect(isPersonalDomain("any@gmail.com")).toBe(true)
    expect(isPersonalDomain("any@outlook.com")).toBe(true)
    expect(isPersonalDomain("any@acme.io")).toBe(false)
  })

  it("keeps the allowlist export available for full-address overrides", () => {
    // Even though gmail is generally allowed now, callers can still
    // explicit-allow specific addresses (and env vars still merge).
    expect(isAllowlistedEmail("sprntly@gmail.com")).toBe(true)
    vi.stubEnv("NEXT_PUBLIC_AUTH_EMAIL_ALLOWLIST", "qa@icloud.com")
    expect(isAllowlistedEmail("qa@icloud.com")).toBe(true)
    expect(isAllowlistedEmail("random@icloud.com")).toBe(false)
  })
})

describe("normalizeEmail (case-insensitive login)", () => {
  it("lowercases and trims", () => {
    expect(normalizeEmail("  David@Gravitios.AI  ")).toBe("david@gravitios.ai")
  })

  it("is idempotent on already-normal input", () => {
    expect(normalizeEmail("a@b.com")).toBe("a@b.com")
  })

  it("treats different-cased addresses as equal after normalizing", () => {
    expect(normalizeEmail("USER@EXAMPLE.COM")).toBe(normalizeEmail("user@example.com"))
  })
})

describe("describeSignInError", () => {
  it("maps email-not-confirmed (by code) to a distinct message and does NOT count as a failed attempt", () => {
    const r = describeSignInError({ code: "email_not_confirmed", message: "Email not confirmed" })
    expect(r.kind).toBe("unconfirmed")
    expect(r.countsAsFailedAttempt).toBe(false)
    expect(r.message).toMatch(/confirm your email/i)
  })

  it("maps email-not-confirmed (by message only) too", () => {
    const r = describeSignInError({ message: "Email not confirmed" })
    expect(r.kind).toBe("unconfirmed")
    expect(r.countsAsFailedAttempt).toBe(false)
  })

  it("keeps wrong-password and no-account merged as generic invalid_credentials (anti-enumeration), counts as a failed attempt", () => {
    const r = describeSignInError({ message: "Invalid login credentials" })
    expect(r.kind).toBe("invalid_credentials")
    expect(r.message).toBe("Email or password incorrect.")
    expect(r.countsAsFailedAttempt).toBe(true)
  })

  it("falls back to a generic retry message for unknown/network errors and counts as a failed attempt", () => {
    expect(describeSignInError(new Error("Failed to fetch")).kind).toBe("unknown")
    expect(describeSignInError(null).kind).toBe("unknown")
    expect(describeSignInError(undefined).countsAsFailedAttempt).toBe(true)
  })
})
