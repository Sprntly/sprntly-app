// @vitest-environment jsdom
//
// Clicking the panel's PRD tab IS the request for a PRD.
//
// Landing on "No PRD draft loaded" made the user hunt for a button to do the
// obvious next thing — most visibly on an evidence tab opened from a Top
// Insights card, which starts with no PRD at all. So the click switches tabs AND
// resolves the insight's PRD. `POST /v1/prd/generate` is find-or-create, so an
// insight that already has one gets it back (a load, not a second document);
// only a genuinely PRD-less insight is written.
//
// These tests lock: resolve-on-click when nothing is loaded, no resolve when a
// PRD is already there or already generating, no resolve without an insight to
// resolve against, and no resolve from the Evidence / Tickets tabs.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
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

// The PRD body fetches on mount — stub it. Only the tab bar is under test.
vi.mock("../PrdPanelContent", () => ({
  PrdPanelContent: () => React.createElement("div", { "data-testid": "prd-body" }),
}))

// The Evidence tab would kick its own load off `detail.meta`; stub the runner so
// these tests stay hermetic and only the PRD path talks to a mock.
vi.mock("../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
  loadEvidenceByInsight: vi.fn().mockResolvedValue(null),
}))

vi.mock("../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))

const navMock = vi.hoisted(() => ({
  tab: "evidence" as string,
  openContentPanel: vi.fn(),
}))
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

const contentMock = vi.hoisted(() => ({
  value: {} as Record<string, unknown>,
  setContent: vi.fn(),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock.value, setContent: contentMock.setContent }),
}))

import { ContentPanel } from "../ContentPanel"
import { runPrdGeneration } from "../../../lib/runPrdGeneration"
import type { PrdState } from "../../../types/content"

const META = { briefId: 7, insightIndex: 2 }

const readyPrd: PrdState = {
  prd_id: 1,
  title: "Measurement stack",
  metaLine: "",
  sections: [],
  source: "brief",
}

function renderPanel(opts: {
  tab?: "prd" | "evidence" | "tickets"
  detail?: unknown
  prd?: PrdState | null
  prdMeta?: typeof META | null
  prdGenerating?: boolean
}) {
  navMock.tab = opts.tab ?? "evidence"
  contentMock.value = {
    detail: opts.detail ?? null,
    prd: opts.prd ?? null,
    prdMeta: opts.prdMeta ?? null,
    prdGenerating: opts.prdGenerating ?? false,
    evidence: null,
    evidenceGenerating: false,
  }
  return render(React.createElement(ContentPanel))
}

// Exact names: the tab buttons read "Evidence" / "PRD" / "Tickets", which a loose
// /PRD/i would confuse with the footer's "Generate PRD" / "View PRD".
const tabButton = (label: "Evidence" | "PRD" | "Tickets") =>
  screen.getByRole("button", { name: label })
const prdTab = () => tabButton("PRD")

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ContentPanel — the PRD tab resolves the insight's PRD on click", () => {
  it("test_prd_tab_click_resolves: with no PRD loaded, clicking PRD switches tabs AND starts the resolve for the open finding", () => {
    renderPanel({ tab: "evidence", detail: { meta: META } })

    fireEvent.click(prdTab())

    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
    expect(runPrdGeneration).toHaveBeenCalledTimes(1)
    expect(vi.mocked(runPrdGeneration).mock.calls[0]![0]).toEqual(META)
  })

  it("falls back to the panel's PRD pointer when no finding is open (an evidence tab restored after reload)", () => {
    renderPanel({ tab: "evidence", detail: null, prdMeta: META })

    fireEvent.click(prdTab())

    expect(runPrdGeneration).toHaveBeenCalledTimes(1)
    expect(vi.mocked(runPrdGeneration).mock.calls[0]![0]).toEqual(META)
  })

  it("test_existing_prd_is_shown_not_regenerated: a PRD already in the panel is just displayed", () => {
    renderPanel({ tab: "evidence", detail: { meta: META }, prd: readyPrd })

    fireEvent.click(prdTab())

    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("never starts a second run while one is already generating", () => {
    renderPanel({ tab: "evidence", detail: { meta: META }, prdGenerating: true })

    fireEvent.click(prdTab())

    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("does nothing but switch tabs when there is no insight to resolve against", () => {
    renderPanel({ tab: "evidence", detail: null, prdMeta: null })

    fireEvent.click(prdTab())

    expect(navMock.openContentPanel).toHaveBeenCalledWith("prd")
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })

  it("test_only_the_prd_tab_generates: the Evidence and Tickets tabs are plain switches", () => {
    renderPanel({ tab: "prd", detail: { meta: META } })

    fireEvent.click(tabButton("Evidence"))
    fireEvent.click(tabButton("Tickets"))

    expect(navMock.openContentPanel).toHaveBeenCalledWith("evidence")
    expect(navMock.openContentPanel).toHaveBeenCalledWith("tickets")
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })
})
