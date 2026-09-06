// @vitest-environment jsdom
//
// ProjectDetailScreen — clicking a PRD artifact row must open the shared
// content panel SYNCHRONOUSLY, before the `GET /v1/prd/{id}` fetch resolves,
// not only after it. The panel opening only inside the fetch's `.then` left a
// dead gap: the artifacts drawer closes on click, the chat sits full-width
// with nothing on the right for the whole round-trip, and only THEN does the
// PRD panel appear. This mirrors main chat's own PRD-by-id load (ChatScreen's
// `savedPrdId` branch): flip the panel open + its loading state first, fill
// in the real document when the fetch settles.
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const getMock = vi.fn()
const artifactsMock = vi.fn().mockResolvedValue([])
const memorySummaryMock = vi.fn().mockResolvedValue(null)
const memoryInsightMock = vi.fn().mockResolvedValue(null)
const prdGetMock = vi.fn()
const openContentPanelMock = vi.fn()
const showToastMock = vi.fn()

vi.mock("../../../../../lib/api", () => {
  class ApiError extends Error {
    status: number
    body: unknown
    constructor(status: number, body: unknown, message?: string) {
      super(message ?? String(status))
      this.status = status
      this.body = body
    }
  }
  return {
    ApiError,
    projectsApi: {
      get: (...a: unknown[]) => getMock(...a),
      artifacts: (...a: unknown[]) => artifactsMock(...a),
      memorySummary: (...a: unknown[]) => memorySummaryMock(...a),
      memoryInsight: (...a: unknown[]) => memoryInsightMock(...a),
      ledgerCounts: vi.fn().mockResolvedValue({ assigned_to_me_open: 0, waiting_on_open: 0 }),
      ledger: vi.fn().mockResolvedValue([]),
    },
    prdApi: {
      get: (...a: unknown[]) => prdGetMock(...a),
      resolveIdByPublicId: vi.fn(),
    },
    artifactsApi: { list: vi.fn().mockResolvedValue([]) },
    isProjectArtifactType: (t: string) =>
      ["prd", "evidence", "prototype", "report", "ticket_set"].includes(t),
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "authed", user: { id: "u1" } }),
}))
vi.mock("../../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    openModal: vi.fn(),
    openContentPanel: (...a: unknown[]) => openContentPanelMock(...a),
    contentPanelTab: null,
    showToast: (...a: unknown[]) => showToastMock(...a),
  }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}))
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: React.PropsWithChildren<{ href: string } & Record<string, unknown>>) =>
    React.createElement("a", { href, ...rest }, children),
}))
vi.mock("../ProjectMainThread", () => ({
  ProjectMainThread: () => React.createElement("div", { "data-testid": "main-thread-stub" }),
}))
// The real drawer's row-click plumbing isn't under test here — only what its
// `onOpenInPlace` callback triggers. Stubbed to a single button that fires
// the PRD-open call with a fixed `prd_id`, standing in for a row click.
vi.mock("../ProjectArtifactsDrawer", () => ({
  ProjectArtifactsDrawer: ({
    onOpenInPlace,
  }: {
    onOpenInPlace?: (a: unknown) => void
  }) =>
    React.createElement(
      "button",
      {
        "data-testid": "open-prd-row",
        onClick: () =>
          onOpenInPlace?.({
            type: "prd",
            id: 1,
            title: "Dark mode on mobile",
            status: "ready",
            created_at: new Date().toISOString(),
            source: { brief_id: 9, week_label: null, insight_index: null },
            open: { brief_id: 9, insight_index: null, prd_id: 42 },
          }),
      },
      "open prd row",
    ),
}))

import { ProjectDetailScreen } from "../ProjectDetailScreen"
import { ContentProvider, useContent } from "../../../../../context/ContentContext"
import type { ProjectDetail } from "../../../../../lib/api"

const PROJECT: ProjectDetail = {
  id: 101,
  company_id: "c1",
  workspace_id: "w1",
  name: "Instant-quote flow",
  origin: "prd_auto",
  created_by: "u1",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  members: [],
}

/** Reads the SAME content store the component writes to, so the test can
 *  assert `prdGenerating`/`prd` transitions without reaching into the
 *  component's internals. */
function ContentProbe() {
  const { content } = useContent()
  return React.createElement(
    "div",
    { "data-testid": "content-probe" },
    JSON.stringify({
      hasPrd: !!content.prd,
      prdGenerating: !!content.prdGenerating,
    }),
  )
}

function renderScreen() {
  return render(
    React.createElement(
      ContentProvider,
      null,
      React.createElement(ContentProbe),
      React.createElement(ProjectDetailScreen, { projectId: "101" }),
    ),
  )
}

afterEach(() => {
  cleanup()
  getMock.mockReset()
  artifactsMock.mockReset()
  artifactsMock.mockResolvedValue([])
  memorySummaryMock.mockReset()
  memorySummaryMock.mockResolvedValue(null)
  memoryInsightMock.mockReset()
  memoryInsightMock.mockResolvedValue(null)
  prdGetMock.mockReset()
  openContentPanelMock.mockReset()
  showToastMock.mockReset()
})

describe("ProjectDetailScreen — PRD artifact-open panel timing (no dead gap)", () => {
  it("test_prd_row_open_calls_openContentPanel_before_fetch_resolves — the panel opens (and its loading state is set) synchronously on click, not inside the fetch's .then", async () => {
    getMock.mockResolvedValue(PROJECT)
    let resolveFetch!: (v: unknown) => void
    prdGetMock.mockReturnValue(
      new Promise((resolve) => {
        resolveFetch = resolve
      }),
    )

    renderScreen()
    await waitFor(() => expect(getMock).toHaveBeenCalled())
    const row = await screen.findByTestId("open-prd-row")

    act(() => {
      row.click()
    })

    // Synchronous, in the SAME tick as the click — before the PRD fetch has
    // any chance to resolve. A regression that moves `openContentPanel` back
    // inside `.then` would only call this AFTER an awaited microtask/macrotask.
    expect(openContentPanelMock).toHaveBeenCalledWith("prd")
    expect(prdGetMock).toHaveBeenCalledWith(42)
    expect(JSON.parse(screen.getByTestId("content-probe").textContent || "{}")).toEqual({
      hasPrd: false,
      prdGenerating: true,
    })

    await act(async () => {
      resolveFetch({
        id: 42,
        status: "ready",
        payload_md: "# Dark mode on mobile",
        public_id: "prd-public-uuid-1",
        share_token: null,
        llm_part: null,
        brief_id: 9,
        insight_index: null,
        source: null,
      })
    })

    await waitFor(() =>
      expect(JSON.parse(screen.getByTestId("content-probe").textContent || "{}")).toEqual({
        hasPrd: true,
        prdGenerating: false,
      }),
    )
  })

  it("test_prd_row_open_skips_refetch_when_already_loaded — clicking a row for the PRD already in `content.prd` opens the panel without a second fetch", async () => {
    getMock.mockResolvedValue(PROJECT)
    prdGetMock.mockResolvedValue({
      id: 42,
      status: "ready",
      payload_md: "# Dark mode on mobile",
      public_id: "prd-public-uuid-1",
      share_token: null,
      llm_part: null,
      brief_id: 9,
      insight_index: null,
      source: null,
    })

    renderScreen()
    await waitFor(() => expect(getMock).toHaveBeenCalled())
    const row = await screen.findByTestId("open-prd-row")

    await act(async () => {
      row.click()
    })
    await waitFor(() => expect(prdGetMock).toHaveBeenCalledTimes(1))

    openContentPanelMock.mockClear()

    act(() => {
      row.click()
    })

    expect(openContentPanelMock).toHaveBeenCalledWith("prd")
    expect(prdGetMock).toHaveBeenCalledTimes(1)
  })
})
