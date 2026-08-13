// @vitest-environment jsdom
//
// AIBar is mounted app-wide (AppShell / AppLayout), and its private
// `isPrdCommand` — `/\b(generate|create|write|draft|make)\b.*\bprd\b/i` — was
// the worst of the two spurious-PRD triggers: unbounded `.*`, no question
// guard, no tickets guard. Worse, `handlePrdCommand` DISCARDED the user's
// message and generated from `brief.insights[0]`, so "how do I write a PRD?"
// produced a real PRD about a completely unrelated topic.
//
// This suite drives the real component through the submit path and asserts on
// which generation actually fires — the unit table in
// app/lib/__tests__/prd-commands.spurious-triggers.test.ts covers the rules
// themselves; this covers the wiring.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

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

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
}))

const { briefCurrentSpy } = vi.hoisted(() => ({ briefCurrentSpy: vi.fn() }))
vi.mock("../../../lib/api", async () => {
  const { isPrdCommand, prdCommandTask } = await import("../../../lib/prd-commands")
  // Declared INSIDE the factory: vi.mock is hoisted, so a module-scope const it
  // closed over could be in its temporal dead zone when the factory runs.
  const MULTI_AGENT_PHRASING =
    /\bprd\s+first\b|\bmulti[- ]?agent\b|\baggressive\s+(?:analysis|mode)\b/i
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    briefApi: { current: briefCurrentSpy },
    prdApi: {},
    // The PLANNER decides what a message asks for; this bar only executes the
    // verdict. Stubbed with the SAME helpers the bar used to run inline, which
    // is what these expectations were written against — so what they assert is
    // still the ROUTING ("a question reaches the ask agent, never the PRD
    // generator"), which is the whole point of the suite.
    //
    // Whether a sentence is a command is now the planner's judgement and is
    // tested in backend/tests/test_ask_planner.py. Reusing the helper here is a
    // double standing in for a model, not a rule the product still applies.
    chatIntentApi: {
      resolve: vi.fn(async (q: string) => ({
        // The multi-agent suite is its own action now. The bar used to detect
        // it with a local regex over "prd first" / "multi-agent" / "aggressive
        // analysis"; the planner owns that call, so the double names the same
        // phrasings to keep this suite asserting the ROUTING.
        intent: MULTI_AGENT_PHRASING.test(q) && isPrdCommand(q)
          ? "multi_agent"
          : isPrdCommand(q)
          ? "generate_prd"
          : "answer",
        confidence: 0.95,
        task: prdCommandTask(q),
        instruction: null,
        reason: "test stub",
        source: "planner",
        prd_id: null,
        prd_title: null,
      })),
    },
  }
})

const { prdFromTaskSpy } = vi.hoisted(() => ({ prdFromTaskSpy: vi.fn() }))
vi.mock("../../../lib/runPrdGeneration", () => ({
  runPrdGenerationFromTask: prdFromTaskSpy,
}))

const { multiAgentSpy } = vi.hoisted(() => ({ multiAgentSpy: vi.fn() }))
vi.mock("../../../lib/runMultiAgentGeneration", () => ({
  runMultiAgentGeneration: multiAgentSpy,
}))

const { askSpy } = vi.hoisted(() => ({ askSpy: vi.fn() }))
vi.mock("../../../lib/runAskGeneration", () => ({ runAskGeneration: askSpy }))

vi.mock("../../../lib/prd-adapter", () => ({ markdownToPrdState: vi.fn() }))

vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

const { contentMock, setContentSpy } = vi.hoisted(() => ({
  contentMock: {
    aiScreenChips: {} as Record<string, string[]>,
    conversations: [] as unknown[],
    prd: null as unknown,
  },
  setContentSpy: vi.fn(),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: contentMock, setContent: setContentSpy }),
}))

// The composer value under test — mutated per case before render.
const { nav } = vi.hoisted(() => ({
  nav: { value: "", showToast: vi.fn(), openContentPanel: vi.fn() },
}))
vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "chat",
    goTo: vi.fn(),
    aiBarValue: nav.value,
    setAIBarValue: vi.fn(),
    showToast: nav.showToast,
    aiPanelWidth: 360,
    setAiPanelWidth: vi.fn(),
    aiPanelCollapsed: false,
    toggleAiPanelCollapsed: vi.fn(),
    expandAiPanel: vi.fn(),
    openContentPanel: nav.openContentPanel,
  }),
  AI_PANEL_COLLAPSED_WIDTH: 56,
  AI_PANEL_WIDTH_MAX: 560,
  AI_PANEL_WIDTH_MIN: 320,
}))

import { AIBar } from "../AIBar"

async function submit(message: string) {
  nav.value = message
  render(<AIBar inline />)
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Send" }))
  })
}

beforeEach(() => {
  prdFromTaskSpy.mockResolvedValue({
    ok: true,
    prd: { prd_id: 42, title: "Magic-link sign-in" },
  })
  askSpy.mockResolvedValue({
    answer: "canned",
    sources: [],
    follow_ups: [],
    key_points: [],
    citations: [],
    confidence: 1,
    unanswered: "",
  })
  briefCurrentSpy.mockResolvedValue({
    id: 1,
    insights: [{ title: "Retention is slipping", recommendation: "Fix onboarding" }],
  })
  multiAgentSpy.mockResolvedValue({ ok: false, message: "noop" })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  contentMock.prd = null
})

describe("AIBar — ordinary chat questions never generate a PRD", () => {
  // Each of these matched the old private regex and produced a real document.
  it.each([
    "how do I write a PRD?",
    "what is a PRD?",
    "Can you give me the PRD for billing?",
    "let's make sure the product specs are updated",
    "I need the requirements doc from legal",
    "I wrote a PRD for billing last week",
    "convert this PRD into tickets",
  ])("%j goes to the ask agent, not the PRD generator", async (q) => {
    await submit(q)

    expect(prdFromTaskSpy).not.toHaveBeenCalled()
    expect(multiAgentSpy).not.toHaveBeenCalled()
    // The old handler's first call — proof it isn't quietly taking the
    // top-insight path either.
    expect(briefCurrentSpy).not.toHaveBeenCalled()
    expect(askSpy).toHaveBeenCalledWith(q, "acme", expect.any(String))
  })
})

describe("AIBar — a genuine command generates from the USER'S task", () => {
  it("passes the extracted task to the generator instead of the brief's top insight", async () => {
    await submit("generate a PRD for magic-link sign-in")

    expect(prdFromTaskSpy).toHaveBeenCalledTimes(1)
    expect(prdFromTaskSpy.mock.calls[0][0]).toBe("magic-link sign-in")
    // The bug: this used to be the ONLY source of the PRD's topic.
    expect(briefCurrentSpy).not.toHaveBeenCalled()
    expect(askSpy).not.toHaveBeenCalled()
  })

  it("reports the task it drafted, not an unrelated insight title", async () => {
    await submit("draft a product brief for offline mode")

    expect(prdFromTaskSpy.mock.calls[0][0]).toBe("offline mode")
    const message = await screen.findByText(/^Drafted a PRD for/)
    expect(message.textContent).toContain("offline mode")
    // The old copy named the brief insight the PRD was really about.
    expect(message.textContent).not.toContain("Retention is slipping")
    expect(screen.queryByText(/Retention is slipping/i)).toBeNull()
  })

  it("opens the PRD panel with no brief meta — a task PRD has no insight index", async () => {
    await submit("write a PRD about usage-based pricing")

    expect(nav.openContentPanel).toHaveBeenCalledWith("prd")
    const openingCall = setContentSpy.mock.calls.find(
      ([arg]) => arg && typeof arg === "object" && "prdGenerating" in arg && arg.prdGenerating,
    )
    expect(openingCall).toBeTruthy()
    expect(openingCall![0].prdMeta).toBeNull()
  })
})

describe("AIBar — a topicless command asks for a topic instead of guessing", () => {
  it.each([
    "generate a PRD",
    "generate a PRD for this",
  ])("%j prompts for a topic and generates nothing", async (q) => {
    await submit(q)

    expect(prdFromTaskSpy).not.toHaveBeenCalled()
    expect(briefCurrentSpy).not.toHaveBeenCalled()
    expect(askSpy).not.toHaveBeenCalled()
    expect(nav.showToast).toHaveBeenCalledWith(
      "What should the PRD cover?",
      expect.stringContaining("Tell me the topic"),
    )
  })
})

describe("AIBar — the multi-agent trigger inherits the same guards", () => {
  it("does not fire on a question that merely contains 'generate a PRD first'", async () => {
    await submit("how do I generate a PRD first, before the tickets?")

    expect(multiAgentSpy).not.toHaveBeenCalled()
    expect(prdFromTaskSpy).not.toHaveBeenCalled()
    expect(askSpy).toHaveBeenCalled()
  })

  it("still fires on the real command", async () => {
    await submit("generate a PRD first for magic-link sign-in")

    expect(multiAgentSpy).toHaveBeenCalledTimes(1)
    expect(prdFromTaskSpy).not.toHaveBeenCalled()
  })
})
