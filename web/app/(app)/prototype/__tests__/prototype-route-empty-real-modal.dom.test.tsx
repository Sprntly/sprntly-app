// @vitest-environment jsdom
//
// Integration test that reproduces the PROD-blocking blank-screen bug on the
// no-prototype empty state. It mounts the REAL PrototypeRoute AND the REAL
// GenerateModal (only the leaf IO + contexts are mocked, exactly like the other
// prototype-route DOM tests) and asserts that clicking "Generate prototype" on
// the Value Hero mounts the modal's ACTUAL content — not just that the
// generateRequested gate flipped.
//
// The earlier empty-hero test mocks GenerateModal with a stub that renders
// whenever `open` is truthy, so it only proved the gate flipped — it could not
// see that the REAL modal renders NOTHING under these props. The repro is: a
// workspace with a saved github design-source preference whose github connector
// is NOT actually connected. GenerateModal then deadlocks in its config-suppress
// render guard (repos never load because the repos fetch is gated on the github
// connector being active, while both the auto-skip effect and the suppress guard
// wait on `repos !== null`), so the modal `return null`s forever: the empty state
// is gone but nothing replaces it — a completely blank page, no locate request,
// no console error. This test fails (blank) before the fix and passes after.

import * as React from "react"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

// Hoisted spies the vi.mock factories close over.
const { getActiveByPrd, getByPrdNull, connectorsList, locate } = vi.hoisted(
  () => ({
    getActiveByPrd: vi.fn(async () => null),
    getByPrdNull: vi.fn(async () => null),
    // No connectors active → github is NOT connected for this workspace.
    connectorsList: vi.fn(async () => ({ connections: [] as unknown[] })),
    // Spy so we can assert locate is (or is not) reached.
    locate: vi.fn(async () => ({ job_id: "job-1" })),
  }),
)

const replace = vi.fn()
const routerBack = vi.fn()
let searchString = "prd=176"
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace,
    push: vi.fn(),
    prefetch: vi.fn(),
    back: routerBack,
  }),
  useSearchParams: () => new URLSearchParams(searchString),
  usePathname: () => "/prototype",
}))

const goTo = vi.fn()
const showToast = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ goTo, showToast }),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: { prd: null, userName: null } }),
}))
// THE repro condition: a saved github design-source preference. This is the
// company-sprntly live shape (codebase-grounded). github is NOT connected
// (connectorsList → []), so repos never load.
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    workspace: {
      design_source: {
        design_source: "github",
        figma_file_key: null,
        github_repo: "sprntly/sprntly-app",
        website_url: null,
      },
    },
  }),
}))

// AppLayout → transparent passthrough.
vi.mock("../../../components/screens/app/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

// GenerationLoadingScreen → inert stub (not under test here).
vi.mock("../../../components/design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: () => null,
}))

// NOTE: GenerateModal is intentionally NOT mocked — we render the REAL modal so
// the test sees its actual render output (or lack thereof).

// api: read-only resolve returns null (empty state). connectorsApi.list returns
// no active connectors. designAgentApi.locate is spied to observe whether the
// locate pipeline is reached.
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  )
  return {
    ...actual,
    connectorsApi: {
      ...actual.connectorsApi,
      list: connectorsList,
    },
    designAgentApi: {
      ...actual.designAgentApi,
      getActiveByPrd,
      getByPrd: getByPrdNull,
      locate,
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

beforeEach(() => {
  searchString = "prd=176"
  replace.mockClear()
  goTo.mockClear()
  showToast.mockClear()
  getActiveByPrd.mockClear()
  getByPrdNull.mockClear()
  connectorsList.mockClear()
  locate.mockClear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function mountEmpty() {
  render(React.createElement(PrototypeRoute))
  await screen.findByTestId("prototype-route-empty")
}

describe("PrototypeRoute — Generate from empty state mounts the REAL GenerateModal", () => {
  it("clicking 'Generate prototype' renders the real modal's config UI (not a blank screen)", async () => {
    await mountEmpty()

    // Sanity: the empty-state hero is present before the click.
    expect(screen.getByText("Bring this PRD to life")).toBeTruthy()

    await act(async () => {
      fireEvent.click(
        screen.getByRole("button", { name: /Generate prototype/i }),
      )
    })

    // The empty state must be gone (generateRequested flipped) …
    await waitFor(() =>
      expect(screen.queryByTestId("prototype-route-empty")).toBeNull(),
    )

    // … AND the REAL GenerateModal's actual content must render. The blank-screen
    // bug shows the empty state vanishing while NOTHING replaces it. We assert on
    // the modal's real, user-visible surface: the "Generate prototype" modal title
    // and its real Generate action button (data-testid="generate-btn"). These come
    // from the modal's config phase, which the suppress guard was wrongly nulling.
    await waitFor(() => {
      expect(screen.getByTestId("generate-btn")).toBeTruthy()
    })
    // The modal chrome (title) is also present — proves the modal body mounted.
    expect(
      screen.getByRole("heading", { name: /Generate prototype/i }),
    ).toBeTruthy()
    // The design-source picker (config form) is visible — the config phase rendered.
    expect(screen.getByText("Design source")).toBeTruthy()
  })
})
