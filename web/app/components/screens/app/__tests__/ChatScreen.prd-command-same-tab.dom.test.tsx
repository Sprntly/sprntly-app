// @vitest-environment jsdom
//
// ChatScreen — same-tab PRD generation. A PRD command typed in a REGULAR chat
// tab generates the PRD in THAT tab's artifacts panel: the command turn appends
// to the live thread (the conversation that motivated the PRD stays on screen
// next to it) and NO new tab spawns. The inline PRD card anchors to the COMMAND
// turn, mid-thread — not to thread[0] as on a command-opened tab. A tab already
// bound to a PRD keeps its binding (one PRD per tab): a new-topic command there
// still opens its own tab, exactly as before.
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

const { generateFromTask, classifyCommand, clarifyTask, importDoc, extractFile, storiesGenerate } = vi.hoisted(() => ({
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3" }),
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
  importDoc: vi.fn().mockResolvedValue({ prd_id: 42, status: "generating", title: "Imported PRD" }),
  extractFile: vi.fn().mockResolvedValue({ name: "Fraznet Enhancements.pptx", markdown: "## Slide 1\n\nFraznet MRT workflow" }),
  storiesGenerate: vi.fn().mockResolvedValue({ job_id: 1, status: "generating" }),
}))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: {
      ask: vi.fn(),
      skills: vi.fn().mockResolvedValue({ skills: [] }),
      extractFile: (...a: unknown[]) => extractFile(...a),
    },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [{ title: "x" }] }) },
    prdApi: { generateFromTask, classifyCommand, clarifyTask, importDoc, listInputQuestions: vi.fn().mockResolvedValue([]) },
    storiesApi: {
      getForPrd: vi.fn().mockResolvedValue({ status: "none", fresh: false, stories: [] }),
      generate: (...a: unknown[]) => storiesGenerate(...a),
    },
    attachmentsApi: { upload: vi.fn().mockResolvedValue(null) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      update: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
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

// Trailing arg of the PRD generate/import calls: the chat's conversation id,
// handed to the backend so it binds conversation → PRD itself (a chat that
// leaves the page mid-generation must still come back attached to its
// document). Same-tab treatment and that binding compose well — a command that
// stays in the current tab already HAS a conversation, so the id is a
// synchronous read and the backend binds at PRD-creation time, leaving no
// window at all. A command that opens its own new tab has no conversation row
// yet and passes null rather than waiting on one (the generation must never
// queue behind persistence); the link is written a moment later.
const NO_CONV_ID = null
const BOUND_CONV_ID = 1

// A PRD command grounds on the CONVERSATION (agent replies included), sent as
// authoritative source material so the document is about what was discussed
// rather than whatever the workspace KG retrieves. A command that spawns its own
// fresh tab has no thread behind it yet, so it still sends no docs.
const CONVERSATION_DOC = expect.arrayContaining([
  expect.objectContaining({ name: "Conversation (this chat)" }),
])

// The ContentPanel itself renders in AppShell (outside this tree) — observe
// which panel tab is open via the navigation context.
function PanelProbe() {
  const { contentPanelTab } = useNavigation()
  return React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "closed")
}

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null,
        React.createElement(ChatScreen),
        React.createElement(PanelProbe),
      ),
    ),
  )
}

// First message of a session goes through the landing composer…
async function typeAndSend(text: string) {
  const textarea = document.querySelector(".chat-home-composer-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".chat-home-composer") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

// …follow-ups go through the active tab's thread composer.
async function typeAndSendInThread(text: string) {
  const threadInput = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
  expect(threadInput).toBeTruthy()
  await act(async () => { fireEvent.change(threadInput, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".bc-composer") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

// One "Close tab" button per (closable) chat tab — the pinned brief tab and the
// "+" control carry none, so this counts exactly the spawned chat tabs.
const chatTabCount = () => screen.queryAllByTitle("Close tab").length

// Attach a document via whichever composer is on screen (landing or thread —
// only one renders at a time, each with its own hidden file input).
async function attachDoc(name = "Fraznet Enhancements.pptx"): Promise<File> {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  expect(input).toBeTruthy()
  const file = new File(["pptx-bytes"], name, {
    type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  })
  await act(async () => { fireEvent.change(input, { target: { files: [file] } }) })
  return file
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  runPrdGeneration.mockClear()
  generateFromTask.mockClear()
  classifyCommand.mockClear()
  importDoc.mockClear()
  extractFile.mockClear()
  storiesGenerate.mockClear()
  clarifyTask.mockClear()
  clarifyTask.mockResolvedValue({ sufficient: true, questions: [], missing: [] })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — same-tab PRD generation", () => {
  it("a PRD command mid-conversation generates in the SAME tab — no new tab, thread intact", async () => {
    renderChat()
    // A real conversation first → one chat tab with one Q&A turn.
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    await waitFor(() => expect(document.body.textContent).toContain("canned"))
    expect(chatTabCount()).toBe(1)

    // The command in the SAME thread…
    await typeAndSendInThread("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))

    // …did NOT spawn a second tab…
    expect(chatTabCount()).toBe(1)
    // …and the earlier conversation is still on screen, with the command turn
    // + ack appended after it.
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
    expect(document.body.textContent).toContain("generate a PRD for dark mode on mobile")
    expect(document.body.textContent).toContain("Generating a PRD for that")

    // The tab adopts the backend title once generation kicks off (same rename
    // the command-opened tab gets), so the strip reflects the PRD it now holds.
    await waitFor(() => expect(document.body.textContent).toContain("PRD · Dark mode on mobile"))
    expect(chatTabCount()).toBe(1)
  })

  it("the inline PRD card anchors to the COMMAND turn, not to the first message", async () => {
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))
    await typeAndSendInThread("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))

    const card = document.querySelector('[data-testid="chat-insight-msg"]')
    expect(card).toBeTruthy()
    const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble"))
    const commandBubble = bubbles.find((b) => b.textContent?.includes("generate a PRD for dark mode on mobile"))
    expect(commandBubble).toBeTruthy()
    // The card renders AFTER the command bubble in document order — under the
    // legacy thread[0] anchor it landed right after the FIRST message, above
    // the command the user just typed.
    expect(commandBubble!.compareDocumentPosition(card!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("a command on a tab ALREADY bound to a PRD still opens its own new tab (one PRD per tab)", async () => {
    renderChat()
    // Command from the landing → a command-opened PRD tab, as before.
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(document.body.textContent).toContain("PRD · Dark mode on mobile"))
    expect(chatTabCount()).toBe(1)

    // A NEW-topic command on that PRD-bound tab must not evict its PRD.
    await typeAndSendInThread("generate a PRD for password reset flows")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(2))
    // Its own NEW tab, so there is no conversation row to bind yet — and no
    // thread behind it either, so nothing to ground on but the topic itself.
    expect(generateFromTask).toHaveBeenLastCalledWith("password reset flows", false, CONVERSATION_DOC, NO_CONV_ID)
    await waitFor(() => expect(chatTabCount()).toBe(2))
  })

  it("clarify questions land in the SAME tab; the answer generates there too", async () => {
    clarifyTask.mockResolvedValueOnce({
      sufficient: false,
      missing: ["Target users"],
      questions: [{ prompt: "Who are the target users?", options: [], skip_default: null }],
    })
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    await typeAndSendInThread("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))
    // The question round stayed in the one tab, prior conversation intact.
    expect(chatTabCount()).toBe(1)
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
    expect(generateFromTask).not.toHaveBeenCalled()

    await typeAndSendInThread("enterprise admins")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(chatTabCount()).toBe(1)
    const combined = generateFromTask.mock.calls[0][0] as string
    expect(combined).toContain("dark mode on mobile")
    expect(combined).toContain("enterprise admins")
  })
})

// Every OTHER chat phrasing that generates a PRD must get the same-tab
// treatment too — they all funnel through the same two command flows, and these
// pin each entry point so a future dispatch change can't quietly regress one.
describe("ChatScreen — same-tab treatment across the other PRD command shapes", () => {
  it("'spec this out' (generic, no PRD noun) seeds from the conversation and stays in the tab", async () => {
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    await typeAndSendInThread("spec this out")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    // Seeded from THIS tab's conversation — and generated right here.
    expect(generateFromTask).toHaveBeenCalledWith("our checkout drops 42% of users at the payment step", false, CONVERSATION_DOC, BOUND_CONV_ID)
    expect(chatTabCount()).toBe(1)
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
  })

  it("an LLM-fallback phrasing (regex miss, classifier yes) generates in the same tab", async () => {
    classifyCommand.mockResolvedValueOnce({ is_prd_command: true, task: "checkout revamp", confidence: 0.92 })
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    await typeAndSendInThread("let's get a PRD going for the checkout revamp")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith("checkout revamp", false, CONVERSATION_DOC, BOUND_CONV_ID)
    expect(chatTabCount()).toBe(1)
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
  })

  it("a doc-attached 'generate a PRD' mid-conversation imports into the SAME tab", async () => {
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    const file = await attachDoc()
    await typeAndSendInThread("generate a PRD from this document")
    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme", BOUND_CONV_ID))

    expect(chatTabCount()).toBe(1)
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
    expect(document.body.textContent).toContain("Importing your document as a PRD")
    expect(runAskGeneration).toHaveBeenCalledTimes(1) // only the first, real question
  })

  it("a doc-attached tickets command mid-conversation stays in the tab and lands on Tickets", async () => {
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    const file = await attachDoc()
    await typeAndSendInThread("convert this PRD into tickets")
    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme", BOUND_CONV_ID))

    expect(chatTabCount()).toBe(1)
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
    // Once the imported PRD is ready, the panel lands on the Tickets tab and
    // user-stories generation kicks — same tab throughout.
    await waitFor(() => expect(storiesGenerate).toHaveBeenCalled())
    await waitFor(() =>
      expect(document.querySelector('[data-testid="panel-probe"]')?.textContent).toBe("tickets"))
    expect(chatTabCount()).toBe(1)
  })
})
