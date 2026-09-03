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

const { generateFromTask, classifyCommand, clarifyTask, importDoc, extractFile, storiesGenerate, resolveIntent } = vi.hoisted(() => ({
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3" }),
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
  importDoc: vi.fn().mockResolvedValue({ prd_id: 42, status: "generating", title: "Imported PRD" }),
  extractFile: vi.fn().mockResolvedValue({ name: "Fraznet Enhancements.pptx", markdown: "## Slide 1\n\nFraznet MRT workflow" }),
  storiesGenerate: vi.fn().mockResolvedValue({ job_id: 1, status: "generating" }),
  // The planner double. Hoisted (unlike this file's other inline stubs) so a
  // test can push a one-shot verdict for a phrasing the regex double below
  // cannot parse — the case the real planner exists for.
  resolveIntent: vi.fn(),
}))
vi.mock("../../../../lib/api", async () => {
  const { isPrdCommand, isTicketsCommand, prdCommandTask } = await import(
    "../../../../lib/prd-commands"
  )
  const IS_QUESTION = new RegExp(
    "^\\s*(?:(?:what|why|where|when|who|which|how)\\b"
    + "|(?:do|does|did|should|shall|is|are|can|could|would|will)"
    + "\\s+(?:we|i|the|this|that|it|there|a|an|our|my)\\b)",
    "i",
  )
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    // The PLANNER decides what a message asks for; this screen only executes the
    // verdict. These tests stub the verdict with the SAME extraction the client
    // used to run inline (`lib/prd-commands`), which is what their expectations
    // were written against — so what they assert is the FLOW ("given
    // generate_prd with this task, a PRD is generated with it"), unchanged.
    //
    // Whether a sentence IS a PRD command, and what its subject is, is now the
    // planner's judgement and is tested in backend/tests/test_ask_planner.py.
    // Reusing the old helper here is a test double standing in for a model, not
    // a rule the product still applies.
    chatIntentApi: {
      resolve: resolveIntent.mockImplementation(async (q: string) => ({
        // An interrogative is a QUESTION, never a request to build — the
        // planner reads the sentence, so this double must too (the old ladder
        // needed TICKETS_QUESTION_RE bolted on for the same case).
        intent: IS_QUESTION.test(q)
          ? "answer"
          : isTicketsCommand(q)
          ? "generate_tickets"
          : isPrdCommand(q)
          ? "generate_prd"
          : "answer",
        confidence: 0.95,
        // null for a pointer-not-a-name phrasing ("our top product
        // opportunity") — the planner leaves `task` empty there too, and the
        // screen asks what the PRD should cover.
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
  const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

// …follow-ups go through the active tab's thread composer.
async function typeAndSendInThread(text: string) {
  const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(threadInput).toBeTruthy()
  await act(async () => { fireEvent.change(threadInput, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
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
    // Trailing `undefined` is the uploaded FORMAT the user asked for — none
    // here, which means the company's active format, exactly as before.
    expect(generateFromTask).toHaveBeenLastCalledWith(
      "password reset flows", false, CONVERSATION_DOC, NO_CONV_ID, undefined,
    )
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
    expect(generateFromTask).toHaveBeenCalledWith("our checkout drops 42% of users at the payment step", false, CONVERSATION_DOC, BOUND_CONV_ID, undefined)
    expect(chatTabCount()).toBe(1)
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
  })

  it("a phrasing only the planner recognizes (regex-unparseable) generates in the same tab", async () => {
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    // "let's get a PRD going for…" defeats every pattern the old ladder had and
    // used to reach its haiku-classifier tier; the planner subsumed that tier.
    // The regex DOUBLE above can't parse it either, so this test injects the
    // verdict the real planner returns for it — the classification itself is
    // locked by backend/tests/test_ask_planner.py, while this test pins what
    // the screen DOES with it: generate in the same tab, with the planner's
    // task, not the raw message.
    resolveIntent.mockResolvedValueOnce({
      intent: "generate_prd", confidence: 0.92, task: "checkout revamp",
      instruction: null, reason: "planner", source: "planner", prd_id: null, prd_title: null,
    })
    await typeAndSendInThread("let's get a PRD going for the checkout revamp")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith("checkout revamp", false, CONVERSATION_DOC, BOUND_CONV_ID, undefined)
    expect(chatTabCount()).toBe(1)
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
  })

  it("a doc-attached 'generate a PRD' mid-conversation imports into the SAME tab", async () => {
    renderChat()
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    const file = await attachDoc()
    await typeAndSendInThread("generate a PRD from this document")
    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme", BOUND_CONV_ID, undefined))

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
    await waitFor(() => expect(importDoc).toHaveBeenCalledWith(file, "acme", BOUND_CONV_ID, undefined))

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


// ── A report promised and not delivered takes its panel back down ────────────
// `/v1/chat/intent` decides `report` BEFORE the answer path runs, so the two
// can disagree: a report pipeline declines (no connected call source), or the
// question turns out to be one its query mode answers inline. The client opened
// a Reports panel on the promise, and clearing the "generating" flag left it on
// screen reading "No reports in this chat" beside a finished answer.
//
// Reported as: "it's actually returning an answer, but it opens the panel
// also… the panel says no reports in the chat".
describe("ChatScreen — a report run that answers in the thread instead", () => {
  it("closes the Reports panel when no report was produced", async () => {
    resolveIntent.mockResolvedValueOnce({
      intent: "answer", confidence: 0.9, task: null, instruction: null,
      reason: "a calls question", source: "planner", prd_id: null, prd_title: null,
      report: true,
    })
    renderChat()

    await typeAndSend("give me summary on last week's customer conversations")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())

    // The answer landed in the thread and carried no `_report`…
    await waitFor(() => expect(document.body.textContent).toContain("canned"))
    // …so the panel the promise opened is gone, rather than sitting empty.
    await waitFor(() =>
      expect(screen.getByTestId("panel-probe").textContent).toBe("closed"))
  })

  it("keeps the panel when the answer IS the report", async () => {
    resolveIntent.mockResolvedValueOnce({
      intent: "answer", confidence: 0.9, task: null, instruction: null,
      reason: "a report request", source: "planner", prd_id: null, prd_title: null,
      report: true,
    })
    runAskGeneration.mockResolvedValueOnce({
      answer: "## Voice of customer", sources: [], follow_ups: [], key_points: [],
      citations: [], confidence: 1, unanswered: "", _report: true,
    })
    renderChat()

    await typeAndSend("give me a voice of customer report for last week")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    await waitFor(() =>
      expect(document.body.textContent).toContain("Voice of customer"))

    expect(screen.getByTestId("panel-probe").textContent).toBe("reports")
  })
})

// ── The reader wandered off while the planner was thinking ──────────────────
// The intent round-trip is SECONDS long (a real one has been measured at 13s
// with a PDF folded in). Switching tabs inside that window used to hand the
// generation to whatever tab was active when the envelope landed: the command
// executors resolve their target from the LIVE active tab, so the PRD was built
// in — and took over — a blank tab the user had just opened, conversation and
// all, while the thread that asked for it stayed empty.
describe("ChatScreen — a PRD command whose planner returns after a tab switch", () => {
  it("generates in the tab it was typed in, without stealing the tab the user moved to", async () => {
    renderChat()
    // Tab A: a real conversation, then the command.
    await typeAndSend("our checkout drops 42% of users at the payment step")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    await waitFor(() => expect(document.body.textContent).toContain("canned"))

    // Hold the planner open for exactly this send, so the tab switch below
    // happens INSIDE the classify window rather than after it.
    let release!: () => void
    resolveIntent.mockImplementationOnce(
      () => new Promise((resolve) => {
        release = () => resolve({
          intent: "generate_prd", confidence: 0.95, task: "dark mode on mobile",
          instruction: null, reason: "test stub", source: "planner",
          prd_id: null, prd_title: null,
        })
      }),
    )
    await typeAndSendInThread("generate a PRD for dark mode on mobile")
    expect(generateFromTask).not.toHaveBeenCalled()

    // …and the user opens a fresh tab while waiting.
    // The strip's own "+" (the sidebar has a same-labelled control that
    // navigates instead of opening a tab here).
    const newTabBtn = document.querySelector(".chat-tab")!
      .parentElement!.querySelector('button[aria-label="New chat"]') as HTMLButtonElement
    await act(async () => { fireEvent.click(newTabBtn) })
    expect(chatTabCount()).toBe(2)

    await act(async () => { release() })
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))

    // The new tab is untouched: still active, still empty, no panel over it.
    const activeTab = document.querySelector('.chat-tab[data-tab-active="true"]')
    expect(activeTab?.textContent).toContain("New chat")
    expect(document.body.textContent).not.toContain("generate a PRD for dark mode on mobile")
    expect(screen.getByTestId("panel-probe").textContent).toBe("closed")
    expect(chatTabCount()).toBe(2)

    // Going back to the tab that asked shows the command, its ack, and the
    // PRD panel — the ordinary refocus route, no special case.
    const prdTab = Array.from(document.querySelectorAll(".chat-tab"))
      .find((t) => t.textContent?.includes("PRD · Dark mode on mobile"))
    expect(prdTab).toBeTruthy()
    await act(async () => { fireEvent.click(prdTab!) })
    expect(document.body.textContent).toContain("our checkout drops 42% of users at the payment step")
    expect(document.body.textContent).toContain("generate a PRD for dark mode on mobile")
    expect(document.body.textContent).toContain("Generating a PRD for that")
    await waitFor(() => expect(screen.getByTestId("panel-probe").textContent).toBe("prd"))
  })
})


// ── A panel belongs to the tab that asked for it ─────────────────────────────
// The owner's rule, verbatim: "when you're in a tab doing something that would
// bring up a panel, and you switch to another tab while it is coming up, don't
// open it over the new tab — that tab has not asked for any artifact. Hold it,
// and show it when you switch back."
//
// Both halves matter and they fail differently. Opening over the new tab shows
// a thread an artifact it never asked for; simply DROPPING the request would
// leave the thread that did the work with nothing to come back to.
describe("ChatScreen — a panel that becomes ready while the reader is elsewhere", () => {
  it("does not open over the tab the reader moved to, and opens when they return", async () => {
    // Hold the import open so the tab switch lands inside the window the panel
    // becomes ready in — the real one is the PRD build, ~80 seconds.
    let release!: (v: { prd_id: number; status: string; title: string }) => void
    importDoc.mockImplementationOnce(
      () => new Promise((resolve) => { release = resolve }),
    )
    renderChat()

    // Tab A: a doc-attached tickets command — "convert this into tickets" —
    // which lands the panel on Tickets once the PRD is ready.
    await attachDoc()
    await typeAndSend("turn this into tickets")
    await waitFor(() => expect(importDoc).toHaveBeenCalled())

    // …and the reader opens a new tab while it works.
    const newTabBtn = document.querySelector(".chat-tab")!
      .parentElement!.querySelector('button[aria-label="New chat"]') as HTMLButtonElement
    await act(async () => { fireEvent.click(newTabBtn) })
    const activeBefore = document.querySelector('.chat-tab[data-tab-active="true"]')
    expect(activeBefore?.textContent).toContain("New chat")

    await act(async () => {
      release({ prd_id: 42, status: "generating", title: "Imported PRD" })
    })
    // The import resolved, the PRD landed, and user-stories generation kicked —
    // i.e. the flow reached the exact line that used to open the panel.
    await waitFor(() => expect(storiesGenerate).toHaveBeenCalled())

    // The fresh tab asked for nothing, so nothing opens over it.
    expect(screen.getByTestId("panel-probe").textContent).toBe("closed")

    // Going back to the tab that DID ask shows it — the request was held, not
    // dropped.
    // Whichever tab is NOT the fresh "New chat" and not the pinned brief — the
    // strip prints its title from the command that opened it.
    const askingTab = Array.from(document.querySelectorAll(".chat-tab"))
      .find((t) => !t.hasAttribute("data-tab-pinned") && !t.textContent?.includes("New chat"))
    expect(askingTab, `tabs: ${Array.from(document.querySelectorAll(".chat-tab")).map((t) => t.textContent).join(" | ")}`).toBeTruthy()
    await act(async () => { fireEvent.click(askingTab!) })
    await waitFor(() =>
      expect(screen.getByTestId("panel-probe").textContent).toBe("tickets"))
  })
})
