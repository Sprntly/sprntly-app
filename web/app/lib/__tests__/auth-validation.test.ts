import { describe, expect, it, vi, afterEach } from "vitest"
import {
  isPersonalDomain,
  isWorkEmail,
  isAllowlistedEmail,
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
