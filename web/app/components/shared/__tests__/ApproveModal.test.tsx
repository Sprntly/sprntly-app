// @vitest-environment jsdom
//
// ApproveModal now delegates its generate/view-prototype state machine to the
// shared useGeneratePrototype() hook (the hook's own branching is exercised
// exhaustively by useGeneratePrototype.test.tsx). These tests are scoped to
// ApproveModal's OWN wiring through that hook:
//   1. option label + click routing (Generate / View / Create a ticket),
//   2. the controlled open/onOpenChange pair driving NavigationContext's
//      `activeModal` union (asserted via the union's own state, not an
//      internal hook boolean),
//   3. the cross-instance "is a generation running" signal
//      (listenForCrossSurfaceGenerating), the highest-risk wiring in this
//      migration,
//   4. two additions this migration needed beyond the hook's own contract,
//      discovered while porting this specific host (both documented in
//      ApproveModal.tsx itself):
//        - closing the approve modal on a real pathname change (the hook's
//          "view" success path calls router.push directly and has no notion
//          of this modal's own activeModal-driven visibility gate),
//        - re-triggering the hook's existence check via refetchExisting() on
//          reopen (the hook's own existence effect only depends on prdId, not
//          on this modal's open/close cycling).
//
// GenerateModal / GenerationLoadingScreen are mocked as thin test doubles
// exposing the hook's wired callback props via clickable buttons — their own
// internal state machines have their own test suites.
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
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

// Sprntly components carry no `import React`; vitest's esbuild transform uses the
// classic runtime, so expose React globally (repo test convention).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// ── next/navigation: controllable router.push + usePathname ─────────────────
// `push` mutates the pathname the mock returns, so ApproveModal's own
// pathname-watching effect (added by this ticket) can be exercised the same
// way a real Next navigation would update usePathname().
let currentPathname = "/prd"
const push = vi.fn((path: string) => {
  currentPathname = path
})
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
  usePathname: () => currentPathname,
}))

// ── designAgentApi: existence check + re-verify ──────────────────────────────
const getByPrd = vi.fn()
const deleteProto = vi.fn(async (..._args: unknown[]) => {})
vi.mock("../../../lib/api", () => ({
  designAgentApi: {
    getByPrd: (...args: [number]) => getByPrd(...args),
    delete: (...args: unknown[]) => deleteProto(...args),
  },
}))

// ── Workspace (saved design-source preference) — not exercised by these
// tests (GenerateModal is mocked away), mocked minimally so the module resolves.
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace: { id: "ws-1", design_source: null }, refresh: vi.fn() }),
}))
vi.mock("../../../lib/onboarding/store", () => ({
  updateWorkspace: vi.fn(async () => {}),
}))

// ── GenerateModal / GenerationLoadingScreen test doubles ─────────────────────
// Expose the hook's wired props via clickable buttons so tests can drive
// onGenStart/onKickoff/onGenDone/onNotifyWhenReady deterministically, without
// depending on either component's own async internals (connector fetches, SSE
// subscriptions, etc.) — those are covered by their own test suites.
vi.mock("../../design-agent/GenerateModal", () => ({
  GenerateModal: (props: {
    open: boolean
    prdId: number | null
    onGenStart: (ctx?: { figmaFileKey?: string | null; githubRepo?: string | null }) => void
    onKickoff: (id: number) => void
    onGenDone: (result?: { ok: boolean; prototype?: unknown; message?: string }) => void
  }) =>
    React.createElement(
      "div",
      { "data-testid": "generate-modal-mount", "data-open": String(props.open) },
      React.createElement(
        "button",
        { "data-testid": "gm-start", onClick: () => props.onGenStart() },
        "start",
      ),
      React.createElement(
        "button",
        { "data-testid": "gm-kickoff", onClick: () => props.onKickoff(props.prdId ?? 1) },
        "kickoff",
      ),
      React.createElement(
        "button",
        {
          "data-testid": "gm-done-success",
          onClick: () =>
            props.onGenDone({
              ok: true,
              prototype: {
                id: props.prdId ?? 1,
                status: "ready",
                bundle_url: "https://example.com/proto.js",
                error: null,
              },
            }),
        },
        "done-success",
      ),
      React.createElement(
        "button",
        {
          "data-testid": "gm-done-fail",
          onClick: () => props.onGenDone({ ok: false, message: "boom" }),
        },
        "done-fail",
      ),
    ),
}))
vi.mock("../../design-agent/GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: (props: { open: boolean; onNotifyWhenReady: () => void }) =>
    props.open
      ? React.createElement(
          "div",
          { "data-testid": "loading-screen-mount" },
          React.createElement(
            "button",
            { "data-testid": "notify-when-ready", onClick: () => props.onNotifyWhenReady() },
            "notify",
          ),
        )
      : null,
}))

import {
  NavigationProvider,
  useNavigation,
} from "../../../context/NavigationContext"
import { ContentProvider, useContent } from "../../../context/ContentContext"
import { GenerateModal } from "../../design-agent/GenerateModal"
import { prototypePath } from "../../../lib/routes"
import type { PrdState } from "../../../types/content"
import { ApproveModal } from "../ApproveModal"

function fakePrd(id: number, figmaFileKey: string | null = null): PrdState {
  return {
    prd_id: id,
    figma_file_key: figmaFileKey,
    metaLine: "meta",
    title: "Test PRD",
    sections: [],
  }
}

function readyRow(id: number) {
  return { id, status: "ready" as const, bundle_url: `https://example.com/${id}.js`, error: null }
}
function generatingRow(id: number) {
  return { id, status: "generating" as const, bundle_url: null, error: null }
}

/** Seeds ContentContext's `prd` once on mount so ApproveModal reads a real PRD id. */
function Seed({ prd }: { prd: PrdState | null }) {
  const { setContent } = useContent()
  React.useEffect(() => {
    setContent({ prd })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return null
}

/** Exposes NavigationContext state + a few controls so tests can drive
 *  activeModal transitions and read toast state the same way the real
 *  <Toast/> (mounted elsewhere in AppShell, not here) would. */
function Probe() {
  const { activeModal, activeDrawer, openModal, closeModal, toast } = useNavigation()
  return React.createElement(
    "div",
    null,
    React.createElement("output", { "data-testid": "active-modal" }, String(activeModal)),
    React.createElement("output", { "data-testid": "active-drawer" }, String(activeDrawer)),
    React.createElement("output", { "data-testid": "toast-title" }, toast?.title ?? ""),
    React.createElement("output", { "data-testid": "toast-sub" }, toast?.sub ?? ""),
    toast?.onAction &&
      React.createElement(
        "button",
        { "data-testid": "toast-action-btn", onClick: () => toast.onAction?.() },
        toast.link ?? "action",
      ),
    React.createElement(
      "button",
      { "data-testid": "open-approve", onClick: () => openModal("approve") },
      "open-approve",
    ),
    React.createElement(
      "button",
      { "data-testid": "open-invite", onClick: () => openModal("invite") },
      "open-invite",
    ),
    React.createElement(
      "button",
      { "data-testid": "close", onClick: () => closeModal() },
      "close",
    ),
  )
}

function TestApp({ prd }: { prd: PrdState | null }) {
  return React.createElement(
    NavigationProvider,
    null,
    React.createElement(
      ContentProvider,
      null,
      React.createElement(Seed, { prd }),
      React.createElement(ApproveModal),
      React.createElement(Probe),
    ),
  )
}

async function openApprove() {
  fireEvent.click(screen.getByTestId("open-approve"))
  await act(async () => {})
}

beforeEach(() => {
  currentPathname = "/prd"
  getByPrd.mockReset()
  push.mockClear()
  deleteProto.mockClear()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("ApproveModal — regression (option labels + click routing)", () => {
  it("test_approve_modal_generate_label_and_opens_via_navigation_union: no existing prototype shows Generate Prototype; clicking opens the generate panel via the navigation union", async () => {
    getByPrd.mockResolvedValue(null)
    render(React.createElement(TestApp, { prd: fakePrd(1) }))
    await openApprove()

    expect(screen.getByText("Generate Prototype")).toBeTruthy()
    expect(screen.getByTestId("active-modal").textContent).toBe("approve")

    fireEvent.click(screen.getByText("Generate Prototype"))
    await act(async () => {})

    // Asserted by reading NavigationContext's state after the click, not an
    // internal hook boolean.
    expect(screen.getByTestId("active-modal").textContent).toBe("generate")
  })

  it("test_approve_modal_view_label_and_navigates_on_reverify_success: existing ready prototype shows View Prototype; click re-verifies and navigates exactly once", async () => {
    getByPrd.mockResolvedValue(readyRow(2))
    render(React.createElement(TestApp, { prd: fakePrd(2) }))
    await openApprove()

    expect(screen.getByText("View Prototype")).toBeTruthy()

    fireEvent.click(screen.getByText("View Prototype"))
    await act(async () => {})

    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith(prototypePath(2))
    // getByPrd called once for the initial existence check + once for the
    // click's own re-verify.
    expect(getByPrd).toHaveBeenCalledTimes(2)
  })

  it("test_approve_modal_ticket_click_unchanged: Create a ticket still closes the modal then opens the ticket drawer", async () => {
    getByPrd.mockResolvedValue(null)
    render(React.createElement(TestApp, { prd: fakePrd(3) }))
    await openApprove()

    fireEvent.click(screen.getByText("Create a ticket"))

    expect(screen.getByTestId("active-modal").textContent).toBe("null")
    expect(screen.getByTestId("active-drawer").textContent).toBe("ticket")
  })
})

describe("ApproveModal — error handling (stale re-verify)", () => {
  it("test_approve_modal_view_reverify_stale_resets_and_toasts: a stale re-verify resets the option and toasts, without navigating", async () => {
    getByPrd.mockResolvedValueOnce(readyRow(4)) // initial existence check
    getByPrd.mockResolvedValueOnce(null) // click re-verify: prototype gone
    render(React.createElement(TestApp, { prd: fakePrd(4) }))
    await openApprove()

    expect(screen.getByText("View Prototype")).toBeTruthy()

    fireEvent.click(screen.getByText("View Prototype"))
    await act(async () => {})

    expect(push).not.toHaveBeenCalled()
    expect(screen.getByTestId("toast-title").textContent).toBe("Prototype unavailable")
    expect(screen.getByText("Generate Prototype")).toBeTruthy()
  })
})

describe("ApproveModal — cross-instance generating signal (highest-risk wiring)", () => {
  it("test_approve_modal_generating_state_disables_option: a prototype already 'generating' on open disables the option and click is a no-op", async () => {
    getByPrd.mockResolvedValue(generatingRow(5))
    render(React.createElement(TestApp, { prd: fakePrd(5) }))
    await openApprove()

    expect(screen.getByText("Generating Prototype")).toBeTruthy()
    const option = screen.getByText("Generating Prototype").closest(".modal-option")
    expect(option?.className).toContain("opacity-50")
    expect(option?.className).toContain("pointer-events-none")

    fireEvent.click(screen.getByText("Generating Prototype"))
    await act(async () => {})

    expect(screen.getByTestId("active-modal").textContent).toBe("approve")
    expect(push).not.toHaveBeenCalled()
    expect(getByPrd).toHaveBeenCalledTimes(1) // no extra re-verify fetch from the no-op click
  })

  it("test_approve_modal_external_da_generating_event_disables_option: an externally-dispatched da:generating event disables THIS modal's option too", async () => {
    getByPrd.mockResolvedValue(null)
    render(React.createElement(TestApp, { prd: fakePrd(6) }))
    await openApprove()

    expect(screen.getByText("Generate Prototype")).toBeTruthy()

    act(() => {
      window.dispatchEvent(new CustomEvent("da:generating"))
    })

    expect(screen.getByText("Generating Prototype")).toBeTruthy()
  })
})

describe("ApproveModal — notify-when-ready then completion (AC5, corrected)", () => {
  it("test_approve_modal_notify_then_completion_shows_actionable_toast: notify closes the overlay + dispatches da:generating; completion shows a persistent actionable toast, never auto-navigates", async () => {
    getByPrd.mockResolvedValue(null)
    const doneEvents: Event[] = []
    const onDone = (e: Event) => doneEvents.push(e)
    window.addEventListener("da:generating-done", onDone)

    render(React.createElement(TestApp, { prd: fakePrd(7) }))
    await openApprove()

    fireEvent.click(screen.getByText("Generate Prototype"))
    await act(async () => {})
    expect(screen.getByTestId("active-modal").textContent).toBe("generate")

    await act(async () => {
      fireEvent.click(screen.getByTestId("gm-start"))
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("gm-kickoff"))
    })
    expect(screen.getByTestId("loading-screen-mount")).toBeTruthy()

    await act(async () => {
      fireEvent.click(screen.getByTestId("notify-when-ready"))
    })
    // Overlay closed; "Notify me when ready" fired the processing toast.
    expect(screen.queryByTestId("loading-screen-mount")).toBeNull()
    expect(screen.getByTestId("toast-title").textContent).toBe("Prototype is processing")

    // The generation resolves later — completion must NOT auto-navigate.
    await act(async () => {
      fireEvent.click(screen.getByTestId("gm-done-success"))
    })

    expect(push).not.toHaveBeenCalled()
    expect(screen.getByTestId("toast-title").textContent).toBe("Prototype ready")
    expect(screen.getByTestId("toast-sub").textContent).toBe(
      "Your prototype finished generating.",
    )
    expect(doneEvents.length).toBe(1)

    // Clicking the toast's "Open" action navigates.
    fireEvent.click(screen.getByTestId("toast-action-btn"))
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith(prototypePath(7))

    window.removeEventListener("da:generating-done", onDone)
  })

  it("test_approve_modal_notify_then_completion_shows_actionable_toast (failure): a failed background generation shows a persistent failure toast", async () => {
    getByPrd.mockResolvedValue(null)
    render(React.createElement(TestApp, { prd: fakePrd(8) }))
    await openApprove()

    fireEvent.click(screen.getByText("Generate Prototype"))
    await act(async () => {})
    await act(async () => {
      fireEvent.click(screen.getByTestId("gm-start"))
    })
    await act(async () => {
      fireEvent.click(screen.getByTestId("notify-when-ready"))
    })

    await act(async () => {
      fireEvent.click(screen.getByTestId("gm-done-fail"))
    })

    expect(push).not.toHaveBeenCalled()
    expect(screen.getByTestId("toast-title").textContent).toBe("Generation failed")
  })
})

describe("ApproveModal — reopen mid-generation reseeds (AC6)", () => {
  it("test_approve_modal_reopen_mid_generation_reseeds_generating: reopening after a status flip re-seeds gen.cta === generating from a fresh getByPrd read", async () => {
    getByPrd.mockResolvedValueOnce(null) // first open: no prototype yet
    render(React.createElement(TestApp, { prd: fakePrd(9) }))
    await openApprove()
    expect(screen.getByText("Generate Prototype")).toBeTruthy()

    // Cycle away (approve -> invite) and back, WITHOUT a fresh da:generating
    // event ever firing — only the reopen's own re-check should catch this.
    getByPrd.mockResolvedValueOnce(generatingRow(9))
    fireEvent.click(screen.getByTestId("open-invite"))
    await act(async () => {})
    fireEvent.click(screen.getByTestId("open-approve"))
    await act(async () => {})

    expect(screen.getByText("Generating Prototype")).toBeTruthy()
    expect(getByPrd).toHaveBeenCalledTimes(2)
  })
})

describe("ApproveModal — pathname-driven close (addition beyond the ticket's original ACs)", () => {
  it("test_approve_modal_view_success_navigate_closes_approve_modal: a successful View click that changes pathname closes the approve modal", async () => {
    getByPrd.mockResolvedValue(readyRow(10))
    render(React.createElement(TestApp, { prd: fakePrd(10) }))
    await openApprove()

    fireEvent.click(screen.getByText("View Prototype"))
    await act(async () => {})

    expect(push).toHaveBeenCalledWith(prototypePath(10))
    expect(screen.getByTestId("active-modal").textContent).toBe("null")
  })

  it("test_approve_modal_view_stale_reverify_keeps_approve_modal_open: a failed/stale View click that does NOT change pathname leaves the approve modal open with the reset label", async () => {
    getByPrd.mockResolvedValueOnce(readyRow(11))
    getByPrd.mockResolvedValueOnce(null)
    render(React.createElement(TestApp, { prd: fakePrd(11) }))
    await openApprove()

    fireEvent.click(screen.getByText("View Prototype"))
    await act(async () => {})

    expect(push).not.toHaveBeenCalled()
    expect(screen.getByTestId("active-modal").textContent).toBe("approve")
    expect(screen.getByText("Generate Prototype")).toBeTruthy()
  })
})

describe("ApproveModal — dead bookkeeping removed (AC9)", () => {
  it("test_approve_modal_dead_bookkeeping_removed: none of the deleted timer/reveal identifiers appear in the source", () => {
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/ApproveModal.tsx"),
      "utf8",
    )
    for (const identifier of [
      "prdIdOf",
      "shownAtRef",
      "resolvedRef",
      "safetyTimerRef",
      "minTimerRef",
      "pendingCanvasRef",
      "generateActiveRef",
      "clearTimers",
      "hideLoading",
    ]) {
      expect(src).not.toContain(identifier)
    }
  })
})

describe("ApproveModal — navigation modal union wiring (non-breakage, AC8/AC10)", () => {
  it("test_approve_modal_wires_generate_via_controlled_open: the hook's controlled open/onOpenChange pair drives the union, not local state", () => {
    const src = readFileSync(
      resolve(process.cwd(), "app/components/shared/ApproveModal.tsx"),
      "utf8",
    )
    expect(src).not.toContain("generateOpen")
    expect(src).not.toContain("setGenerateOpen")
    expect(src).toContain('open: activeModal === "generate"')
    expect(src).toContain('openModal("generate")')
    // Navigation is delegated entirely to the hook now — no local
    // router.push(prototypePath(...)) call remains in this file.
    expect(src).not.toContain("router.push(prototypePath(")
    expect(src).not.toContain("goToCanvas")
    expect(src).not.toContain("canvasPath(")
  })
})

// ── Generic NavigationContext modal-union infra (independent of ApproveModal
// itself — retained from the prior generate-visibility-lift ticket). ─────────
const wrapper = ({ children }: { children: React.ReactNode }) =>
  React.createElement(NavigationProvider, null, children)

function UnionHarness() {
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
    render(React.createElement(NavigationProvider, null, React.createElement(UnionHarness)))
    expect(screen.getByTestId("generate-modal-mount").getAttribute("data-open")).toBe("false")

    fireEvent.click(screen.getByTestId("open-generate"))
    expect(screen.getByTestId("active").textContent).toBe("generate")
    expect(screen.getByTestId("generate-modal-mount").getAttribute("data-open")).toBe("true")
    await act(async () => {})

    fireEvent.click(screen.getByTestId("close"))
    expect(screen.getByTestId("active").textContent).toBe("null")
    expect(screen.getByTestId("generate-modal-mount").getAttribute("data-open")).toBe("false")
  })

  it("test_existing_modal_does_not_show_generate: opening 'approve' does NOT open the generate modal", () => {
    render(React.createElement(NavigationProvider, null, React.createElement(UnionHarness)))
    fireEvent.click(screen.getByTestId("open-approve"))
    expect(screen.getByTestId("active").textContent).toBe("approve")
    expect(screen.getByTestId("generate-modal-mount").getAttribute("data-open")).toBe("false")
  })
})
