// @vitest-environment jsdom
//
// STAGED ATTACHMENTS BELONG TO THE CONVERSATION THEY WERE STAGED IN.
//
// THE MEASURED FAILURE. Attach files in a chat, click "New chat", and the new
// chat's composer opened still holding the chips. Attaching the same pack again
// stacked them — 12 became 24, which is past MAX_RUN_ATTACHMENTS (16) and the
// send died at the validator. The user reads that as the product rejecting
// their files, not as duplicates it kept for them.
//
// THE CAUSE. There is ONE composer instance for every tab, so nothing in it is
// scoped to a conversation unless a switch explicitly says so. Every switch
// cleared the DRAFT; exactly one of them (the tab chip) also cleared the
// ATTACHMENTS. `startNewThread` — the "+" button AND the sidebar's "New chat",
// which routes through `/?new=1` into the same function — was not one of them.
//
// WHY THE ASSERTION IS "CLEARED" RATHER THAN "CARRIED PER TAB". The draft text
// is already discarded on every switch, so preserving the files while dropping
// the words would leave chips for a message whose text is gone. The two are
// halves of one message and move together.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false, media: query, onchange: null,
      addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// ── Boundary mocks (network / router / heavy contexts) ─────────────────────
vi.mock("../../../../lib/api", () => {
  class ApiError extends Error {
    status = 0
    body: unknown = null
  }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: { create: vi.fn(), addTurn: vi.fn() },
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

let searchString = ""
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(searchString),
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

// A tab that already has a turn, so the THREAD composer renders and the tab
// strip (with its "+" New chat control) is on screen.
function seedThreadTab() {
  const tabId = "tab-seed-1"
  sessionStorage.setItem(
    "sprntly_chat_tabs_anon_acme",
    JSON.stringify([{
      id: tabId,
      title: "Seeded chat",
      thread: [{
        id: "turn-1",
        query: "first question",
        reply: {
          answer: "first answer", sources: [], follow_ups: [], key_points: [],
          citations: [], confidence: 1, unanswered: "",
        },
      }],
      dbConvId: null,
      briefMeta: null,
    }]),
  )
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", tabId)
}

const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement | null

// "New chat" is labelled twice on this surface: the sidebar rail's entry (which
// navigates via `/?new=1`, and whose router.push is mocked away here) and the
// tab strip's "+" control. We want the "+": it calls `startNewThread` directly,
// which is the same function `/?new=1` lands in, so it exercises both entries.
const newChatPlus = () => {
  const all = Array.from(
    document.querySelectorAll('button[aria-label="New chat"]'),
  ) as HTMLButtonElement[]
  const plus = all.find((b) => !b.className.includes("sb-rail"))
  if (!plus) throw new Error(`no tab-strip "+" among ${all.length} "New chat" controls`)
  return plus
}
const chips = () => screen.queryAllByTestId("attachment-chip")

async function attach(names: string[]) {
  const input = fileInput()
  expect(input).toBeTruthy()
  const files = names.map((n) => new File([`contents of ${n}`], n, { type: "text/plain" }))
  await act(async () => {
    fireEvent.change(input!, { target: { files } })
  })
  await waitFor(() => expect(chips().length).toBe(names.length))
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  searchString = ""
})
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

describe("staged attachments do not follow the user into another conversation", () => {
  it('drops the staged files when "New chat" opens a fresh conversation', async () => {
    seedThreadTab()
    renderScreen()

    await attach(["notes.txt", "pricing.txt", "calls.txt"])

    // The sidebar's "New chat" pushes `/?new=1`, which lands in the SAME
    // `startNewThread` this control calls — so this covers both entry points.
    await act(async () => { fireEvent.click(newChatPlus()) })

    await waitFor(() => {
      expect(
        chips().map((c) => c.textContent),
        "the new chat opened still holding the previous chat's attachments — "
        + "attaching the same pack again stacks them past MAX_RUN_ATTACHMENTS "
        + "and the send fails at the validator",
      ).toEqual([])
    })
  })

  // HONEST LABEL: THIS ONE PASSES ON BOTH SIDES OF THE FIX, ON PURPOSE. The tab
  // chip was the single switch that already cleared the attachments — it is
  // where the behaviour was first got right. It is pinned here because the fix
  // routes that site through the shared clear along with the other switches, and
  // a refactor that silently dropped the one place that worked would be a
  // regression wearing the shape of a tidy-up. The test above is the one that
  // fails on the unfixed code.
  it("drops the staged files when switching to an existing conversation", async () => {
    seedThreadTab()
    renderScreen()

    // Open a second conversation, then come back to the seeded one via its chip.
    await act(async () => { fireEvent.click(newChatPlus()) })

    await attach(["draft.txt"])

    const seeded = await screen.findByText("Seeded chat")
    await act(async () => { fireEvent.click(seeded) })

    await waitFor(() => expect(chips().map((c) => c.textContent)).toEqual([])) 
  })
})
