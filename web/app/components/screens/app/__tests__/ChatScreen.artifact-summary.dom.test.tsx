// @vitest-environment jsdom
//
// ChatScreen — a fresh PRD generation posts an LLM chat summary of what got
// built (POST /v1/artifacts/chat-summary), as an agent-only turn ending with
// the "View PRD button" pointer line (which doubles as the grounding-filter
// marker). Best-effort: a null summary posts nothing; the artifact flow is
// never disturbed.
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

const { generateFromTask, classifyCommand, clarifyTask, chatSummary } = vi.hoisted(() => ({
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3" }),
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
  chatSummary: vi.fn().mockResolvedValue({ summary: "Targets mobile users; anchored on a system-follow default." }),
}))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [{ title: "x" }] }) },
    prdApi: { generateFromTask, classifyCommand, clarifyTask },
    artifactsApi: { chatSummary: (...a: unknown[]) => chatSummary(...a) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn().mockResolvedValue({ ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] } }),
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
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams("new=1"),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  // Envelope dispatch is DEFAULT ON, so a null/flagless workspace no longer
  // means "flag off" — this suite locks the LEGACY regex ladder, so it asks for
  // the kill switch by name.
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))
vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))
vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

const { protoMap } = vi.hoisted(() => ({ protoMap: new Map<number, unknown>() }))
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: protoMap, loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

async function typeAndSend(text: string) {
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  generateFromTask.mockClear()
  classifyCommand.mockClear()
  clarifyTask.mockClear()
  chatSummary.mockClear()
  clarifyTask.mockResolvedValue({ sufficient: true, questions: [], missing: [] })
  chatSummary.mockResolvedValue({ summary: "Targets mobile users; anchored on a system-follow default." })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — artifact chat summary on fresh PRD generation", () => {
  it("posts the summary as an agent-only turn once generation completes", async () => {
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    // The completion asked the backend to summarize THIS artifact…
    await waitFor(() => expect(chatSummary).toHaveBeenCalledWith("prd", 501))
    // …and the summary landed in the thread, with the pointer line that keeps
    // it out of future PRD grounding.
    await waitFor(() =>
      expect(document.body.textContent).toContain("Targets mobile users; anchored on a system-follow default."))
    expect(document.body.textContent).toContain("Use the View PRD button in this chat to reopen it anytime.")
    // Agent-only: the summary invented no user bubble.
    const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble")).map((b) => b.textContent)
    expect(bubbles).toEqual(["generate a PRD for dark mode on mobile"])
  })

  it("posts the summary after a clarify-card answer flow too", async () => {
    clarifyTask.mockResolvedValueOnce({
      sufficient: false,
      missing: ["Target users"],
      questions: [{ prompt: "Who are the target users?", options: ["Admins", "End users"], skip_default: "all" }],
    })
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("clarify-questions")).toBeTruthy())

    await act(async () => {
      fireEvent.click(within(screen.getByTestId("clarify-questions")).getAllByTestId("clarify-choice")[0])
    })
    await act(async () => { fireEvent.click(screen.getByTestId("clarify-submit")) })

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(chatSummary).toHaveBeenCalledWith("prd", 501))
    await waitFor(() =>
      expect(document.body.textContent).toContain("Targets mobile users"))
  })

  it("a null summary posts nothing and disturbs nothing", async () => {
    chatSummary.mockResolvedValue({ summary: null })
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(chatSummary).toHaveBeenCalledTimes(1))
    // The ack is there; no summary bubble, no error, no phantom turn.
    await waitFor(() => expect(document.body.textContent).toContain("Generating a PRD for that"))
    expect(document.body.textContent).not.toContain("View PRD button in this chat to reopen it anytime")
  })

  it("a summary endpoint failure never breaks the artifact flow", async () => {
    chatSummary.mockRejectedValue(new Error("boom"))
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(chatSummary).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(document.body.textContent).toContain("Generating a PRD for that"))
    expect(document.body.textContent).not.toContain("boom")
  })

  it("shows a 'Summarizing…' indicator while the summary is being written", async () => {
    // Before this, the summary appeared out of nowhere seconds after the panel
    // had already settled. Hold the summary call open and assert the chat says
    // what it's doing.
    let resolveSummary!: (v: unknown) => void
    chatSummary.mockImplementationOnce(() => new Promise((res) => { resolveSummary = res }))

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(chatSummary).toHaveBeenCalledTimes(1))

    // Visible, labelled, and in the thread — not a silent gap.
    await waitFor(() => expect(screen.getByTestId("summary-pending")).toBeTruthy())
    expect(document.body.textContent).toContain("Summarizing what got built…")

    await act(async () => { resolveSummary({ summary: "Targets mobile users." }) })

    // The SAME turn becomes the summary — no second bubble, no leftover skeleton.
    await waitFor(() => expect(screen.queryByTestId("summary-pending")).toBeNull())
    expect(document.body.textContent).toContain("Targets mobile users.")
  })

  it("retires the indicator when the summary comes back empty", async () => {
    let resolveSummary!: (v: unknown) => void
    chatSummary.mockImplementationOnce(() => new Promise((res) => { resolveSummary = res }))

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(screen.getByTestId("summary-pending")).toBeTruthy())

    await act(async () => { resolveSummary({ summary: null }) })

    // No stranded skeleton and no empty agent bubble left behind.
    await waitFor(() => expect(screen.queryByTestId("summary-pending")).toBeNull())
    expect(document.body.textContent).not.toContain("Summarizing what got built…")
    expect(document.body.textContent).not.toContain("No response was generated for this message.")
  })

  it("the indicator does not disturb an ask that is still in flight", async () => {
    // The placeholder turn is appended at the END of the thread. Naively that
    // steals "last" from a genuinely in-flight ask, flipping it to "No response
    // was generated" mid-answer and yanking the artifact-action row off screen.
    let resolveAsk!: (v: unknown) => void
    runAskGeneration.mockImplementationOnce(() => new Promise((res) => { resolveAsk = res }))
    let resolveSummary!: (v: unknown) => void
    chatSummary.mockImplementationOnce(() => new Promise((res) => { resolveSummary = res }))

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(chatSummary).toHaveBeenCalledTimes(1))

    // A question sent while the summary is still being written.
    const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => { fireEvent.change(threadInput, { target: { value: "what did we decide?" } }) })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())

    // Now the summary lands FIRST, appending/resolving behind the live ask…
    await act(async () => { resolveSummary({ summary: "Targets mobile users." }) })
    await waitFor(() => expect(document.body.textContent).toContain("Targets mobile users."))

    // …and the in-flight question is still shown as in flight, not written off.
    expect(document.body.textContent).not.toContain("No response was generated for this message.")

    await act(async () => {
      resolveAsk({ answer: "We decided on system-follow.", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" })
    })
    await waitFor(() => expect(document.body.textContent).toContain("We decided on system-follow."))
  })

  it("summarizes once per artifact even if a completion double-fires", async () => {
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(chatSummary).toHaveBeenCalledTimes(1))

    // A plain ask afterwards must not re-trigger anything.
    const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => { fireEvent.change(threadInput, { target: { value: "what did we decide?" } }) })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(chatSummary).toHaveBeenCalledTimes(1)
  })
})
