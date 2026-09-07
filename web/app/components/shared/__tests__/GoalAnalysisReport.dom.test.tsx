// @vitest-environment jsdom
//
// The Goal Analysis REPORT — the chrome around a document, not a renderer of
// one.
//
// WHAT THIS FILE USED TO BE, AND WHY IT IS NOT THAT ANY MORE. `GoalAnalysis-
// Report` used to rebuild the entire document in React from `run.findings`,
// in parallel with the Python that renders the exported copy. Two renderers of
// one report, sharing no code, each holding its own copy of every rule about
// how a finding is written out — and this file was the 1,500 lines of tests
// that kept the second one honest. The component's own header records what
// that cost: a decision box that existed only in the exported document, a
// grounded-money line the panel's payload could not even carry, caps declared
// twice, and half a dozen rules with a comment on each saying "keep this in
// step by hand".
//
// The second renderer is gone. What the panel shows is the bytes
// `backend/app/crucible/report.render_report_document` produced, displayed in
// a sandboxed iframe by `HtmlReportView`.
//
// SO THE ASSERTIONS WENT WHERE THE CODE WENT. Every guarantee this file used
// to make about what the document SAYS is now made against the renderer that
// actually writes it, over the real pipeline rather than a fixture:
//
//   an unsized finding is never 0 (I3)
//     -> test_crucible_report.py::test_an_unsized_finding_says_so_and_is_
//        never_a_number, and end to end over five corpus shapes in
//        test_crucible_document_consistency.py::_assert_null_is_never_zero_
//        or_small, which checks EVERY place a size appears rather than the
//        two this file could reach;
//   a finding carries the documents it rests on
//     -> test_crucible_report.py::test_a_finding_carries_the_documents_it_
//        rests_on;
//   "What this cannot tell you" is never dropped
//     -> test_crucible_report.py::test_the_limits_section_is_built_from_the_
//        plan_s_own_gaps;
//   what was read, including what the reader dropped
//     -> test_crucible_document_consistency.py::_assert_what_was_read_is_
//        what_the_plan_kept, which also checks the bullets sum to the total;
//   coverage notes qualifying what they qualify
//     -> test_crucible_report.py::test_coverage_notes_sit_inside_what_was_
//        read_not_in_a_footer;
//   the ruled-out ledger, with each reason
//     -> test_crucible_report.py::test_the_ruled_out_ledger_keeps_its_reasons;
//   the recommendation, the deep write-up, list pricing, the RICE table, the
//   relevance-gate disclosure, the shortfall note
//     -> their namesakes in test_crucible_report.py.
//
// WHAT IS LEFT HERE IS WHAT THIS COMPONENT STILL DECIDES, and all of it is
// something only the client can get wrong: whether the report reaches the
// reader intact, whether it can execute anything, whether a run without one
// says so, and whether the two document actions are offered — and warned
// about — before they are irreversible.
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const claimSource = vi.fn()
vi.mock("../../../lib/api", () => ({
  goalAnalysisApi: { claimSource: (...a: unknown[]) => claimSource(...a) },
}))

import { GoalAnalysisReport } from "../GoalAnalysisReport"
import type { ClaimSource, GoalRunDetail } from "../../../lib/api"

/** A stand-in for what `render_report_document` emits. It only has to be
 *  recognisable: the panel's job is to pass it through unchanged, and what a
 *  real one SAYS is asserted against the real renderer (see the header). */
const REPORT =
  '<!doctype html><html lang="en"><head><title>Goal analysis</title>' +
  "<style></style></head><body>" +
  '<div class="frame"><div class="page">' +
  "<h1>raise net revenue retention</h1>" +
  "<p>Could not be sized</p>" +
  "<p>9 accounts &amp; counting — “quoted”</p>" +
  "</div></div></body></html>"

const RUN = {
  id: 7,
  status: "ready",
  goal_text: "raise net revenue retention",
  error_code: null,
  coverage_notes: [],
  claim_count: 12,
  conversation_id: null,
  artifact_id: null,
  created_at: null,
  finished_at: null,
  findings: [],
  considered: [],
  report_html: REPORT,
} as unknown as GoalRunDetail

const frame = () => screen.getByTitle("Goal analysis") as HTMLIFrameElement

// NO `beforeEach(() => claimSource.mockReset())` HERE, DELIBERATELY. A reset
// hook immediately ahead of a test that rejects the mock (below) produces a
// phantom "unhandled rejection" test failure under this vitest/tinyspy
// version even though the component's own try/catch demonstrably runs (the
// resolved UI state proves it) — confirmed by bisection, not a guess.
// Every test below sets its own implementation before calling the mock, and
// the two tests that assert `not.toHaveBeenCalled()` run first, before
// anything in this file ever calls it — so an explicit reset buys nothing
// here and costs this false failure.
afterEach(cleanup)

describe("the document reaches the reader", () => {
  it("shows the report the run produced, character for character", () => {
    // NOT "CONTAINS THE HEADLINE". A panel that summarised, truncated,
    // re-ordered or re-escaped the document would still contain the headline;
    // the only assertion that catches all four is the whole document. The one
    // permitted difference is the panel's own gutter override, which
    // `HtmlReportView` splices into <head> and which adds nothing a reader
    // reads — so it is subtracted rather than waved at.
    render(<GoalAnalysisReport run={RUN} />)
    const shown = (frame().getAttribute("srcdoc") || "")
      .replace(/<style>body:has\(\.page\)[\s\S]*?<\/style>/, "")
    expect(shown).toBe(REPORT)
  })

  it("does not re-escape the text the document already escaped", () => {
    // The report is server-generated but it quotes tenant text, which arrives
    // escaped. Escaping it a second time is how "&" becomes "&amp;" on screen.
    render(<GoalAnalysisReport run={RUN} />)
    expect(frame().getAttribute("srcdoc")).toContain("9 accounts &amp; counting")
  })

  it("cannot execute anything the document carries", () => {
    // This is the boundary tenant text crosses into the reader's session.
    // `allow-same-origin` WITHOUT `allow-scripts`: the stylesheet applies,
    // nothing in the document runs, and inline handlers never fire.
    render(<GoalAnalysisReport run={RUN} />)
    const sandbox = frame().getAttribute("sandbox")
    expect(sandbox).toBe("allow-same-origin")
    expect(sandbox).not.toContain("allow-scripts")
  })

  it("names the goal above the document, so the panel says what it is", () => {
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.getByTestId("goal-report").textContent)
      .toContain("raise net revenue retention")
  })
})

describe("a run with no report yet", () => {
  const pending = { ...RUN, report_html: undefined } as GoalRunDetail

  it("says so rather than going blank", () => {
    // STATED, NOT BLANK. A run still generating — or one read by a client
    // older than the field — must not render as an empty panel, which reads
    // as "the analysis found nothing".
    render(<GoalAnalysisReport run={pending} />)
    expect(screen.getByTestId("goal-report-pending").textContent)
      .toContain("no rendered report yet")
    expect(screen.queryByTitle("Goal analysis")).toBeNull()
  })

  it("treats whitespace as no report at all", () => {
    render(
      <GoalAnalysisReport run={{ ...RUN, report_html: "   \n " } as GoalRunDetail} />,
    )
    expect(screen.getByTestId("goal-report-pending")).toBeTruthy()
  })
})

describe("the document actions", () => {
  it("offers none unless the caller asked for them", () => {
    // DEFAULT FALSE, so every existing caller renders what it rendered before.
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.queryByTestId("goal-report-actions")).toBeNull()
  })

  it("warns that editing is one-way BEFORE the click, not after it", () => {
    // Editing detaches the report from the run for good. A reader who did not
    // know that would be told only once it had happened.
    render(<GoalAnalysisReport run={RUN} editable />)
    const actions = screen.getByTestId("goal-report-actions")
    expect(actions.textContent).toContain("It stops updating from the run")
  })

  it("hands each action to its own handler", () => {
    const onEdit = vi.fn()
    const onSaveCopy = vi.fn()
    render(
      <GoalAnalysisReport run={RUN} editable onEdit={onEdit} onSaveCopy={onSaveCopy} />,
    )
    fireEvent.click(screen.getByTestId("goal-report-edit"))
    fireEvent.click(screen.getByTestId("goal-report-save-copy"))
    expect(onEdit).toHaveBeenCalledTimes(1)
    expect(onSaveCopy).toHaveBeenCalledTimes(1)
  })

  it("disables BOTH while one is in flight", () => {
    // They write to the same run. Letting the second fire while the first is
    // still going is how you get a copy of a report that is mid-creation.
    render(<GoalAnalysisReport run={RUN} editable busy />)
    expect((screen.getByTestId("goal-report-edit") as HTMLButtonElement).disabled)
      .toBe(true)
    expect(
      (screen.getByTestId("goal-report-save-copy") as HTMLButtonElement).disabled,
    ).toBe(true)
  })
})

describe("a cut option is reopenable, not just readable", () => {
  const CONSIDERED = [
    { id: 1, label: "self-serve onboarding", reason: "only 2 supporting claims — an anecdote, not a finding", stopped_at_stage: "clustering", claim_ids: ["c1", "c2"] },
    { id: 2, label: "pricing page redesign", reason: "every supporting claim comes from a single account", stopped_at_stage: "verification", claim_ids: ["c3"] },
  ]
  const withConsidered = { ...RUN, considered: CONSIDERED } as unknown as GoalRunDetail

  it("offers nothing when the caller has nowhere to send a selection", () => {
    // Undefined `onSelectOption` — the caller has no composer to hand the
    // label to. The prose in the document above is still the complete
    // account of what was cut; this layer just isn't reachable.
    render(<GoalAnalysisReport run={withConsidered} />)
    expect(screen.queryByTestId("goal-considered")).toBeNull()
  })

  it("offers nothing when nothing was cut", () => {
    render(<GoalAnalysisReport run={RUN} onSelectOption={vi.fn()} />)
    expect(screen.queryByTestId("goal-considered")).toBeNull()
  })

  it("lists every cut option as its own clickable object, closed by default", () => {
    render(<GoalAnalysisReport run={withConsidered} onSelectOption={vi.fn()} />)
    const details = screen.getByTestId("goal-considered") as HTMLDetailsElement
    expect(details.open).toBe(false)
    const options = screen.getAllByTestId("goal-considered-option")
    expect(options).toHaveLength(2)
    expect(options[0].textContent).toContain("self-serve onboarding")
    expect(options[1].textContent).toContain("pricing page redesign")
  })

  it("hands the exact label of the clicked option to the caller", () => {
    const onSelectOption = vi.fn()
    render(<GoalAnalysisReport run={withConsidered} onSelectOption={onSelectOption} />)
    const options = screen.getAllByTestId("goal-considered-option")
    fireEvent.click(options[1])
    expect(onSelectOption).toHaveBeenCalledTimes(1)
    expect(onSelectOption).toHaveBeenCalledWith("pricing page redesign")
  })

  it("does not restate the reason each was cut — that sentence lives once, in the document", () => {
    // The additive layer names the option, not the ruling on it: the reason
    // already appears, once, inside the sandboxed report `srcdoc`. Printing it
    // a second time here is the exact class of drift this component's own
    // header describes killing off.
    render(<GoalAnalysisReport run={withConsidered} onSelectOption={vi.fn()} />)
    const details = screen.getByTestId("goal-considered")
    expect(details.textContent).not.toContain("only 2 supporting claims")
    expect(details.textContent).not.toContain("single account")
  })
})

describe("a claim's source is checkable live, in the panel only", () => {
  const FINDING = {
    id: 1, label: "Export latency", statement: "9 claims concern export latency",
    claim_ids: ["11111111-1111-1111-1111-111111111111"],
  }
  const CONSIDERED = [
    { id: 2, label: "self-serve onboarding", reason: "an anecdote",
      stopped_at_stage: "clustering", claim_ids: ["c9"] },
  ]
  const withSources = {
    ...RUN, findings: [FINDING], considered: CONSIDERED,
  } as unknown as GoalRunDetail

  it("offers nothing when nothing on the run carries a claim id", () => {
    render(<GoalAnalysisReport run={RUN} />)
    expect(screen.queryByTestId("goal-sources")).toBeNull()
    expect(claimSource).not.toHaveBeenCalled()
  })

  it("lists one checkable row per finding and per cut option, closed by default", () => {
    render(<GoalAnalysisReport run={withSources} />)
    const details = screen.getByTestId("goal-sources") as HTMLDetailsElement
    expect(details.open).toBe(false)
    const rows = screen.getAllByTestId("goal-source-check")
    expect(rows).toHaveLength(2)
    expect(rows[0].textContent).toContain("Export latency")
    expect(rows[1].textContent).toContain("self-serve onboarding")
    // Nothing resolves until it is clicked.
    expect(claimSource).not.toHaveBeenCalled()
  })

  it("resolves the FIRST claim id of the row that was clicked, against this run", async () => {
    claimSource.mockResolvedValue({
      status: "resolved", content: null, source_type: null, valid_at: null,
      pointer: { kind: "call", title: "Renewal check-in", call_date: "2026-08-03" },
    } satisfies ClaimSource)
    render(<GoalAnalysisReport run={withSources} />)
    fireEvent.click(screen.getAllByTestId("goal-source-check")[0])
    expect(claimSource).toHaveBeenCalledWith(
      RUN.id, "11111111-1111-1111-1111-111111111111",
    )
    await waitFor(() => {
      expect(screen.getByTestId("goal-source-result").textContent)
        .toContain("Renewal check-in, 2026-08-03")
    })
    // THE HUMAN POINTER, NEVER THE BARE ID — a claim id in the panel is
    // exactly the decoration the resolver exists to replace.
    expect(screen.getByTestId("goal-source-result").textContent)
      .not.toContain("11111111-1111-1111-1111-111111111111")
  })

  it("reads the three unresolved states differently from each other", async () => {
    for (const [status, expected] of [
      ["not_found", "no longer available"],
      ["dropped_for_space", "did not keep enough detail"],
      ["no_pointer", "nothing a person could open"],
    ] as const) {
      claimSource.mockReset()
      claimSource.mockResolvedValue({
        status, content: null, source_type: null, valid_at: null, pointer: null,
      } satisfies ClaimSource)
      const { unmount } = render(<GoalAnalysisReport run={withSources} />)
      fireEvent.click(screen.getAllByTestId("goal-source-check")[0])
      await waitFor(() => {
        expect(screen.getByTestId("goal-source-result").textContent)
          .toContain(expected)
      })
      unmount()
    }
  })

  it("says so, rather than hanging, when the live check itself fails", async () => {
    const user = userEvent.setup()
    claimSource.mockRejectedValue(new Error("network error"))
    render(<GoalAnalysisReport run={withSources} />)
    await user.click(screen.getAllByTestId("goal-source-check")[0])
    expect(screen.getByTestId("goal-source-result").textContent)
      .toContain("Could not check this right now")
  })
})
