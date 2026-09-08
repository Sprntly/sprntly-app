// @vitest-environment jsdom
//
// The disclosure channel on the finished report panel — outside the
// sandboxed document, alongside the title and the document actions. `notes`
// rides on `run.prioritisation.plan`, which survives from the plan gate into
// `ready` (see that field's own comment in `lib/api.ts`), so this is the same
// array the gate showed before the run started, read back rather than
// recomputed.
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("../../../lib/api", () => ({
  goalAnalysisApi: { claimSource: vi.fn() },
}))

import { GoalAnalysisReport } from "../GoalAnalysisReport"
import type { GoalRunDetail } from "../../../lib/api"

const BASE_RUN = {
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
  report_html: "<!doctype html><html><body>report</body></html>",
} as unknown as GoalRunDetail

afterEach(cleanup)

describe("the disclosure channel on the report panel", () => {
  it("renders a note carried on the run's plan, outside the sandboxed document", () => {
    const run = {
      ...BASE_RUN,
      prioritisation: {
        plan: { notes: [{ kind: "source_scope", text: "Scoped to attachments only." }] },
      },
    } as unknown as GoalRunDetail
    render(<GoalAnalysisReport run={run} />)
    expect(screen.getByTestId("goal-report-note-source_scope").textContent)
      .toBe("Scoped to attachments only.")
  })

  it("renders a note of a kind it has never seen, same as any other", () => {
    const run = {
      ...BASE_RUN,
      prioritisation: {
        plan: { notes: [{ kind: "invented_for_this_test", text: "A future disclosure." }] },
      },
    } as unknown as GoalRunDetail
    render(<GoalAnalysisReport run={run} />)
    expect(screen.getByTestId("goal-report-note-invented_for_this_test").textContent)
      .toBe("A future disclosure.")
  })

  it("renders nothing extra when the run carries no notes — same as before the channel existed", () => {
    render(<GoalAnalysisReport run={BASE_RUN} />)
    expect(screen.queryByTestId(/goal-report-note-/)).toBeNull()
  })
})
