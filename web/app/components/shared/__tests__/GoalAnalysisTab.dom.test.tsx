// @vitest-environment jsdom
//
// The Goal Analysis panel. What is tested here is not layout — it is the four
// places where a plausible-looking rendering would be a lie:
//
//   1. An unsized finding rendered as 0. "We could not size this" and "this is
//      worth nothing" lead to OPPOSITE decisions (I3).
//   2. The confirmation step rendered as a spinner. A run stops and asks what
//      the goal means; that question is the product, not an interruption.
//   3. Coverage notes buried under the findings they qualify.
//   4. The considered list dropped, leaving a ranking to be taken on faith.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const get = vi.fn()
const confirm = vi.fn()
vi.mock("../../../lib/api", () => ({
  goalAnalysisApi: {
    get: (...a: unknown[]) => get(...a),
    confirm: (...a: unknown[]) => confirm(...a),
  },
}))

import { GoalAnalysisTab } from "../GoalAnalysisTab"

const RUN = {
  id: 7, status: "ready", goal_text: "raise net revenue retention",
  error_code: null, coverage_notes: [], claim_count: 12,
  conversation_id: null, created_at: null, finished_at: null,
  findings: [], considered: [],
}

const FINDING = {
  id: 1, statement: "9 claims across 4 accounts concern export latency.",
  claim_ids: ["c1"], adjudication: "corroborated",
  impact_value: 4, currency: "accounts", confidence_band: "medium",
  assumed_params: [{ name: "value_per_account", basis: "no revenue data connected" }],
  impact: { value: 4, affected_population: 4 },
  confidence: { band: "medium", weakest_leg: "problem", weakest_leg_reason: null, cap_reason: null },
}

beforeEach(() => { get.mockReset(); confirm.mockReset() })
afterEach(cleanup)

describe("sizing", () => {
  it("renders an unsizeable finding as unsized, never as zero", async () => {
    // THE ONE THAT MATTERS. A dash and a 0 look similar and mean opposites.
    get.mockResolvedValue({
      ...RUN,
      findings: [{ ...FINDING, impact_value: null, impact: { value: null, affected_population: null } }],
    })
    render(<GoalAnalysisTab runId={7} />)
    const el = await screen.findByTestId("goal-unsized")
    expect(el.textContent).toBe("Could not be sized")
    expect(screen.queryByText("0")).toBeNull()
    expect(screen.queryByText("0 accounts")).toBeNull()
  })

  it("renders a sized finding in the goal's own currency", async () => {
    get.mockResolvedValue({ ...RUN, findings: [FINDING] })
    render(<GoalAnalysisTab runId={7} />)
    expect((await screen.findByTestId("goal-sized")).textContent).toBe("4 accounts")
  })

  it("discloses every assumed parameter beside the number", async () => {
    // I8. A methodology page nobody opens is not a disclosure.
    get.mockResolvedValue({ ...RUN, findings: [FINDING] })
    render(<GoalAnalysisTab runId={7} />)
    expect((await screen.findByTestId("goal-ready")).textContent)
      .toContain("no revenue data connected")
  })
})

// THE CONFIRMATION GATE MOVED TO THE CHAT THREAD.
//
// It is a question, and questions belong in the conversation — a PM has to be
// able to scroll back and see what was asked and what they answered. The four
// guarantees that lived here (the question renders rather than a spinner, the
// proposal prefills, the user's EDIT is what gets sent, an empty definition
// cannot be confirmed) now live in `GoalGateCard.dom.test.tsx` against the card
// that renders them.

describe("nothing is quietly dropped", () => {
  it("renders coverage notes above the findings they qualify", async () => {
    get.mockResolvedValue({
      ...RUN,
      coverage_notes: [{ reason: "undated evidence", actual: "40 of 300 signals carried no date" }],
      findings: [FINDING],
    })
    render(<GoalAnalysisTab runId={7} />)
    const ready = await screen.findByTestId("goal-ready")
    const notes = screen.getByTestId("goal-coverage")
    const findings = screen.getByTestId("goal-finding")
    expect(notes.textContent).toContain("40 of 300 signals carried no date")
    // Order is the point: a note that changes how a number reads cannot sit
    // underneath it.
    expect(ready.textContent!.indexOf("undated evidence"))
      .toBeLessThan(ready.textContent!.indexOf(findings.textContent!.slice(0, 20)))
  })

  it("renders the considered list with each reason", async () => {
    get.mockResolvedValue({
      ...RUN,
      considered: [{
        id: 3, label: "onboarding friction",
        reason: "all 4 supporting claims land within 6 days",
        stopped_at_stage: "verification", claim_ids: ["c9"],
      }],
    })
    render(<GoalAnalysisTab runId={7} />)
    expect((await screen.findByTestId("goal-considered")).textContent)
      .toContain("all 4 supporting claims land within 6 days")
  })

  it("a run where nothing survived says so, and still shows why", async () => {
    get.mockResolvedValue({
      ...RUN, findings: [],
      considered: [{ id: 1, label: "x", reason: "single account", stopped_at_stage: "verification", claim_ids: [] }],
    })
    render(<GoalAnalysisTab runId={7} />)
    const ready = await screen.findByTestId("goal-ready")
    expect(ready.textContent).toContain("Nothing survived verification")
    expect(screen.getByTestId("goal-considered")).toBeTruthy()
  })
})

describe("failure", () => {
  it("turns the closed-set code into something a reader can act on", async () => {
    get.mockResolvedValue({ ...RUN, status: "failed", error_code: "no_evidence" })
    render(<GoalAnalysisTab runId={7} />)
    expect((await screen.findByTestId("goal-failed")).textContent)
      .toContain("Connect a source")
  })

  it("an unknown code still renders a failure rather than a blank panel", async () => {
    get.mockResolvedValue({ ...RUN, status: "failed", error_code: "something_new" })
    render(<GoalAnalysisTab runId={7} />)
    expect((await screen.findByTestId("goal-failed")).textContent)
      .toContain("did not finish")
  })
})

describe("polling", () => {
  // These two used to wait 120ms against a 3000ms interval, so they passed
  // identically with TERMINAL = new Set() — they proved the clock had not
  // ticked, not that polling had stopped. Fake timers make them mean it.
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
  afterEach(() => vi.useRealTimers())

  it("stops polling once the run is terminal", async () => {
    get.mockResolvedValue(RUN)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-ready")
    const calls = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(12_000)
    expect(get.mock.calls.length).toBe(calls)
  })

  it("keeps polling while the run is still working", async () => {
    // The control the old pair was missing entirely: a test that fails if
    // polling stops when it should not.
    get.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-running")
    const calls = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(9_500)
    expect(get.mock.calls.length).toBeGreaterThan(calls)
  })

  it("KEEPS polling through a gate, because only the chat can release it", async () => {
    // The gates are answered in the thread now, so the click that releases one
    // happens somewhere this panel cannot see. Treating a gate as terminal —
    // which the code did, on reasoning that stopped being true when the gates
    // moved — meant a panel opened on a gate never advanced to the report,
    // however long the reader waited.
    //
    // This had NO test: putting both gate statuses back into `TERMINAL` left
    // 2322 web tests across 221 files green.
    get.mockResolvedValue({ ...RUN, status: "awaiting_approval" })
    render(<GoalAnalysisTab runId={7} />)
    await waitFor(() => expect(get).toHaveBeenCalled())
    const calls = get.mock.calls.length
    // Gate polling is deliberately SLOW (15s), not the 3s working rate: a run
    // waiting on a person should not cost 1,200 requests an hour.
    await vi.advanceTimersByTimeAsync(16_000)
    expect(get.mock.calls.length).toBeGreaterThan(calls)
  })

  it("stops polling a gate eventually, and says that it has", async () => {
    // A run left at a gate overnight would otherwise have an open tab asking
    // about it all night. Stopping silently would look identical to still
    // watching, so it stops and says so.
    get.mockResolvedValue({ ...RUN, status: "awaiting_confirmation" })
    render(<GoalAnalysisTab runId={7} />)
    await waitFor(() => expect(get).toHaveBeenCalled())
    await vi.advanceTimersByTimeAsync(31 * 60 * 1000)
    const settled = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000)
    expect(get.mock.calls.length).toBe(settled)
    expect(document.body.textContent).toContain("stopped checking")
  })

  // MOVED with the gate: confirming happens in the thread now, and re-arming
  // after it is `confirmGoalDefinition`'s job in ChatScreen.
})

describe("transient failure", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
  afterEach(() => vi.useRealTimers())

  it("one failed poll does not brick the panel", async () => {
    // A multi-minute run spans deploys. A single 502 used to set a sticky
    // error that short-circuited the whole panel with no way back.
    get.mockResolvedValueOnce({ ...RUN, status: "running" })
       .mockRejectedValueOnce(new Error("502"))
       .mockResolvedValue(RUN)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-running")
    await vi.advanceTimersByTimeAsync(9_500)
    await waitFor(() => expect(screen.getByTestId("goal-ready")).toBeTruthy())
  })

  it("gives up after repeated failures rather than hammering", async () => {
    get.mockRejectedValue(new Error("down"))
    render(<GoalAnalysisTab runId={7} />)
    await vi.advanceTimersByTimeAsync(15_000)
    await waitFor(() =>
      expect(screen.getByText(/Lost contact/)).toBeTruthy())
  })
})


describe("the error is visible without destroying the panel", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
  afterEach(() => vi.useRealTimers())

  it("a run that loaded and THEN went down still says so", async () => {
    // The regression the first fix introduced: `error && !run` made the
    // message unreachable once a run existed, so the panel simply froze on
    // its last status with no explanation. The fixture the old suite never
    // had — a run that loads first and fails after.
    get.mockResolvedValueOnce({ ...RUN, status: "running" })
       .mockRejectedValue(new Error("down"))
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-running")
    await vi.advanceTimersByTimeAsync(15_000)
    await waitFor(() => expect(screen.getByTestId("goal-error")).toBeTruthy())
    // And the run it had is still on screen underneath.
    expect(screen.getByTestId("goal-running")).toBeTruthy()
  })

  // MOVED: a refused confirm now surfaces on the TURN that asked, via
  // `failGoalTurn`. Covered in `ChatScreen.goal-restore.dom.test.tsx`.

  it("a recovered poll clears the warning it showed", async () => {
    // Was vacuous: the banner only appeared on the THIRD consecutive failure,
    // by which point polling had already stopped — so there was no moment at
    // which it was visible and could then clear. It has to be seen before it
    // can be seen to go.
    get.mockResolvedValueOnce({ ...RUN, status: "running" })
       .mockRejectedValueOnce(new Error("blip"))
       .mockResolvedValue(RUN)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-running")

    await vi.advanceTimersByTimeAsync(3_500)
    await waitFor(() => expect(screen.getByTestId("goal-error")).toBeTruthy())

    await vi.advanceTimersByTimeAsync(3_500)
    await waitFor(() => expect(screen.getByTestId("goal-ready")).toBeTruthy())
    expect(screen.queryByTestId("goal-error")).toBeNull()
  })

  // MOVED with the confirm button itself — see `GoalGateCard.dom.test.tsx`.

  // MOVED with the gate; the panel has no confirm to fail.
})

describe("the running view narrates instead of spinning", () => {
  it("renders the funnel once the run has published one", async () => {
    get.mockResolvedValue({
      ...RUN,
      status: "running",
      prioritisation: {
        progress: {
          step: "done", claims: 2410, sources: 4,
          groups: 1744, themes: 1744, findings: 168, conflicts: 3, deep: 5,
          dropped: { anecdote: 1576, echo: 9, single_account: 0,
                     no_authority: 0, uncausal: 0, ungroupable: 0 },
        },
      },
    })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-narration")
    expect(screen.getByTestId("goal-running").textContent).toContain("1,576")
  })

  it("falls back to the old line when there is no funnel yet", async () => {
    // A run that has only just started, and EVERY run that finished before
    // narration shipped. The fallback is the honest shape — a funnel of
    // zeroes would state that this run dropped nothing.
    get.mockResolvedValue({ ...RUN, status: "running", claim_count: 42,
                            prioritisation: {} })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-running")
    expect(screen.queryByTestId("goal-narration")).toBeNull()
    expect(screen.getByTestId("goal-running").textContent).toContain("42")
  })
})


describe("the funnel survives the run", () => {
  // The gap between the final progress write and `status="ready"` is about a
  // second against a 3s poll, so a reader who could only see this live would
  // usually see nothing — and the drop rows ARE the feature.
  const DONE = {
    step: "done", claims: 2410, sources: 4, groups: 830, themes: 622,
    findings: 168, conflicts: 3, deep: 5,
    dropped: { ungroupable: 208, anecdote: 396, echo: 9, single_account: 41,
               no_authority: 2, uncausal: 6 },
  }

  it("a finished run can still say how its ranking was narrowed", async () => {
    get.mockResolvedValue({ ...RUN, status: "ready",
                            prioritisation: { progress: DONE } })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-ready")
    const recap = await screen.findByTestId("goal-narration-recap")
    expect(recap.textContent).toContain("How this was narrowed")
    expect(recap.textContent).toContain("396")
  })

  it("shows no recap for a run that predates the feature", async () => {
    get.mockResolvedValue({ ...RUN, status: "ready", prioritisation: {} })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-ready")
    expect(screen.queryByTestId("goal-narration-recap")).toBeNull()
  })

  it("shows no recap when the final write was lost mid-run", async () => {
    // A restart between the `analysing` and `done` writes leaves a partial
    // funnel on the row. Half a funnel beside a finished report is worse than
    // none: it reads as the whole story.
    get.mockResolvedValue({
      ...RUN, status: "ready",
      prioritisation: { progress: { step: "analysing", claims: 2410,
                                    claims_themed: 1502, claims_unthemed: 908 } },
    })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-ready")
    expect(screen.queryByTestId("goal-narration-recap")).toBeNull()
  })
})
