// @vitest-environment jsdom
//
// ChatScreen — a send always moves the viewport to the bottom.
//
// Reading back through a long thread leaves the viewport far above the fold.
// Hitting send from up there used to leave the user exactly where they were:
// the message they just typed, and the answer streaming in under it, were both
// off screen. The scroll was keyed on the thread GROWING, which happens a full
// intent round-trip after the send — and it animated, which sampled the
// "is the user pinned to the bottom?" check mid-flight and switched the
// stream-follow off on its way down.
//
// This suite pins the two halves of the fix: the jump happens on the SEND's own
// commit (while the dispatch is still in flight), and it's a jump, not a glide.
import * as React from "react"
import { act, cleanup, fireEvent, waitFor, within, render } from "@testing-library/react"
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

const { classifyCommand, clarifyTask, resolveIntent } = vi.hoisted(() => ({
  classifyCommand: vi.fn().mockResolvedValue({ is_prd_command: false, task: null, confidence: 0.9 }),
  clarifyTask: vi.fn().mockResolvedValue({ sufficient: true, questions: [], missing: [] }),
  resolveIntent: vi.fn(),
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
    prdApi: { generateFromTask: vi.fn(), classifyCommand, clarifyTask },
    chatIntentApi: { resolve: resolveIntent },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(),
  resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(),
  loadPrdById: vi.fn(),
}))

const ASK_RESULT = {
  answer: "Because enterprise admins asked for it.",
  sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "",
}
const runAskGeneration = vi.fn().mockResolvedValue(ASK_RESULT)
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

const ANSWER_ENVELOPE = {
  intent: "answer", confidence: 0.9, task: null, instruction: null,
  reason: "plain question", source: "llm", prd_id: null, prd_title: null,
}

const THREAD_PX = 4000
const VIEWPORT_PX = 600

/** Every scrollTo the component asked for, so "did it glide?" is answerable. */
const scrollCalls: { top: number; behavior?: ScrollBehavior }[] = []
const realScrollTo = Element.prototype.scrollTo

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

/** jsdom has no layout engine — scrollHeight/clientHeight are hard 0 and
 *  scrollTop is inert. Stand in a thread far taller than its viewport so the
 *  "am I at the bottom?" arithmetic has something real to measure. */
function stubScroller(el: HTMLElement) {
  let top = 0
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => THREAD_PX })
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => VIEWPORT_PX })
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => top,
    set: (v: number) => { top = v },
  })
  return el
}

function scroller(): HTMLElement {
  const el = document.querySelector(".od-center-scroll") as HTMLElement
  expect(el).toBeTruthy()
  return el
}

async function typeAndSend(selector: string, text: string) {
  const textarea = document.querySelector(selector) as HTMLTextAreaElement
  expect(textarea).toBeTruthy()
  await act(async () => { fireEvent.change(textarea, { target: { value: text } }) })
  const send = within(textarea.closest(".cx") as HTMLElement)
    .getByLabelText("Send")
  await act(async () => { fireEvent.click(send) })
}

/** Hold the intent envelope open so the pre-dispatch window — the multi-second
 *  gap in production — is observable, and hand back the release fn. */
function deferIntent() {
  let release!: (envelope: Record<string, unknown>) => void
  resolveIntent.mockImplementation(
    () => new Promise((res) => { release = res as (e: Record<string, unknown>) => void }),
  )
  return (envelope: Record<string, unknown>) => act(async () => { release(envelope) })
}

/** First exchange, settled end-to-end, so the surface is a real thread with a
 *  docked composer and NOTHING in flight — a turn still landing would grow the
 *  thread during the next send and scroll the viewport for its own reasons. */
async function openThread() {
  renderChat()
  resolveIntent.mockResolvedValue(ANSWER_ENVELOPE)
  await typeAndSend(".cx-input", "why are enterprise users asking for this?")
  await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
  await waitFor(() => expect(document.querySelector(".cx-input")).toBeTruthy())
  await waitFor(() => expect(document.body.textContent).toContain(ASK_RESULT.answer))
}

/** Read back through history: park the viewport at the top and let the thread's
 *  scroll handler see it, which is what un-pins the stream-follow. */
function scrollUpAway() {
  const el = stubScroller(scroller())
  el.scrollTop = 0
  fireEvent.scroll(el)
  scrollCalls.length = 0
  return el
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  protoMap.clear()
  scrollCalls.length = 0
  runAskGeneration.mockClear()
  classifyCommand.mockClear()
  clarifyTask.mockClear()
  resolveIntent.mockReset()
  resolveIntent.mockResolvedValue(ANSWER_ENVELOPE)
  ;(Element.prototype as unknown as { scrollTo: (o: ScrollToOptions) => void }).scrollTo =
    function (this: HTMLElement, o: ScrollToOptions) {
      scrollCalls.push({ top: o?.top ?? 0, behavior: o?.behavior })
      this.scrollTop = o?.top ?? 0
    }
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  protoMap.clear()
  Element.prototype.scrollTo = realScrollTo
})

describe("ChatScreen — sending from up the thread jumps to the newest message", () => {
  it("lands at the bottom on the send's own commit, before the dispatch resolves", async () => {
    await openThread()
    const el = scrollUpAway()
    expect(el.scrollTop).toBe(0)

    const release = deferIntent()
    await typeAndSend(".cx-input", "and what did they ask for exactly?")

    // The intent call is STILL in flight — no new turn, so nothing but the send
    // itself could have moved the viewport. The message is on screen and the
    // viewport is under it.
    expect(document.querySelector('[data-testid="pending-send"]')).toBeTruthy()
    expect(runAskGeneration).toHaveBeenCalledTimes(1) // the first ask only
    expect(el.scrollTop).toBe(THREAD_PX)

    await release(ANSWER_ENVELOPE)
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(2))
    expect(el.scrollTop).toBe(THREAD_PX)
  })

  it("jumps rather than animating, so a long thread doesn't glide past the send", async () => {
    await openThread()
    const el = scrollUpAway()

    deferIntent()
    await typeAndSend(".cx-input", "and what did they ask for exactly?")

    expect(scrollCalls.length).toBeGreaterThan(0)
    expect(scrollCalls.every((c) => c.behavior !== "smooth")).toBe(true)
    expect(scrollCalls.every((c) => c.top === THREAD_PX)).toBe(true)
    expect(el.scrollTop).toBe(THREAD_PX)
  })

  // End-state guard rather than a regression pin: jsdom's scrolling is inert, so
  // the animated scroll this replaced lands instantly here too and this passes
  // either way. It holds the invariant the other two protect the mechanism for —
  // once the answer lands, the viewport is on it.
  it("still sits at the newest content once the answer lands", async () => {
    await openThread()
    const el = scrollUpAway()

    runAskGeneration.mockResolvedValueOnce({ ...ASK_RESULT, answer: "SSO and audit logs." })
    const release = deferIntent()
    await typeAndSend(".cx-input", "and what did they ask for exactly?")
    await release(ANSWER_ENVELOPE)
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalledTimes(2))

    // The thread grew under the answer and the viewport tracked it.
    await waitFor(() => expect(document.body.textContent).toContain("SSO and audit logs."))
    expect(el.scrollTop).toBe(THREAD_PX)
  })
})
