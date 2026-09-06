// @vitest-environment jsdom
//
// Documents attached to the message and read as PROSE, on the plan gate.
//
// THE THIRD THING A PLAN CAN SAY ABOUT AN ATTACHMENT, and until now it was a
// silence. A workbook became an upload row; everything else became an
// "unread" row — so a reader who attached ten customer calls was told,
// accurately, that their PDF was not a spreadsheet, and the run then computed
// its answer without a word of it.
//
// What these tests pin is the difference between the three statements, because
// the whole value of the disclosure is that a reader can tell them apart:
//
//   read as tables   -> "3 tables", and its columns informed the method
//   read as prose    -> "10 conversations", and what was said in them is
//                       evidence the findings rest on
//   not read at all  -> a dash, and the reason
//
// A screen that showed a document under any of the other two headings would be
// telling the reader something untrue about their own file.
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

const PROSE = [
  {
    name: "customer_calls",
    conversations: 10,
    // Copied from what the server actually composes
    // (`prose.ProseDocument.how_it_was_read`) rather than invented here. A
    // fixture that drifts from the real sentence is a test asserting a string
    // no user will ever be shown.
    how: "read as 10 separate conversations — the document says it holds 10, "
      + "and it does, split at the per-call headers, each dated from its own "
      + "header",
  },
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

describe("documents read as prose on the plan gate", () => {
  it("names the document and how many conversations it was read as", () => {
    renderPlan(base({ prose_uploads: PROSE }))
    const row = screen.getByTestId("goal-plan-upload-prose")
    expect(within(row).getByText("customer_calls")).toBeTruthy()
    expect(within(row).getByText("10 conversations")).toBeTruthy()
  })

  it("renders the segmentation sentence verbatim", () => {
    // THE ONE LINE A READER CAN DISAGREE WITH — "that pack holds eleven calls,
    // not ten". A client that summarised it would be deciding for them which
    // part of the engine's account of itself they get to check.
    renderPlan(base({ prose_uploads: PROSE }))
    expect(
      within(screen.getByTestId("goal-plan-upload-prose"))
        .getByText(PROSE[0].how),
    ).toBeTruthy()
  })

  it("says the document is read for this run and not kept", () => {
    renderPlan(base({ prose_uploads: PROSE }))
    expect(
      within(screen.getByTestId("goal-plan-uploads"))
        .getByText(/not added to your knowledge graph/i),
    ).toBeTruthy()
  })

  it("shows the read and the unread documents in ONE list", () => {
    // NOT TWO SECTIONS. The failure this guards is one of omission: a reader
    // who attached six documents and saw three ticks had nothing on screen to
    // tell them the list was short. A separate section further down reproduces
    // that at one scroll's distance.
    renderPlan(base({
      prose_uploads: PROSE,
      unread_uploads: [{ name: "scan", reason: "it held no text" }],
    }))
    const block = screen.getByTestId("goal-plan-uploads")
    expect(within(block).getByText("customer_calls")).toBeTruthy()
    expect(within(block).getByText("scan")).toBeTruthy()
    expect(within(block).getByText("it held no text")).toBeTruthy()
  })

  it("keeps prose documents out of the connected-source list", () => {
    // THE NEGATIVE HALF. A document read for one run is not a standing fact
    // about the workspace, and a row that looks like a connector row says it
    // is — so the same question asked tomorrow without the file would get a
    // different answer with nothing on this screen to have predicted it.
    // Asserted the way the sibling upload test asserts it: the attachment
    // block is INSIDE the sources section by design, so what has to be true
    // is that the connector rows are not in the attachment block and the
    // document is not among the connector rows.
    renderPlan(base({ prose_uploads: PROSE }))
    const block = screen.getByTestId("goal-plan-uploads")
    expect(within(block).getAllByTestId("goal-plan-upload-prose"))
      .toHaveLength(1)
    for (const row of screen.getAllByText(/revenue data/)) {
      expect(block.contains(row)).toBe(false)
    }
  })

  it("offers no checkbox for a prose document", () => {
    // Dropping a connected source is a change to the plan. "Dropping" a
    // document means detaching it from the message — a different act, in a
    // different place — and a control here would imply this screen could do
    // it.
    renderPlan(base({ prose_uploads: PROSE }))
    expect(screen.getByTestId("goal-plan-uploads")
      .querySelectorAll("input[type=checkbox]")).toHaveLength(0)
  })

  it("counts as something to read, so the verdict is not 'nothing connected'", () => {
    // A workspace with no connectors is exactly the one most likely to attach
    // a transcript. Telling that reader "nothing is connected for this to
    // read" directly above the document it goes on to list is the screen
    // contradicting itself in its own first sentence.
    renderPlan(base({ sources: [], total_signals: 0, prose_uploads: PROSE }))
    expect(screen.queryByText(/Nothing is connected for this to read/i))
      .toBeNull()
  })

  it("renders nothing at all for a plan stored before this existed", () => {
    renderPlan(base())
    expect(screen.queryByTestId("goal-plan-upload-prose")).toBeNull()
    expect(screen.queryByTestId("goal-plan-uploads")).toBeNull()
  })
})
