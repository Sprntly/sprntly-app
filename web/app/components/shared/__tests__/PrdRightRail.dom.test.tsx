// @vitest-environment jsdom
//
// Regression tests for the "Create/Generate PRD always opens the RIGHT RAIL"
// fix. Every PRD creation/open trigger must:
//   1. call `openContentPanel("prd")` (open the rail), and
//   2. surface the PRD in the rail — driven by `content.prd` / the in-progress
//      `content.prdGenerating` flag — NOT only as a bottom chat thread turn.
//
// We mock the two context hooks (so `openContentPanel` / `setContent` are
// spyable), `next/navigation`, and the PRD-generation API so generation
// resolves deterministically. The components under test are the REAL
// `BriefChat`, `ContentPanel`, and the real handler closures inside them — so
// these assertions hold against shipped logic, not a re-implementation.
import * as React from "react"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Sprntly components rely on React 17+ automatic JSX (no `import React`), which
// Next's SWC supplies in prod but vitest's classic esbuild transform does not.
// Expose a global `React` BEFORE the component modules evaluate their top-level
// JSX. `vi.hoisted` runs before the hoisted `import` of the components below.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// ── Spyable navigation + content context ──────────────────────────────────
const openContentPanel = vi.fn()
const closeContentPanel = vi.fn()
const showToast = vi.fn()
const setContent = vi.fn()
const goTo = vi.fn()
const setAIBarValue = vi.fn()

// Mutable content the components read; setContent mirrors merges into it so
// the in-progress flag is observable just like the real ContentProvider.
let content: Record<string, unknown> = {}
let contentPanelTab: string = "prd"

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    aiBarValue: "",
    setAIBarValue,
    openContentPanel,
    closeContentPanel,
    showToast,
    goTo,
    contentPanelTab,
    expandAiPanel: vi.fn(),
    openModal: vi.fn(),
    shareMenuOpen: false,
    setShareMenuOpen: vi.fn(),
  }),
}))

// `setContent` MUST be a stable reference across renders. `ContentPanel`'s
// evidence-loading effect lists `setContent` in its dependency array; if the
// mock handed back a fresh closure on every render, the effect would re-run
// each render, call `setContent`, re-render, and spin forever — allocating
// until the worker OOMs. A single hoisted function keeps the identity stable,
// exactly like the real ContentProvider's memoized setter.
const stableSetContent = (patch: Record<string, unknown>) => {
  setContent(patch)
  content = { ...content, ...patch }
}
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({
    content,
    setContent: stableSetContent,
  }),
}))

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "meridian", setActiveCompany: vi.fn() }),
}))

// BriefChat reads the active workspace via useWorkspace(), which throws outside a
// WorkspaceProvider; mock it to an idle workspace alongside the other contexts.
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: null,
    refresh: async () => {},
  }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
}))

// PRD generation API — resolves to a tiny ready PRD without polling/network.
const FAKE_PRD = {
  prd_id: 42,
  title: "Handoff Threshold PRD",
  metaLine: "From Brief · insight 0",
  sections: [],
  figma_file_key: undefined,
}
const runPrdGeneration = vi.fn(async (_meta: unknown) => ({ ok: true as const, prd: FAKE_PRD }))
vi.mock("../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (meta: unknown) => runPrdGeneration(meta),
}))

// Evidence generation is fired by ContentPanel's EvidenceTab on mount when a
// detail.meta is present; stub it so the tab renders without network/polling.
vi.mock("../../../lib/runEvidenceGeneration", () => ({
  runEvidenceGeneration: vi.fn(async () => ({ ok: true as const, evidence: { title: "ev", metaLine: "", sections: [] } })),
}))

// briefApi.current → one insight so prdFlow proceeds past the guard.
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    briefApi: {
      ...actual.briefApi,
      current: vi.fn(async () => ({ id: 7, insights: [{ title: "Day-30 retention dip" }] })),
    },
    prdApi: {
      ...actual.prdApi,
      latest: vi.fn(async () => { throw new actual.ApiError(404, "none") }),
    },
  }
})

import { BriefChat } from "../BriefChat"
import { PrdPanelContent } from "../PrdPanelContent"
import { ContentPanel } from "../ContentPanel"

const EMPTY_CONTENT = {
  prd: null,
  prdMeta: null,
  prdGenerating: false,
  evidence: null,
  evidenceGenerating: false,
  detail: null,
  briefDetails: {},
  brief: { findings: [] },
  teamMembers: [],
  connectedConnectorIds: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  content = { ...EMPTY_CONTENT }
  contentPanelTab = "prd"
})
afterEach(cleanup)

// ── Helper: a PRD generation surfaced via setContent always carries the rail
//    in-progress flag, never a bottom-only turn. ─────────────────────────────
function setContentCalls() {
  return setContent.mock.calls.map((c) => c[0] as Record<string, unknown>)
}

describe("PRD always opens the right rail — composer command path", () => {
  it("`generate PRD` in the composer opens the rail immediately with an in-progress state, then lands the PRD in the rail (not only a bottom turn)", async () => {
    render(<BriefChat />)
    const composer = screen.getByPlaceholderText(/Ask anything/i)
    fireEvent.change(composer, { target: { value: "generate PRD" } })
    fireEvent.click(screen.getByLabelText("Send"))

    // Rail opens up front (before generation resolves) — the user always sees
    // the PRD on the right, not just a bottom message.
    await waitFor(() => expect(openContentPanel).toHaveBeenCalledWith("prd"))

    // The rail is driven by an in-progress flag at generation start…
    const calls = setContentCalls()
    expect(calls.some((c) => c.prdGenerating === true)).toBe(true)

    // …and the final PRD lands in content (rail), with the flag cleared.
    await waitFor(() => {
      const done = setContentCalls().find((c) => c.prd && (c.prd as { prd_id?: number }).prd_id === 42)
      expect(done).toBeTruthy()
      expect(done?.prdGenerating).toBe(false)
    })
    expect(runPrdGeneration).toHaveBeenCalled()
  })
})

describe("PRD panel renders an in-progress (generating) state in the rail", () => {
  it("PrdPanelContent shows a 'Generating PRD…' spinner when content.prdGenerating is true and no prd yet", () => {
    content = { ...EMPTY_CONTENT, prdGenerating: true }
    render(<PrdPanelContent />)
    expect(screen.getByTestId("prd-generating")).toBeTruthy()
    expect(screen.getByText(/Generating PRD/i)).toBeTruthy()
  })

  it("PrdPanelContent does NOT show the generating spinner once the PRD is present", () => {
    content = { ...EMPTY_CONTENT, prd: FAKE_PRD, prdGenerating: false }
    render(<PrdPanelContent />)
    expect(screen.queryByTestId("prd-generating")).toBeNull()
  })
})

describe("PRD always opens the right rail — Evidence panel 'Generate PRD' CTA", () => {
  it("clicking 'Generate PRD' in the Evidence rail switches to the PRD tab immediately (in-progress in the rail), then lands the PRD", async () => {
    contentPanelTab = "evidence"
    content = {
      ...EMPTY_CONTENT,
      detail: { title: "Day-30 retention", tags: [], evidenceSections: [], meta: { briefId: 7, insightIndex: 0 } } as unknown,
    }
    render(<ContentPanel />)
    const btn = await screen.findByRole("button", { name: /Generate PRD/i })
    fireEvent.click(btn)

    await waitFor(() => expect(openContentPanel).toHaveBeenCalledWith("prd"))
    const calls = setContentCalls()
    expect(calls.some((c) => c.prdGenerating === true)).toBe(true)
    await waitFor(() => {
      const done = setContentCalls().find((c) => c.prd && (c.prd as { prd_id?: number }).prd_id === 42)
      expect(done).toBeTruthy()
      expect(done?.prdGenerating).toBe(false)
    })
  })

  it("re-opening an already-generated PRD opens the rail without re-generating", async () => {
    contentPanelTab = "evidence"
    content = {
      ...EMPTY_CONTENT,
      prd: FAKE_PRD,
      prdMeta: { briefId: 7, insightIndex: 0 },
      detail: { title: "Day-30 retention", tags: [], evidenceSections: [], meta: { briefId: 7, insightIndex: 0 } } as unknown,
    }
    render(<ContentPanel />)
    const btn = await screen.findByRole("button", { name: /Generate PRD/i })
    fireEvent.click(btn)

    await waitFor(() => expect(openContentPanel).toHaveBeenCalledWith("prd"))
    // Same insight already has a PRD → must NOT re-generate, and must NOT flip
    // the in-progress flag.
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(setContentCalls().some((c) => c.prdGenerating === true)).toBe(false)
  })
})
