// @vitest-environment jsdom
//
// ProjectDetailScreen — the one-shot `?prd=` restore's no-flash fast path
// (client decision D1, 2026-09-02). The seamless auto-nav into a
// just-created PRD (`ChatScreen.project-bind.dom.test.tsx`) lands here with
// `content.prd` ALREADY holding that exact PRD — set by the main-chat
// generate success path before it navigated. Refetching it via
// `GET /v1/prd/{id}` anyway is a needless round-trip that reads as a flash +
// delay. The restore effect must skip the network fetch (`loadPrdById` →
// `prdApi.get`) whenever `content.prd` already matches the `?prd=` param
// (by either id form the URL carries), and only open the content panel.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const getMock = vi.fn()
const artifactsMock = vi.fn().mockResolvedValue([])
const memorySummaryMock = vi.fn().mockResolvedValue(null)
const memoryInsightMock = vi.fn().mockResolvedValue(null)
const prdGetMock = vi.fn()
const resolveIdByPublicIdMock = vi.fn()
const openContentPanelMock = vi.fn()

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
      resolveIdByPublicId: (...a: unknown[]) => resolveIdByPublicIdMock(...a),
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
    contentPanelTab: "prd",
  }),
}))
let searchParams = new URLSearchParams()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => searchParams,
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
vi.mock("../ProjectArtifactDrawer", () => ({
  ProjectArtifactDrawer: () => React.createElement("aside", { "data-testid": "drawer-stub" }),
}))

import { ProjectDetailScreen } from "../ProjectDetailScreen"
import { ContentProvider, useContent } from "../../../../../context/ContentContext"
import type { ProjectDetail } from "../../../../../lib/api"
import type { PrdState } from "../../../../../types/content"

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

const PRD: PrdState = {
  prd_id: 501,
  public_id: "prd-public-uuid-1",
  title: "Dark mode on mobile",
  metaLine: "",
  sections: [],
}

/** Seeds `content.prd` (as the main-chat generate success path already did,
 *  BEFORE navigating) the moment it mounts — alongside `ProjectDetailScreen`,
 *  under the same `ContentProvider`, exactly as the real AppShell tree keeps
 *  the same content store live across the `/`→`/projects` SPA transition. */
function SeedPrd({ prd }: { prd: PrdState | null }) {
  const { setContent } = useContent()
  React.useEffect(() => {
    if (prd) setContent({ prd })
  }, [])
  return null
}

function renderScreen(opts: { seedPrd: PrdState | null; prdParam: string }) {
  searchParams = new URLSearchParams(`prd=${opts.prdParam}`)
  return render(
    React.createElement(
      ContentProvider,
      null,
      React.createElement(SeedPrd, { prd: opts.seedPrd }),
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
  resolveIdByPublicIdMock.mockReset()
  openContentPanelMock.mockReset()
})

describe("ProjectDetailScreen — ?prd= restore no-flash fast path (D1)", () => {
  it("test_restore_skips_refetch_on_prd_id_match — content.prd already holds the SAME prd_id as the ?prd= param: no network fetch, panel just opens", async () => {
    getMock.mockResolvedValue(PROJECT)
    renderScreen({ seedPrd: PRD, prdParam: String(PRD.prd_id) })

    await waitFor(() => expect(getMock).toHaveBeenCalled())
    await waitFor(() => expect(openContentPanelMock).toHaveBeenCalledWith("prd"))

    expect(prdGetMock).not.toHaveBeenCalled()
    expect(resolveIdByPublicIdMock).not.toHaveBeenCalled()
  })

  it("test_restore_skips_refetch_on_public_id_match — the ?prd= param carries the public_id uuid form; still matches content.prd, still skips the fetch", async () => {
    getMock.mockResolvedValue(PROJECT)
    renderScreen({ seedPrd: PRD, prdParam: PRD.public_id as string })

    await waitFor(() => expect(getMock).toHaveBeenCalled())
    await waitFor(() => expect(openContentPanelMock).toHaveBeenCalledWith("prd"))

    expect(prdGetMock).not.toHaveBeenCalled()
    expect(resolveIdByPublicIdMock).not.toHaveBeenCalled()
  })

  it("test_restore_fetches_on_cold_load — no matching content.prd (a fresh deep-link/refresh): falls through to the real fetch, exactly as before", async () => {
    getMock.mockResolvedValue(PROJECT)
    prdGetMock.mockResolvedValue({
      id: 999, status: "ready", payload_md: "# Doc", public_id: "other-uuid",
      share_token: null, llm_part: null, brief_id: null, insight_index: null, source: null,
    })
    renderScreen({ seedPrd: null, prdParam: "999" })

    await waitFor(() => expect(getMock).toHaveBeenCalled())
    await waitFor(() => expect(prdGetMock).toHaveBeenCalledWith(999))
  })

  it("test_restore_fetches_when_prd_differs — content.prd holds a DIFFERENT PRD than the ?prd= param: still fetches the requested one", async () => {
    getMock.mockResolvedValue(PROJECT)
    prdGetMock.mockResolvedValue({
      id: 777, status: "ready", payload_md: "# Doc", public_id: "other-uuid",
      share_token: null, llm_part: null, brief_id: null, insight_index: null, source: null,
    })
    renderScreen({ seedPrd: PRD, prdParam: "777" })

    await waitFor(() => expect(getMock).toHaveBeenCalled())
    await waitFor(() => expect(prdGetMock).toHaveBeenCalledWith(777))
  })
})
