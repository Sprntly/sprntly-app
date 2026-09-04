import { describe, expect, it } from "vitest"
import { apiErrorMessage } from "../api"

/**
 * Structured error details.
 *
 * The billing routes return `{error, message, …}` so the client can BRANCH on
 * `error` (insufficient_credits vs subscription_inactive) while still having
 * something readable to show. Before this was handled, every one of them
 * rendered as "Request failed (502)" — the actionable half thrown away, which
 * is what makes a failed button look like a button that did nothing.
 */
describe("apiErrorMessage — structured details", () => {
  it("surfaces the message from an object detail", () => {
    expect(
      apiErrorMessage(502, {
        detail: {
          error: "stripe_error",
          op: "checkout",
          message: "No such price: 'price_bogus'",
        },
      }),
    ).toBe("No such price: 'price_bogus'")
  })

  it("surfaces a credit shortfall the user can act on", () => {
    expect(
      apiErrorMessage(402, {
        detail: {
          error: "insufficient_credits",
          message: "This needs 25 credits and you have 2.",
          needed: 25,
          balance: 2,
        },
      }),
    ).toBe("This needs 25 credits and you have 2.")
  })

  it("still prefers a plain string detail", () => {
    expect(apiErrorMessage(400, { detail: "Bad request" })).toBe("Bad request")
  })

  it("still joins FastAPI validation arrays", () => {
    expect(
      apiErrorMessage(422, { detail: [{ msg: "too small" }, { msg: "too big" }] }),
    ).toBe("too small · too big")
  })

  it("falls back when an object detail carries no message", () => {
    expect(apiErrorMessage(500, { detail: { error: "boom" } })).toBe(
      "Request failed (500)",
    )
  })

  it("falls back on an empty message rather than rendering blank", () => {
    expect(apiErrorMessage(502, { detail: { message: "   " } })).toBe(
      "Request failed (502)",
    )
  })
})
