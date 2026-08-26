import { describe, it, expect } from "vitest"
import { planNarrative } from "../goalPlanNarrative"

const src = (source_type: string, label: string, signal_count: number) =>
  ({ source_type, label, signal_count, witnesses: "what people said" }) as never

const PLAN = {
  sources: [
    src("customer_voice", "customer calls", 412),
    src("project_mgmt", "the tracker", 88),
    src("analytics", "product analytics", 1500),
  ],
  definition_text: "revenue means new ARR closed this quarter",
  will_produce: [
    "A ranked list of what is blocking this",
    "The evidence behind each one",
  ],
  cannot_answer: [
    { question: "how much is it worth?", because: "no revenue source is connected", remedy: "connect billing" },
  ],
}

describe("the plan reads as an approach, not a form", () => {
  it("names the sources a reader would recognise, with the total", () => {
    const steps = planNarrative(PLAN, new Set())
    expect(steps[0]).toContain("customer calls")
    expect(steps[0]).toContain("the tracker")
    expect(steps[0]).toContain("product analytics")
    // 412 + 88 + 1500 — the number the reader is agreeing to.
    expect(steps[0]).toContain("2,000")
  })

  it("rewrites itself when a source is dropped, rather than describing the old run", () => {
    // THE WHOLE POINT OF PUTTING IT ABOVE THE CHECKBOXES. A narrative that
    // kept describing analytics after the reader unticked analytics would be
    // the most confidently wrong sentence on the card.
    const steps = planNarrative(PLAN, new Set(["analytics"]))
    expect(steps[0]).not.toContain("product analytics")
    expect(steps[0]).toContain("customer calls")
    expect(steps[0]).toContain("500")      // 412 + 88
    expect(steps[0]).not.toContain("2,000")
  })

  it("says so plainly when everything is unticked", () => {
    const steps = planNarrative(PLAN, new Set(["customer_voice", "project_mgmt", "analytics"]))
    expect(steps[0]).toMatch(/read nothing/i)
  })

  it("promises a number nowhere, because nothing has been read yet", () => {
    // The requested copy ended "which if addressed can drive $230K in
    // revenue". The plan step takes an inventory and reads no content — it
    // cannot know what the findings are worth, and a figure quoted here would
    // be invention wearing the clothes of a promise. The size of the prize is
    // the report's to state, from evidence, afterwards.
    const all = planNarrative(PLAN, new Set()).join(" ")
    expect(all).not.toMatch(/[$£€]\s?\d/)
    expect(all).not.toMatch(/\bARR\b(?!\s*closed)/)
    expect(all).not.toMatch(/\b\d+\s?%/)
    expect(all).not.toMatch(/worth|drive .*revenue|uplift|forecast/i)
  })

  it("states the rules that make the answer short", () => {
    // A reader who does not know the corroboration rules reads a short report
    // as a thin one. Saying them up front is what buys the short report its
    // credibility.
    const steps = planNarrative(PLAN, new Set()).join(" ")
    expect(steps).toMatch(/one account/i)
    expect(steps).toMatch(/one voice/i)
    expect(steps).toMatch(/not in a position to know/i)
  })

  it("measures against the adopted definition, and stays silent without one", () => {
    expect(planNarrative(PLAN, new Set()).join(" "))
      .toContain("revenue means new ARR closed this quarter")
    // I9: a definition is adopted or elicited, never inferred. No definition,
    // no step claiming one.
    const undefinedDef = planNarrative({ ...PLAN, definition_text: "  " }, new Set())
    expect(undefinedDef.join(" ")).not.toMatch(/your own definition/i)
  })

  it("takes the deliverables from the planner rather than writing its own", () => {
    const steps = planNarrative(PLAN, new Set()).join(" ")
    expect(steps).toContain("ranked list of what is blocking this")
    expect(steps).toContain("the evidence behind each one")
    // Swap what the planner promised and the narrative must follow it.
    const other = planNarrative(
      { ...PLAN, will_produce: ["A one-line answer and nothing else"] }, new Set(),
    ).join(" ")
    expect(other).toContain("one-line answer and nothing else")
    expect(other).not.toContain("ranked list")
  })

  it("counts the declared gaps and puts them last, as part of the approach", () => {
    const steps = planNarrative(PLAN, new Set())
    const last = steps[steps.length - 1]
    expect(last).toMatch(/1 question is/)
    expect(last).toMatch(/cannot settle/i)
    const two = planNarrative(
      { ...PLAN, cannot_answer: [...PLAN.cannot_answer, { question: "q", because: "b", remedy: "r" }] },
      new Set(),
    )
    expect(two[two.length - 1]).toMatch(/2 questions are/)
  })

  it("leaves an acronym's capitals alone when folding a promise into a sentence", () => {
    const steps = planNarrative({ ...PLAN, will_produce: ["ARR at risk, by account"] }, new Set())
    expect(steps.join(" ")).toContain("ARR at risk")
  })
})
