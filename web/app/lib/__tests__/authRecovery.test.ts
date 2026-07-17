import { describe, expect, it } from "vitest"
import { authFlowType, isInviteFlow, isRecoveryFlow } from "../authRecovery"

describe("isRecoveryFlow", () => {
  it("detects type=recovery in the query string", () => {
    expect(
      isRecoveryFlow("https://app.sprntly.ai/auth/callback?code=abc&type=recovery"),
    ).toBe(true)
  })

  it("detects type=recovery in the URL hash (implicit flow)", () => {
    expect(
      isRecoveryFlow(
        "https://app.sprntly.ai/auth/callback#access_token=x&type=recovery",
      ),
    ).toBe(true)
  })

  it("returns false for a normal sign-in callback", () => {
    expect(
      isRecoveryFlow("https://app.sprntly.ai/auth/callback?code=abc"),
    ).toBe(false)
  })

  it("returns false for a sign-up email-confirm callback (type=signup)", () => {
    expect(
      isRecoveryFlow(
        "https://app.sprntly.ai/auth/callback?code=abc&type=signup",
      ),
    ).toBe(false)
  })

  it("returns false for an empty URL", () => {
    expect(isRecoveryFlow("https://app.sprntly.ai/auth/callback")).toBe(false)
  })

  it("does not crash on a malformed URL", () => {
    expect(isRecoveryFlow("not a url")).toBe(false)
  })
})

describe("authFlowType / isInviteFlow (workspace-invite landings)", () => {
  it("reads the type from the query string", () => {
    expect(
      authFlowType("https://app.sprntly.ai/auth/callback?code=abc&type=invite"),
    ).toBe("invite")
  })

  it("reads the type from the URL hash (implicit flow)", () => {
    expect(
      authFlowType(
        "https://app.sprntly.ai/auth/callback#access_token=x&type=invite",
      ),
    ).toBe("invite")
  })

  it("isInviteFlow is true only for type=invite", () => {
    expect(
      isInviteFlow("https://app.sprntly.ai/auth/callback#access_token=x&type=invite"),
    ).toBe(true)
    expect(
      isInviteFlow("https://app.sprntly.ai/auth/callback?type=recovery"),
    ).toBe(false)
    expect(isInviteFlow("https://app.sprntly.ai/auth/callback")).toBe(false)
    expect(isInviteFlow("not a url")).toBe(false)
  })

  it("returns null when no type is carried", () => {
    expect(authFlowType("https://app.sprntly.ai/auth/callback?code=abc")).toBe(null)
  })
})
