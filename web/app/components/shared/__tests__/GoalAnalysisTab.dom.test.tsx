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

describe("the confirmation gate", () => {
  it("shows the question, not a spinner", async () => {
    get.mockResolvedValue({
      ...RUN, status: "awaiting_confirmation",
      prioritisation: { ask: "Which revenue does this mean?", proposed_definition: "" },
    })
    render(<GoalAnalysisTab runId={7} />)
    const panel = await screen.findByTestId("goal-confirm")
    expect(panel.textContent).toContain("Which revenue does this mean?")
    expect(screen.queryByTestId("goal-running")).toBeNull()
  })

  it("prefills the proposal so adopting it is one click", async () => {
    get.mockResolvedValue({
      ...RUN, status: "awaiting_confirmation",
      prioritisation: {
        ask: "Confirm what this means.",
        proposed_definition: "expansion minus churn across renewing accounts",
        proposed_source: "your KPI tree",
      },
    })
    render(<GoalAnalysisTab runId={7} />)
    const box = await screen.findByLabelText("What this goal means")
    expect((box as HTMLTextAreaElement).value)
      .toBe("expansion minus churn across renewing accounts")
  })

  it("sends the user's edit, not the proposal", async () => {
    // An edited definition is theirs, and the backend records it as elicited
    // rather than adopted. Sending the proposal instead would mislabel it.
    get.mockResolvedValue({
      ...RUN, status: "awaiting_confirmation",
      prioritisation: { ask: "?", proposed_definition: "gross revenue" },
    })
    confirm.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    const box = await screen.findByLabelText("What this goal means")
    fireEvent.change(box, { target: { value: "net revenue, excluding one-offs" } })
    fireEvent.click(screen.getByText("Confirm and analyse"))
    await waitFor(() =>
      expect(confirm).toHaveBeenCalledWith(7, "net revenue, excluding one-offs"))
  })

  it("cannot confirm an empty definition", async () => {
    get.mockResolvedValue({
      ...RUN, status: "awaiting_confirmation",
      prioritisation: { ask: "?", proposed_definition: "" },
    })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-confirm")
    expect((screen.getByText("Confirm and analyse") as HTMLButtonElement).disabled).toBe(true)
  })
})

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

  it("stops polling while waiting on the human", async () => {
    get.mockResolvedValue({ ...RUN, status: "awaiting_confirmation", prioritisation: { ask: "?" } })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-confirm")
    const calls = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(12_000)
    expect(get.mock.calls.length).toBe(calls)
  })

  it("RESTARTS polling after the user confirms", async () => {
    // THE ONE THAT MATTERS. `awaiting_confirmation` is terminal, so the loop
    // had already stopped; `load` is keyed on runId, which never changes, so
    // nothing re-armed it. Every user confirmed and then watched
    // "Reading 0 claims…" forever while the run finished on the server.
    get.mockResolvedValue({
      ...RUN, status: "awaiting_confirmation",
      prioritisation: { ask: "?", proposed_definition: "net revenue" },
    })
    confirm.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-confirm")

    get.mockResolvedValue({ ...RUN, status: "running" })
    fireEvent.click(screen.getByText("Confirm and analyse"))
    await waitFor(() => expect(confirm).toHaveBeenCalled())

    const calls = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(9_500)
    expect(get.mock.calls.length).toBeGreaterThan(calls)
  })
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
