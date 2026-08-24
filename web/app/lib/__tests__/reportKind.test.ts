import { describe, expect, it } from "vitest"

import { reportKindLabel, reportTitleFromDoc } from "../reportKind"

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

// This function's output is a JOIN KEY: a report card in a chat thread resolves
// which of the thread's reports it is by matching this against the stored title,
// which the backend derives with report_capture.report_title. The two must agree
// exactly — see the ordering test below for the bug that proves why.
describe("reportTitleFromDoc", () => {
  it("prefers <title> over <h1>, exactly as the backend does", () => {
    // The regression this locks: reading the <h1> first gave "Voice of Customer
    // Report" while the stored row said "…— 30 July 2026 · 1 day", so a card
    // never matched its own report and every click fell through to the list.
    const html =
      "<!DOCTYPE html><html><head><title>Voice of Customer Report — 30 July 2026 · 1 day</title></head>" +
      "<body><h1>Voice of Customer Report</h1></body></html>"
    expect(reportTitleFromDoc(html, "voice-of-customer-report")).toBe(
      "Voice of Customer Report — 30 July 2026 · 1 day",
    )
  })

  it("falls back to the first <h1> when there is no <title>", () => {
    const html = "<!DOCTYPE html><html><body><h1>Competitive teardown</h1><h1>second</h1></body></html>"
    expect(reportTitleFromDoc(html, "competitive-intelligence-review")).toBe("Competitive teardown")
  })

  it("strips markup and collapses whitespace inside the title", () => {
    const html = "<html><head><title>\n  Voice of <em>Customer</em>\n  Report\n</title></head></html>"
    expect(reportTitleFromDoc(html, null)).toBe("Voice of Customer Report")
  })

  it("falls back to the humanised kind when the document names itself nothing", () => {
    expect(reportTitleFromDoc("<html><body>no title</body></html>", "voice-of-customer-report"))
      .toBe("Voice of Customer")
    expect(reportTitleFromDoc("<html><head><title>  </title></head></html>", null)).toBe("Report")
    expect(reportTitleFromDoc(null, "status-report")).toBe("Status")
  })

  it("caps at the same length the backend stores", () => {
    const long = "x".repeat(300)
    expect(reportTitleFromDoc(`<html><head><title>${long}</title></head></html>`, null))
      .toHaveLength(200)
  })
})

describe("reportTitleFromDoc — markdown reports", () => {
  // Every pipeline answers in markdown since the pinned HTML templates were
  // removed, so this rung is the one real reports actually take. It has to agree
  // with `report_capture.report_title`'s markdown rung, because the chat card
  // resolves WHICH report a turn is by matching this against the stored title.
  it("reads the first markdown heading", () => {
    expect(
      reportTitleFromDoc(
        "# Voice-of-Customer Report — Sprntly\n\n## Themes\n\n- friction\n",
        "voice-of-customer-report",
      ),
    ).toBe("Voice-of-Customer Report — Sprntly")
  })

  it("takes the FIRST heading, at any level, and collapses its whitespace", () => {
    expect(reportTitleFromDoc("###   Q3   market   scan  \n\n# Later\n", null))
      .toBe("Q3 market scan")
  })

  it("ignores a hash that is not a heading", () => {
    expect(reportTitleFromDoc("Ticket #14 is blocked.\n", "public-feedback-report"))
      .toBe("Public Feedback")
  })

  it("still prefers an HTML title over a heading in the same document", () => {
    expect(
      reportTitleFromDoc("<title>Stored name</title>\n# Markdown name\n", null),
    ).toBe("Stored name")
  })
})
