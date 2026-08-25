// @vitest-environment jsdom
//
// Goal Analysis, END TO END, in one mount: a sentence typed into the composer,
// two gates answered in the thread, and the REPORT read off the right panel.
//
// WHY THIS FILE EXISTS. Every other test of this feature stops one hop short of
// the reader. The thread-side tests end at `openContentPanel("goal")` and probe
// a STRING out of NavigationContext; the panel-side tests mount
// `GoalAnalysisTab` directly with a hand-built run. Nothing rendered the lines
// between them — ContentPanel's lazy boundary and its `content.goalRunId !=
// null` body guard — so "approve → the reader gets the report" was asserted by
// nobody. Short-circuiting that body to `false` left the entire goal suite
// green; it turns all six tests here red.
//
// ONE GATE IN THAT SEAM IS STILL NOT PINNED, AND SAYING SO IS THE POINT.
// `hidden.goal` cannot be reached from this flow: the panel opens ON the goal
// tab, and `visibleTabs` always keeps whichever tab is being shown, so forcing
// `hidden.goal` true changes nothing a reader could see here. It bites when a
// run is RESTORED while the panel sits elsewhere, which belongs to
// `ChatScreen.goal-restore`. Claiming it here would be the same defect this
// file exists to remove.
//
// AND NOTHING ASSERTED THE DECISIONS ON THE WIRE. The definition confirmed at
// gate 1 and the sources/hypotheses decided at gate 2 could be replaced with
// empty or wrong values and the thread would keep showing the reader their own
// words back. So the fake server here is not a fixture that returns a finished
// report — it BEHAVES like `routes/crucible.py`: the plan's `definition_text`
// is whatever `confirm` was sent, and `approve` filters the plan's sources,
// re-totals the signals and records `excluded_sources` / `hypotheses` exactly
// as the route does. Every assertion below therefore runs through the network
// boundary, and a decision dropped between the card and the request shows up as
// a report that misstates its own inputs — which is the one failure this whole
// feature exists to prevent.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type {
  GoalPlanSource, GoalRunDetail, GoalRunPlan,
} from "../../../../lib/api"

// HOISTED, unlike the sibling goal file's plain assignment: `ContentPanel`
// builds its tab list with JSX at MODULE level, which runs while this file's
// imports are still being evaluated — long before a statement down here could
// have set the global.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const listRuns = vi.fn()
const startRun = vi.fn()
const getRun = vi.fn()
const confirmRun = vi.fn()
const approveRun = vi.fn()
const listTurns = vi.fn()
const createConv = vi.fn()
const resolveIntent = vi.fn()

vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    apiErrorMessage: (_s: number, b: unknown) =>
      (b as { detail?: string })?.detail || "",
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: (...a: unknown[]) => createConv(...a),
      addTurn: vi.fn().mockResolvedValue({}),
      listTurns: (...a: unknown[]) => listTurns(...a),
    },
    chatIntentApi: { resolve: (...a: unknown[]) => resolveIntent(...a) },
    goalAnalysisApi: {
      list: (...a: unknown[]) => listRuns(...a),
      start: (...a: unknown[]) => startRun(...a),
      get: (...a: unknown[]) => getRun(...a),
      confirm: (...a: unknown[]) => confirmRun(...a),
      approve: (...a: unknown[]) => approveRun(...a),
      // The panel asks for a report document only when the run carries an
      // `artifact_id`; this run does not, so these exist to keep the mock
      // honest rather than because they are reached.
      document: vi.fn().mockRejectedValue(new Error("no document")),
      createDocument: vi.fn(),
      forkDocument: vi.fn(),
    },
    // ── Reached by ContentPanel's own subtree, not by the goal path ────────
    storiesApi: { getForPrd: vi.fn().mockResolvedValue({ stories: [] }) },
    ticketSetsApi: {
      get: vi.fn(), list: vi.fn().mockResolvedValue({ sets: [] }),
    },
    artifactTemplatesApi: { list: vi.fn().mockResolvedValue({ templates: [] }) },
    documentsApi: { get: vi.fn(), update: vi.fn() },
    reportsApi: {
      listForConversation: vi.fn().mockResolvedValue({ reports: [] }),
      get: vi.fn().mockResolvedValue(null),
      share: vi.fn(),
      downloadPdf: vi.fn(),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(), resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(), loadPrdById: vi.fn(),
}))
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(), resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn(),
  }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({
    entriesByInsight: new Map(), loading: false, refetch: vi.fn(),
  }),
}))
// The PRD body is a large subtree with its own coverage and nothing to do with
// this flow. Stubbed so the panel under test is the TAB BAR plus the goal body
// — the two halves this file exists to join.
vi.mock("../../../shared/PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: { feature_flags: { crucible: true } },
    refresh: async () => {},
  }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ContentPanel } from "../../../shared/ContentPanel"
import { ChatScreen } from "../ChatScreen"

// ── The reader's side of the conversation ───────────────────────────────────

/** What the reader TYPES. Never what the run works from. */
const TYPED = "How can I increase revenue by 5%?"
/** What the planner EXTRACTS from it, and what the run is started with. */
const EXTRACTED = "increase revenue by 5%"
/** What the reader confirms at gate 1 — an EDIT of the proposal below, so a
 *  report that echoes the proposal instead of the confirmation is visible. */
const PROPOSED = "Revenue, all accounts, this quarter"
const CONFIRMED =
  "Net revenue retention across all paying accounts, trailing 90 days"
/** What the reader adds at gate 2. */
const HYPOTHESIS = "onboarding is where they drop off"
/** The source the reader UNTICKS at gate 2. */
const DROPPED_TYPE = "project_mgmt"
const DROPPED_LABEL = "the tracker"
const KEPT_LABEL = "customer conversations"

const RUN_ID = 77
const CONV_ID = 7

// ── The fake server, behaving like `routes/crucible.py` ─────────────────────

const SOURCES: GoalPlanSource[] = [
  {
    source_type: "customer_voice",
    label: KEPT_LABEL,
    signal_count: 18,
    witnesses: "what customers said, in their own words",
  },
  {
    source_type: DROPPED_TYPE,
    label: DROPPED_LABEL,
    signal_count: 12,
    witnesses: "what the team planned and shipped",
  },
]

type Decision = { excluded_sources: string[]; hypotheses: string[] }

/** Whatever `confirm` was actually sent. The route stores the reader's own
 *  sentence on the plan, so the report can only quote what the wire carried. */
let confirmedDefinition = ""

const planAsOffered = (): GoalRunPlan => ({
  goal_text: EXTRACTED,
  definition_text: confirmedDefinition,
  currency: "accounts",
  total_signals: SOURCES.reduce((n, s) => n + s.signal_count, 0),
  sources: SOURCES,
  cannot_answer: [
    {
      question: "How much would closing this move revenue?",
      because: "nothing connected here carries a revenue figure per account",
      remedy: "connect billing",
    },
  ],
  will_produce: ["Themes ranked by how many accounts they touch"],
})

/** `_approve` in `routes/crucible.py`, faithfully: the stored plan describes
 *  the run that was OFFERED, so the reader's answer is folded into it — dropped
 *  sources removed, signals re-totalled, exclusions and hypotheses recorded. */
const planAsApproved = (d: Decision): GoalRunPlan => {
  const kept = SOURCES.filter((s) => !d.excluded_sources.includes(s.source_type))
  return {
    ...planAsOffered(),
    sources: kept,
    total_signals: kept.reduce((n, s) => n + s.signal_count, 0),
    excluded_sources: [...d.excluded_sources],
    hypotheses: [...d.hypotheses],
  }
}

const FINDING_STATEMENT =
  "Accounts that hit the export row limit raise it again within the same month"

const runRow = (status: GoalRunDetail["status"]): GoalRunDetail => ({
  id: RUN_ID,
  status,
  goal_text: EXTRACTED,
  error_code: null,
  coverage_notes: [],
  claim_count: 0,
  conversation_id: CONV_ID,
  artifact_id: null,
  created_at: null,
  finished_at: null,
  findings: [],
  considered: [],
})

const runAwaitingConfirmation = (): GoalRunDetail => ({
  ...runRow("awaiting_confirmation"),
  prioritisation: {
    ask: "Revenue could mean bookings or recognised revenue. Which did you mean?",
    proposed_definition: PROPOSED,
    proposed_source: "your KPI tree",
  },
})

const runAwaitingApproval = (): GoalRunDetail => ({
  ...runRow("awaiting_approval"),
  prioritisation: { plan: planAsOffered() },
})

const runReady = (d: Decision): GoalRunDetail => ({
  ...runRow("ready"),
  claim_count: 31,
  coverage_notes: [
    { reason: "Undated evidence", actual: "6 of 18 signals carry no date" },
  ],
  findings: [
    {
      id: 1,
      statement: FINDING_STATEMENT,
      claim_ids: ["c1", "c2", "c3"],
      adjudication: null,
      impact_value: 9,
      currency: "accounts",
      confidence_band: "medium",
      surfaced_by: ["Northwind renewal call", "Vandelay support thread"],
      assumed_params: [],
      impact: { value: 9, affected_population: null },
      confidence: {
        band: "medium",
        weakest_leg: "outcome",
        weakest_leg_reason: "no outcome evidence was found for this theme",
        cap_reason: null,
      },
    },
    {
      id: 2,
      statement: "Invoicing questions cluster around the end of the month",
      claim_ids: ["c4"],
      adjudication: null,
      // NULL, not 0 (I3). Present so the report has an unsized finding to
      // render honestly beside a sized one.
      impact_value: null,
      currency: "accounts",
      confidence_band: "low",
      surfaced_by: ["Initech onboarding call"],
      assumed_params: [],
      impact: { value: null, affected_population: null },
      confidence: {
        band: "low",
        weakest_leg: "reach",
        weakest_leg_reason: "only one account speaks to this",
        cap_reason: null,
      },
    },
  ],
  considered: [
    {
      id: 9,
      label: "Globex asked for SSO",
      reason: "only one account raised it",
      stopped_at_stage: "reach",
      claim_ids: ["c9"],
    },
  ],
  prioritisation: { plan: planAsApproved(d) },
})

// ── Mount ───────────────────────────────────────────────────────────────────

function Harness() {
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(ChatScreen),
    // THE PANEL, MOUNTED FOR REAL. This is the hop nothing else covers: every
    // thread-side test stops at the navigation string, and every panel-side
    // test mounts `GoalAnalysisTab` itself.
    React.createElement(ContentPanel),
  )
}

function mountApp() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(Harness)),
    ),
  )
}

function seedPersistedTab(tab: Record<string, unknown>, activeId: string) {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([tab]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", activeId)
}

/** The composer, as a reader uses it — no `+` menu, so the planner runs. */
async function typeAndSend(text: string) {
  const box = screen.getAllByPlaceholderText(/Ask Sprntly anything/)[0]
  await act(async () => { fireEvent.change(box, { target: { value: text } }) })
  await act(async () => { fireEvent.click(screen.getAllByLabelText("Send")[0]) })
}

const panelBody = () => document.querySelector(".cpanel-body")
const panelTabLabels = () =>
  Array.from(document.querySelectorAll(".cpanel-tab")).map((b) =>
    b.textContent?.trim())

/** The whole flow, once, in one mount. Returns nothing: everything a test
 *  asserts is on screen or on the wire. */
async function walkTheFlow() {
  seedPersistedTab(
    { id: "t1", title: "chat", dbConvId: CONV_ID, thread: [], messages: [] }, "t1")
  mountApp()

  // 1. The reader types a goal. The planner classifies it and EXTRACTS.
  await typeAndSend(TYPED)
  await waitFor(() => expect(startRun).toHaveBeenCalled())

  // 2. Gate 1, in the thread: what will this be read as?
  await waitFor(() =>
    expect(screen.getByTestId("goal-gate-definition")).toBeTruthy())
  await act(async () => {
    fireEvent.change(screen.getByLabelText("What this goal means"),
      { target: { value: CONFIRMED } })
  })
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /confirm and plan/i }))
  })

  // 3. Gate 2, in the thread: drop a source, say what you already believe.
  await waitFor(() => expect(screen.getByTestId("goal-gate-plan")).toBeTruthy())
  const gate = screen.getByTestId("goal-gate-plan")
  await act(async () => {
    fireEvent.click(within(gate).getByLabelText(`Read ${DROPPED_LABEL}`))
  })
  await act(async () => {
    fireEvent.change(within(gate).getByLabelText("What you already believe"),
      { target: { value: HYPOTHESIS } })
  })
  await act(async () => {
    fireEvent.click(
      within(gate).getByRole("button", { name: /approve and run/i }))
  })

  // 4. The report, in the panel.
  await waitFor(
    () => expect(panelBody()?.querySelector("[data-testid='goal-report']"))
      .toBeTruthy(),
    { timeout: 8_000 },
  )
}

beforeEach(() => {
  confirmedDefinition = ""

  listRuns.mockReset()
  listRuns.mockResolvedValue({ runs: [] })
  listTurns.mockReset()
  listTurns.mockResolvedValue({ turns: [] })
  createConv.mockReset()
  createConv.mockResolvedValue({ id: CONV_ID })

  resolveIntent.mockReset()
  // THE PLANNER EXTRACTS. What the run works from and what the reader said are
  // deliberately different strings — only this path produces both.
  resolveIntent.mockResolvedValue({ intent: "analyse_goal", task: EXTRACTED })

  startRun.mockReset()
  startRun.mockImplementation(async (
    _goal: string, opts?: { conversation_id?: number },
  ) => ({
    id: RUN_ID,
    conversation_id: opts?.conversation_id ?? null,
    status: "resolving_goal",
  }))

  getRun.mockReset()
  getRun.mockImplementation(async () => runAwaitingConfirmation())

  confirmRun.mockReset()
  confirmRun.mockImplementation(async (_runId: number, definition: string) => {
    // The route stores the reader's sentence, and everything downstream quotes
    // it. Sending the wrong string here is how a report ends up describing a
    // goal nobody confirmed.
    confirmedDefinition = definition
    getRun.mockImplementation(async () => runAwaitingApproval())
    return { id: RUN_ID, status: "awaiting_approval" }
  })

  approveRun.mockReset()
  approveRun.mockImplementation(async (
    _runId: number, opts?: Partial<Decision>,
  ) => {
    const decision: Decision = {
      excluded_sources: opts?.excluded_sources ?? [],
      hypotheses: opts?.hypotheses ?? [],
    }
    getRun.mockImplementation(async () => runReady(decision))
    return { id: RUN_ID, status: "running" }
  })

  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  cleanup()
  sessionStorage.clear()
  localStorage.clear()
})

describe("a goal typed in chat, answered in the thread, read in the panel", () => {
  it("shows the reader's own sentence, and runs on the planner's extraction",
    async () => {
      // Two different strings, and each belongs somewhere specific: the
      // transcript keeps what was said, the run gets what was meant. Emitting
      // the extraction as the message rewrote the reader's own words in their
      // own thread.
      await walkTheFlow()
      const bubble = document.querySelector(".bc-user-bubble")
      expect(bubble?.textContent).toBe(TYPED)
      expect(startRun).toHaveBeenCalledWith(EXTRACTED, expect.anything())
    }, 30_000)

  it("quotes the definition the reader confirmed, not the one proposed",
    async () => {
      // Through the wire, not from a fixture: the plan's `definition_text` is
      // whatever `confirm` carried, so a confirm that posted the proposal (or
      // nothing) shows up here as a report about a goal nobody agreed to.
      await walkTheFlow()
      const asked = within(panelBody() as HTMLElement)
        .getByTestId("goal-definition")
      // The report quotes the EDIT. Quoting the proposal instead fails here,
      // because `PROPOSED` and `CONFIRMED` are deliberately different
      // sentences — a fixture where the reader accepts the proposal unchanged
      // could not tell the two apart.
      expect(asked.textContent).toContain(CONFIRMED)
      expect(confirmRun).toHaveBeenCalledWith(RUN_ID, CONFIRMED)
    }, 30_000)

  it("does not count the dropped source in what was read, and still names it",
    async () => {
      // The exclusion has to survive three hops — the checkbox, the approve
      // body, the stored plan — and the report has to state it rather than
      // merely omit it. A quietly narrower run is exactly what coverage notes
      // exist to prevent.
      await walkTheFlow()
      const read = within(panelBody() as HTMLElement)
        .getByTestId("goal-what-was-read")
      // 18 signals across 1 source — the tracker's 12 are not in the total and
      // it is not in the list.
      expect(read.textContent).toContain("18 signals across 1 source")
      expect(read.textContent).toContain(KEPT_LABEL)
      expect(read.textContent).not.toContain(DROPPED_LABEL)
      // ...and it is NAMED as dropped, in the same section.
      expect(within(read).getByTestId("goal-excluded").textContent)
        .toContain("project mgmt")
      // ...and the request that produced all of the above carried the drop.
      // Asserted last on purpose: the rendered page is the thing that can be
      // wrong while every mock stays happy, so it is what fails first.
      expect(approveRun).toHaveBeenCalledWith(
        RUN_ID,
        expect.objectContaining({ excluded_sources: [DROPPED_TYPE] }),
      )
    }, 30_000)

  it("carries the reader's hypothesis into the report", async () => {
      // The other half of the gate-2 decision, and the one that is silently
      // droppable: nothing else on screen would look wrong without it.
      await walkTheFlow()
      expect(within(panelBody() as HTMLElement)
        .getByTestId("goal-hypotheses").textContent).toContain(HYPOTHESIS)
      expect(approveRun).toHaveBeenCalledWith(
        RUN_ID,
        expect.objectContaining({ hypotheses: [HYPOTHESIS] }),
      )
    }, 30_000)

  it("renders the findings the run produced", async () => {
      // The panel body, not the document: this is the hop nothing else covers.
      await walkTheFlow()
      const body = panelBody() as HTMLElement
      // The bar NAMES the run's tab. It does not pin ContentPanel's
      // `hidden.goal` gate, and deliberately does not pretend to: the panel is
      // opened ON the goal tab here, and `visibleTabs` always keeps the tab
      // being shown, so `hidden.goal` is unreachable from this flow. The gate
      // bites when a run is RESTORED while the panel sits on another tab,
      // which is `ChatScreen.goal-restore`'s subject, not this file's.
      expect(panelTabLabels()).toContain("Goal Analysis")
      expect(within(body).getByTestId("goal-report")).toBeTruthy()
      expect(within(body).getAllByTestId("goal-finding")[0].textContent)
        .toContain(FINDING_STATEMENT)
      // I3, at the far end of the whole flow: the unsized finding says so.
      expect(within(body).getByTestId("goal-unsized").textContent)
        .toBe("Could not be sized")
      // The ledger the ranking rests on is on screen too.
      expect(within(body).getByTestId("goal-considered").textContent)
        .toContain("Globex asked for SSO")
    }, 30_000)

  it("leaves the plan in the thread, read-only, with the dropped source struck",
    async () => {
      // The thread is the record a PM defends the decision from, so the plan
      // stays where it was agreed to — with no live controls on it.
      await walkTheFlow()
      const settled = screen.getByTestId("goal-gate-plan-done")
      expect(within(settled).getByText(DROPPED_LABEL).closest(".ggc-src-struck"))
        .toBeTruthy()
      expect(settled.textContent).toContain("dropped by you")
      expect(settled.textContent).toContain(HYPOTHESIS)
      // A record, not a control.
      expect(settled.querySelectorAll("input[type=checkbox]").length).toBe(0)
      // DOCUMENT-WIDE, not scoped to the settled card. Scoped, it could only
      // say the record has no button; whole-document it also says no SECOND
      // live gate is sitting somewhere on the page for a run that has already
      // been approved — a click there would 409 against the reader's own
      // approve.
      expect(screen.queryByRole("button", { name: /approve and run/i }))
        .toBeNull()
    }, 30_000)
})
