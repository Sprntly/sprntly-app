// @vitest-environment jsdom
//
// ChatScreen PERSIST-FOLD regression (R2) — what a page reload restores from the
// sessionStorage snapshot (distinct from the live pending-send overlay).
//
//   (a) PRE-ASK-ID window: between the optimistic send and the real awaiting
//       turn (the multi-second intent-classify gap), the just-sent question
//       lives ONLY in the transient `pendingSend` overlay. The persist effect
//       folds it into the SAVED snapshot (marked `interrupted`, id `pending-…`)
//       so a reload in that window restores the question instead of a blank.
//   (b) Once `resolveSendTarget` seeds the REAL awaiting turn, the fold is
//       SKIPPED — the snapshot carries the question exactly once (the awaiting/
//       settled turn), never a duplicate.
//   (c) A `summaryPending && !reply` placeholder is NEVER persisted — its
//       in-flight call dies with the page, so restoring it would strand a
//       "Summarizing…" spinner forever.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined") window.scrollTo = () => {}
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: /prefers-reduced-motion/.test(query), media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const { resolveIntent } = vi.hoisted(() => ({ resolveIntent: vi.fn() }))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 7, insights: [] }) },
    chatIntentApi: { resolve: resolveIntent },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
    },
  }
})

// Parked by default so the awaiting turn stays in flight (busy) after dispatch —
// case (b) reads the snapshot while the ask is awaiting.
let resolveAsk: ((v: unknown) => void) | undefined
const runAskGeneration = vi.fn(() => new Promise((res) => { resolveAsk = res }))
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
  useSearchParams: () => new URLSearchParams(""),
}))
// Flag ON — the intent-envelope call is the await the pre-ask-id window spans.
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
vi.mock("../../../design-agent/useBriefPrototypeMap", () => ({
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, error: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

const TABS_KEY = "sprntly_chat_tabs_anon_acme"
const ACTIVE_KEY = "sprntly_chat_active_tab_anon_acme"

const ANSWER_ENVELOPE = {
  intent: "answer", confidence: 0.9, task: null, instruction: null,
  reason: "plain question", source: "llm", prd_id: null, prd_title: null,
}

type SnapTurn = { id: string; query?: string; reply?: unknown; summaryPending?: boolean; interrupted?: boolean }

function seedActiveTab(thread: SnapTurn[]) {
  sessionStorage.setItem(
    TABS_KEY,
    JSON.stringify([{ id: "tab-1", title: "Seeded chat", thread, dbConvId: null, briefMeta: null }]),
  )
  sessionStorage.setItem(ACTIVE_KEY, "tab-1")
}

function savedThread(): SnapTurn[] {
  const saved = JSON.parse(sessionStorage.getItem(TABS_KEY) ?? "[]") as Array<{ id: string; thread: SnapTurn[] }>
  return saved.find((t) => t.id === "tab-1")?.thread ?? []
}

function renderChat() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

/** Hold the intent envelope open (the pre-dispatch window); returns a release fn. */
function deferIntent() {
  let release!: (envelope: Record<string, unknown>) => void
  resolveIntent.mockImplementation(() => new Promise((res) => { release = res as (e: Record<string, unknown>) => void }))
  return (envelope: Record<string, unknown>) => act(async () => { release(envelope) })
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
  runAskGeneration.mockClear()
  resolveAsk = undefined
  resolveIntent.mockReset()
  resolveIntent.mockResolvedValue(ANSWER_ENVELOPE)
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
  resolveAsk?.({ answer: "late", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" })
})

describe("ChatScreen — persist-fold: what a reload restores from the snapshot", () => {
  it("(a) a pre-ask-id reload restores the in-flight question (folded into the saved snapshot)", async () => {
    seedActiveTab([])
    renderChat()
    await screen.findByLabelText("Send")

    const release = deferIntent()
    await typeAndSend("why are enterprise users asking for this?")

    // The intent call is parked: no awaiting turn exists yet (the pre-dispatch
    // window). The question lives only in `pendingSend` — but the SAVED snapshot
    // folds it in so a reload here restores it.
    expect(runAskGeneration).not.toHaveBeenCalled()
    await waitFor(() => {
      const t = savedThread()
      const pending = t.find((x) => x.query === "why are enterprise users asking for this?")
      expect(pending).toBeTruthy()
      expect(pending?.interrupted).toBe(true)
      expect(pending?.id.startsWith("pending-")).toBe(true)
    })

    void release // parked intentionally; afterEach settles
  })

  it("(b) once the real awaiting turn is seeded the fold is skipped — the question is saved exactly once", async () => {
    seedActiveTab([])
    renderChat()
    await screen.findByLabelText("Send")

    // Intent resolves immediately → resolveSendTarget seeds the awaiting turn and
    // pendingSend clears; runAskGeneration is parked so the turn stays awaiting.
    await typeAndSend("what changed in onboarding last week?")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())

    await waitFor(() => {
      const t = savedThread()
      const matching = t.filter((x) => x.query === "what changed in onboarding last week?")
      // Exactly one turn carries the question — the seeded awaiting turn, NOT a
      // second `pending-…` fold on top of it.
      expect(matching.length).toBe(1)
      expect(matching[0].id.startsWith("pending-")).toBe(false)
    })
  })

  it("(c) a summaryPending && !reply turn is never persisted (no forever-spinner on restore)", async () => {
    seedActiveTab([
      { id: "turn-real", query: "first question", reply: { answer: "first answer", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } },
      { id: "turn-summary", query: "", summaryPending: true },
    ])
    renderChat()
    await screen.findByLabelText("Send")

    await waitFor(() => {
      const t = savedThread()
      // The real answered turn survives…
      expect(t.some((x) => x.id === "turn-real")).toBe(true)
      // …but the reply-less summary placeholder is stripped from the saved copy.
      expect(t.some((x) => x.id === "turn-summary")).toBe(false)
      expect(t.some((x) => x.summaryPending && !x.reply)).toBe(false)
    })
  })
})
