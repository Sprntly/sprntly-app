import { describe, expect, it, vi, afterEach } from "vitest"
import {
  isWorkEmail,
  isAllowlistedEmail,
  validateWorkEmail,
} from "../auth-validation"

describe("auth email allowlist", () => {
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("allows sprntly@gmail.com via builtin allowlist", () => {
    expect(isAllowlistedEmail("sprntly@gmail.com")).toBe(true)
    expect(isWorkEmail("sprntly@gmail.com")).toBe(true)
    expect(validateWorkEmail("sprntly@gmail.com")).toBeNull()
  })

  it("blocks other gmail addresses", () => {
    expect(isWorkEmail("other@gmail.com")).toBe(false)
    expect(validateWorkEmail("other@gmail.com")).toMatch(/work email/i)
  })

  it("merges env allowlist", () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_EMAIL_ALLOWLIST", "qa@icloud.com")
    expect(isWorkEmail("qa@icloud.com")).toBe(true)
    expect(isWorkEmail("other@icloud.com")).toBe(false)
  })
})
