// @vitest-environment jsdom
//
// Copy / edit / retry on a PAST prompt, at the screen level.
//
// The mapper decides which turns are eligible and `ChatBubble` renders the
// buttons; both are covered in their own suites. What only the screen can prove
// is the part that makes editing an ANSWERED question safe at all: re-asking
// REWINDS the conversation to that turn — the turn and everything after it
// leave the thread, and the persisted record is rewound to match. Get that
// wrong and the thread becomes a record of a conversation nobody had.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (q: string) =>
    ({
      matches: true, // reduced motion → replies settle instantly, no timers
      media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// ── Boundary mocks, mirroring the sibling ChatScreen.*.dom.test.tsx suites ──
const api = vi.hoisted(() => ({
  create: vi.fn().mockResolvedValue({ id: 500 }),
  addTurn: vi.fn().mockResolvedValue({ id: 900 }),
}))
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  const noop = vi.fn()
  return {
    ApiError,
    skillsApi: { list: vi.fn().mockResolvedValue({ skills: [] }) },
    askApi: { ask: noop, skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: api,
    artifactsApi: {},
    attachmentsApi: {},
    chatSuggestionsApi: { forTab: vi.fn().mockResolvedValue({ suggestions: [] }) },
    customArtifactsApi: {},
    storiesApi: {},
    ticketDataApi: {},
  }
})
const rewindToUserTurn = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
vi.mock("../../../../lib/chatPersistence", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../lib/chatPersistence")>()
  return {
    ...actual,
    // The real persistence, with ONE seam spied. Asserting on the DELETE itself
    // would be testing `chatPersistence` through three layers of ChatScreen's
    // send pipeline; its own suite already does that properly.
    createChatPersistence: (deps: Parameters<typeof actual.createChatPersistence>[0]) => ({
      ...actual.createChatPersistence(deps),
      rewindToUserTurn,
    }),
  }
})
const runAskGeneration = vi.hoisted(() => vi.fn())
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration,
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
  AskCancelledError: class extends Error {},
  AskStoppedError: class extends Error {},
  AskTimeoutError: class extends Error {},
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
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

const reply = (answer: string) => ({
  answer, sources: [], follow_ups: [], key_points: [], citations: [],
  confidence: 1, unanswered: "",
})

/** Two settled exchanges plus a third, on one tab bound to conversation 500. */
function seedThread() {
  sessionStorage.setItem(
    "sprntly_chat_tabs_anon_acme",
    JSON.stringify([{
      id: "tab-1",
      title: "Seeded chat",
      dbConvId: 500,
      briefMeta: null,
      thread: [
        { id: "t1", query: "first question", reply: reply("first answer"), dbTurnId: 11 },
        { id: "t2", query: "second question", reply: reply("second answer"), dbTurnId: 22 },
        { id: "t3", query: "third question", reply: reply("third answer"), dbTurnId: 33 },
      ],
    }]),
    )
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-1")
}

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

/** The turn wrapper whose user bubble reads `text`. */
function turnFor(text: string): HTMLElement {
  const bubble = Array.from(document.querySelectorAll(".bc-user-bubble"))
    .find((el) => el.textContent === text)
  if (!bubble) throw new Error(`no user bubble reading ${JSON.stringify(text)}`)
  return bubble.closest(".bc-turn") as HTMLElement
}

const bubbleTexts = () =>
  Array.from(document.querySelectorAll(".bc-user-bubble")).map((el) => el.textContent)

beforeEach(() => {
  sessionStorage.clear()
  vi.clearAllMocks()
  // Never resolves: the send stays in flight, so the assertions are about the
  // rewind rather than about whatever a fake answer would have appended.
  runAskGeneration.mockImplementation(() => new Promise(() => {}))
  seedThread()
})
afterEach(() => {
  cleanup()
  sessionStorage.clear()
})

async function settle() {
  await act(async () => { await Promise.resolve() })
}

describe("retrying a past prompt", () => {
  it("rewinds the thread to that turn — it and everything after it go", async () => {
    await act(async () => { renderScreen() })
    await waitFor(() => expect(bubbleTexts()).toContain("third question"))

    const retry = turnFor("second question").querySelector('[data-testid="user-turn-retry"]')!
    await act(async () => { fireEvent.click(retry) })
    await settle()

    // The first exchange survives; the re-asked turn and the one after it are
    // gone, replaced by a single fresh send of the same question.
    await waitFor(() => expect(bubbleTexts()).not.toContain("third question"))
    expect(bubbleTexts()).toEqual(["first question", "second question"])
  })

  it("rewinds the PERSISTED conversation to the same turn", async () => {
    await act(async () => { renderScreen() })
    await waitFor(() => expect(bubbleTexts()).toContain("third question"))

    const retry = turnFor("second question").querySelector('[data-testid="user-turn-retry"]')!
    await act(async () => { fireEvent.click(retry) })
    await waitFor(() => expect(rewindToUserTurn).toHaveBeenCalled())
    // The tab, the client turn id, and the DB row id the restored turn carried
    // — without the last one a rehydrated thread could not be rewound at all,
    // and the reopened conversation would show the old pair AND the new one.
    expect(rewindToUserTurn).toHaveBeenCalledWith("tab-1", "t2", 22)
  })

  it("re-asks the question verbatim", async () => {
    await act(async () => { renderScreen() })
    await waitFor(() => expect(bubbleTexts()).toContain("third question"))

    const retry = turnFor("first question").querySelector('[data-testid="user-turn-retry"]')!
    await act(async () => { fireEvent.click(retry) })
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(runAskGeneration.mock.calls[0][0]).toContain("first question")
  })
})

describe("editing a past prompt", () => {
  it("opens an editor seeded with the message, and rewinds on save", async () => {
    await act(async () => { renderScreen() })
    await waitFor(() => expect(bubbleTexts()).toContain("third question"))

    const edit = turnFor("second question").querySelector('[data-testid="user-turn-edit"]')!
    await act(async () => { fireEvent.click(edit) })

    const editor = screen.getByLabelText("Edit your message") as HTMLTextAreaElement
    expect(editor.value).toBe("second question")
    await act(async () => {
      fireEvent.change(editor, { target: { value: "second question, rephrased" } })
    })
    await act(async () => { fireEvent.click(screen.getByTestId("user-turn-edit-save")) })
    await settle()

    await waitFor(() => expect(bubbleTexts()).not.toContain("third question"))
    expect(bubbleTexts()).toContain("first question")
    await waitFor(() => expect(runAskGeneration).toHaveBeenCalled())
    expect(runAskGeneration.mock.calls[0][0]).toContain("second question, rephrased")
  })

  it("saving an UNCHANGED message spends nothing", async () => {
    // Re-running an identical question is what Retry is for; doing it here
    // would burn a generation on a keystroke the user took back.
    await act(async () => { renderScreen() })
    await waitFor(() => expect(bubbleTexts()).toContain("third question"))

    const edit = turnFor("second question").querySelector('[data-testid="user-turn-edit"]')!
    await act(async () => { fireEvent.click(edit) })
    await act(async () => { fireEvent.click(screen.getByTestId("user-turn-edit-save")) })
    await settle()

    expect(runAskGeneration).not.toHaveBeenCalled()
    expect(rewindToUserTurn).not.toHaveBeenCalled()
    expect(bubbleTexts()).toContain("third question")
  })

  it("cancelling changes nothing", async () => {
    await act(async () => { renderScreen() })
    await waitFor(() => expect(bubbleTexts()).toContain("third question"))

    const edit = turnFor("second question").querySelector('[data-testid="user-turn-edit"]')!
    await act(async () => { fireEvent.click(edit) })
    await act(async () => { fireEvent.click(screen.getByTestId("user-turn-edit-cancel")) })
    await settle()

    expect(runAskGeneration).not.toHaveBeenCalled()
    expect(rewindToUserTurn).not.toHaveBeenCalled()
    expect(bubbleTexts()).toEqual(["first question", "second question", "third question"])
  })
})

describe("copying a past prompt", () => {
  it("writes the message to the clipboard and confirms on the button", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText }, configurable: true, writable: true,
    })
    await act(async () => { renderScreen() })
    await waitFor(() => expect(bubbleTexts()).toContain("third question"))

    const copy = turnFor("second question").querySelector('[data-testid="user-turn-copy"]')!
    await act(async () => { fireEvent.click(copy) })

    expect(writeText).toHaveBeenCalledWith("second question")
    await waitFor(() =>
      expect(
        turnFor("second question").querySelector('[data-testid="user-turn-copy"]')
          ?.getAttribute("aria-label"),
      ).toBe("Copied"),
    )
    // Copying is not a send.
    expect(runAskGeneration).not.toHaveBeenCalled()
  })
})
