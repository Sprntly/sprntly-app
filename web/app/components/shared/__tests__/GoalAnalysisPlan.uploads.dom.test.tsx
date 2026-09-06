// @vitest-environment jsdom
//
// Files attached to the message, on the plan the reader is asked to approve.
//
// The gate is a DISCLOSURE, not a receipt: the reader is being asked to agree
// to a method before it runs. Two things follow, and they pull in opposite
// directions, which is why both are asserted here.
//
//   1. An upload has to be VISIBLE. A person who attached twelve files and is
//      shown a plan describing their connected sources has no way to tell
//      whether the files were picked up, and the honest reading of that
//      screen is that they were not.
//   2. An upload has to be visibly NOT a connected source. It is read for
//      this run and written nowhere, so the same question asked tomorrow
//      without the file gets a different answer — and a row that looks
//      exactly like a connector row conceals precisely that.
//
// The two are only compatible if the uploads are shown SEPARATELY, which is
// what these tests pin.
import * as React from "react"
import { cleanup, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GoalAnalysisPlan } from "../GoalAnalysisPlan"
import type { GoalRunPlan } from "../../../lib/api"

const CONNECTED = [
  {
    source_type: "revenue", signal_count: 260,
    label: "revenue data", witnesses: "how much something moved",
  },
]

const UPLOADS = [
  { name: "08_sales_data", tables: 3, records: 74 },
  { name: "02_support_tickets", tables: 1, records: 350 },
]

const base = (over: Record<string, unknown> = {}) => ({
  goal_text: "grow revenue this year",
  definition_text: "expansion minus churn",
  currency: "accounts",
  total_signals: 260,
  sources: CONNECTED,
  cannot_answer: [],
  will_produce: [],
  excluded_sources: [],
  hypotheses: [],
  steps: [],
  observations: [],
  questions: [],
  coverage: {},
  ...over,
}) as unknown as GoalRunPlan

const renderPlan = (plan: GoalRunPlan) =>
  render(<GoalAnalysisPlan plan={plan} approving={false} onApprove={vi.fn()} />)

afterEach(cleanup)

describe("uploads on the plan gate", () => {
  it("names each attached file, so the reader can see it was picked up", () => {
    renderPlan(base({ uploads: UPLOADS }))
    const block = screen.getByTestId("goal-plan-uploads")
    expect(within(block).getByText("08_sales_data")).toBeTruthy()
    expect(within(block).getByText("02_support_tickets")).toBeTruthy()
  })

  it("says the files are read for this run and not kept", () => {
    // The one sentence that distinguishes this from the connector upload,
    // which would ingest the same bytes permanently.
    renderPlan(base({ uploads: UPLOADS }))
    expect(
      within(screen.getByTestId("goal-plan-uploads"))
        .getByText(/not added to your knowledge graph/i),
    ).toBeTruthy()
  })

  it("keeps the uploads out of the connected-source list", () => {
    // THE NEGATIVE HALF. Without this, a renderer that simply appended the
    // uploads to the tick list above would satisfy every assertion so far
    // while erasing the distinction the block exists to draw.
    renderPlan(base({ uploads: UPLOADS }))
    const uploadNames = within(screen.getByTestId("goal-plan-uploads"))
      .getAllByTestId("goal-plan-upload")
    expect(uploadNames).toHaveLength(2)
    for (const row of screen.getAllByText(/revenue data/)) {
      expect(screen.getByTestId("goal-plan-uploads").contains(row)).toBe(false)
    }
  })

  it("offers no checkbox for an upload, even while sources are being changed", () => {
    // Dropping a connected source is a change to the plan. "Dropping" an
    // upload means detaching it from the message — a different act, in a
    // different place — and a control here would imply this screen could do
    // it.
    renderPlan(base({ uploads: UPLOADS }))
    const block = screen.getByTestId("goal-plan-uploads")
    expect(block.querySelectorAll("input[type=checkbox]")).toHaveLength(0)
  })

  it("does not say nothing is connected when the reader attached files", () => {
    // A workspace with no connectors is exactly the workspace most likely to
    // attach a spreadsheet. Telling that reader "nothing is connected for
    // this to read" above a list of the files just read is the screen
    // contradicting itself in its own first sentence.
    renderPlan(base({ sources: [], uploads: UPLOADS }))
    expect(screen.queryByTestId("goal-plan-no-sources")).toBeNull()
    expect(screen.getByTestId("goal-plan-uploads")).toBeTruthy()
  })

  it("still says nothing is connected when there is genuinely nothing", () => {
    renderPlan(base({ sources: [], uploads: [] }))
    expect(screen.getByTestId("goal-plan-no-sources")).toBeTruthy()
  })

  // ── What was attached and NOT read ──────────────────────────────────────
  //
  // ONE FILE PER CASE. The defect that produced these was three files missing
  // from a twelve-file pack, and it was only ever diagnosable because the
  // same files attached ALONE came back correctly. An assertion over an
  // aggregate would have been satisfied by the broken engine too.

  it("names a file it could not read, rather than leaving it off the list", () => {
    renderPlan(base({
      uploads: [{ name: "08_sales_data", tables: 3, records: 74 }],
      unread_uploads: [{
        name: "09_crm_win_loss",
        reason: "it could not be opened",
      }],
    }))
    const block = screen.getByTestId("goal-plan-uploads")
    expect(within(block).getByText("09_crm_win_loss")).toBeTruthy()
    expect(within(block).getByText("it could not be opened")).toBeTruthy()
  })

  it("does not present an unread file as one of the files it read", () => {
    // THE ASSERTION THAT MAKES THE ONE ABOVE WORTH HAVING. A renderer that
    // appended the unread names to the tick list would satisfy "the name is
    // on screen" while telling the reader the file was read.
    renderPlan(base({
      uploads: [{ name: "08_sales_data", tables: 3, records: 74 }],
      unread_uploads: [{ name: "09_crm_win_loss", reason: "it could not be opened" }],
    }))
    const read = screen.getAllByTestId("goal-plan-upload")
    expect(read).toHaveLength(1)
    expect(within(read[0]).getByText("08_sales_data")).toBeTruthy()
    expect(screen.getAllByTestId("goal-plan-upload-unread")).toHaveLength(1)
  })

  it("says how many files were not read, in the reader's own terms", () => {
    renderPlan(base({
      uploads: [],
      unread_uploads: [{ name: "00_readme", reason: "this pass reads spreadsheets" }],
    }))
    expect(
      screen.getByTestId("goal-plan-unread-note").textContent,
    ).toContain("1 file you attached was not read")
  })

  it("does not claim it read anything when every attachment failed", () => {
    // A plan whose upload block says "read for this analysis only" over a
    // list on which nothing was read is the screen asserting the thing this
    // whole disclosure exists to correct.
    renderPlan(base({
      uploads: [],
      unread_uploads: [{ name: "00_readme", reason: "this pass reads spreadsheets" }],
    }))
    const block = screen.getByTestId("goal-plan-uploads")
    expect(within(block).queryByText(/not added to your knowledge graph/i)).toBeNull()
    expect(screen.queryByTestId("goal-plan-no-sources")).toBeNull()
  })

  it("shows a PDF-only run the block naming the file, and still says nothing is connected", () => {
    // MEASURED ON STAGING: a strategy PDF attached on its own produced a plan
    // with no attachment block at all — the run presented exactly as if
    // nothing had been sent. Both halves are asserted, because the fix is
    // only correct if it adds the disclosure WITHOUT also claiming there is
    // something to read: this workspace has no connectors and the one file it
    // was given carries prose.
    renderPlan(base({
      sources: [], uploads: [],
      unread_uploads: [{
        name: "07_company_strategy_FY2027",
        reason: "this pass reads the structure of spreadsheets and CSVs, and "
          + "this is a .pdf file",
      }],
    }))
    const block = screen.getByTestId("goal-plan-uploads")
    expect(within(block).getByText("07_company_strategy_FY2027")).toBeTruthy()
    expect(within(block).getByText(/this is a \.pdf file/)).toBeTruthy()
    expect(screen.getByTestId("goal-plan-no-sources")).toBeTruthy()
  })

  it("renders a plan stored before this disclosure existed exactly as before", () => {
    const { unread_uploads: _u, ...legacy } = base({
      uploads: UPLOADS, unread_uploads: [],
    }) as Record<string, unknown>
    renderPlan(legacy as unknown as GoalRunPlan)
    expect(screen.queryByTestId("goal-plan-upload-unread")).toBeNull()
    expect(screen.queryByTestId("goal-plan-unread-note")).toBeNull()
    expect(screen.getAllByTestId("goal-plan-upload")).toHaveLength(2)
  })

  it("renders a plan stored before uploads existed exactly as before", () => {
    // `uploads` is absent, not empty, on every plan written before this
    // shipped — and those plans are re-rendered whenever an old run is opened.
    const { uploads: _drop, ...legacy } = base({ uploads: [] }) as Record<string, unknown>
    renderPlan(legacy as unknown as GoalRunPlan)
    expect(screen.queryByTestId("goal-plan-uploads")).toBeNull()
    expect(screen.getByTestId("goal-plan-sources")).toBeTruthy()
  })
})
