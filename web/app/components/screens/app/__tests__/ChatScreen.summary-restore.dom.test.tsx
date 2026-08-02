// @vitest-environment jsdom
//
// ChatScreen — artifact chat summaries SURVIVE history restore.
//
// The summary is persisted as a second consecutive assistant row ([user
// command, assistant ack, assistant summary]). The restore pairing loops used
// to walk user→next-assistant and silently DROP any assistant row that didn't
// immediately follow a user row — so a persisted summary survived in the DB
// but vanished from every reopened thread. Unconsumed assistant rows now
// restore as agent-only turns (empty query — the same convention the clarify
// gate's orphan turn uses live).
//
// Also pins the transcript-grounding filter: summaries must never feed back
// into the next PRD as fake requirements.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}
window.scrollTo = (() => {}) as typeof window.scrollTo

const SUMMARY_TEXT =
  "This PRD targets mobile end users struggling with night-time readability.\n\n" +
  "Use the View PRD button in this chat to reopen it anytime."

// A PRD-command conversation as the server holds it after the summary landed:
// command → ack → SUMMARY (two assistant rows in a row).
const TURNS_WITH_SUMMARY = {
  turns: [
    { id: 1, role: "user", content: "generate a PRD for dark mode" },
    { id: 2, role: "assistant", content: "Generating a PRD for that — it'll open in the panel on the right when ready. Use the View PRD button just below to reopen the panel anytime." },
    { id: 3, role: "assistant", content: SUMMARY_TEXT },
  ],
}

const listTurns = vi.fn().mockResolvedValue(TURNS_WITH_SUMMARY)
const { create, addTurn, byPrd } = vi.hoisted(() => ({
  create: vi.fn(),
  addTurn: vi.fn(),
  byPrd: vi.fn(),
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
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: (...args: unknown[]) => create(...args),
      addTurn: (...args: unknown[]) => addTurn(...args),
      byPrd: (...args: unknown[]) => byPrd(...args),
      listTurns: (...args: unknown[]) => listTurns(...args),
      update: vi.fn().mockResolvedValue({}),
    },
    prdApi: { importDoc: vi.fn() },
  }
})

vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn(),
  }),
}))

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(""),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({ loading: false, profile: null, workspace: null, refresh: async () => {} }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({ useAuth: () => ({ kind: "anonymous" }) }))

vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: {}, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen, conversationTranscriptDoc } from "../ChatScreen"

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  listTurns.mockReset()
  listTurns.mockResolvedValue(TURNS_WITH_SUMMARY)
  create.mockReset()
  create.mockResolvedValue({ id: 1 })
  addTurn.mockReset()
  addTurn.mockResolvedValue({})
  byPrd.mockReset()
  byPrd.mockResolvedValue({ conversation: null, turns: [] })
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen — the summary survives reopening from Chat history", () => {
  it("restores the second consecutive assistant row instead of dropping it", async () => {
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "generate a PRD for dark mode",
      fallbackTurns: [{ role: "user", content: "generate a PRD for dark mode" }],
    }))

    await act(async () => { renderScreen() })
    await waitFor(() => expect(listTurns).toHaveBeenCalledWith(42))

    // Both assistant messages are back: the ack paired to the command AND the
    // summary that followed it. The summary used to vanish here.
    await waitFor(() => {
      expect(document.body.textContent).toContain("Generating a PRD for that")
      expect(document.body.textContent).toContain("targets mobile end users struggling with night-time readability")
    })
    // The summary restored as an AGENT-ONLY turn — no phantom user bubble was
    // invented to carry it.
    const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble")).map((b) => b.textContent)
    expect(bubbles).toEqual(["generate a PRD for dark mode"])
  })

  it("drops empty assistant rows rather than restoring blank agent turns", async () => {
    listTurns.mockResolvedValue({
      turns: [
        { id: 1, role: "user", content: "hello" },
        { id: 2, role: "assistant", content: "hi" },
        { id: 3, role: "assistant", content: "   " },
      ],
    })
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 43, title: "hello", fallbackTurns: [{ role: "user", content: "hello" }],
    }))

    await act(async () => { renderScreen() })
    await waitFor(() => expect(document.body.textContent).toContain("hi"))
    // One agent body per real message — the whitespace row produced nothing.
    expect(document.body.textContent).not.toContain("No response was generated")
  })
})

describe("conversationTranscriptDoc — summaries stay out of PRD grounding", () => {
  const turn = (answer: string) => ({
    id: "t1",
    query: "",
    reply: { answer, sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" },
  })

  it("excludes all three artifact summaries via their pointer lines", () => {
    const thread = [
      { id: "u", query: "generate a PRD for dark mode", reply: undefined },
      turn("Summary of the PRD. Use the View PRD button in this chat to reopen it anytime."),
      turn("Summary of the evidence. Use the View Evidence button in this chat to reopen it anytime."),
      turn("Summary of the build. Use the View Prototype button in this chat to reopen it anytime."),
    ] as Parameters<typeof conversationTranscriptDoc>[0]
    const doc = conversationTranscriptDoc(thread)
    // The user's own words remain; every summary is stripped — fed back in,
    // they would read as fresh requirements.
    expect(doc?.content).toContain("generate a PRD for dark mode")
    expect(doc?.content).not.toContain("Summary of the PRD")
    expect(doc?.content).not.toContain("Summary of the evidence")
    expect(doc?.content).not.toContain("Summary of the build")
  })

  it("keeps ordinary assistant answers", () => {
    const thread = [
      { id: "u", query: "what drives churn?", reply: undefined },
      turn("Churn concentrates in week two."),
    ] as Parameters<typeof conversationTranscriptDoc>[0]
    expect(conversationTranscriptDoc(thread)?.content).toContain("Churn concentrates in week two.")
  })
})
