import { describe, it, expect } from "vitest"
import { planNarrative } from "../goalPlanNarrative"

/** Every step as one string — text plus any nested items. The steps carry
 *  structure now, and most of these assertions are about WORDS rather than
 *  about where the words sit. */
const flat = (steps: ReturnType<typeof planNarrative>): string[] =>
  steps.map((s) => [s.text, ...(s.items ?? [])].join(" "))

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
    const steps = flat(planNarrative(PLAN, new Set()))
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
    const steps = flat(planNarrative(PLAN, new Set(["analytics"])))
    expect(steps[0]).not.toContain("product analytics")
    expect(steps[0]).toContain("customer calls")
    expect(steps[0]).toContain("500")      // 412 + 88
    expect(steps[0]).not.toContain("2,000")
  })

  it("says so plainly when everything is unticked", () => {
    const steps = flat(planNarrative(PLAN, new Set(["customer_voice", "project_mgmt", "analytics"])))
    expect(steps[0]).toMatch(/read nothing/i)
  })

  it("promises a number nowhere, because nothing has been read yet", () => {
    // The requested copy ended "which if addressed can drive $230K in
    // revenue". The plan step takes an inventory and reads no content — it
    // cannot know what the findings are worth, and a figure quoted here would
    // be invention wearing the clothes of a promise. The size of the prize is
    // the report's to state, from evidence, afterwards.
    const all = flat(planNarrative(PLAN, new Set())).join(" ")
    expect(all).not.toMatch(/[$£€]\s?\d/)
    expect(all).not.toMatch(/\bARR\b(?!\s*closed)/)
    expect(all).not.toMatch(/\b\d+\s?%/)
    expect(all).not.toMatch(/worth|drive .*revenue|uplift|forecast/i)
  })

  it("states the rules that make the answer short", () => {
    // A reader who does not know the corroboration rules reads a short report
    // as a thin one. Saying them up front is what buys the short report its
    // credibility.
    const steps = flat(planNarrative(PLAN, new Set())).join(" ")
    expect(steps).toMatch(/one account/i)
    expect(steps).toMatch(/one voice/i)
    expect(steps).toMatch(/not in a position to know/i)
  })

  it("measures against the adopted definition, and stays silent without one", () => {
    expect(flat(planNarrative({ ...PLAN, definition_adopted: true }, new Set())).join(" "))
      .toContain("revenue means new ARR closed this quarter")
    // I9: a definition is adopted or elicited, never inferred. No definition,
    // no step claiming one.
    const undefinedDef = flat(planNarrative({ ...PLAN, definition_text: "  " }, new Set()))
    expect(undefinedDef.join(" ")).not.toMatch(/your own definition/i)
  })

  it("takes the deliverables from the planner rather than writing its own", () => {
    const steps = flat(planNarrative(PLAN, new Set())).join(" ")
    expect(steps).toContain("ranked list of what is blocking this")
    // VERBATIM, capital and all: several promises render as a LIST, and a list
    // item is not a clause inside someone else's sentence.
    expect(steps).toContain("The evidence behind each one")
    // Swap what the planner promised and the narrative must follow it.
    const other = flat(planNarrative(
      { ...PLAN, will_produce: ["A one-line answer and nothing else"] }, new Set(),
    )).join(" ")
    expect(other).toContain("one-line answer and nothing else")
    expect(other).not.toContain("ranked list")
  })

  it("counts the declared gaps and puts them last, as part of the approach", () => {
    const steps = flat(planNarrative(PLAN, new Set()))
    const last = steps[steps.length - 1]
    expect(last).toMatch(/1 question is/)
    expect(last).toMatch(/cannot settle/i)
    const two = flat(planNarrative(
      { ...PLAN, cannot_answer: [...PLAN.cannot_answer, { question: "q", because: "b", remedy: "r" }] },
      new Set(),
    ))
    expect(two[two.length - 1]).toMatch(/2 questions are/)
  })

  it("leaves an acronym's capitals alone when folding a promise into a sentence", () => {
    const steps = flat(planNarrative({ ...PLAN, will_produce: ["ARR at risk, by account"] }, new Set()))
    expect(steps.join(" ")).toContain("ARR at risk")
  })
  it("points at an unsettled definition instead of printing it twice", () => {
    // While the definition is a PROPOSAL it sits in an editable field a few
    // lines below the narrative. Quoting it here put the same sentence on the
    // card twice — the repetition the feedback asked us to cut, reintroduced
    // by the fix for the rest of it.
    const steps = flat(planNarrative(PLAN, new Set())).join(" ")
    expect(steps).not.toContain("revenue means new ARR closed this quarter")
    expect(steps).toMatch(/confirm below/i)
  })

  it("keeps several promises as a list rather than one run-on sentence", () => {
    // MEASURED ON A LIVE RUN. `will_produce` is short noun phrases in every
    // fixture here and full sentences in production — five of them, joined
    // with commas and an "and", made ONE 487-character step sitting between
    // four others of 87 to 173. An unreadable paragraph in the middle of the
    // thing whose whole purpose is being readable.
    const real = {
      ...PLAN,
      will_produce: [
        "Themes ranked by how much of your book they touch, each with the source documents it rests on",
        "A considered-and-dropped list, with the reason each candidate died",
        "Every degradation disclosed beside the findings it affects",
        "Sizes stated in reach — how many accounts a theme touches, not how many points it will move the metric",
        "Your revenue data is connected and will be read as evidence, but it cannot yet be turned into a point estimate — that is the next thing being built",
      ],
    }
    const steps = planNarrative(real, new Set())
    const give = steps.find((s) => s.text.startsWith("Give you"))!
    expect(give.items).toHaveLength(5)
    // The step's own sentence stays short; the promises hang under it.
    expect(give.text.length).toBeLessThan(40)
    for (const s of steps) expect(s.text.length).toBeLessThan(250)
    // And each promise survives verbatim — nothing is summarised away.
    expect(give.items).toContain(real.will_produce[4])
  })

  it("keeps a lone promise inline, where a list of one would be noise", () => {
    const steps = planNarrative({ ...PLAN, will_produce: ["A ranked list"] }, new Set())
    const give = steps.find((s) => s.text.startsWith("Give you"))!
    expect(give.items).toBeUndefined()
    expect(give.text).toBe("Give you a ranked list.")
  })
  it("names the ranking framework in the plan, before the run", () => {
    // Apurva: "in the initial plan that we are going to output, we should
    // highlight that we are using the RICE framework." A ranking method
    // discovered in the output is a convention; one stated in the plan is a
    // choice the reader can still say no to.
    const steps = planNarrative({ ...PLAN, framework: "RICE" } as never, new Set())
    const rank = steps.find((s) => s.text.includes("RICE"))!
    expect(rank).toBeTruthy()
    expect(rank.items).toBeTruthy()
    const terms = (rank.items ?? []).join(" ")
    expect(terms).toMatch(/Reach/)
    expect(terms).toMatch(/Impact/)
    expect(terms).toMatch(/Confidence/)
    // AND THAT EFFORT IS NOT IN THE DATA. RICE's letters carry an assumption
    // this corpus cannot satisfy, and saying so in the plan is cheaper than
    // explaining it under a table afterwards.
    expect(terms).toMatch(/not in your connected data/i)
  })

  it("says nothing about ranking when no framework was set", () => {
    const steps = planNarrative({ ...PLAN, framework: "" } as never, new Set())
    expect(steps.find((s) => /RICE|Rank what survives/.test(s.text))).toBeUndefined()
  })

  it("names MoSCoW's own terms, not RICE's, when that is the chosen framework", () => {
    // AC-2: the chosen framework is named, and its terms are ITS terms — a
    // plan that picked MoSCoW because nothing carries a number must not then
    // describe Reach/Impact/Effort, which is exactly the arithmetic MoSCoW
    // was chosen to avoid promising.
    // `framework` here is lowercase — the real stored/comparison value a run
    // actually sends — so this also locks in the display casing: the step's
    // own sentence must say "MoSCoW", never the raw stored value.
    const steps = planNarrative({ ...PLAN, framework: "moscow" } as never, new Set())
    const rank = steps.find((s) => s.text.includes("MoSCoW"))!
    expect(rank).toBeTruthy()
    expect(rank.text).not.toContain("moscow")
    const terms = (rank.items ?? []).join(" ")
    expect(terms).toMatch(/MUST/)
    expect(terms).toMatch(/SHOULD|COULD/)
    expect(terms).not.toMatch(/Reach —|Impact —|Effort —/)
  })

  it("says RICE in the step's sentence even when the stored value is lowercase", () => {
    // Mirrors the MoSCoW case above, for the other direction: a real run
    // stores "rice", never pre-cased text.
    const steps = planNarrative({ ...PLAN, framework: "rice" } as never, new Set())
    const rank = steps.find((s) => s.text.includes("RICE"))!
    expect(rank).toBeTruthy()
    expect(rank.text).not.toContain("rice:")
  })

  it("states why this framework was chosen, alongside its name", () => {
    // AC-2: "the chosen framework and the reason it was chosen appear in the
    // plan and in the final report."
    const steps = planNarrative(
      { ...PLAN, framework: "RICE", framework_reason: "a numeric source is connected" } as never,
      new Set(),
    )
    const rank = steps.find((s) => s.text.includes("RICE"))!
    expect((rank.items ?? []).join(" ")).toContain("a numeric source is connected")
  })
})
