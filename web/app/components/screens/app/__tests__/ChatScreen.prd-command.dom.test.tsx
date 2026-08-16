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

const { generateFromTask, classifyCommand, clarifyTask, resolveIntent } = vi.hoisted(() => ({
  // The planner's verdict. Defaulted inside the api mock from the old
  // extraction helpers; individual tests override it per-case.
  resolveIntent: vi.fn(),
  generateFromTask: vi.fn().mockResolvedValue({ prd_id: 501, title: "Dark mode on mobile", status: "generating", variant: "v3" }),
  // Tier-2 LLM fallback (POST /v1/prd/classify-command). Default: not a command
  // — individual tests override per-case.
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  // Clarify-first gate (POST /v1/prd/clarify-task). Default: sufficient —
  // individual tests override to exercise the question loop.
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
}))
vi.mock("../../../../lib/api", async () => {
  const { isPrdCommand, isTicketsCommand, prdCommandTask } = await import(
    "../../../../lib/prd-commands"
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
      resolve: vi.fn(async (q: string, ...rest: unknown[]) => {
        const override = await resolveIntent(q, ...rest)
        if (override) return override
        return {
          intent: isTicketsCommand(q)
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
        }
      }),
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

// Trailing arg of the PRD generate calls: the chat's conversation id, handed to
// the backend so it binds conversation → PRD itself (a chat that leaves the page
// mid-generation must still come back attached to its document).
//
// A command typed on a FRESH surface has no conversation row yet, and the call
// must not wait for one — queueing every generation behind a persistence
// round-trip is the latency bug this flow avoids — so it goes out with null.
// A command typed MID-CONVERSATION stays in the current tab (#881), which
// already has its conversation, so the id is a synchronous read and the
// backend binds at PRD-creation time.
const NO_CONV_ID = null
const BOUND_CONV_ID = 1

// A PRD command grounds on the CONVERSATION, not just the task text: the thread
// (agent replies included) rides along as authoritative source material so the
// document is about what was discussed rather than whatever the workspace KG
// happens to retrieve. A command typed on a FRESH surface has no thread yet, so
// it still sends no docs.
const CONVERSATION_DOC = expect.arrayContaining([
  expect.objectContaining({ name: "Conversation (this chat)" }),
])

// Surfaces the current toast title — the Toast UI is mounted by AppShell, not in
// this isolated render, so this probe is how we observe the "ask for a topic"
// prompt.
function ToastProbe() {
  const { toast } = useNavigation()
  return React.createElement("div", { "data-testid": "toast-probe" }, toast?.title ?? "")
}

// The ContentPanel renders in AppShell, outside this tree — observe which tab is
// open (if any) straight from the navigation context instead.
function PanelProbe() {
  const { contentPanelTab } = useNavigation()
  return React.createElement("div", { "data-testid": "panel-probe" }, contentPanelTab ?? "closed")
}

function panelTab(): string {
  return document.querySelector('[data-testid="panel-probe"]')?.textContent ?? ""
}

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null,
        React.createElement(ChatScreen),
        React.createElement(ToastProbe),
        React.createElement(PanelProbe),
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

beforeEach(() => {
  localStorage.clear()
  // Tabs persist to sessionStorage — without clearing, a previous test's PRD
  // tab is restored into the next render and thread-composer selectors hit the
  // wrong tab.
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  runPrdGeneration.mockClear()
  generateFromTask.mockClear()
  classifyCommand.mockClear()
  clarifyTask.mockClear()
  clarifyTask.mockResolvedValue({ sufficient: true, questions: [], missing: [] })
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
    expect(generateFromTask).toHaveBeenCalledWith("dark mode on mobile", false, undefined, NO_CONV_ID, undefined)
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

  it("a novel command phrasing generates — with no second classifier call", async () => {
    // There used to be a TIER 2 here: when the client's regexes could not parse
    // a phrasing but the message mentioned a PRD, it spent an extra haiku call
    // (POST /v1/prd/classify-command) asking whether it was a command after all.
    // That tier existed only to paper over the regexes, and both are gone — the
    // planner reads the whole message and does not need a second opinion on it.
    //
    // The behaviour it protected is unchanged: a phrasing no pattern would have
    // caught still generates, and generates from the SUBJECT, not the sentence.
    resolveIntent.mockResolvedValueOnce({
      intent: "generate_prd", confidence: 0.92, task: "checkout revamp",
      instruction: null, reason: "asked for a spec", source: "planner",
      prd_id: null, prd_title: null,
    })
    renderChat()
    await typeAndSend("let's get a PRD going for the checkout revamp")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith("checkout revamp", false, undefined, NO_CONV_ID, undefined)
    expect(runAskGeneration).not.toHaveBeenCalled()
    // The extra round trip is gone, not merely unused.
    expect(classifyCommand).not.toHaveBeenCalled()
  })

  it("a PRD mention that is NOT a command falls through to the ask agent", async () => {
    renderChat()
    await typeAndSend("the requirements doc needs another pass from legal")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(generateFromTask).not.toHaveBeenCalled()
    expect(classifyCommand).not.toHaveBeenCalled()
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
    expect(generateFromTask).toHaveBeenCalledWith("dark mode on mobile", false, undefined, NO_CONV_ID, undefined)
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
    const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(threadInput).toBeTruthy()
    await act(async () => { fireEvent.change(threadInput, { target: { value: "generate a PRD" } }) })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => { fireEvent.click(sendBtn) })

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    // Mid-conversation, so the tab already carries its conversation — and the
    // thread rides along as grounding material (CONVERSATION_DOC), which is what
    // keeps the PRD about what was discussed instead of the workspace at large.
    expect(generateFromTask).toHaveBeenCalledWith(
      "our checkout drops 42% of users at the payment step", false, CONVERSATION_DOC, BOUND_CONV_ID,
     undefined,)
    expect(runPrdGeneration).not.toHaveBeenCalled()
  })
})

async function typeAndSendInThread(text: string) {
  const threadInput = document.querySelector(".cx-input") as HTMLTextAreaElement
  expect(threadInput).toBeTruthy()
  await act(async () => { fireEvent.change(threadInput, { target: { value: text } }) })
  const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
  await act(async () => { fireEvent.click(sendBtn) })
}

describe("ChatScreen — clarify-first sufficiency gate", () => {
  const QUESTIONS = {
    sufficient: false,
    missing: ["Target users", "Success criteria"],
    questions: [
      { prompt: "Who are the target users?", options: ["Admins", "End users"], skip_default: "all end users" },
      { prompt: "How will you measure success?", options: [] },
    ],
  }

  it("an insufficient task asks questions INSTEAD of generating; the answer then generates with the details folded in", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    // The gate ran over the extracted task…
    await waitFor(() => expect(clarifyTask).toHaveBeenCalledWith("dark mode on mobile", undefined))
    // …questions appear (in the dock's popup stepper), and NOTHING generated
    // yet. Skipping is per-question in the head; the popup carries no
    // skip-them-all footer.
    await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))
    expect(screen.queryByTestId("question-popup-skip-all")).toBeNull()
    // Skips are informed: the stated default rides with its question.
    expect(document.body.textContent).toContain("if skipped, I'll assume: all end users")
    expect(generateFromTask).not.toHaveBeenCalled()

    // The user answers in the same tab → generation runs with the combined task.
    await typeAndSendInThread("admins only; success = 30% fewer support tickets")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    const combined = generateFromTask.mock.calls[0][0] as string
    expect(combined).toContain("dark mode on mobile")
    expect(combined).toContain("Additional details from the user:")
    expect(combined).toContain("admins only; success = 30% fewer support tickets")
    // The answer was NOT misrouted to the ask agent or a new command.
    expect(runAskGeneration).not.toHaveBeenCalled()
  })

  // The acknowledgment used to be written the instant the command was seen, and
  // only THEN did the gate discover the task was too thin — so "Generating a PRD
  // for that — it'll open in the panel on the right…" sat above the questions
  // that contradicted it. It is now deferred until the gate settles.
  describe("the deferred acknowledgment", () => {
    it("shows no acknowledgment when the answer turns out to be questions", async () => {
      clarifyTask.mockResolvedValueOnce(QUESTIONS)
      renderChat()
      await typeAndSend("generate a PRD for dark mode on mobile")
      await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))

      expect(document.body.textContent).not.toContain("Generating a PRD for that")
      expect(document.body.textContent).not.toContain("View PRD button")
    })

    it("DOES acknowledge once generation actually starts — deferred, not deleted", async () => {
      renderChat() // default clarifyTask mock: sufficient
      await typeAndSend("generate a PRD for dark mode on mobile")

      await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
      expect(document.body.textContent).toContain("Generating a PRD for that")
    })

    it("never leaves the command turn with no reply when the generate call fails", async () => {
      // Deferring the reply creates a way for the turn to end up with none at
      // all. The gate passed, so the ack was settled before the POST went out —
      // a later failure must not regress the turn to "No response was generated".
      generateFromTask.mockRejectedValueOnce(new Error("gateway down"))
      renderChat()
      await typeAndSend("generate a PRD for dark mode on mobile")

      await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
      await waitFor(() => expect(document.body.textContent).toContain("Generating a PRD for that"))
      expect(document.body.textContent).not.toContain("No response was generated for this message.")
    })
  })

  it("'generate now' skips the questions and generates from the ORIGINAL task", async () => {
    clarifyTask.mockResolvedValueOnce(QUESTIONS)
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")
    await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))

    await typeAndSendInThread("generate now")
    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask.mock.calls[0][0]).toBe("dark mode on mobile")
  })

  // Reported twice by the user, from the same window: the moment between "…I'm
  // generating a PRD" and the agent's actual response. It used to be dead air
  // with an empty rail beside it — and an empty rail is what the old
  // "load the workspace's latest PRD" fallback filled with a completely
  // unrelated document, which read as the answer to the request.
  describe("the wait after a PRD command", () => {
    it("keeps a thinking indicator running until the questions land — and never opens an empty rail", async () => {
      // Hold the sufficiency check unresolved so the in-flight window is
      // observable rather than a microtask blur.
      let resolveClarify!: (v: unknown) => void
      clarifyTask.mockImplementationOnce(() => new Promise((res) => { resolveClarify = res as (v: unknown) => void }))

      renderChat()
      await typeAndSend("generate a PRD for dark mode on mobile")

      // Visibly working — but NOT acknowledged, because this may resolve to
      // QUESTIONS rather than a document. Silence here is one bug; "Generating a
      // PRD for that…" sitting above the questions that follow it is the other,
      // so the window is carried by the indicator alone.
      expect(document.body.textContent).not.toContain("Generating a PRD for that")
      expect(document.querySelector('[data-testid="prd-command-thinking"]')).toBeTruthy()
      // The rail stays shut: this may resolve to questions, not a document, and
      // an empty PRD panel next to a question is worse than no panel.
      expect(panelTab()).toBe("closed")

      await act(async () => { resolveClarify(QUESTIONS) })

      await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))
      // The questions ARE the response — the indicator stops with them.
      expect(document.querySelector('[data-testid="prd-command-thinking"]')).toBeNull()
      expect(panelTab()).toBe("closed")
      expect(generateFromTask).not.toHaveBeenCalled()
    })

    it("hands the indicator over to the generating rail when the task is sufficient", async () => {
      renderChat() // default clarifyTask mock: sufficient
      await typeAndSend("generate a PRD for dark mode on mobile")

      await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
      // Generation is really underway now, so the rail earns its place and the
      // chat indicator hands off rather than stacking a second spinner.
      await waitFor(() => expect(panelTab()).toBe("prd"))
      expect(document.querySelector('[data-testid="prd-command-thinking"]')).toBeNull()
    })

    it("posts the questions as an agent-only turn — no phantom user header above them", async () => {
      clarifyTask.mockResolvedValueOnce(QUESTIONS)
      renderChat()
      await typeAndSend("generate a PRD for dark mode on mobile")
      await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))

      // The clarify turn carries an empty `query`, so it must render with NO
      // user header. The header used to be unconditional, which put the user's
      // name and avatar above questions they never asked — reading as a blank
      // message of their own sitting in their own thread.
      const heads = document.querySelectorAll(".bc-user-head")
      const bubbles = document.querySelectorAll(".bc-user-bubble")
      expect(heads.length).toBe(1)
      expect(bubbles.length).toBe(1)
      expect(bubbles[0].textContent).toContain("generate a PRD for dark mode on mobile")
    })

    it("opens the rail once the user answers the questions", async () => {
      clarifyTask.mockResolvedValueOnce(QUESTIONS)
      renderChat()
      await typeAndSend("generate a PRD for dark mode on mobile")
      await waitFor(() => expect(document.body.textContent).toContain("Who are the target users?"))
      expect(panelTab()).toBe("closed")

      // Answering is what starts the generation — and what opens the rail. The
      // deferral must not strand the panel shut for the actual document.
      await typeAndSendInThread("generate now")
      await waitFor(() => expect(panelTab()).toBe("prd"))
    })
  })

  it("a sufficient task generates immediately (the gate ran, no questions)", async () => {
    renderChat() // default clarifyTask mock: sufficient
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(clarifyTask).toHaveBeenCalledTimes(1)
    expect(document.body.textContent).not.toContain("Who are the target users?")
  })

  it("a gate failure fails OPEN — generation proceeds as if sufficient", async () => {
    clarifyTask.mockRejectedValueOnce(new Error("gateway down"))
    renderChat()
    await typeAndSend("generate a PRD for dark mode on mobile")

    await waitFor(() => expect(generateFromTask).toHaveBeenCalledTimes(1))
    expect(generateFromTask).toHaveBeenCalledWith("dark mode on mobile", false, undefined, NO_CONV_ID, undefined)
  })
})
