// @vitest-environment jsdom
//
// The SECOND gate: the run says what it will read and what it cannot answer,
// and the user says go — having optionally dropped a source or written down
// what they already believe.
//
// What is guarded here is not layout:
//
//   1. The approve call must carry the user's ACTUAL decision. An approve that
//      posts an empty body looks identical on screen and silently reads the
//      source they dropped.
//   2. `awaiting_approval` is terminal for the poller, so approving has to
//      re-arm it — the same bug that shipped once for confirm, where the user
//      clicked and then watched a stale panel while the run finished.
//   3. A missing plan must never render an approve button. That click would be
//      agreement to something never shown.
//   4. The engine's name never reaches the screen, in ANY state.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const get = vi.fn()
const confirm = vi.fn()
const approve = vi.fn()
vi.mock("../../../lib/api", () => ({
  goalAnalysisApi: {
    get: (...a: unknown[]) => get(...a),
    confirm: (...a: unknown[]) => confirm(...a),
    approve: (...a: unknown[]) => approve(...a),
  },
}))

import { GoalAnalysisTab } from "../GoalAnalysisTab"

const PLAN = {
  goal_text: "raise net revenue retention",
  definition_text: "expansion minus churn across renewing accounts",
  currency: "accounts",
  total_signals: 412,
  sources: [
    {
      source_type: "customer_voice",
      signal_count: 260,
      label: "calls and customer tickets",
      witnesses: "what customers asked for and reported",
    },
    {
      source_type: "project_mgmt",
      signal_count: 152,
      label: "the tracker",
      witnesses: "what was built, broken, blocked or attempted",
    },
  ],
  cannot_answer: [
    {
      question: "How many points will this move the metric?",
      because: "nothing connected here carries numbers",
      remedy: "connect Amplitude, or upload a cohort export",
    },
  ],
  will_produce: ["Themes ranked by how much of your book they touch"],
  excluded_sources: [],
  hypotheses: [],
}

const RUN = {
  id: 7,
  status: "ready",
  goal_text: "raise net revenue retention",
  error_code: null,
  coverage_notes: [],
  claim_count: 412,
  conversation_id: null,
  created_at: null,
  finished_at: null,
  findings: [],
  considered: [],
}

const WAITING = { ...RUN, status: "awaiting_approval", prioritisation: { plan: PLAN } }

beforeEach(() => { get.mockReset(); confirm.mockReset(); approve.mockReset() })
afterEach(cleanup)

describe("the plan is shown before anything is spent", () => {
  it("lists each source with its count and what it can witness", async () => {
    get.mockResolvedValue(WAITING)
    render(<GoalAnalysisTab runId={7} />)
    const panel = await screen.findByTestId("goal-plan")
    expect(panel.textContent).toContain("calls and customer tickets")
    expect(panel.textContent).toContain("260")
    expect(panel.textContent).toContain("what customers asked for and reported")
  })

  it("states what the run will NOT be able to answer, with the fix", async () => {
    // Said BEFORE the run, this is a decision — connect the source, or accept
    // a qualitative answer knowingly. Said after, it is an apology.
    get.mockResolvedValue(WAITING)
    render(<GoalAnalysisTab runId={7} />)
    const gaps = await screen.findByTestId("goal-plan-gaps")
    expect(gaps.textContent).toContain("How many points will this move the metric?")
    expect(gaps.textContent).toContain("connect Amplitude")
  })

  it("is not a loading state — the running view does not render", async () => {
    get.mockResolvedValue(WAITING)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-approve")
    expect(screen.queryByTestId("goal-running")).toBeNull()
  })

  it("falls back to the running view when the plan is missing", async () => {
    // An approve button over nothing is a click agreeing to something never
    // shown. The poll keeps going instead and the row corrects itself.
    get.mockResolvedValue({ ...RUN, status: "awaiting_approval", prioritisation: {} })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-running")
    expect(screen.queryByTestId("goal-approve")).toBeNull()
    expect(screen.queryByText("Approve and run")).toBeNull()
  })
})

describe("approving carries the user's decision", () => {
  it("posts nothing extra when the user changed nothing", async () => {
    get.mockResolvedValue(WAITING)
    approve.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")
    fireEvent.click(screen.getByText("Approve and run"))
    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith(7, {
        excluded_sources: [],
        hypotheses: [],
      }))
  })

  it("posts the source the user dropped", async () => {
    // THE ONE THAT MATTERS. An approve that loses the exclusion looks
    // identical on screen and reads the source anyway.
    get.mockResolvedValue(WAITING)
    approve.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")
    fireEvent.click(screen.getByLabelText("Read the tracker"))
    fireEvent.click(screen.getByText("Approve and run"))
    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith(7, {
        excluded_sources: ["project_mgmt"],
        hypotheses: [],
      }))
  })

  it("posts the hypotheses the user typed, one per line", async () => {
    get.mockResolvedValue(WAITING)
    approve.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")
    fireEvent.change(screen.getByLabelText("What you already believe"), {
      target: { value: "onboarding is where they drop off\n\npricing is the blocker\n" },
    })
    fireEvent.click(screen.getByText("Approve and run"))
    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith(7, {
        excluded_sources: [],
        // Blank lines dropped: an empty hypothesis would be counted and
        // reported back as something the user believed.
        hypotheses: ["onboarding is where they drop off", "pricing is the blocker"],
      }))
  })

  it("unchecking and re-checking a source leaves it read", async () => {
    get.mockResolvedValue(WAITING)
    approve.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")
    const box = screen.getByLabelText("Read the tracker")
    fireEvent.click(box)
    fireEvent.click(box)
    fireEvent.click(screen.getByText("Approve and run"))
    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith(7, {
        excluded_sources: [],
        hypotheses: [],
      }))
  })

  it("refuses to run with every source excluded", async () => {
    // A run with nothing to read produces a confident-looking empty report,
    // which is the worst output this feature has.
    get.mockResolvedValue(WAITING)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")
    fireEvent.click(screen.getByLabelText("Read calls and customer tickets"))
    fireEvent.click(screen.getByLabelText("Read the tracker"))
    expect((screen.getByText("Approve and run") as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByTestId("goal-plan-empty-warning")).toBeTruthy()
    fireEvent.click(screen.getByText("Approve and run"))
    expect(approve).not.toHaveBeenCalled()
  })

  it("a failed approve is not a silent no-op", async () => {
    get.mockResolvedValue(WAITING)
    approve.mockRejectedValue(new Error("500"))
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")
    fireEvent.click(screen.getByText("Approve and run"))
    await waitFor(() => expect(screen.getByTestId("goal-error")).toBeTruthy())
  })
})

describe("polling around the plan gate", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }))
  afterEach(() => vi.useRealTimers())

  it("stops polling while waiting on the human", async () => {
    get.mockResolvedValue(WAITING)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")
    const calls = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(12_000)
    expect(get.mock.calls.length).toBe(calls)
  })

  it("RESTARTS polling after the user approves", async () => {
    // `awaiting_approval` is terminal, so the loop has already stopped and
    // `load` is keyed on runId, which never changes. Without the re-arm the
    // user approves and then watches a stale panel while the run finishes.
    get.mockResolvedValue(WAITING)
    approve.mockResolvedValue({ ...RUN, status: "running" })
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")

    get.mockResolvedValue({ ...RUN, status: "running" })
    fireEvent.click(screen.getByText("Approve and run"))
    await waitFor(() => expect(approve).toHaveBeenCalled())

    const calls = get.mock.calls.length
    await vi.advanceTimersByTimeAsync(9_500)
    expect(get.mock.calls.length).toBeGreaterThan(calls)
  })

  it("a failed approve re-arms the poll instead of stranding the run", async () => {
    // The server CLAIMS the row before starting work, so a lost response means
    // the run is going and nothing is watching it.
    get.mockResolvedValue(WAITING)
    approve.mockRejectedValue(new Error("504"))
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-plan")

    get.mockResolvedValue({ ...RUN, status: "running" })
    fireEvent.click(screen.getByText("Approve and run"))
    await waitFor(() => expect(screen.getByTestId("goal-error")).toBeTruthy())
    await waitFor(() => expect(screen.getByTestId("goal-running")).toBeTruthy())
  })
})

describe("the engine's name never reaches the screen", () => {
  // The word is all over the routes, the types and this component's own
  // comments. It must not survive into anything a user can read — including a
  // class name or a test id, which is why this reads innerHTML rather than
  // textContent.
  const READY = {
    ...RUN,
    findings: [{
      id: 1, statement: "9 claims across 4 accounts concern export latency.",
      claim_ids: ["c1"], adjudication: "corroborated", impact_value: 4,
      currency: "accounts", confidence_band: "medium",
      assumed_params: [{ name: "value_per_account", basis: "no revenue data" }],
      surfaced_by: ["Renewal call — Vandelay Industries"],
      impact: { value: 4, affected_population: 4 },
      confidence: { band: "medium", weakest_leg: null, weakest_leg_reason: null, cap_reason: null },
    }],
    considered: [{ id: 2, label: "onboarding friction", reason: "one account", stopped_at_stage: "verification", claim_ids: [] }],
    coverage_notes: [{ reason: "undated evidence", actual: "40 of 300 signals" }],
    prioritisation: { plan: { ...PLAN, hypotheses: ["pricing is the blocker"] } },
  }

  const states: [string, Record<string, unknown>, string][] = [
    ["awaiting_confirmation", {
      ...RUN, status: "awaiting_confirmation",
      prioritisation: { ask: "Which revenue does this mean?", proposed_definition: "net revenue" },
    }, "goal-confirm"],
    ["awaiting_approval", WAITING, "goal-plan"],
    ["running", { ...RUN, status: "running" }, "goal-running"],
    ["ready", READY, "goal-report"],
  ]

  it.each(states)("says nothing about the engine in %s", async (_name, row, testid) => {
    get.mockResolvedValue(row)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId(testid)
    expect(document.body.innerHTML).not.toMatch(/crucible/i)
  })
})
