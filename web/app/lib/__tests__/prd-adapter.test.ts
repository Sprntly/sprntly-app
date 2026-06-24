import { describe, expect, it } from "vitest"
import { markdownToPrdState } from "../prd-adapter"

describe("markdownToPrdState", () => {
  it("extracts the title from the first H1", () => {
    const out = markdownToPrdState("# The PRD\n\nbody")
    expect(out.title).toBe("The PRD")
  })

  it("falls back to a default title when no H1 is present", () => {
    const out = markdownToPrdState("body only")
    expect(out.title).toBe("PRD")
  })

  it("parses an H2 + paragraph + bullet list as PRD primitives", () => {
    const out = markdownToPrdState(
      ["# T", "", "## Section", "", "Paragraph.", "", "- one", "- two"].join("\n"),
    )
    const types = out.sections.map((s) => s.type)
    expect(types).toEqual(["h2", "p", "ul"])
    const ul = out.sections[2]
    if (ul.type !== "ul") throw new Error("expected ul")
    expect(ul.items).toEqual(["one", "two"])
  })

  it("parses :::context-chip as the shared v2-context-chip variant", () => {
    const md = [
      ":::context-chip",
      "Claims · Author: A. Jain · Status: Draft",
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    expect(c.type).toBe("v2-context-chip")
    if (c.type === "v2-context-chip") {
      expect(c.text).toContain("A. Jain")
    }
  })

  it("parses :::tldr into prd-tldr with problem/fix/impact", () => {
    const md = [
      ":::tldr",
      JSON.stringify({
        problem: "57% abandon at deductible step",
        fix: "Move deductible to step 1",
        impact: "Completion 43% → 58%, +$143M",
      }),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-tldr") throw new Error("expected prd-tldr")
    expect(c.problem).toContain("57%")
    expect(c.fix).toContain("step 1")
    expect(c.impact).toContain("$143M")
  })

  it("parses :::problem with user_story + tone-tagged impact cells", () => {
    const md = [
      ":::problem",
      JSON.stringify({
        user_story: "A claimant tries to file a claim and abandons.",
        impact: [
          { label: "Affected", value: "81k / mo", tone: "negative" },
          { label: "Trajectory", value: "+9% QoQ", tone: "unknown" },
          { label: "Annualized", value: "$143M / yr" },
        ],
      }),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-problem") throw new Error("expected prd-problem")
    expect(c.userStory).toContain("claimant")
    expect(c.impact).toHaveLength(3)
    expect(c.impact[0].tone).toBe("negative")
    // Unknown tones get clamped to "neutral".
    expect(c.impact[1].tone).toBe("neutral")
    // Missing tone is omitted (renderer defaults to neutral).
    expect(c.impact[2].tone).toBeUndefined()
  })

  it("parses :::hypothesis with normalized then_metric → thenMetric camelcase", () => {
    const md = [
      ":::hypothesis",
      JSON.stringify({
        if_we: "Move disclosure",
        then_metric: {
          name: "Funnel completion",
          current: "43%",
          target: "58%",
        },
        because: "Surprise abandonment goes away",
        secondary: "Slight dip in step-1 entry",
      }),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-hypothesis")
      throw new Error("expected prd-hypothesis")
    expect(c.ifWe).toBe("Move disclosure")
    expect(c.thenMetric.target).toBe("58%")
    expect(c.because).toContain("Surprise")
    expect(c.secondary).toContain("dip")
  })

  it("parses :::requirements into rows with normalized categories", () => {
    const md = [
      ":::requirements",
      JSON.stringify([
        { behavior: "Show deductible upfront", category: "functional", detail: "Step 1" },
        { behavior: "feature_flag_enabled", category: "flag", detail: "boolean" },
        // Unknown category passes through unchanged after lowercasing.
        { behavior: "weird", category: "MYSTERY", detail: "hmm" },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-requirements")
      throw new Error("expected prd-requirements")
    expect(c.rows).toHaveLength(3)
    expect(c.rows[0].category).toBe("functional")
    expect(c.rows[1].category).toBe("flag")
    expect(c.rows[2].category).toBe("mystery")
  })

  it("parses :::acceptance-criteria with camelcased keys", () => {
    const md = [
      ":::acceptance-criteria",
      JSON.stringify([
        {
          id: "AC1",
          kind: "happy-path",
          given_when_then: "Given X, when Y, then Z",
          verified_by: "Integration test",
        },
        {
          id: "AC2",
          kind: "perf",
          given_when_then: "P95 < 80ms",
          verified_by: "CI",
        },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-acceptance-criteria")
      throw new Error("expected prd-acceptance-criteria")
    expect(c.rows).toHaveLength(2)
    expect(c.rows[0].givenWhenThen).toContain("Given X")
    expect(c.rows[0].verifiedBy).toBe("Integration test")
    expect(c.rows[1].id).toBe("AC2")
  })

  it("parses :::metrics into primary/secondary/guardrails", () => {
    const md = [
      ":::metrics",
      JSON.stringify({
        primary: { name: "Funnel completion", current: "43%", target: "58%" },
        secondary: [
          { name: "Step 1→2", current: "97%", target: "95%" },
        ],
        guardrails: [
          { name: "Tier mix shift", baseline: "23%", bound: "≤27%" },
        ],
      }),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-metrics")
      throw new Error("expected prd-metrics")
    expect(c.primary.name).toBe("Funnel completion")
    expect(c.primary.target).toBe("58%")
    expect(c.secondary).toHaveLength(1)
    expect(c.guardrails[0].bound).toBe("≤27%")
  })

  it("parses :::risks rows and clamps unknown severity", () => {
    const md = [
      ":::risks",
      JSON.stringify([
        { risk: "Tier mix shift", severity: "medium", mitigation: "Kill at 4pp" },
        { risk: "Plan-purchase drop", severity: "EXTREME", mitigation: "Limit scope" },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-risks") throw new Error("expected prd-risks")
    expect(c.rows[0].severity).toBe("medium")
    // Unknown severities are preserved (lowercased) — renderer falls back
    // to the neutral chip styling.
    expect(c.rows[1].severity).toBe("extreme")
    expect(c.rows[1].mitigation).toContain("scope")
  })

  it("parses :::milestones into phases with items[]", () => {
    const md = [
      ":::milestones",
      JSON.stringify([
        {
          phase: "Pre-launch",
          items: ["Dogfood — 1wk", "Closed beta — 2wk"],
        },
        { phase: "Rollout", items: ["A/B 50/50", "Ramp 1→100"] },
      ]),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-milestones")
      throw new Error("expected prd-milestones")
    expect(c.phases).toHaveLength(2)
    expect(c.phases[0].phase).toBe("Pre-launch")
    expect(c.phases[0].items).toHaveLength(2)
    expect(c.phases[1].items[1]).toBe("Ramp 1→100")
  })

  it("parses :::dod into a flat checklist items[] array", () => {
    const md = [
      ":::dod",
      JSON.stringify([
        "All AC pass in CI",
        "Telemetry events emit",
        "P95 latency verified",
      ]),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-dod") throw new Error("expected prd-dod")
    expect(c.items).toHaveLength(3)
    expect(c.items[0]).toContain("AC pass")
  })

  it("falls back to a paragraph for a malformed JSON-bodied block", () => {
    const md = [":::tldr", "{this is not json", ":::"].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    expect(c.type).toBe("p")
    if (c.type === "p") {
      expect(c.text).toContain("[tldr block")
      expect(c.text).toContain("could not parse")
    }
  })

  it("parses a ```chart``` fenced block alongside v2 blocks (Context can emit one)", () => {
    const md = [
      "# T",
      "",
      "## 1. Context",
      "",
      "```chart",
      JSON.stringify({
        kind: "bar",
        title: "Step abandonment",
        data: [
          { label: "Step 1", value: 3 },
          { label: "Step 4", value: 57 },
        ],
      }),
      "```",
      "",
      ":::tldr",
      JSON.stringify({ problem: "p", fix: "f", impact: "i" }),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const types = out.sections.map((s) => s.type)
    expect(types).toEqual(["h2", "chart", "prd-tldr"])
  })

  it("salvages JSON when the requirements body has surrounding noise", () => {
    const md = [
      ":::requirements",
      "noise before",
      JSON.stringify([
        { behavior: "X", category: "functional", detail: "Y" },
      ]),
      "noise after",
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    if (c.type !== "prd-requirements")
      throw new Error("expected prd-requirements")
    expect(c.rows[0].behavior).toBe("X")
  })

  it("strips horizontal rules and blank lines between sections", () => {
    const md = [
      "# T",
      "",
      "──────────────",
      "",
      "## Section",
      "",
      "body",
    ].join("\n")
    const out = markdownToPrdState(md)
    const types = out.sections.map((s) => s.type)
    expect(types).toEqual(["h2", "p"])
  })

  it("treats unknown :::name blocks as paragraph fallback", () => {
    const md = [":::mystery", "some body", ":::"].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    expect(c.type).toBe("p")
    if (c.type === "p") {
      expect(c.text).toContain("[mystery block")
    }
  })

  it("parses :::qa-scenarios into a qa-scenarios section with rows + openQuestions", () => {
    const md = [
      "# QA Test Scenarios — Checkout flow",
      "",
      "Verify the deductible-step move end to end.",
      "",
      ":::qa-scenarios",
      JSON.stringify({
        scenarios: [
          {
            id: "QA-001",
            group: "happy",
            title: "Completes with default deductible",
            given: "a returning claimant on step 1",
            when: "they accept the default deductible",
            then: "the claim is submitted",
            traces: "REQ-1 deductible default",
            risk: "low",
          },
          {
            id: "QA-002",
            group: "failure",
            title: "Network drop mid-submit",
            given: "a claimant on the final step",
            when: "the network drops during submit",
            then: "the draft is preserved and an error shows",
            traces: "REQ-4 resilience",
            risk: "high",
          },
        ],
        open_questions: ["Should partial drafts expire?"],
      }),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    expect(out.title).toBe("QA Test Scenarios — Checkout flow")
    // The strategy line renders as a normal paragraph; the block as qa-scenarios.
    const qa = out.sections.find((s) => s.type === "qa-scenarios")
    if (!qa || qa.type !== "qa-scenarios") throw new Error("expected qa-scenarios")
    expect(qa.rows).toHaveLength(2)
    expect(qa.rows[0].id).toBe("QA-001")
    expect(qa.rows[0].group).toBe("happy")
    expect(qa.rows[0].risk).toBe("low")
    expect(qa.rows[0].traces).toContain("REQ-1")
    expect(qa.rows[1].group).toBe("failure")
    expect(qa.rows[1].risk).toBe("high")
    expect(qa.openQuestions).toEqual(["Should partial drafts expire?"])
  })

  it("skips qa scenarios with no given/when/then and clamps unknown group/risk", () => {
    const md = [
      ":::qa-scenarios",
      JSON.stringify({
        scenarios: [
          { id: "QA-1", group: "weird", title: "ok", given: "g", when: "w", then: "t", traces: "", risk: "critical" },
          { id: "QA-2", group: "edge", title: "empty row" }, // no g/w/t → skipped
        ],
      }),
      ":::",
    ].join("\n")
    const out = markdownToPrdState(md)
    const qa = out.sections.find((s) => s.type === "qa-scenarios")
    if (!qa || qa.type !== "qa-scenarios") throw new Error("expected qa-scenarios")
    expect(qa.rows).toHaveLength(1)
    // Unknown group/risk clamp to "".
    expect(qa.rows[0].group).toBe("")
    expect(qa.rows[0].risk).toBe("")
    expect(qa.openQuestions).toEqual([])
  })

  it("falls back to a paragraph when :::qa-scenarios body is malformed JSON", () => {
    const md = [":::qa-scenarios", "{ not json at all", ":::"].join("\n")
    const out = markdownToPrdState(md)
    const c = out.sections[0]
    expect(c.type).toBe("p")
    if (c.type === "p") {
      expect(c.text).toContain("[qa-scenarios block")
    }
  })
})
