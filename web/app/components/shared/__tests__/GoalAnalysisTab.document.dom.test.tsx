// @vitest-environment jsdom
//
// The Goal Analysis panel, once its report can be edited.
//
// Four ways this could go wrong in a manner nobody would notice until someone
// lost work:
//
//   1. A DETACHED REPORT NOT OPENING ITSELF. If the panel shows the read-only
//      findings for a report someone has rewritten, their words are invisible
//      and look thrown away — the one outcome this feature exists to prevent.
//   2. THE ORIGINAL BECOMING UNREACHABLE. Once a report is edited, the run's
//      own findings still exist, and a reader has to be able to get back to
//      them or the edit really did replace the analysis.
//   3. THE PANEL FETCHING A DOCUMENT THAT DOES NOT EXIST. Most runs never have
//      one; a request per ready run, answered 404, is load bought with nothing.
//   4. "SAVE AS DOCUMENT" TOUCHING THE REPORT. It is the fork half: a separate
//      document, and the run's own report left exactly as it was.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const get = vi.fn()
const getDocument = vi.fn()
const createDocument = vi.fn()
const forkDocument = vi.fn()
// `importOriginal`, not a bare factory: the component calls the real
// `apiErrorMessage` to parse a FastAPI `detail`, and a factory that omits it
// leaves it undefined — the catch block then throws and the error note never
// renders, which is a test artifact that looks exactly like a product bug.
vi.mock("../../../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../lib/api")>()),
  goalAnalysisApi: {
    get: (...a: unknown[]) => get(...a),
    confirm: vi.fn(),
    document: (...a: unknown[]) => getDocument(...a),
    createDocument: (...a: unknown[]) => createDocument(...a),
    forkDocument: (...a: unknown[]) => forkDocument(...a),
  },
}))

vi.mock("../DocumentTab", () => ({
  DocumentTab: ({ documentId }: { documentId: number }) => (
    <div data-testid="stub-document-tab">editing {documentId}</div>
  ),
}))

import { GoalAnalysisTab } from "../GoalAnalysisTab"

const FINDING = {
  id: 1,
  statement: "9 claims across 4 accounts concern export latency.",
  claim_ids: ["c1"],
  adjudication: "corroborated",
  impact_value: 4,
  currency: "accounts",
  confidence_band: "medium",
  surfaced_by: ["Renewal call — Vandelay Industries"],
  assumed_params: [],
  impact: { value: 4, affected_population: 4 },
  confidence: {
    band: "medium", weakest_leg: "problem",
    weakest_leg_reason: null, cap_reason: null,
  },
}

const READY = {
  id: 7, status: "ready", goal_text: "raise net revenue retention",
  error_code: null, coverage_notes: [], claim_count: 12,
  conversation_id: null, artifact_id: null,
  created_at: null, finished_at: null,
  findings: [FINDING], considered: [],
  // WHAT THE PANEL ACTUALLY SHOWS. The run's report is rendered server-side
  // and displayed in a sandboxed iframe — the panel is no longer a second
  // implementation of the document (see `GoalAnalysisReport.tsx`) — so a
  // fixture without this renders the "no report yet" note instead.
  report_html:
    "<!doctype html><html><body><h1>raise net revenue retention</h1>" +
    "<p>4 accounts</p></body></html>",
}

const DOC = {
  run_id: 7, id: 41, kind: "goal_analysis", title: "Goal analysis",
  status: "ready", body_html: "<h1>Goal analysis</h1>", version: 2,
  updated_at: null, updated_by: null, detached: false,
}

beforeEach(() => {
  get.mockReset(); getDocument.mockReset()
  createDocument.mockReset(); forkDocument.mockReset()
})
afterEach(cleanup)

describe("a report that has been edited", () => {
  it("opens itself rather than showing the run's findings over the top", async () => {
    // THE ONE THAT MATTERS. Someone rewrote this report; showing the original
    // findings instead would look exactly like their edit was discarded.
    get.mockResolvedValue({ ...READY, artifact_id: 41 })
    getDocument.mockResolvedValue({ ...DOC, detached: true })

    render(<GoalAnalysisTab runId={7} />)
    expect(await screen.findByTestId("goal-report-detached")).toBeTruthy()
    await waitFor(() =>
      expect(screen.getByTestId("stub-document-tab").textContent).toBe(
        "editing 41",
      ),
    )
    expect(screen.queryByTestId("goal-report")).toBeNull()
  })

  it("still lets the reader get back to the analysis it came from", async () => {
    // The findings are untouched on the server. A panel that could not reach
    // them would make that fact worthless.
    get.mockResolvedValue({ ...READY, artifact_id: 41 })
    getDocument.mockResolvedValue({ ...DOC, detached: true })

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-back"))
    expect(await screen.findByTestId("goal-report")).toBeTruthy()
    // And it is the RUN's own report that comes back, not the edited copy.
    const frame = screen.getByTitle("Goal analysis") as HTMLIFrameElement
    expect(frame.getAttribute("srcdoc")).toContain("4 accounts")
  })

  it("says so on the read-only view, so the edited version is not orphaned", async () => {
    // Having gone back, the reader is looking at the run's own findings while
    // a document holding someone's rewrite exists. Without this line that
    // document is unreachable and invisible.
    get.mockResolvedValue({ ...READY, artifact_id: 41 })
    getDocument.mockResolvedValue({ ...DOC, detached: true })

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-back"))
    expect(await screen.findByTestId("goal-report-has-edit")).toBeTruthy()
    fireEvent.click(screen.getByTestId("goal-report-open-edited"))
    expect(await screen.findByTestId("goal-report-detached")).toBeTruthy()
  })
})

describe("an untouched report", () => {
  it("renders read-only, with no edited marker", async () => {
    get.mockResolvedValue({ ...READY, artifact_id: 41 })
    getDocument.mockResolvedValue(DOC)

    render(<GoalAnalysisTab runId={7} />)
    expect(await screen.findByTestId("goal-report")).toBeTruthy()
    await waitFor(() => expect(getDocument).toHaveBeenCalled())
    expect(screen.queryByTestId("goal-report-detached")).toBeNull()
    expect(screen.queryByTestId("goal-report-has-edit")).toBeNull()
  })

  it("does not ask for a document a run has never had", async () => {
    // Most runs never have one. `artifact_id` rides the run row, so knowing
    // costs nothing and asking anyway would be load with no answer in it.
    get.mockResolvedValue(READY)
    render(<GoalAnalysisTab runId={7} />)
    await screen.findByTestId("goal-report")
    expect(getDocument).not.toHaveBeenCalled()
  })
})

describe("edit", () => {
  it("creates the document and opens the editor", async () => {
    get.mockResolvedValue(READY)
    createDocument.mockResolvedValue(DOC)

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-edit"))
    await waitFor(() => expect(createDocument).toHaveBeenCalledWith(7))
    expect(await screen.findByTestId("goal-report-document")).toBeTruthy()
  })

  it("warns that editing detaches the report BEFORE the click", async () => {
    // Editing is not a mode you can back out of. Being told afterwards is
    // being told too late.
    get.mockResolvedValue(READY)
    render(<GoalAnalysisTab runId={7} />)
    const actions = await screen.findByTestId("goal-report-actions")
    expect(actions.textContent).toMatch(/stops updating from the run/i)
  })

  it("says so plainly when the report will not open", async () => {
    // A dead button is worse than an error: the user presses it again.
    get.mockResolvedValue(READY)
    createDocument.mockRejectedValue(new Error("nope"))

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-edit"))
    expect((await screen.findByTestId("goal-doc-note")).textContent)
      .toMatch(/could not open this report/i)
  })

  it("shows the server's reason for a 413 instead of a generic failure", async () => {
    // The bug this closes rendered a fixed string, so the server's
    // explanation — the report is too large, and the RUN IS FINE — never
    // reached anyone. "Your analysis is broken" is the wrong thing to imply.
    get.mockResolvedValue(READY)
    const tooBig = Object.assign(new Error("Payload Too Large"), {
      status: 413,
      body: { detail: "This report is too large to open as a document (run 7 is unaffected)." },
    })
    createDocument.mockRejectedValue(tooBig)

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-edit"))
    const note = (await screen.findByTestId("goal-doc-note")).textContent || ""
    expect(note).toMatch(/too large/i)
    expect(note).toMatch(/run 7 is unaffected/i)
    expect(note).not.toMatch(/could not open this report/i)
  })

  it("falls back to its own sentence when the error carries no reason", async () => {
    // `apiErrorMessage` invents "Request failed (500)" when there is no detail.
    // That is worse than the sentence this component already writes, so it must
    // not win.
    get.mockResolvedValue(READY)
    createDocument.mockRejectedValue(
      Object.assign(new Error("boom"), { status: 500, body: { oops: true } }),
    )

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-edit"))
    const note = (await screen.findByTestId("goal-doc-note")).textContent || ""
    expect(note).toMatch(/could not open this report/i)
    expect(note).not.toMatch(/Request failed/i)
  })
})

describe("save as document", () => {
  it("forks a separate copy and says the report is unchanged", async () => {
    get.mockResolvedValue(READY)
    forkDocument.mockResolvedValue({
      id: 99, title: "Goal analysis (copy)", kind: "Goal analysis copy",
      version: 1, conversation_id: null, run_id: null, detached: false,
    })

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-save-copy"))
    await waitFor(() => expect(forkDocument).toHaveBeenCalledWith(7))
    expect((await screen.findByTestId("goal-doc-note")).textContent)
      .toMatch(/This report is unchanged/i)
    // And it did NOT turn the report into a document behind the user's back.
    expect(createDocument).not.toHaveBeenCalled()
    expect(screen.getByTestId("goal-report")).toBeTruthy()
  })

  it("shows the server's reason when the copy is refused for size", async () => {
    // The fork writer is guarded by the same `_body_or_413`. A reason surfaced
    // on one button and swallowed on the other is still a silent failure.
    get.mockResolvedValue(READY)
    forkDocument.mockRejectedValue(
      Object.assign(new Error("Payload Too Large"), {
        status: 413,
        body: { detail: "This report is too large to copy into a document." },
      }),
    )

    render(<GoalAnalysisTab runId={7} />)
    fireEvent.click(await screen.findByTestId("goal-report-save-copy"))
    const note = (await screen.findByTestId("goal-doc-note")).textContent || ""
    expect(note).toMatch(/too large to copy/i)
    expect(note).not.toMatch(/could not save a copy/i)
  })
})
