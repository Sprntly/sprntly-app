import { describe, expect, it } from "vitest"

import { reportKindLabel } from "../reportKind"

describe("reportKindLabel", () => {
  it("uses the established name for the known report skills", () => {
    expect(reportKindLabel("voice-of-customer-report")).toBe("Voice of Customer")
    expect(reportKindLabel("public-feedback-report")).toBe("Public Feedback")
    expect(reportKindLabel("competitive-intelligence-review")).toBe(
      "Competitive Intelligence",
    )
  })

  it("humanises an unmapped skill id rather than showing a raw slug", () => {
    expect(reportKindLabel("retention-churn")).toBe("Retention churn")
  })

  it("drops a trailing -report/-review so the surface never says 'report report'", () => {
    expect(reportKindLabel("status-report")).toBe("Status")
    expect(reportKindLabel("red-team-review")).toBe("Red team")
  })

  it("upper-cases acronyms", () => {
    expect(reportKindLabel("okr-nct")).toBe("OKR NCT")
    expect(reportKindLabel("pmf-survey")).toBe("PMF survey")
  })

  it("falls back to 'Report' for a missing or empty skill", () => {
    expect(reportKindLabel(null)).toBe("Report")
    expect(reportKindLabel(undefined)).toBe("Report")
    expect(reportKindLabel("   ")).toBe("Report")
    // An id that is nothing BUT the dropped suffix still needs a label.
    expect(reportKindLabel("-report")).toBe("Report")
  })
})
