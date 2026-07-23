// @vitest-environment jsdom
//
// ChatScreen — "Generate a PRD …" typed in the main chat is a COMMAND, not an
// ask. A command that NAMES a task ("generate a PRD for dark mode") builds the
// PRD from the user's words (generateFromTask). A GENERIC "generate a PRD" (no
// topic) is seeded from the current conversation; with no conversation to seed
// from it ASKS for a topic — it must NOT default to the brief's top insight
// (which served an unrelated PRD). A normal question still goes to the ask agent.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const { generateFromTask, classifyCommand } = vi.hoisted(() => ({
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3" }),
  // Tier-2 LLM fallback (POST /v1/prd/classify-command). Default: not a command
  // — individual tests override per-case.
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
}))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [{ title: "x" }] }) },
    prdApi: { generateFromTask, classifyCommand },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: vi.fn().mockResolvedValue({ ok: true, prd: { prd_id: 501, title: "Dark mode on mobile", metaLine: "", sections: [] } }),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn(),
}))

const runAskGeneration = vi.fn().mockResolvedValue({
  answer: "canned", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
})
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: (...args: unknown[]) => runAskGeneration(...args),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn().mockReturnValue(null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))
// `?new=1` puts ChatScreen on its OWN new-chat landing (empty thread), so a
// generic PRD command here has no conversation to seed from.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams("new=1"),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

const { protoMap } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>() }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider, useNavigation } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// Surfaces the current toast title — the Toast UI is mounted by AppShell, not in
// this isolated render, so this probe is how we observe the "ask for a topic"
// prompt.
function ToastProbe() {
  const { toast } = useNavigation()
  return React.createElement("div", { "data-testid": "toast-probe" }, toast?.title ?? "")
}

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null,
        React.createElement(ChatScreen),
        React.createElement(ToastProbe),
      ),
    ),
  )
}

async function typeAndSend(text: string) {
  const textarea = document.querySelector(".chat-home-composer-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".chat-home-composer") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

beforeEach(() => {
  localStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  runPrdGeneration.mockClear()
  generateFromTask.mockClear()
  classifyCommand.mockClear()
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — 'Generate a PRD' command", () => {
  it("a GENERIC command with no conversation asks for a topic (never the brief's top insight)", async () => {
    renderChat()
    // "…for our top product opportunity." is a GENERIC phrasing (prdCommandTask
    // returns null). On a fresh landing there's no conversation to seed from.
    await typeAndSend("Generate a PRD for our top product opportunity.")

    await waitFor(() =>
      expect(screen.getByTestId("toast-probe").textContent).toMatch(/What should the PRD cover/i))
    // Nothing generated — crucially NOT the brief's top-insight PRD — and it
    // never fell through to the ask agent.
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("a TASK-SPECIFIC command builds the PRD from the user's words (generateFromTask)", async () => {
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith("dark mode on mobile")
    // Not the brief-insight path, and not the ask agent.
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("routes a normal question to the ask agent unchanged (no classifier call)", async () => {
    renderChat()
    await typeAndSend("Why did enterprise churn spike last month?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(generateFromTask).not.toHaveBeenCalled()
    // No PRD mention → the LLM fallback tier must not even be consulted.
    expect(classifyCommand).not.toHaveBeenCalled()
  })

  it("LLM fallback: a novel command phrasing the regex can't parse still generates", async () => {
    // No verb from the tier-1 list, not noun-first — regex says "not a command".
    // The message names a PRD, so tier 2 asks the classifier, which says yes.
    classifyCommand.mockResolvedValueOnce({ is_prd_command: true, task: "checkout revamp", confidence: 0.92 })
    renderChat()
    await typeAndSend("let's get a PRD going for the checkout revamp")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(classifyCommand).toHaveBeenCalledWith("let's get a PRD going for the checkout revamp")
    // The classifier-extracted task drives generation (the regex extractor
    // can't parse this phrasing by definition).
    expect(generateFromTask).toHaveBeenCalledWith("checkout revamp")
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("LLM fallback: a PRD mention that is NOT a command falls through to the ask agent", async () => {
    renderChat() // default classifyCommand mock: not a command
    await typeAndSend("the requirements doc needs another pass from legal")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(classifyCommand).toHaveBeenCalledTimes(1)
    expect(generateFromTask).not.toHaveBeenCalled()
  })

  it("LLM fallback: low confidence is not enough to hijack the message", async () => {
    classifyCommand.mockResolvedValueOnce({ is_prd_command: true, task: "something", confidence: 0.4 })
    renderChat()
    await typeAndSend("maybe the prd angle covers this?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(generateFromTask).not.toHaveBeenCalled()
  })

  it("LLM fallback: a classifier error fails open to the ask agent (send never breaks)", async () => {
    classifyCommand.mockRejectedValueOnce(new Error("gateway down"))
    renderChat()
    await typeAndSend("circulate a prd summary to the team")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(generateFromTask).not.toHaveBeenCalled()
  })

  it("seeds the command turn + generating card BEFORE generateFromTask resolves (optimistic-first)", async () => {
    // The latency bug: the previous flow awaited generateFromTask BEFORE opening
    // the tab, so the composer cleared and the chat sat empty for the multi-second
    // call. Hold the POST unresolved and assert the optimistic UI is already up.
    let resolveGen!: (v: unknown) => void
    generateFromTask.mockImplementationOnce(() => new Promise((res) => { resolveGen = res as (v: unknown) => void }))

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    // The generate POST is in flight (called with the parsed task) but NOT
    // resolved…
    expect(generateFromTask).toHaveBeenCalledWith("dark mode on mobile")
    // …yet the user's command, the acknowledgment, and the generating PRD card
    // are already on screen.
    expect(document.body.textContent).toContain("generate a PRD for dark mode on mobile")
    expect(document.body.textContent).toContain("Generating a PRD for that")
    expect(document.body.textContent).toContain("Generating PRD…")
    expect(document.querySelector('[data-testid="chat-insight-msg"]')).toBeTruthy()
    expect(runAskGeneration).not.toHaveBeenCalled()

    // Resolve the generate → the tab drives the result in via the resume machinery.
    await act(async () => { resolveGen({ prd_id: 501, title: "Dark mode on mobile", status: "generating" }) })
  })

  it("a GENERIC command MID-conversation seeds the PRD from the conversation", async () => {
    renderChat()
    // First a real message → the tab now carries a conversation turn.
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())

    // Now a GENERIC "generate a PRD" (no topic) — it must build the PRD from the
    // conversation (the user's turn), NOT the brief's top insight.
    const threadInput = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
    expect(threadInput).toBeTruthy()
    await act(async () => { fireEvent.change(threadInput, { target: { value: "generate a PRD" } }) })
    const sendBtn = within(document.querySelector(".bc-composer") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith("our checkout drops 42% of users at the payment step")
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })
})
