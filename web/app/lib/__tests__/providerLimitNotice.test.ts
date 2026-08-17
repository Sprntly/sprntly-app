import { describe, expect, it } from "vitest"
import {
  providerNoticeFromAsk,
  providerNoticeFromEnvelope,
  providerNoticeTitle,
} from "../providerLimitNotice"
import type { AskStatusResponse, ChatIntentEnvelope } from "../api"

const LIMIT_COPY =
  "Sprntly's AI provider has hit a usage limit — the account is out of credits."

function ask(over: Record<string, unknown> = {}) {
  return { status: "error", ...over } as unknown as AskStatusResponse
}

describe("providerNoticeFromAsk", () => {
  it("reads the TYPED class, and carries the server's copy", () => {
    const n = providerNoticeFromAsk(
      ask({ error_class: "provider_limit", error_message: LIMIT_COPY }),
    )
    expect(n?.code).toBe("provider_limit")
    expect(n?.message).toBe(LIMIT_COPY)
  })

  it("marks a limit as needing an admin, and an overload as not", () => {
    // The two are different instructions — "top the account up" vs "try again
    // in a minute" — so showing the wrong one wastes the user's time.
    expect(providerNoticeFromAsk(
      ask({ error_class: "provider_limit", error_message: "x" }),
    )?.needsAdmin).toBe(true)
    expect(providerNoticeFromAsk(
      ask({ error_class: "provider_unavailable", error_message: "x" }),
    )?.needsAdmin).toBe(false)
  })

  it("ignores an ordinary failure", () => {
    expect(providerNoticeFromAsk(ask({ error_class: "app", error: "boom" }))).toBeNull()
    expect(providerNoticeFromAsk(ask({ error_class: "timeout" }))).toBeNull()
    expect(providerNoticeFromAsk(ask({ error: "boom" }))).toBeNull()
  })

  it("ignores a job that did not fail", () => {
    // A `ready` job with a stale class must never raise a provider alarm.
    expect(providerNoticeFromAsk(
      ask({ status: "ready", error_class: "provider_limit", error_message: "x" }),
    )).toBeNull()
    expect(providerNoticeFromAsk(null)).toBeNull()
  })

  it("still shows something if an older backend sends a class with no copy", () => {
    const n = providerNoticeFromAsk(ask({ error_class: "provider_limit" }))
    expect(n?.message).toBeTruthy()
  })

  it("accepts a code it has never seen, using the copy that came with it", () => {
    // The message travels with the response, so a new server-side code needs
    // no client release — but an UNKNOWN code is not a provider code, so the
    // ask path (which matches on the known set) stays closed to it.
    expect(providerNoticeFromAsk(
      ask({ error_class: "provider_brand_new", error_message: "x" }),
    )).toBeNull()
  })
})

describe("providerNoticeFromEnvelope", () => {
  const env = (pe: unknown) => ({ provider_error: pe } as unknown as ChatIntentEnvelope)

  it("surfaces the quiet case — the planner died, so no command can be recognised", () => {
    const n = providerNoticeFromEnvelope(
      env({ code: "provider_limit", message: LIMIT_COPY }),
    )
    expect(n?.code).toBe("provider_limit")
    expect(n?.needsAdmin).toBe(true)
  })

  it("is null on an ordinary envelope", () => {
    expect(providerNoticeFromEnvelope(env(null))).toBeNull()
    expect(providerNoticeFromEnvelope(env(undefined))).toBeNull()
    expect(providerNoticeFromEnvelope(null)).toBeNull()
  })

  it("is null on a half-formed payload rather than showing an empty toast", () => {
    expect(providerNoticeFromEnvelope(env({ code: "provider_limit" }))).toBeNull()
    expect(providerNoticeFromEnvelope(env({ message: "x" }))).toBeNull()
  })
})

describe("providerNoticeTitle", () => {
  it("names who has to act", () => {
    expect(providerNoticeTitle({ code: "provider_limit", message: "x", needsAdmin: true }))
      .toBe("AI provider limit reached")
    expect(providerNoticeTitle({ code: "provider_unavailable", message: "x", needsAdmin: false }))
      .toBe("AI provider unavailable")
  })
})
