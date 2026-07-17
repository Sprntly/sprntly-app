// @vitest-environment jsdom
import * as React from "react"
import { cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

const apiMocks = vi.hoisted(() => ({
  get: vi.fn(),
  getActiveByPrd: vi.fn(),
  getLatestByPrd: vi.fn(),
  getByPrd: vi.fn(),
  runDesignAgentGeneration: vi.fn(),
}))

let searchString = "prd=1&pid=250"
const routerBack = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: routerBack,
  }),
  useSearchParams: () => new URLSearchParams(searchString),
  usePathname: () => "/prototype",
}))

const goTo = vi.fn()
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ goTo }),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: { prd: null, userName: null } }),
}))
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace: null }),
}))

vi.mock("../../../components/screens/app/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

vi.mock("../../../components/design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: ({
    open,
    prototypeId,
  }: {
    open: boolean
    prototypeId?: number | null
  }) =>
    open
      ? React.createElement(
          "div",
          {
            "data-testid": "pid-loading",
            "data-prototype-id": String(prototypeId ?? ""),
          },
          "loading",
        )
      : null,
}))

vi.mock("../../../components/design-agent/GenerateModal", () => ({
  GenerateModal: () => null,
}))

vi.mock("../../../components/design-agent/PostGenerationResult", () => ({
  PostGenerationResult: ({ prototype }: { prototype: { id: number } }) =>
    React.createElement(
      "div",
      { "data-testid": "rendered-prototype" },
      `prototype ${prototype.id}`,
    ),
}))

vi.mock("../../../lib/runDesignAgentGeneration", () => ({
  runDesignAgentGeneration: apiMocks.runDesignAgentGeneration,
}))

vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>(
    "../../../lib/api",
  )
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      get: apiMocks.get,
      getActiveByPrd: apiMocks.getActiveByPrd,
      getLatestByPrd: apiMocks.getLatestByPrd,
      getByPrd: apiMocks.getByPrd,
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

const readyProto = {
  id: 250,
  prd_id: 1,
  status: "ready",
  bundle_url: "https://cdn.example/prototype/index.html",
  is_complete: false,
  share_token: null,
}

beforeEach(() => {
  searchString = "prd=1&pid=250"
  routerBack.mockClear()
  goTo.mockClear()
  apiMocks.get.mockReset()
  apiMocks.getActiveByPrd.mockReset()
  apiMocks.getLatestByPrd.mockReset()
  apiMocks.getByPrd.mockReset()
  apiMocks.runDesignAgentGeneration.mockReset()
  apiMocks.getLatestByPrd.mockResolvedValue(null)
  // Default: the by-id lookup resolves nothing, so the pre-existing handoff
  // tests exercise exactly the PRD-lookup-authoritative paths they always did.
  apiMocks.get.mockResolvedValue(null)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("PrototypeRoute pid handoff", () => {
  it("test_pid_param_shows_loading_on_mount", async () => {
    apiMocks.getActiveByPrd.mockImplementation(() => new Promise<null>(() => {}))

    render(React.createElement(PrototypeRoute))

    const loading = await screen.findByTestId("pid-loading")
    expect(loading.getAttribute("data-prototype-id")).toBe("250")
    expect(screen.queryByTestId("prototype-route-empty")).toBeNull()
    expect(apiMocks.getActiveByPrd).toHaveBeenCalledWith(1)
  })

  it("test_pid_loading_swaps_to_render_when_ready", async () => {
    apiMocks.getActiveByPrd.mockResolvedValue(readyProto)

    render(React.createElement(PrototypeRoute))

    await waitFor(() =>
      expect(screen.getByTestId("rendered-prototype").textContent).toContain("250"),
    )
    expect(screen.queryByTestId("pid-loading")).toBeNull()
    expect(screen.queryByTestId("prototype-route-gen-error")).toBeNull()
  })

  it("test_pid_loading_swaps_to_error_when_failed", async () => {
    apiMocks.getActiveByPrd.mockResolvedValue(null)
    apiMocks.getLatestByPrd.mockResolvedValue({
      id: 250,
      prd_id: 1,
      status: "failed",
      bundle_url: null,
    })

    render(React.createElement(PrototypeRoute))

    await waitFor(() =>
      expect(screen.getByTestId("prototype-route-gen-error")).toBeTruthy(),
    )
    expect(screen.queryByTestId("pid-loading")).toBeNull()
    expect(screen.queryByTestId("prototype-route-empty")).toBeNull()
  })

  it("test_no_pid_unchanged", async () => {
    searchString = "prd=1"
    apiMocks.getActiveByPrd.mockResolvedValue(null)

    render(React.createElement(PrototypeRoute))

    await screen.findByTestId("prototype-route-empty")
    expect(screen.queryByTestId("pid-loading")).toBeNull()
    expect(apiMocks.getActiveByPrd).toHaveBeenCalledWith(1)
    expect(apiMocks.getLatestByPrd).toHaveBeenCalledWith(1)
  })

  it("test_pid_param_resolves_ready_prototype", async () => {
    // The prototype-ready notification's deep link: `?pid=` ONLY (no `?prd=`),
    // clicked AFTER the prototype is ready. The route must resolve the row by
    // id and select it — not the loading state, not the no-PRD empty state.
    searchString = "pid=250"
    apiMocks.get.mockResolvedValue(readyProto)

    render(React.createElement(PrototypeRoute))

    await waitFor(() =>
      expect(screen.getByTestId("rendered-prototype").textContent).toContain("250"),
    )
    expect(apiMocks.get).toHaveBeenCalledWith(250)
    expect(screen.queryByTestId("prototype-route-empty")).toBeNull()
    expect(screen.queryByTestId("pid-loading")).toBeNull()
  })

  it("test_pid_only_non_ready_falls_back_to_empty_state", async () => {
    // A pid-only URL whose row is NOT ready (still generating / deleted /
    // foreign) keeps today's behaviour: the no-PRD empty state, no crash.
    searchString = "pid=250"
    apiMocks.get.mockResolvedValue({ ...readyProto, status: "generating" })

    render(React.createElement(PrototypeRoute))

    await screen.findByTestId("prototype-route-empty")
    expect(screen.queryByTestId("rendered-prototype")).toBeNull()
  })

  it.each(["prd=1&pid=abc", "prd=1&pid="])(
    "test_malformed_pid_ignored: %s",
    async (query) => {
      searchString = query
      apiMocks.getActiveByPrd.mockResolvedValue(null)

      render(React.createElement(PrototypeRoute))

      await screen.findByTestId("prototype-route-empty")
      expect(screen.queryByTestId("pid-loading")).toBeNull()
      expect(apiMocks.runDesignAgentGeneration).not.toHaveBeenCalled()
    },
  )
})
