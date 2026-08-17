// @vitest-environment jsdom
//
// ChatScreen — the main-chat PRD-fork project binding (Regression, the
// hardening for LOAD-BEARING RISK #1). A main-chat PRD generation that forks a
// project (server returns `project_id`) must RECORD that id onto shared
// content state (`content.activeProjectId`) so the content panel can surface a
// project-menu affordance — and must NOT navigate away from the current
// route. This supersedes the old away-nav mechanism (`router.push` guarded by
// `NavigationContext.skipArtifactReflectOnNavRef`): the entry-flow reshape
// keeps the user on `/` with the just-generated PRD open in the panel.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
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

const { generateFromTask, classifyCommand, clarifyTask } = vi.hoisted(() => ({
  generateFromTask: vi.fn().mockResolvedValue({
    prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3", project_id: null,
  }),
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
}))
vi.mock("../../../../lib/api", async () => {
  // Action dispatch is unconditional (no client fallback ladder) — the
  // "generate a PRD for …" trigger this file exercises only reaches
  // `prdApi.generateFromTask` via a `chatIntentApi.resolve` verdict, so this
  // stubs the planner with the SAME extraction the client used to run
  // inline (`lib/prd-commands`), matching the pattern already established in
  // `ChatScreen.prd-command.dom.test.tsx`.
  const { isPrdCommand, prdCommandTask } = await import("../../../../lib/prd-commands")
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    chatIntentApi: {
      resolve: vi.fn(async (q: string) => ({
        intent: isPrdCommand(q) ? "generate_prd" : "answer",
        confidence: 0.95,
        task: prdCommandTask(q),
        instruction: null,
        reason: "test stub",
        source: "planner",
        prd_id: null,
        prd_title: null,
      })),
    },
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [{ title: "x" }] }) },
    prdApi: { generateFromTask, classifyCommand, clarifyTask },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

const runPrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 77, title: "Generated PRD", metaLine: "", sections: [] },
})
const resumePrdGeneration = vi.fn().mockResolvedValue({
  ok: true, prd: { prd_id: 501, title: "Dark mode on mobile", metaLine: "", sections: [] },
})
vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: (...args: unknown[]) => runPrdGeneration(...args),
  resumePrdGeneration: (...args: unknown[]) => resumePrdGeneration(...args),
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

const pushSpy = vi.fn()
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushSpy, replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams("new=1"),
}))
vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
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
import { ContentProvider, useContent } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

// Exposes `content.activeProjectId` as text so the test can observe the bind
// without a second useContent() call anywhere in ChatScreen itself (same
// probe posture as the retired fork-nav suite's GuardProbe).
function ActiveProjectProbe() {
  const { content } = useContent()
  return React.createElement(
    "div",
    { "data-testid": "active-project-probe" },
    String(content.activeProjectId),
  )
}

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null,
        React.createElement(ChatScreen),
        React.createElement(ActiveProjectProbe),
      ),
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

function activeProjectValue(): string {
  return document.querySelector('[data-testid="active-project-probe"]')?.textContent ?? ""
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  pushSpy.mockClear()
  replaceSpy.mockClear()
  runAskGeneration.mockClear()
  runPrdGeneration.mockClear()
  resumePrdGeneration.mockClear()
  generateFromTask.mockClear()
  generateFromTask.mockResolvedValue({
    prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3", project_id: null,
  })
  classifyCommand.mockClear()
  clarifyTask.mockClear()
  clarifyTask.mockResolvedValue({ sufficient: true, questions: [], missing: [] })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — main-chat PRD-fork project binding (Regression — LOAD-BEARING #1)", () => {
  it("test_fork_binds_active_project_no_navigation — a fork (project_id set) records content.activeProjectId and never navigates", async () => {
    generateFromTask.mockResolvedValue({
      prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3", project_id: 555,
    })

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    // The success chain (resumePrdGeneration resolving → the synchronous
    // success block → bindActiveProject) contains NO further `await` — a
    // short-timeout waitFor is the regression proof: a reverted async/
    // setTimeout form would still be pending here and this assertion would
    // time out and fail.
    await waitFor(() => expect(activeProjectValue()).toBe("555"), { timeout: 100 })

    // The entry-flow reshape stays PUT — no push, no away-navigation of any
    // kind (ChatScreen's own pre-existing, unrelated `?new=1`-stripping
    // replace(`/`) is untouched by this change and is not asserted here).
    expect(pushSpy).not.toHaveBeenCalled()
    expect(replaceSpy).not.toHaveBeenCalledWith(expect.stringContaining("/projects"))
  })

  it("test_null_project_id_no_bind — a null project_id never sets activeProjectId and never navigates", async () => {
    generateFromTask.mockResolvedValue({
      prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3", project_id: null,
    })

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(resumePrdGeneration).toHaveBeenCalled())
    // Give the (non-fork) success chain the same settle window as the fork
    // case above — long enough to prove a STEADY absence, not a race.
    await act(async () => {
      await new Promise((r) => setTimeout(r, 100))
    })

    expect(pushSpy).not.toHaveBeenCalled()
    expect(activeProjectValue()).toBe("null")
  })

  it("no navigation call remains in bindActiveProject's own definition (source scan — a router.push/replace here is a stop-ship)", () => {
    const src = readFileSync(join(__dirname, "../ChatScreen.tsx"), "utf8")
    const start = src.indexOf("const bindActiveProject")
    expect(start).toBeGreaterThan(-1)
    // Scope the scan to the callback's own definition (a generous window —
    // the function body is short), not the whole file, which legitimately
    // uses router.push/replace elsewhere for unrelated navigation.
    const callbackBlock = src.slice(start, start + 400)
    expect(callbackBlock).not.toContain("router.push")
    expect(callbackBlock).not.toContain("router.replace")
    expect(callbackBlock).not.toContain("setTimeout")
  })
})
