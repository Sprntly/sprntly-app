// @vitest-environment jsdom
//
// ChatScreen — resuming a chat from Chat history restores its FULL thread.
//
// Clicking a row in Chat history (ChatsScreen) writes `sprntly_resume_conv`
// {dbId, title, fallbackTurns} and navigates to the chat surface. ChatScreen's
// checkResume opens a tab for that conversation and hydrates ALL its turns via
// conversationsApi.listTurns(dbId) — not just the first ask. This reproduces the
// reported bug where reopening a multi-ask chat showed a fresh chat "without the
// other asks".
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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

// The FULL persisted thread (2 asks + 2 replies) the server holds.
const FULL_TURNS = {
  turns: [
    { id: 1, role: "user", content: "First question about retention" },
    { id: 2, role: "assistant", content: "Retention answer." },
    { id: 3, role: "user", content: "Second question about churn" },
    { id: 4, role: "assistant", content: "Churn answer." },
  ],
}
// listTurns is configured per-test (happy path vs transient failure + retry).
// create/addTurn/byPrd are hoisted spies so the plain-chat tests can assert that
// an ordinary conversation neither spawns a second row nor touches the PRD APIs.
const listTurns = vi.fn().mockResolvedValue(FULL_TURNS)
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
import { ChatScreen } from "../ChatScreen"

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
  listTurns.mockResolvedValue(FULL_TURNS)
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

describe("ChatScreen — resume a chat from history", () => {
  it("restores ALL asks in the thread, not just the first", async () => {
    // The handoff ChatsScreen.handleRowClick writes: dbId + title + a
    // preview-only fallback (just the FIRST ask). The full thread must come
    // from listTurns, not the fallback.
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "First question about retention",
      fallbackTurns: [{ role: "user", content: "First question about retention" }],
    }))

    await act(async () => {
      renderScreen()
    })

    // The reopened tab fetched the conversation's turns…
    await waitFor(() => expect(listTurns).toHaveBeenCalledWith(42))
    // …and the FOLLOW-UP ask is present (the bug: only the first ask showed).
    // Scope to user bubbles so the tab-title copy of the first ask doesn't
    // create a false "multiple elements" match.
    await waitFor(() => {
      const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble")).map((b) => b.textContent)
      expect(bubbles).toContain("First question about retention")
      expect(bubbles).toContain("Second question about churn")
    })
  })

  it("retries a transient listTurns failure instead of collapsing to one ask", async () => {
    // A single failed request must NOT drop the thread to the preview-only
    // fallback (the reported "opens a new chat without the other asks" bug).
    listTurns
      .mockRejectedValueOnce(new Error("network blip"))
      .mockResolvedValueOnce(FULL_TURNS)

    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "First question about retention",
      fallbackTurns: [{ role: "user", content: "First question about retention" }],
    }))

    await act(async () => {
      renderScreen()
    })

    // It retried (2 calls) and restored the FULL thread, not just the fallback.
    await waitFor(() => expect(listTurns).toHaveBeenCalledTimes(2))
    await waitFor(() => {
      const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble")).map((b) => b.textContent)
      expect(bubbles).toContain("Second question about churn")
    })
  })
})

// The PRD conversation↔document binding (the backend now links the two rows so
// leaving the page mid-generation can't orphan a PRD chat) must stay entirely
// out of the way of ORDINARY chats. A plain Q&A conversation carries no prd_id,
// so reopening it must restore a plain thread: no PRD card, no View PRD button,
// and a follow-up that still records against the SAME conversation.
describe("ChatScreen — a plain (non-PRD) chat resumes untouched", () => {
  it("reopens with its turns and NO PRD affordances", async () => {
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "First question about retention",
      fallbackTurns: [{ role: "user", content: "First question about retention" }],
      // A plain chat's conversation row has prd_id NULL — the resume handoff
      // therefore carries no prdId, and nothing may infer one.
      prdId: null,
    }))

    await act(async () => { renderScreen() })

    await waitFor(() => {
      const bubbles = Array.from(document.querySelectorAll(".bc-user-bubble")).map((b) => b.textContent)
      expect(bubbles).toContain("Second question about churn")
    })
    // No PRD card is pinned above (or inlined into) the thread, and no PRD
    // button is offered — this chat is not about a document.
    expect(document.querySelector('[data-testid="chat-insight-msg"]')).toBeNull()
    const buttonText = Array.from(document.querySelectorAll("button")).map((b) => b.textContent ?? "")
    expect(buttonText.some((t) => /View PRD|Generate PRD|Generating PRD/.test(t))).toBe(false)
    // …and the resumed chat never reaches for the PRD APIs.
    expect(byPrd).not.toHaveBeenCalled()
  })

  it("records a follow-up against the SAME conversation, creating no new one", async () => {
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "First question about retention",
      fallbackTurns: [{ role: "user", content: "First question about retention" }],
    }))

    await act(async () => { renderScreen() })
    await waitFor(() => expect(listTurns).toHaveBeenCalledWith(42))

    const composer = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(composer, { target: { value: "and what about weekly cohorts?" } })
      fireEvent.keyDown(composer, { key: "Enter" })
    })

    // The follow-up appended to conversation 42 — the resumed tab is already
    // bound to it, so no second conversation is spun up for the same room.
    await waitFor(() => expect(addTurn).toHaveBeenCalledWith(42, "user", "and what about weekly cohorts?", undefined))
    expect(create).not.toHaveBeenCalled()
  })
})

describe("ChatScreen — resumed threads keep their persisted attachments", () => {
  it("rehydrates turn attachments from listTurns so the documents survive reload", async () => {
    // The persisted thread carries an extracted document on its first turn
    // (conversation_turns.attachments) — the reopened tab must restore it onto
    // the ThreadTurn so the card renders AND a later "generate a PRD" can
    // ground on it (conversationPrdDocs reads turn.attachments).
    listTurns.mockResolvedValue({
      turns: [
        {
          id: 1, role: "user", content: "here's the requirements deck",
          attachments: [{ name: "requirements.pdf", content: "MUST prefill cart from deal" }],
        },
        { id: 2, role: "assistant", content: "Got it — summarizing." },
      ],
    })
    localStorage.setItem("sprntly_resume_conv", JSON.stringify({
      dbId: 42,
      title: "here's the requirements deck",
      fallbackTurns: [{ role: "user", content: "here's the requirements deck" }],
    }))

    await act(async () => { renderScreen() })

    await waitFor(() => expect(listTurns).toHaveBeenCalledWith(42))
    await waitFor(() => {
      const cards = document.querySelector(".bc-user-attachments")
      expect(cards?.textContent).toContain("requirements.pdf")
    })
  })
})
