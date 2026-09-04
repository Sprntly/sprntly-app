// @vitest-environment jsdom
//
// The Goal Analysis REPORT — the chrome around a document, which is all this
// component is now.
//
// WHY THIS FILE SHRANK FROM 92 TESTS TO A DOZEN. It used to test a React
// renderer that rebuilt the whole report from `run.findings`: sizing, the RICE
// and MoSCoW tables, the finding cards, the coverage notes, the ruled-out
// ledger, the closing section. That renderer is gone (see the component's own
// note). The document is now produced once, in Python, by
// `backend/app/crucible/report.render_report_document`, and displayed here in
// a sandboxed iframe — the same path the PRD, the evidence brief and the VoC
// report already take.
//
// The rules those 92 tests protected did not go with them. They moved to the
// generator, where they are asserted against the real document:
//
//   * an unsized finding reads as "could not be sized" and never as 0 (I3),
//     the RICE and MoSCoW tables, the coverage notes, the ruled-out ledger and
//     the closing "what this cannot tell you" — `test_crucible_report.py`;
//   * the counts in the prose matching the data they describe —
//     `test_crucible_document_consistency.py`;
//   * the document's conformance to the memo it is modelled on —
//     `test_crucible_memo_conformance.py`.
//
// Testing them here as well is what the deleted renderer WAS: a second copy of
// every rule, kept in step by hand. What is left to test on this side is the
// chrome, and the two ways it can misrepresent the run: showing an empty
// document as though the analysis found nothing, and offering an irreversible
// action without saying so.
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { GoalAnalysisReport } from "../GoalAnalysisReport"

const DOC =
  "<!doctype html><html><body><h1>improve revenue by 2%</h1>" +
  "<h2>The ask</h2><p>Two findings bear on this goal.</p></body></html>"

const RUN = {
  id: 7,
  status: "ready" as const,
  goal_text: "raise net revenue retention",
  error_code: null,
  coverage_notes: [] as { reason: string; actual: string }[],
  claim_count: 412,
  conversation_id: null,
  artifact_id: null,
  created_at: null,
  finished_at: null,
  findings: [],
  considered: [],
  report_html: DOC,
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const run = (patch: Record<string, unknown> = {}) => ({ ...RUN, ...patch }) as any

afterEach(cleanup)

describe("the document", () => {
  it("shows the run's own rendered report", () => {
    render(<GoalAnalysisReport run={run()} />)
    const frame = screen.getByTitle("Goal analysis") as HTMLIFrameElement
    expect(frame.tagName).toBe("IFRAME")
    expect(frame.getAttribute("srcdoc")).toContain("improve revenue by 2%")
  })

  it("renders it without scripts, whatever the document contains", () => {
    // The document is model-derived HTML rendered inside the app. The sandbox
    // is the whole reason an iframe is an acceptable envelope for it, so it is
    // asserted rather than assumed: same-origin for the stylesheet, and no
    // allow-scripts.
    render(<GoalAnalysisReport run={run()} />)
    const frame = screen.getByTitle("Goal analysis")
    expect(frame.getAttribute("sandbox")).toBe("allow-same-origin")
  })

  it("titles the run with the goal the reader asked about", () => {
    render(<GoalAnalysisReport run={run()} />)
    const report = screen.getByTestId("goal-report")
    expect(report.textContent).toContain("raise net revenue retention")
    expect(report.textContent).toContain("Goal analysis")
  })
})

describe("a run with no document yet", () => {
  // STATED, NOT BLANK. An empty panel under a heading reads as "the analysis
  // found nothing", which is the one thing a run still generating must not
  // appear to say.
  it.each([
    ["missing", undefined],
    ["empty", ""],
    ["whitespace", "   \n "],
  ])("says so when the report is %s", (_label, html) => {
    render(<GoalAnalysisReport run={run({ report_html: html })} />)
    expect(screen.getByTestId("goal-report-pending").textContent).toMatch(
      /no rendered report yet/i,
    )
    expect(screen.queryByTitle("Goal analysis")).toBeNull()
  })

  it("still names the goal, so the panel is not anonymous", () => {
    render(<GoalAnalysisReport run={run({ report_html: "" })} />)
    expect(screen.getByTestId("goal-report").textContent).toContain(
      "raise net revenue retention",
    )
  })
})

describe("the document actions", () => {
  it("are absent unless the caller asks for them", () => {
    // `editable` defaults false so every existing caller renders what it always
    // rendered — a read-only report with no way to fork it by accident.
    render(<GoalAnalysisReport run={run()} />)
    expect(screen.queryByTestId("goal-report-actions")).toBeNull()
  })

  it("offer editing in place and saving a separate copy", () => {
    const onEdit = vi.fn()
    const onSaveCopy = vi.fn()
    render(
      <GoalAnalysisReport run={run()} editable onEdit={onEdit} onSaveCopy={onSaveCopy} />,
    )
    fireEvent.click(screen.getByTestId("goal-report-edit"))
    fireEvent.click(screen.getByTestId("goal-report-save-copy"))
    expect(onEdit).toHaveBeenCalledTimes(1)
    expect(onSaveCopy).toHaveBeenCalledTimes(1)
  })

  it("says what editing costs BEFORE the click, not after it", () => {
    // Editing detaches the report from the run for good. A reader told only
    // once it had happened would have no way back.
    render(<GoalAnalysisReport run={run()} editable />)
    const note = screen.getByTestId("goal-report-actions").textContent ?? ""
    expect(note).toMatch(/stops updating from the run/i)
  })

  it("disables BOTH while one is in flight", () => {
    // They write to the same run: letting the second fire while the first is
    // still going is how you get a copy of a report that is mid-creation.
    render(<GoalAnalysisReport run={run()} editable busy />)
    for (const id of ["goal-report-edit", "goal-report-save-copy"]) {
      expect((screen.getByTestId(id) as HTMLButtonElement).disabled).toBe(true)
    }
  })

  it("offers them on a run whose document has not landed yet", () => {
    // The actions belong to the RUN, not to the rendered document — a reader
    // who opens a finishing run should not find the panel's controls missing.
    render(<GoalAnalysisReport run={run({ report_html: "" })} editable />)
    expect(screen.getByTestId("goal-report-actions")).toBeTruthy()
  })
})
