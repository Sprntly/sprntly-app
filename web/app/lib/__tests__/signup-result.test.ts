import { describe, expect, it } from "vitest"
import { interpretSignUpResponse } from "../auth"

describe("interpretSignUpResponse", () => {
  it("existing email (obfuscated user, no identities) → already_registered", () => {
    expect(
      interpretSignUpResponse({ user: { identities: [] }, session: null }),
    ).toBe("already_registered")
  })

  it("fresh signup awaiting confirmation → confirm_email", () => {
    expect(
      interpretSignUpResponse({ user: { identities: [{}] }, session: null }),
    ).toBe("confirm_email")
  })

  it("autoconfirm/session issued → session", () => {
    expect(
      interpretSignUpResponse({ user: { identities: [{}] }, session: {} }),
    ).toBe("session")
  })

  it("null identities treated as already_registered (defensive)", () => {
    expect(
      interpretSignUpResponse({ user: { identities: null }, session: null }),
    ).toBe("already_registered")
  })
})
