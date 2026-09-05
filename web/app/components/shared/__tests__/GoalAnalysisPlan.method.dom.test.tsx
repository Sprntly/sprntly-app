// @vitest-environment jsdom
//
// The plan gate rendering the METHOD the server now composes.
//
// The card used to render an inventory: what would be read, what could not be
// answered, and a client-side narrative derived from those two lists. The
// server now sends `plan.steps` — a numbered sequence of operations it can
// actually perform — and these are the guarantees that follow from that:
//
//   1. The server's steps are what render. `planNarrative` is the fallback for
//      plans stored before this shipped and must be unreachable otherwise, or
//      two live generators drift and the plan describes a run that no longer
//      happens.
//   2. Figures in a step come from the server, never from the card. That is
//      the property that used to be protected by asserting the narrative had
//      no numbers at all — the right assertion while the card was the author,
//      and the wrong one now that the server is.
//   3. Every question renders. The old card looked its id up in a table of
//      three and returned null on a miss, so everything derived from the
//      evidence vanished with no trace.
//   4. The reading surface has no form fields, and the gate is two steps.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { GoalAnalysisPlan } from "../GoalAnalysisPlan"
import type { GoalRunPlan } from "../../../lib/api"

const STEPS = [
  {
    n: 1, part: "Get the unit right", primitive: "select_evidence",
    what: "Fix which sources this answer may rest on",
    why: "Everything below is computed over 5 tables across 4 sources.",
  },
  {
    n: 2, part: "Get the unit right", primitive: "set_counting_unit",
    what: "Count in accounts, and price an account from your own contracts",
    why: "The median account is 283,526 and the mean is 288,312.",
  },
  {
    n: 3, part: "Work out where the money is", primitive: "reconcile_value_columns",
    what: "Reconcile base_acv_usd against total_acv_usd before either is used",
    why: "They disagree on 2 of 8 rows, understating the book by 273,378.",
    observations: ["contracts:value_columns:base_acv_usd:total_acv_usd"],
  },
  {
    n: 4, part: "Decide what to recommend", primitive: "rank_findings",
    what: "Order what survived",
    why: "A disagreement between two sources outranks either claim.",
  },
]

const OBSERVATIONS = [
  {
    id: "contracts:value_columns:base_acv_usd:total_acv_usd",
    kind: "value_columns_disagree", severity: "high",
    source: "08_sales_data:contracts",
    source_label: "sales data — contracts",
    what: "They disagree on 2 of 8 rows.",
    figures: { rows_differing: 2, gap_total: 273378 },
  },
]

const QUESTIONS = [
  {
    id: "value_column_choice",
    prompt: "Which figure is your book — total_acv_usd, or base_acv_usd?",
    why: "Every size in the finished document is denominated in this.",
    what_i_saw: "They differ on 2 of 8 rows; the gap is exactly expansion_acv_usd.",
    affects: "every size, and the ranking that follows from them",
    default_if_skipped: "total_acv_usd is used, because it is the complete column",
    options: ["total_acv_usd", "base_acv_usd"],
  },
  {
    id: "decision_owner", prompt: "Who decides this?",
    why: "Named on the decision box so the ranking has an owner.",
    affects: "who the recommendation is addressed to",
    default_if_skipped: "the decision box is rendered without an owner",
  },
]

const PLAN = {
  goal_text: "grow revenue this year",
  definition_text: "expansion minus churn across renewing accounts",
  currency: "accounts",
  total_signals: 412,
  sources: [
    {
      source_type: "revenue", signal_count: 260,
      label: "revenue data", witnesses: "how much something moved",
    },
    {
      source_type: "customer_voice", signal_count: 152,
      label: "calls and customer tickets", witnesses: "what customers reported",
    },
  ],
  cannot_answer: [
    {
      question: "Did a change like this work last time?",
      because: "no measured outcomes are connected",
      remedy: "connect your experiment tool",
    },
  ],
  will_produce: ["A ranked list of what is blocking this goal"],
  excluded_sources: [],
  hypotheses: [],
  steps: STEPS,
  observations: OBSERVATIONS,
  questions: QUESTIONS,
  coverage: {
    records: 1676, tables: 16, origins: 6,
    earliest: "2024-07", latest: "2026-09",
  },
} as unknown as GoalRunPlan

const renderPlan = (plan: GoalRunPlan = PLAN, onApprove = vi.fn()) => {
  render(<GoalAnalysisPlan plan={plan} approving={false} onApprove={onApprove} />)
  return onApprove
}

const toQuestions = () =>
  fireEvent.click(screen.getByRole("button", { name: /approve this plan/i }))
const approve = () => {
  toQuestions()
  fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
}

afterEach(cleanup)

// ─── 1. The server's method is what renders ────────────────────────────────

describe("the steps are the server's, not the card's", () => {
  it("renders the composed steps verbatim", () => {
    renderPlan()
    const approach = screen.getByTestId("goal-plan-steps").textContent ?? ""
    for (const step of STEPS) expect(approach).toContain(step.what)
  })

  it("numbers them continuously across their parts", () => {
    // The number is the reader's handle on the whole method. Restarting it
    // inside each part turns one sequence of four into three sequences.
    renderPlan()
    const shown = [...screen.getByTestId("goal-plan-steps").querySelectorAll("li")]
      .filter((li) => !li.hasAttribute("aria-hidden"))
      .map((li) => (li.textContent ?? "").trim())
    expect(shown[0].startsWith("1")).toBe(true)
    expect(shown[shown.length - 1].startsWith("4")).toBe(true)
  })

  it("groups them under their parts, each named once", () => {
    renderPlan()
    const text = screen.getByTestId("goal-plan-steps").textContent ?? ""
    expect(text.split("Get the unit right").length - 1).toBe(1)
    expect(text).toContain("Work out where the money is")
    expect(text).toContain("Decide what to recommend")
  })

  it("does NOT fall back to the client narrative when the server sent steps", () => {
    // TWO LIVE GENERATORS FOR ONE LIST is the drift that makes a plan describe
    // a run that no longer happens. `planNarrative` writes sentences no server
    // step contains — its presence here would mean both ran.
    renderPlan()
    const text = screen.getByTestId("goal-plan-approach").textContent ?? ""
    expect(text).not.toMatch(/Group what they say into findings/)
    expect(text).not.toMatch(/Show you what got set aside/)
  })

  it("still narrates a plan stored before the server composed steps", () => {
    // Every plan sitting `awaiting_approval` right now. The document is built
    // from what it has, not blank.
    const legacy = { ...PLAN, steps: undefined } as unknown as GoalRunPlan
    renderPlan(legacy)
    const text = screen.getByTestId("goal-plan-approach").textContent ?? ""
    expect(text).toMatch(/Group what they say into findings/)
  })
})

// ─── 2. The `why` stacks underneath and is hidden ──────────────────────────

describe("the why is a second line, behind one control", () => {
  it("hides every why until asked", () => {
    // Inline after a dash defeats scanning: the eye has to read every line to
    // find the next action.
    renderPlan()
    const text = screen.getByTestId("goal-plan-steps").textContent ?? ""
    for (const step of STEPS) expect(text).not.toContain(step.why)
  })

  it("shows all of them on one control, not one per step", () => {
    renderPlan()
    fireEvent.click(screen.getByRole("button", { name: /show how/i }))
    const text = screen.getByTestId("goal-plan-steps").textContent ?? ""
    for (const step of STEPS) expect(text).toContain(step.why)
  })

  it("closes again", () => {
    renderPlan()
    fireEvent.click(screen.getByRole("button", { name: /show how/i }))
    fireEvent.click(screen.getByRole("button", { name: /hide how/i }))
    expect(screen.getByTestId("goal-plan-steps").textContent)
      .not.toContain(STEPS[0].why)
  })
})

// ─── 3. Figures come from the server, never from the card ─────────────────

describe("a figure on this screen came from the evidence", () => {
  it("renders only figures the server put in the step", () => {
    // THE REPLACEMENT FOR "the narrative contains no numbers". That assertion
    // was right while the CARD was the author of these sentences — it could
    // only invent. The server is the author now, its steps quote measured
    // figures, and every one of them is verified against an observation
    // before it is stored. What has to hold here is that the card adds
    // nothing of its own: the rendered text of a step is its `what` and its
    // `why`, exactly as they arrived.
    renderPlan()
    fireEvent.click(screen.getByRole("button", { name: /show how/i }))
    const rendered = [...screen.getByTestId("goal-plan-steps")
      .querySelectorAll("li")]
      .filter((li) => !li.hasAttribute("aria-hidden"))
      .map((li) => (li.textContent ?? "").replace(/^\d+/, ""))
    expect(rendered).toEqual(STEPS.map((s) => `${s.what}${s.why}`))
  })

  it("carries a measured figure through to the reader unchanged", () => {
    renderPlan()
    fireEvent.click(screen.getByRole("button", { name: /show how/i }))
    // 273,378 is `gap_total` on the observation the step cites.
    expect(screen.getByTestId("goal-plan-steps").textContent)
      .toContain("273,378")
  })

  it("invents no total of its own beside the steps", () => {
    // The stat strip reports the coverage summary the server sent. It must
    // not also restate the signal count the steps already carry — the same
    // number twice on one card is a duplication this section removed once
    // before and reintroduced the moment it was given a lede again.
    renderPlan()
    const card = screen.getByTestId("goal-plan").textContent ?? ""
    expect(card.split("412 signal").length - 1).toBeLessThanOrEqual(1)
  })
})

// ─── 4. The verdict and the stat strip ─────────────────────────────────────

describe("the reader learns whether this can be answered, and off how much", () => {
  it("leads with a verdict, then the coverage", () => {
    renderPlan()
    expect(screen.getByTestId("goal-plan-verdict").textContent)
      .toMatch(/I can answer this/i)
    const stats = screen.getByTestId("goal-plan-stats").textContent ?? ""
    expect(stats).toContain("1,676")
    expect(stats).toContain("2024-07")
    expect(stats).toContain("2026-09")
  })

  it("shows no strip at all for a plan that carries no coverage", () => {
    // "Nothing was read" and "we did not look" are different statements, and
    // only one of them is true for a plan stored before the pass existed.
    const legacy = { ...PLAN, coverage: undefined } as unknown as GoalRunPlan
    renderPlan(legacy)
    expect(screen.queryByTestId("goal-plan-stats")).toBeNull()
  })
})

// ─── 5. Nothing on the reading surface is a form field ────────────────────

describe("the plan is read, not filled in", () => {
  it("opens with no input, textarea or checkbox anywhere", () => {
    renderPlan()
    const card = screen.getByTestId("goal-plan")
    expect(card.querySelectorAll("input")).toHaveLength(0)
    expect(card.querySelectorAll("textarea")).toHaveLength(0)
  })

  it("shows the definition as a quotation, not a box", () => {
    renderPlan()
    const box = screen.getByTestId("goal-plan-definition")
    expect(box.querySelector("blockquote")?.textContent)
      .toContain(PLAN.definition_text)
    expect(box.querySelector("textarea")).toBeNull()
  })

  it("still lets the reader reword it, one control away", () => {
    // I9 is unchanged: a definition is adopted or elicited, never inferred,
    // and it stays editable at the moment of approval. What changed is that
    // the box no longer greets the reader.
    renderPlan()
    fireEvent.click(screen.getByRole("button", { name: /reword this/i }))
    expect(screen.getByLabelText("What this goal means")).toBeTruthy()
  })

  it("shows sources as ticks, with changing them one control away", () => {
    renderPlan()
    const sources = screen.getByTestId("goal-plan-sources")
    expect(sources.querySelectorAll("input[type=checkbox]")).toHaveLength(0)
    expect(sources.textContent).toContain("revenue data")
    fireEvent.click(screen.getByRole("button", { name: /change what gets read/i }))
    expect(screen.getByTestId("goal-plan-sources")
      .querySelectorAll("input[type=checkbox]")).toHaveLength(2)
  })
})

// ─── 6. The gate is two steps ──────────────────────────────────────────────

describe("the questions follow the plan they depend on", () => {
  it("asks nothing until the plan is approved", () => {
    renderPlan()
    expect(screen.queryByTestId("goal-plan-unknowns")).toBeNull()
    expect(screen.getByTestId("goal-plan-needs").textContent)
      .toMatch(/I will ask you 2 things/i)
  })

  it("collapses the plan to a record and asks", () => {
    renderPlan()
    toQuestions()
    expect(screen.getByTestId("goal-plan-record").textContent)
      .toMatch(/Approved: 4 steps over 2 sources/i)
    expect(screen.getByTestId("goal-plan-unknowns")).toBeTruthy()
    // The document itself is out of the way — the questions are about the
    // plan, and a reader scrolling past it to reach them has been handed a
    // second document rather than a second step.
    expect(screen.queryByTestId("goal-plan-steps")).toBeNull()
  })

  it("goes back to the plan without losing the approval", () => {
    renderPlan()
    toQuestions()
    fireEvent.click(screen.getByRole("button", { name: /back to the plan/i }))
    expect(screen.getByTestId("goal-plan-steps")).toBeTruthy()
  })

  it("starts nothing on the first press", () => {
    // The first button is a client-side commit: `/approve` is the only
    // endpoint and it starts the run, so the answers have to travel with it.
    const onApprove = renderPlan()
    toQuestions()
    expect(onApprove).not.toHaveBeenCalled()
  })
})

// ─── 7. Every question renders, however it was derived ────────────────────

describe("a derived question is asked, not dropped", () => {
  it("renders a question whose id the card has never seen", () => {
    // THE DEFECT THIS REPLACES: `questionInputs[q.id]` with `return null` on a
    // miss. Every question the reconnaissance pass derived vanished silently,
    // which is the worst of the three possible behaviours — the reader is not
    // asked, and nobody can see that they were not asked.
    renderPlan()
    toQuestions()
    expect(screen.getByTestId("goal-plan-question-value_column_choice"))
      .toBeTruthy()
  })

  it("shows what the run actually saw that made it ask", () => {
    renderPlan()
    toQuestions()
    expect(screen.getByTestId("goal-plan-question-value_column_choice").textContent)
      .toContain("They differ on 2 of 8 rows")
  })

  it("shows what happens if it is skipped", () => {
    renderPlan()
    toQuestions()
    expect(screen.getByTestId("goal-plan-question-value_column_choice").textContent)
      .toContain("total_acv_usd is used, because it is the complete column")
  })

  it("renders a closed set as choices rather than a text box", () => {
    // A text box beside two known options invites a third that nothing
    // downstream can read.
    renderPlan()
    toQuestions()
    const q = screen.getByTestId("goal-plan-question-value_column_choice")
    expect(q.querySelectorAll("input")).toHaveLength(0)
    expect(q.querySelectorAll("button")).toHaveLength(2)
  })

  it("uses a text box only where there is no option set", () => {
    renderPlan()
    toQuestions()
    const q = screen.getByTestId("goal-plan-question-decision_owner")
    expect(q.querySelectorAll("input")).toHaveLength(1)
  })

  it("keeps what changes the analysis apart from what annotates the report", () => {
    // "Who decides this" never touches a number and must not sit beside a
    // question that rescales every size on the page.
    renderPlan()
    toQuestions()
    const box = screen.getByTestId("goal-plan-unknowns").textContent ?? ""
    expect(box).toContain("These change the analysis")
    expect(box).toContain("These only annotate the report")
    expect(box.indexOf("These change the analysis"))
      .toBeLessThan(box.indexOf("These only annotate the report"))
  })

  it("says plainly that a derived answer is recorded and not yet applied", () => {
    // No pass reads these yet: the run carries out the default each question
    // states. Collecting an answer and quietly ignoring it is the dishonesty
    // this gate exists to remove, so the card says so where it asks.
    renderPlan()
    toQuestions()
    expect(screen.getByTestId("goal-plan-derived-note").textContent)
      .toMatch(/recorded with the plan/i)
  })
})

// ─── 8. The answers reach the wire ────────────────────────────────────────

describe("what the reader answers is what gets posted", () => {
  it("carries a chosen option under its question id", () => {
    const onApprove = renderPlan()
    toQuestions()
    fireEvent.click(screen.getByRole("button", { name: "base_acv_usd" }))
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove.mock.calls[0][0].answers)
      .toEqual({ value_column_choice: "base_acv_usd" })
  })

  it("lets a misclick be undone", () => {
    // Otherwise a wrong tap is permanent and the reader has answered
    // something they did not mean.
    const onApprove = renderPlan()
    toQuestions()
    const opt = screen.getByRole("button", { name: "base_acv_usd" })
    fireEvent.click(opt)
    fireEvent.click(opt)
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    expect(onApprove.mock.calls[0][0].answers).toBeUndefined()
  })

  it("keeps a wired question on its own field, not in the generic map", () => {
    // `decision_owner` has a dedicated field on `/approve`; routing it through
    // `answers` as well would record the same answer twice.
    const onApprove = renderPlan()
    toQuestions()
    fireEvent.change(screen.getByLabelText(/who decides/i),
      { target: { value: "VP Product" } })
    fireEvent.click(screen.getByRole("button", { name: /approve and run/i }))
    const d = onApprove.mock.calls[0][0]
    expect(d.decision_owner).toBe("VP Product")
    expect(d.answers).toBeUndefined()
  })

  it("posts nothing extra when the reader answered nothing", () => {
    // The approve body is asserted against exact objects elsewhere; an
    // always-present key here would change every call this card makes.
    const onApprove = renderPlan()
    approve()
    expect(onApprove).toHaveBeenCalledWith({
      excluded_sources: [], hypotheses: [],
    })
  })
})


// ─── 9. The legacy fallback, which is what production renders today ───────
//
// Every plan currently sitting `awaiting_approval` has no `steps`, so the
// narrative path is not a museum piece — it is the live one until those runs
// drain. A browser pass over it found three defects that no assertion here
// could see, because nothing rendered that path.

// A framework is what produces a step with sub-points, and it is the step the
// browser pass caught: "Rank what survives with MoSCoW:" followed by three
// bullets, all four welded into one line.
const LEGACY = {
  ...PLAN,
  steps: undefined,
  coverage: undefined,
  framework: "moscow",
  framework_reason: "nothing connected here carries a number",
} as unknown as GoalRunPlan

describe("the narrative fallback renders as a list, not a run-on", () => {
  it("keeps a step's sub-points as separate list items", () => {
    // Joined with a space they became "…an account asked for Graded by how
    // many independent source documents…" — four bullets welded into one
    // five-line paragraph, with two steps rendered that way and the rest as
    // single lines, so the column of actions had no left edge to run down.
    renderPlan(LEGACY)
    const items = screen.getByTestId("goal-plan-steps")
      .querySelectorAll("ul li")
    expect(items.length).toBeGreaterThan(2)
    for (const li of items) {
      expect((li.textContent ?? "").length).toBeGreaterThan(0)
    }
  })

  it("never welds two sentences together without a boundary", () => {
    renderPlan(LEGACY)
    const text = screen.getByTestId("goal-plan-steps").textContent ?? ""
    // A lowercase word butting straight against a capitalised one is the
    // signature of the join: "asked for Graded", "claim count your team".
    expect(text).not.toMatch(/[a-z]{2} [A-Z][a-z]+ by how many/)
  })
})

describe("show how is offered only when there is a how to show", () => {
  it("is absent when no step carries a why", () => {
    // It rendered on the legacy plan, flipped its own `aria-expanded` and its
    // own label, and changed nothing else — measured live at exactly the same
    // card height before and after the click.
    renderPlan(LEGACY)
    expect(screen.queryByRole("button", { name: /show how/i })).toBeNull()
  })

  it("is present when a step does carry one", () => {
    renderPlan()
    expect(screen.getByRole("button", { name: /show how/i })).toBeTruthy()
  })

  it("still reveals the why it was offered for", () => {
    renderPlan()
    fireEvent.click(screen.getByRole("button", { name: /show how/i }))
    expect(screen.getByTestId("goal-plan-steps").textContent)
      .toContain(STEPS[0].why)
  })
})

describe("the card says each word once", () => {
  it("does not say 'your your'", () => {
    // The template prefixed "Read your " and one source label is literally
    // "your own business context", so the first sentence a reader got was
    // "Read your your own business context."
    const withPossessive = {
      ...LEGACY,
      sources: [{
        source_type: "pm_manual", signal_count: 12,
        label: "your own business context",
        witnesses: "the company's stated constraints",
      }],
    } as unknown as GoalRunPlan
    renderPlan(withPossessive)
    const text = screen.getByTestId("goal-plan").textContent ?? ""
    expect(text).toContain("your own business context")
    expect(text.toLowerCase()).not.toContain("your your")
  })
})
