/** What the approve gate actually PUTS ON THE WIRE.
 *
 *  The plan card collects three answers to things the run cannot know —
 *  account value, decision owner, needed-by — and `GoalGateCard.plan.dom`
 *  asserts they reach `onApprove`. They did. They then stopped at the client:
 *  `approve()` did not declare them, so they were dropped before the POST and
 *  `account_value` was null on every run started from the UI, leaving the
 *  server's decision box and its one multiplication permanently unreachable.
 *
 *  A component-boundary assertion cannot see that. These tests sit at the
 *  network boundary instead, which is the only place the whole path is
 *  observable. */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { API_URL, goalAnalysisApi } from "../api"

describe("goalAnalysisApi.approve request body", () => {
  let originalFetch: typeof globalThis.fetch
  let lastCall: { url: string; init: RequestInit | undefined } | null

  const body = () => JSON.parse(String(lastCall!.init!.body))

  beforeEach(() => {
    originalFetch = globalThis.fetch
    lastCall = null
    globalThis.fetch = (async (url: RequestInfo | URL, init?: RequestInit) => {
      lastCall = { url: String(url), init }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "content-type": "application/json" },
      })
    }) as typeof globalThis.fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it("POSTs to the run's approve endpoint", async () => {
    await goalAnalysisApi.approve(42)
    expect(lastCall!.url).toBe(`${API_URL}/v1/crucible/42/approve`)
    expect(lastCall!.init?.method).toBe("POST")
  })

  it("carries the gate's three answers through to the server", async () => {
    await goalAnalysisApi.approve(42, {
      excluded_sources: [],
      hypotheses: [],
      account_value: 12000,
      decision_owner: "VP Product",
      needed_by: "before the Q3 review",
    })
    expect(body()).toMatchObject({
      account_value: 12000,
      decision_owner: "VP Product",
      needed_by: "before the Q3 review",
    })
  })

  it("omits an unanswered question rather than sending it empty", async () => {
    // The server's own test for "unanswered" is absence: a key present with a
    // blank value would be recorded as an answer the reader never gave.
    await goalAnalysisApi.approve(42, {
      excluded_sources: [],
      hypotheses: [],
      decision_owner: "VP Product",
    })
    const sent = body()
    expect(sent).toMatchObject({ decision_owner: "VP Product" })
    expect(sent).not.toHaveProperty("account_value")
    expect(sent).not.toHaveProperty("needed_by")
  })

  it("still always sends both lists, including empty", async () => {
    // Pre-existing contract: an omitted list would silently mean "changed
    // nothing", which is a different statement from "excluded nothing".
    await goalAnalysisApi.approve(42)
    expect(body()).toMatchObject({ excluded_sources: [], hypotheses: [] })
  })

  it("sends a zero account value if one is ever supplied", async () => {
    // The card cannot produce this (it guards `value > 0`), but the transport
    // must not be the thing that decides — a truthiness test here would drop a
    // real 0 silently, and silence is what this whole file exists to prevent.
    await goalAnalysisApi.approve(42, {
      excluded_sources: [],
      hypotheses: [],
      account_value: 0,
    })
    expect(body()).toMatchObject({ account_value: 0 })
  })
})
