// @vitest-environment jsdom
//
// The right-panel Evidence tab must only appear for PRDs that actually have
// research Evidence. Brief-insight PRDs (`source: 'brief'`, or legacy rows with
// no source) do; ideation and uploaded PRDs do not — an uploaded PRD may have no
// evidence at all — so the tab is hidden for them. These tests lock both the
// pure predicate and the rendered tab bar (plus the redirect off a now-hidden
// Evidence tab).
import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// ContentPanel has module-level JSX (the TABS array), so global React must exist
// before the import below evaluates. vi.hoisted runs before hoisted imports.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))

// The real PrdPanelContent fetches the latest PRD on mount — stub it so the
// tab-bar test stays hermetic. Only the header tab buttons are under test.
vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

const navMock = vi.hoisted(() => ({ openContentPanel: vi.fn() }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    contentPanelTab: navMock.tab,
    openContentPanel: navMock.openContentPanel,
    closeContentPanel: vi.fn(),
    showToast: vi.fn(),
    expandAiPanel: vi.fn(),
    setAIBarValue: vi.fn(),
  }),
}))

const contentMock = vi.hoisted(() => ({ value: {} as Record<string, unknown> }))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: vi.fn() }),
}))

import { ContentPanel, isEvidenceTabHidden } from "../ContentPanel"
import type { PrdState } from "../../../types/content"

const prd = (source?: PrdState["source"]): PrdState => ({
  prd_id: 1,
  title: "T",
  metaLine: "",
  sections: [],
  source,
})

// Minimal content stub: the panel only reads prd/evidence/evidenceGenerating/
// prd.title for the tab bar + header.
function renderPanel(opts: {
  tab: "prd" | "evidence" | "tickets"
  prd?: PrdState | null
  evidence?: unknown
  evidenceGenerating?: boolean
}) {
  ;(navMock as Record<string, unknown>).tab = opts.tab
  contentMock.value = {
    prd: opts.prd ?? null,
    evidence: opts.evidence ?? null,
    evidenceGenerating: opts.evidenceGenerating ?? false,
  }
  return render(React.createElement(ContentPanel))
}

const evidenceTab = () =>
  screen.queryByRole("button", { name: /Evidence/i })

afterEach(() => {
  cleanup()
  navMock.openContentPanel.mockClear()
})

describe("isEvidenceTabHidden — pure predicate", () => {
  const base = { evidence: null, evidenceGenerating: false }

  it("shows the tab when no PRD is loaded", () => {
    expect(isEvidenceTabHidden({ ...base, prd: null })).toBe(false)
  })

  it("shows the tab for a brief PRD", () => {
    expect(isEvidenceTabHidden({ ...base, prd: prd("brief") })).toBe(false)
  })

  it("shows the tab for a legacy PRD with no source", () => {
    expect(isEvidenceTabHidden({ ...base, prd: prd(undefined) })).toBe(false)
  })

  it("hides the tab for an ideation PRD (and legacy 'backlog' rows)", () => {
    expect(isEvidenceTabHidden({ ...base, prd: prd("ideation") })).toBe(true)
    expect(isEvidenceTabHidden({ ...base, prd: prd("backlog") })).toBe(true)
  })

  it("hides the tab for an uploaded PRD", () => {
    expect(isEvidenceTabHidden({ ...base, prd: prd("upload") })).toBe(true)
  })

  it("keeps the tab for an uploaded PRD once evidence is loaded", () => {
    expect(
      isEvidenceTabHidden({ ...base, prd: prd("upload"), evidence: { title: "E", metaLine: "", sections: [] } }),
    ).toBe(false)
  })

  it("keeps the tab for an uploaded PRD while evidence is generating", () => {
    // generating ⇒ evidence is on its way ⇒ tab stays visible (hidden = false)
    expect(
      isEvidenceTabHidden({ prd: prd("upload"), evidence: null, evidenceGenerating: true }),
    ).toBe(false)
  })
})

describe("ContentPanel — Evidence tab visibility", () => {
  it("renders the Evidence tab for a brief PRD", () => {
    renderPanel({ tab: "prd", prd: prd("brief") })
    expect(evidenceTab()).not.toBeNull()
  })

  it("hides the Evidence tab for an uploaded PRD", () => {
    renderPanel({ tab: "prd", prd: prd("upload") })
    expect(evidenceTab()).toBeNull()
  })

  it("hides the Evidence tab for an ideation PRD", () => {
    renderPanel({ tab: "prd", prd: prd("ideation") })
    expect(evidenceTab()).toBeNull()
  })

  it("redirects to the PRD tab when parked on Evidence for an uploaded PRD", () => {
    renderPanel({ tab: "evidence", prd: prd("upload") })
    // Evidence tab gone from the bar…
    expect(evidenceTab()).toBeNull()
    // …and navigation is corrected to a real tab.
    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
    // …with the PRD body rendered as the fallback (never a stranded blank body).
    expect(screen.getByTestId("prd-body")).toBeTruthy()
  })
})
