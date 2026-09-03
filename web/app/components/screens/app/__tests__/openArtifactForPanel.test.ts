// The referent for "the report" / "that document" on a classify call.
//
// The bug this pins: reading `content.reportFocusId` alone reported "nothing
// open" while a report sat on screen, because a thread with exactly ONE report
// opens straight into it and never sets that pointer (ReportsTab's `onlyReport`
// rule). The planner then chose `edit_artifact` at 0.95 confidence and the
// endpoint downgraded it to `answer` for want of a target — so "remove the
// product-feedback description" came back as prose with the report unchanged.
import { describe, expect, it } from "vitest"
import { openArtifactForPanel } from "../useConversation"
import type { AppContentState } from "../../../../types/content"

const CONV = 998

function content(patch: Partial<AppContentState>): AppContentState {
  return {
    documentId: null,
    reportFocusId: null,
    threadReports: [],
    threadReportsConversationId: null,
    ...patch,
  } as unknown as AppContentState
}

const row = (id: number) => ({ id }) as AppContentState["threadReports"][number]

describe("openArtifactForPanel", () => {
  it("names the thread's ONLY report, which the panel opens without a pointer", () => {
    const c = content({ threadReports: [row(12)], threadReportsConversationId: CONV })
    expect(openArtifactForPanel(c, CONV)).toEqual({ kind: "report", id: 12 })
  })

  it("names the picked report when the reader chose one from a list", () => {
    const c = content({
      reportFocusId: 40,
      threadReports: [row(12), row(40)],
      threadReportsConversationId: CONV,
    })
    expect(openArtifactForPanel(c, CONV)).toEqual({ kind: "report", id: 40 })
  })

  it("names nothing on a LIST of several with none picked", () => {
    // The reader has not chosen one, so neither does this — the endpoint's gate
    // turns that into an answer, which can ask which report they mean.
    const c = content({
      threadReports: [row(12), row(40)],
      threadReportsConversationId: CONV,
    })
    expect(openArtifactForPanel(c, CONV)).toBeNull()
  })

  it("ignores rows fetched for a DIFFERENT thread", () => {
    // The list is fetched globally and lags a thread switch by a commit; naming
    // another thread's report here would point an edit at it.
    const c = content({ threadReports: [row(12)], threadReportsConversationId: 7 })
    expect(openArtifactForPanel(c, CONV)).toBeNull()
  })

  it("ignores rows on a tab with no conversation yet", () => {
    const c = content({ threadReports: [row(12)], threadReportsConversationId: null })
    expect(openArtifactForPanel(c, null)).toBeNull()
  })

  it("prefers the open document", () => {
    const c = content({
      documentId: 5,
      threadReports: [row(12)],
      threadReportsConversationId: CONV,
    })
    expect(openArtifactForPanel(c, CONV)).toEqual({ kind: "document", id: 5 })
  })

  it("names nothing on a thread with neither", () => {
    expect(openArtifactForPanel(content({}), CONV)).toBeNull()
  })
})

// ── the evidence page ────────────────────────────────────────────────────────
// Reported: "improve the evidence with an analytical chart of the evidence"
// drew the chart into the chat. Evidence was never a referent here, so
// `edit_artifact` had no target and the endpoint downgraded it to an answer.

describe("openArtifactForPanel — evidence", () => {
  it("names the evidence page the tab has open", () => {
    const c = content({
      evidenceId: 42,
      evidence: { html: "<h1>Export failures</h1>" },
    } as Partial<AppContentState>)
    expect(openArtifactForPanel(c, CONV)).toEqual({ kind: "evidence", id: 42 })
  })

  it("needs the document AND the id, never one alone", () => {
    // An id can outlive the tab it was read on; the document alone does not say
    // which row it came from. Either half by itself would risk pointing an edit
    // at a page the reader is not looking at.
    expect(
      openArtifactForPanel(content({ evidenceId: 42 } as Partial<AppContentState>), CONV),
    ).toBeNull()
    expect(
      openArtifactForPanel(
        content({ evidence: { html: "<h1>x</h1>" } } as Partial<AppContentState>),
        CONV,
      ),
    ).toBeNull()
  })

  it("yields to a report the reader has open", () => {
    // The panel shows one thing at a time, and the report is the nearer
    // referent when a thread has both.
    const c = content({
      evidenceId: 42,
      evidence: { html: "<h1>x</h1>" },
      threadReports: [row(12)],
      threadReportsConversationId: CONV,
    } as Partial<AppContentState>)
    expect(openArtifactForPanel(c, CONV)).toEqual({ kind: "report", id: 12 })
  })

  it("yields to an open team document", () => {
    const c = content({
      documentId: 5,
      evidenceId: 42,
      evidence: { html: "<h1>x</h1>" },
    } as Partial<AppContentState>)
    expect(openArtifactForPanel(c, CONV)).toEqual({ kind: "document", id: 5 })
  })
})
