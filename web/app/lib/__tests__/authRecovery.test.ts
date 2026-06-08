import { describe, expect, it } from "vitest"
import { isRecoveryFlow } from "../authRecovery"

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
