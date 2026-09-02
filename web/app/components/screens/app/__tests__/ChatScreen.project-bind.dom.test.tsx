// @vitest-environment jsdom
//
// ChatScreen — the main-chat PRD-fork project binding (client decision D1,
// 2026-09-02: seamless landing; refined 2026-09-02 per product-owner review).
// A main-chat PRD generation that forks a project (server returns
// `project_id`) must RECORD that id onto shared content state
// (`content.activeProjectId`, so the content panel's header pill stays
// correct) AND ALWAYS navigate the user straight into the project with the
// just-created PRD open (`/projects?id=<id>&prd=<id>&chat=individual`) — no
// busy-guard: the user initiated the generate, so a focused composer is the
// resting state, not "busy". Any text the user had half-typed for their next
// message travels with them (`content.pendingComposerDraft`), since the
// main-chat conversation IS (server-side) the project's private chat going
// forward. `/` and `/projects` share the same `(app)` layout, so this is an
// SPA transition (`router.push`), not a full reload.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { act, cleanup, fireEvent, render, waitFor, within } from "@testing-library/react"
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

// Exposes `content.activeProjectId`/`content.pendingComposerDraft` as text so
// the test can observe the bind + hand-off without a second useContent() call
// anywhere in ChatScreen itself (same probe posture as the retired fork-nav
// suite's GuardProbe).
function ActiveProjectProbe() {
  const { content } = useContent()
  return React.createElement(
    React.Fragment,
    null,
    React.createElement(
      "div", { "data-testid": "active-project-probe" }, String(content.activeProjectId),
    ),
    React.createElement(
      "div", { "data-testid": "pending-draft-probe" }, content.pendingComposerDraft ?? "",
    ),
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
function pendingDraftValue(): string {
  return document.querySelector('[data-testid="pending-draft-probe"]')?.textContent ?? ""
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

describe("ChatScreen — main-chat PRD-fork project binding + seamless landing (D1)", () => {
  it("test_fork_binds_active_project_and_always_navigates — a fork (project_id set) records content.activeProjectId AND navigates into the project with the PRD open, even while the composer is focused (no busy-guard)", async () => {
    generateFromTask.mockResolvedValue({
      prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3", project_id: 555,
    })

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    // The composer auto-focuses on mount (`ChatComposer.tsx`) and nothing in
    // this flow blurs it, so it is STILL the focused element here — the
    // realistic default state for anyone who hasn't deliberately looked
    // away. Documents the precondition: this test proves the nav fires
    // regardless.
    expect(document.activeElement?.tagName).toBe("TEXTAREA")

    // The success chain (resumePrdGeneration resolving → the synchronous
    // success block → bindActiveProject) contains NO further `await` — a
    // short-timeout waitFor is the regression proof: a reverted async/
    // setTimeout form would still be pending here and this assertion would
    // time out and fail.
    await waitFor(() => expect(activeProjectValue()).toBe("555"), { timeout: 100 })

    // D1 (2026-09-02): the fork ALWAYS navigates the user straight into the
    // project with the just-generated PRD open — an SPA transition
    // (`router.push`, no reload) since `/` and `/projects` share the `(app)`
    // layout. No busy-guard: focus in the composer never suppresses this.
    expect(pushSpy).toHaveBeenCalledWith("/projects?id=555&chat=individual&prd=501")
  })

  it("test_null_project_id_no_bind_no_nav — a null project_id never sets activeProjectId and never navigates", async () => {
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

  it("test_draft_handoff_stashed_and_composer_cleared — a half-typed NEXT message in the composer is stashed on content.pendingComposerDraft and cleared from the main-chat composer on the fork-nav", async () => {
    let resolveGenerate!: (v: { prd_id: number; title: string; status: string; variant: string; project_id: number | null }) => void
    generateFromTask.mockImplementation(
      () => new Promise((resolve) => { resolveGenerate = resolve }),
    )

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))

    // While the PRD generates, the user starts typing their NEXT message
    // into the (new command tab's) composer.
    const textareaWhileGenerating = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textareaWhileGenerating).toBeTruthy()
    await act(async () => {
      fireEvent.change(textareaWhileGenerating, { target: { value: "also check the tablet layout" } })
    })

    await act(async () => {
      resolveGenerate({
        prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3", project_id: 555,
      })
    })
    await waitFor(() => expect(activeProjectValue()).toBe("555"), { timeout: 100 })

    // The half-typed draft MOVED: it is stashed on shared content for the
    // project chat to pick up, and the main-chat composer is cleared —
    // not duplicated, not lost.
    expect(pendingDraftValue()).toBe("also check the tablet layout")
    expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("")
  })

  it("test_no_draft_handoff_when_composer_empty — an empty composer never stashes a pendingComposerDraft", async () => {
    generateFromTask.mockResolvedValue({
      prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3", project_id: 555,
    })

    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(activeProjectValue()).toBe("555"), { timeout: 100 })

    expect(pendingDraftValue()).toBe("")
  })

  it("bindActiveProject's own definition ALWAYS calls router.push, with no document.activeElement busy-guard (source scan — proves the no-guard invariant is wired, not silently reintroduced)", () => {
    const src = readFileSync(join(__dirname, "../ChatScreen.tsx"), "utf8")
    const start = src.indexOf("const bindActiveProject")
    expect(start).toBeGreaterThan(-1)
    // Scope the scan to the callback's own definition (a generous window —
    // the function body is short), not the whole file, which legitimately
    // uses document.activeElement/router.push elsewhere for unrelated logic.
    const callbackBlock = src.slice(start, start + 900)
    expect(callbackBlock).toContain("router.push")
    expect(callbackBlock).not.toContain("document.activeElement")
    expect(callbackBlock).not.toContain("busy")
  })
})
