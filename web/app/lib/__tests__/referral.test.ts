// @vitest-environment jsdom
//
// The referral handoff. A code arrives on a sign-up URL and has to survive
// several onboarding steps (and, for a Google sign-up, a full round trip off
// the site) before the company it credits exists.
import { beforeEach, describe, expect, it } from "vitest"
import { captureReferralCode, takeReferralCode } from "../referral"

beforeEach(() => {
  window.localStorage.clear()
})

describe("captureReferralCode", () => {
  it("stashes a code so it outlives the URL it arrived on", () => {
    captureReferralCode("?ref=abc123")
    expect(takeReferralCode()).toBe("abc123")
  })

  it("ignores a URL with no ref", () => {
    captureReferralCode("?share=xyz")
    expect(takeReferralCode()).toBeNull()
  })

  it("keeps the most recent link when someone opens two", () => {
    captureReferralCode("?ref=first")
    captureReferralCode("?ref=second")
    expect(takeReferralCode()).toBe("second")
  })

  it("does not clobber a stashed code when landing on a plain sign-up URL", () => {
    // Reachable: capture runs on every mount, and a Google round trip returns
    // without the query string.
    captureReferralCode("?ref=kept")
    captureReferralCode("")
    expect(takeReferralCode()).toBe("kept")
  })

  it("decodes an escaped code", () => {
    captureReferralCode("?ref=a%20b")
    expect(takeReferralCode()).toBe("a b")
  })
})

describe("takeReferralCode", () => {
  it("clears on read, so a second workspace cannot re-claim the same referral", () => {
    captureReferralCode("?ref=once")
    expect(takeReferralCode()).toBe("once")
    expect(takeReferralCode()).toBeNull()
  })

  it("is null when nothing was ever captured", () => {
    expect(takeReferralCode()).toBeNull()
  })
})
