// @vitest-environment jsdom
//
// ChatScreen — "Generate a PRD …" typed in the main chat is a COMMAND, not an
// ask. It must open the PRD as its own tab (from the brief's top insight, index
// 0) via openPrdTab — NOT hit the ask agent, which would answer with a raw
// prd-author HTML document dumped into the chat bubble. A normal question must
// still go to the ask agent unchanged.
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

// A controllable brief mock so a test can supply insights (or none).
const { briefCurrent } = vi.hoisted(() => ({ briefCurrent: vi.fn() }))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: briefCurrent },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
const loadPrdById = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 796, title: "Loaded PRD", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromBacklog: vi.fn().mockResolvedValue({ ok: true, prd: { prd_id: 88, title: "B", metaLine: "", sections: [] } }),
  loadPrdById: (...args: unknown[]) => loadPrdById(...args),
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
// `?new=1` puts ChatScreen on its OWN new-chat landing (its landing composer),
// not the default brief tab (which renders <BriefChat/> with its own composer).
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
  briefCurrent.mockReset()
  briefCurrent.mockResolvedValue({ id: 7, insights: [{ title: "Enterprise expansion is stalled" }] })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — 'Generate a PRD' is a command, not an ask", () => {
  it("opens the PRD tab from the brief's 1st insight and does NOT hit the ask agent", async () => {
    renderChat()
    await typeAndSend("Generate a PRD for our top product opportunity.")

    // Resolved the current brief and drove the generate flow (openPrdTab →
    // ChatScreen consumes pendingPrdTab → runPrdGeneration for insight index 0)…
    await waitFor(() => expect(briefCurrent).toHaveBeenCalledWith("acme"))
    await waitFor(() => expect(runPrdGeneration).toHaveBeenCalled())
    expect(runPrdGeneration.mock.calls[0][0]).toMatchObject({ briefId: 7, insightIndex: 0 })
    // …and it NEVER went to the ask agent (no raw prd-author HTML dump in chat).
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("does not open a PRD or hit the ask agent when there is no brief yet", async () => {
    briefCurrent.mockResolvedValue({ id: 7, insights: [] })
    renderChat()
    await typeAndSend("write a prd")

    // Checked the brief, found no insights → bailed (a toast is shown), and
    // crucially never regenerated a PRD nor fell through to the ask agent.
    await waitFor(() => expect(briefCurrent).toHaveBeenCalled())
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  it("routes a normal question to the ask agent unchanged", async () => {
    renderChat()
    await typeAndSend("Why did enterprise churn spike last month?")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(runPrdGeneration).not.toHaveBeenCalled()
    expect(briefCurrent).not.toHaveBeenCalled()
  })
})
