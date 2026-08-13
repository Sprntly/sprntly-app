// @vitest-environment jsdom
//
// ChatScreen — the assign_tickets envelope: "assign this ticket to Dave".
//
// The planner's verdict dispatches POST /v1/tickets/assign-plan against the
// thread's PRD. Pairs the request stated OUTRIGHT are applied immediately
// through the same PUT /fields the drawer's picker makes; everything left
// open steps through the dock's QuestionPopup — picks stay LOCAL until the
// last question settles (owner directive: finish all the questions before
// anything is sent), then every pair is written and a summary turn posts.
// Nothing here invents a write path: the popup's picks and the explicit pairs
// go through the one existing fields endpoint.
import * as React from "react"
import { act, cleanup, fireEvent, screen, waitFor, within, render } from "@testing-library/react"
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

const DAVE = { user_id: "u-dave", display_name: "Dave Okafor", email: "dave@acme.com", role: "member", avatar_url: null }
const PRIYA = { user_id: "u-priya", display_name: "Priya Nair", email: "priya@acme.com", role: "member", avatar_url: null }

const { resolveIntent, assignPlan, saveFields } = vi.hoisted(() => ({
  resolveIntent: vi.fn(),
  assignPlan: vi.fn(),
  saveFields: vi.fn().mockResolvedValue({ ok: true }),
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
    prdApi: {
      generateFromTask: vi.fn(), classifyCommand: vi.fn(),
      clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
    },
    chatIntentApi: { resolve: resolveIntent },
    ticketDataApi: { assignPlan, saveFields },
    ticketSetsApi: {
      byConversation: vi.fn().mockResolvedValue({ ticket_sets: [] }),
      get: vi.fn(),
    },
    artifactsApi: { chatSummary: vi.fn().mockResolvedValue({ summary: null }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

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
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: true } },
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

/** Open a thread (first turn is a plain answer), then arm the envelope so the
 *  NEXT send arrives as assign_tickets against PRD 501. */
async function openThreadThenArmAssign(instruction: string) {
  renderChat()
  await typeAndSend("what changed for enterprise users last week?")
  await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
  resolveIntent.mockResolvedValue({
    intent: "assign_tickets", confidence: 0.95, task: null, instruction,
    artifact_template_id: null, artifact_template_name: null,
    reason: "ownership change", source: "planner", prd_id: 501, prd_title: "Dark mode",
  })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  runAskGeneration.mockClear()
  saveFields.mockClear()
  assignPlan.mockReset()
  resolveIntent.mockReset()
  resolveIntent.mockResolvedValue({
    intent: "answer", confidence: 0.9, task: null, instruction: null,
    reason: "plain question", source: "llm", prd_id: null, prd_title: null,
  })
})
afterEach(() => { cleanup(); localStorage.clear(); protoMap.clear() })

describe("ChatScreen — assign_tickets dispatch", () => {
  it("an explicit pair is applied immediately and confirmed in the thread", async () => {
    assignPlan.mockResolvedValue({
      assignments: [{ ticket_key: "prd-501-s1", ticket_title: "Login flow", assignee: DAVE }],
      questions: [],
      note: "",
    })
    await openThreadThenArmAssign("assign the login ticket to Dave")
    await typeAndSend("assign the login ticket to Dave")

    await waitFor(() => expect(assignPlan).toHaveBeenCalledWith(501, "assign the login ticket to Dave"))
    // The write went through the ONE existing fields endpoint, full record.
    await waitFor(() => expect(saveFields).toHaveBeenCalledWith("prd-501-s1", { assignee: DAVE }))
    await waitFor(() => expect(document.body.textContent).toContain("“Login flow” → Dave Okafor"))
    // An assignment is not an ask.
    expect(runAskGeneration).toHaveBeenCalledTimes(1)
    expect(screen.queryByTestId("question-popup")).toBeNull()
  })

  it("open questions step through the popup; nothing writes until the last one settles", async () => {
    assignPlan.mockResolvedValue({
      assignments: [],
      questions: [
        {
          header: "Assignee", prompt: "Who should “Login flow” go to?",
          fixed: { kind: "ticket", ticket_key: "prd-501-s1", ticket_title: "Login flow" },
          options: [
            { value: "u-dave", label: "Dave Okafor", description: "dave@acme.com", assignee: DAVE },
            { value: "u-priya", label: "Priya Nair", description: "priya@acme.com", assignee: PRIYA },
          ],
        },
        {
          header: "Assignee", prompt: "Who should “Settings page” go to?",
          fixed: { kind: "ticket", ticket_key: "prd-501-s2", ticket_title: "Settings page" },
          options: [
            { value: "u-dave", label: "Dave Okafor", description: "dave@acme.com", assignee: DAVE },
            { value: "u-priya", label: "Priya Nair", description: "priya@acme.com", assignee: PRIYA },
          ],
        },
      ],
      note: "",
    })
    await openThreadThenArmAssign("assign these tickets to Dave and Priya")
    await typeAndSend("assign these tickets to Dave and Priya")

    // The stepper opens on question 1 of 2, wearing the plan's chip.
    await waitFor(() => expect(screen.getByTestId("question-popup")).toBeTruthy())
    expect(screen.getByTestId("question-popup-prompt").textContent).toBe("Who should “Login flow” go to?")

    // Click Dave → the pick is LOCAL; the stepper advances, nothing written.
    await act(async () => {
      fireEvent.click(screen.getAllByTestId("question-popup-option")[0])
    })
    expect(saveFields).not.toHaveBeenCalled()
    expect(screen.getByTestId("question-popup-prompt").textContent).toBe("Who should “Settings page” go to?")

    // Click Priya on the second → the batch settles → BOTH writes go out, then
    // the summary turn posts.
    await act(async () => {
      fireEvent.click(screen.getAllByTestId("question-popup-option")[1])
    })
    await waitFor(() => expect(saveFields).toHaveBeenCalledWith("prd-501-s1", { assignee: DAVE }))
    await waitFor(() => expect(saveFields).toHaveBeenCalledWith("prd-501-s2", { assignee: PRIYA }))
    await waitFor(() => expect(screen.queryByTestId("question-popup")).toBeNull())
    // The reply body types itself in — wait for the tail of the summary, not
    // just its first line.
    await waitFor(() => expect(document.body.textContent).toContain("All set — assigned:"))
    await waitFor(() => expect(document.body.textContent).toContain("“Login flow” → Dave Okafor"))
    await waitFor(() => expect(document.body.textContent).toContain("“Settings page” → Priya Nair"))
  })

  it("a skipped question leaves that ticket alone and the summary says so", async () => {
    assignPlan.mockResolvedValue({
      assignments: [],
      questions: [{
        header: "Assignee", prompt: "Who should “Login flow” go to?",
        fixed: { kind: "ticket", ticket_key: "prd-501-s1", ticket_title: "Login flow" },
        options: [{ value: "u-dave", label: "Dave Okafor", assignee: DAVE }],
      }],
      note: "",
    })
    await openThreadThenArmAssign("assign the tickets")
    await typeAndSend("assign the tickets")
    await waitFor(() => expect(screen.getByTestId("question-popup")).toBeTruthy())

    await act(async () => { fireEvent.click(screen.getByTestId("question-popup-skip")) })

    await waitFor(() => expect(screen.queryByTestId("question-popup")).toBeNull())
    expect(saveFields).not.toHaveBeenCalled()
    await waitFor(() => expect(document.body.textContent).toContain("No assignments made"))
  })

  it("a member-fixed question assigns the CLICKED ticket to that member", async () => {
    assignPlan.mockResolvedValue({
      assignments: [],
      questions: [{
        header: "Which ticket", prompt: "Which ticket should Dave get?",
        fixed: { kind: "member", assignee: DAVE },
        options: [
          { value: "prd-501-s1", label: "Login flow", description: "prd-501-s1" },
          { value: "prd-501-s2", label: "Settings page", description: "prd-501-s2" },
        ],
      }],
      note: "",
    })
    await openThreadThenArmAssign("give one of these to Dave")
    await typeAndSend("give one of these to Dave")
    await waitFor(() => expect(screen.getByTestId("question-popup")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getAllByTestId("question-popup-option")[1])
    })
    await waitFor(() => expect(saveFields).toHaveBeenCalledWith("prd-501-s2", { assignee: DAVE }))
  })

  it("a plan failure lands as an honest reply, never a dead turn", async () => {
    assignPlan.mockRejectedValue(new Error("backend down"))
    await openThreadThenArmAssign("assign the login ticket to Dave")
    await typeAndSend("assign the login ticket to Dave")

    await waitFor(() => expect(document.body.textContent).toContain("I couldn't plan that assignment"))
    expect(saveFields).not.toHaveBeenCalled()
  })

  it("assign_tickets with no PRD in the envelope or tab falls through to the grounded ask", async () => {
    renderChat()
    await typeAndSend("what changed for enterprise users last week?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(1))
    resolveIntent.mockResolvedValue({
      intent: "assign_tickets", confidence: 0.95, task: null,
      instruction: "assign the tickets to Dave",
      reason: "ownership change", source: "planner", prd_id: null, prd_title: null,
    })
    await typeAndSend("assign the tickets to Dave")

    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(2))
    expect(assignPlan).not.toHaveBeenCalled()
  })
})
