// @vitest-environment jsdom
//
// AIBar's "Generate prototype" action, in the agent-command reply block, used
// to navigate with a bare `goTo("prototype")` — no PRD id — landing on
// PrototypeRoute with prdId === null, which shows a persistent "No PRD
// selected" empty state with nothing to resolve it. Both call sites (the
// inline-panel variant and the side/bottom-panel variant) now carry the open
// PRD's id via `router.push(prototypePath(prdId))`, mirroring the idiom
// BriefChat.tsx already established for this exact situation.
//
// AIBar has no established test harness (zero prior coverage), so this mounts
// the real component with its three context hooks + next/navigation + the
// generation libs it calls internally stubbed — mirroring the mocking
// convention already used for ChatScreen/BriefChat's prototype-navigation
// suites in this repo.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// jsdom doesn't implement window.matchMedia; AIBar reads it on mount to pick
// the side/bottom layout.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const { pushSpy } = vi.hoisted(() => ({ pushSpy: vi.fn() }))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
}))

vi.mock("../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    briefApi: {
      current: vi.fn().mockResolvedValue({
        id: 1,
        insights: [{ title: "Retention is slipping", recommendation: "Fix onboarding" }],
      }),
    },
    prdApi: {},
  }
})

vi.mock("../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({
    ok: true,
    prd: { prd_id: 42, title: "Retention PRD" },
  }),
}))
vi.mock("../../../lib/runMultiAgentGeneration", () => ({
  runMultiAgentGeneration: vi.fn().mockResolvedValue({ ok: false, message: "noop" }),
}))
vi.mock("../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn().mockResolvedValue({
    answer: "canned",
    sources: [],
    follow_ups: [],
    key_points: [],
    citations: [],
    confidence: 1,
    unanswered: "",
  }),
}))
vi.mock("../../../lib/prd-adapter", () => ({
  markdownToPrdState: vi.fn(),
}))

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

// `content` is a plain object read fresh each render — mutate it in-place
// between tests to control `content.prd` for the guarded/unguarded cases.
const { contentMock } = vi.hoisted(() => ({
  contentMock: {
    aiScreenChips: {} as Record<string, string[]>,
    conversations: [] as unknown[],
    prd: null as { prd_id: number; title: string } | null,
  },
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock, setContent: vi.fn() }),
}))

const { goToSpy } = vi.hoisted(() => ({ goToSpy: vi.fn() }))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "chat",
    goTo: goToSpy,
    aiBarValue: "generate a prd for this",
    setAIBarValue: vi.fn(),
    showToast: vi.fn(),
    aiPanelWidth: 360,
    setAiPanelWidth: vi.fn(),
    aiPanelCollapsed: false,
    toggleAiPanelCollapsed: vi.fn(),
    expandAiPanel: vi.fn(),
    openContentPanel: vi.fn(),
  }),
  AI_PANEL_COLLAPSED_WIDTH: 56,
  AI_PANEL_WIDTH_MAX: 560,
  AI_PANEL_WIDTH_MIN: 320,
}))

// AI_BAR_SCREENS is [] in production (the side/bottom panel is currently
// unreachable via AppShell's non-inline <AIBar /> mount), but the branch
// containing the second call site (:670) is still real source this ticket
// touches — force it reachable here so both call sites get exercised, the
// same way this suite would need to if AI_BAR_SCREENS is ever repopulated.
vi.mock("../../../types", async () => {
  const actual = await vi.importActual<typeof import("../../../types")>(
    "../../../types",
  )
  return { ...actual, AI_BAR_SCREENS: ["chat"] }
})

import { prototypePath } from "../../../lib/routes"
import { AIBar } from "../AIBar"

async function driveGeneratePrompt() {
  const sendBtn = screen.getByRole("button", { name: "Send" })
  await act(async () => {
    fireEvent.click(sendBtn)
  })
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  contentMock.prd = null
})

describe("AIBar — inline panel 'Generate prototype' action (call site :494)", () => {
  it("test_aibar_generate_prototype_click_navigates_with_prd_id — router.push(prototypePath(prdId)) when a PRD is open", async () => {
    contentMock.prd = { prd_id: 42, title: "Retention PRD" }
    render(<AIBar inline />)

    await driveGeneratePrompt()

    const genBtn = await screen.findByRole("button", { name: "Generate prototype" })
    fireEvent.click(genBtn)

    expect(pushSpy).toHaveBeenCalledWith(prototypePath(42))
    expect(pushSpy).toHaveBeenCalledWith("/prototype?prd=42")
    expect(goToSpy).not.toHaveBeenCalledWith("prototype")
  })

  it("test_aibar_generate_prototype_click_noop_without_prd — no router.push and no goTo when no PRD is open", async () => {
    contentMock.prd = null
    render(<AIBar inline />)

    await driveGeneratePrompt()

    const genBtn = await screen.findByRole("button", { name: "Generate prototype" })
    fireEvent.click(genBtn)

    expect(pushSpy).not.toHaveBeenCalled()
    expect(goToSpy).not.toHaveBeenCalledWith("prototype")
  })
})

describe("AIBar — side/bottom panel 'Generate prototype' action (call site :670)", () => {
  it("test_aibar_generate_prototype_click_navigates_with_prd_id — router.push(prototypePath(prdId)) when a PRD is open", async () => {
    contentMock.prd = { prd_id: 42, title: "Retention PRD" }
    render(<AIBar />)

    await driveGeneratePrompt()

    const genBtn = await screen.findByRole("button", { name: "Generate prototype" })
    fireEvent.click(genBtn)

    expect(pushSpy).toHaveBeenCalledWith(prototypePath(42))
    expect(pushSpy).toHaveBeenCalledWith("/prototype?prd=42")
    expect(goToSpy).not.toHaveBeenCalledWith("prototype")

    // The sibling "Create tickets" action in this variant still uses goTo —
    // untouched, still reachable (regression guard against an over-broad edit).
    const ticketsBtn = screen.getByRole("button", { name: "Create tickets" })
    fireEvent.click(ticketsBtn)
    expect(goToSpy).toHaveBeenCalledWith("tickets")
  })

  it("test_aibar_generate_prototype_click_noop_without_prd — no router.push and no goTo when no PRD is open", async () => {
    contentMock.prd = null
    render(<AIBar />)

    await driveGeneratePrompt()

    const genBtn = await screen.findByRole("button", { name: "Generate prototype" })
    fireEvent.click(genBtn)

    expect(pushSpy).not.toHaveBeenCalled()
    expect(goToSpy).not.toHaveBeenCalledWith("prototype")
  })
})

describe("AIBar source invariants (AC6 / AC7)", () => {
  it("test_aibar_source_has_no_bare_goto_prototype — no occurrence of the literal goTo(\"prototype\") remains", async () => {
    const fs = await import("node:fs")
    const path = await import("node:path")
    const { fileURLToPath } = await import("node:url")
    const here = path.dirname(fileURLToPath(import.meta.url))
    const src = fs.readFileSync(path.join(here, "..", "AIBar.tsx"), "utf8")
    expect(src).not.toContain('goTo("prototype")')
  })

  it("test_aibar_source_imports_router_and_prototype_path — imports useRouter + prototypePath, still destructures goTo", async () => {
    const fs = await import("node:fs")
    const path = await import("node:path")
    const { fileURLToPath } = await import("node:url")
    const here = path.dirname(fileURLToPath(import.meta.url))
    const src = fs.readFileSync(path.join(here, "..", "AIBar.tsx"), "utf8")
    expect(src).toMatch(/import\s*\{\s*useRouter\s*\}\s*from\s*"next\/navigation"/)
    expect(src).toMatch(
      /import\s*\{\s*prototypePath\s*\}\s*from\s*"\.\.\/\.\.\/lib\/routes"/,
    )
    // other handlers in this file depend on goTo (e.g. goTo("tickets")) — must
    // still be destructured from useNavigation()
    expect(src).toMatch(/goTo[,\s]/)
    expect(src).toContain("} = useNavigation()")
  })
})
