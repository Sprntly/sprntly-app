// @vitest-environment jsdom
//
// Generate-modal visibility lift: the GenerateModal's open/close state now lives
// in the shared navigation modal union (`activeModal === "generate"`) instead of
// ApproveModal local component state. These tests prove:
//   1. openModal("generate") sets activeModal, closeModal() clears it.
//   2. The real GenerateModal renders iff activeModal === "generate", wired the
//      same way ApproveModal wires it (open={activeModal === "generate"}).
//   3. The pre-existing "approve" / "invite" modal behaviour is unchanged.
//   4. ApproveModal no longer declares the old local visibility state.
//
// jsdom is opted into per-file (the global vitest config stays node-env); this
// mirrors the existing ShareMenu DOM test. Native DOM matchers only (no
// jest-dom). The api module is mocked so the connector fetch the modal kicks on
// open resolves to an empty list instead of hitting the network.
import * as React from "react"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import {
  act,
  cleanup,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// NavigationProvider depends on next/navigation. Stub the router/pathname so the
// provider mounts without a Next router context.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  usePathname: () => "/prd",
}))

// The GenerateModal fetches connector status on open; mock the api module so it
// resolves to an empty list rather than hitting the network.
vi.mock("../../../lib/api", () => ({
  connectorsApi: {
    list: vi.fn().mockResolvedValue({ connections: [] }),
    listGithubRepos: vi.fn().mockResolvedValue({ repositories: [] }),
    figmaAuthorizeUrl: "https://figma.example/auth",
    githubAuthorizeUrl: "https://github.example/auth",
  },
  designAgentApi: { generate: vi.fn() },
}))

import {
  NavigationProvider,
  useNavigation,
} from "../../../context/NavigationContext"
import { GenerateModal } from "../../design-agent/GenerateModal"

const wrapper = ({ children }: { children: React.ReactNode }) =>
  React.createElement(NavigationProvider, null, children)

// A harness wired the SAME way ApproveModal wires the modal: the union drives
// `open`, never local state.
function Harness() {
  const { activeModal, openModal, closeModal } = useNavigation()
  return React.createElement(
    "div",
    null,
    React.createElement("output", { "data-testid": "active" }, String(activeModal)),
    React.createElement(
      "button",
      { "data-testid": "open-generate", onClick: () => openModal("generate") },
      "gen",
    ),
    React.createElement(
      "button",
      { "data-testid": "open-approve", onClick: () => openModal("approve") },
      "approve",
    ),
    React.createElement(
      "button",
      { "data-testid": "open-invite", onClick: () => openModal("invite") },
      "invite",
    ),
    React.createElement(
      "button",
      { "data-testid": "close", onClick: () => closeModal() },
      "close",
    ),
    React.createElement(GenerateModal, {
      open: activeModal === "generate",
      onClose: closeModal,
      prdId: 1,
      figmaFileKey: null,
    }),
  )
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("navigation modal union — generate member", () => {
  it("test_open_generate_sets_active_modal: openModal('generate') sets activeModal", () => {
    const { result } = renderHook(() => useNavigation(), { wrapper })
    expect(result.current.activeModal).toBeNull()
    act(() => result.current.openModal("generate"))
    expect(result.current.activeModal).toBe("generate")
  })

  it("test_close_modal_clears_generate: closeModal() clears activeModal", () => {
    const { result } = renderHook(() => useNavigation(), { wrapper })
    act(() => result.current.openModal("generate"))
    expect(result.current.activeModal).toBe("generate")
    act(() => result.current.closeModal())
    expect(result.current.activeModal).toBeNull()
  })

  it("test_existing_modals_unaffected: approve and invite still set/clear the union", () => {
    const { result } = renderHook(() => useNavigation(), { wrapper })
    act(() => result.current.openModal("approve"))
    expect(result.current.activeModal).toBe("approve")
    act(() => result.current.openModal("invite"))
    expect(result.current.activeModal).toBe("invite")
    act(() => result.current.closeModal())
    expect(result.current.activeModal).toBeNull()
  })
})

describe("GenerateModal visibility driven off the union", () => {
  it("test_generate_modal_renders_on_active_modal_generate: in the DOM iff activeModal === 'generate'", async () => {
    render(React.createElement(NavigationProvider, null, React.createElement(Harness)))
    // Hidden initially (activeModal === null).
    expect(document.querySelector("#modal-generate")).toBeNull()

    // openModal('generate') → the modal mounts.
    fireEvent.click(screen.getByTestId("open-generate"))
    expect(screen.getByTestId("active").textContent).toBe("generate")
    expect(document.querySelector("#modal-generate")).not.toBeNull()
    // Flush the on-open connector fetch so its state update lands inside act().
    await act(async () => {})

    // closeModal() → the modal unmounts.
    fireEvent.click(screen.getByTestId("close"))
    expect(screen.getByTestId("active").textContent).toBe("null")
    expect(document.querySelector("#modal-generate")).toBeNull()
  })

  it("test_existing_modal_does_not_show_generate: opening 'approve' does NOT mount the generate modal", () => {
    render(React.createElement(NavigationProvider, null, React.createElement(Harness)))
    fireEvent.click(screen.getByTestId("open-approve"))
    expect(screen.getByTestId("active").textContent).toBe("approve")
    expect(document.querySelector("#modal-generate")).toBeNull()
  })
})

describe("ApproveModal no longer owns generate visibility locally", () => {
  it("test_approve_modal_has_no_generate_open_local_state: source declares no local generate state", () => {
    // Read the working-tree source (not a git rev — CI clones are shallow).
    // vitest runs from web/, so resolve against cwd. The lift removes the local
    // visibility state and routes through the union.
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/ApproveModal.tsx"),
      "utf8",
    )
    expect(src).not.toContain("generateOpen")
    expect(src).not.toContain("setGenerateOpen")
    expect(src).toContain('openModal("generate")')
    expect(src).toContain('open={activeModal === "generate"}')
  })
})
