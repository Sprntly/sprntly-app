// @vitest-environment jsdom
//
// ChatScreen COMPOSER DOM tests.
//
// The unified home surface (ChatScreen) renders two distinct composers:
//   • the LANDING composer — the fresh-chat state shown when an active chat tab
//     has an empty thread (reached via `?new=1` / the "+" New chat button). It
//     lives in `.chat-home-composer`.
//   • the THREAD composer — `.bc-composer` inside `.bc-dock`, shown once the
//     active chat tab has at least one turn.
//
// These tests mount the REAL ChatScreen inside the real Navigation + Content
// providers, mocking only the network/router/heavy-context boundaries the screen
// touches on mount (the same boundary-mock convention as the brief-tab test).
//
// What is covered (mapped to the task's A1–A4):
//   A1. The LANDING composer renders a hidden file input AND an "Attach file"
//       button wired (onClick) to open it; firing a `change` on the input with a
//       fake File reflects the attachment — it rides the outgoing query on send
//       (and the preview chip appears in the thread dock).
//   A2. NO Voice affordance in EITHER composer — no aria-label "Voice input" and
//       no "Voice"/"Stop" tool button anywhere in the rendered chat composer.
//   A3. The THREAD composer also has a working Attach (hidden file input present
//       + an Attach button wired to it).
//   A4. An attached file's content is appended to the outgoing query on send
//       (the send path folds `attachments` into the query string).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// jsdom doesn't implement window.matchMedia; AskReplyBody's typing-animation
// hook (useAnswerSimulatedStream) reads prefers-reduced-motion on mount when a
// fresh reply renders. Test-only stub — real browsers provide it natively.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
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

// runAskGeneration is the send path's network call. We mock it to (a) keep the
// ask off the network and (b) capture the query string ChatScreen sends so A4
// can assert the attached file content was folded in.
const askedQueries: string[] = []
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(async (query: string) => {
    askedQueries.push(query)
    return { answer: "ok", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" }
  }),
  resumeAskGeneration: vi.fn(),
  getPendingAsk: vi.fn(() => null),
}))

vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({
    runStatus: null,
    isTriggering: false,
    showCompleted: false,
    triggerRun: vi.fn(),
  }),
}))

let searchString = ""
const replaceSpy = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: replaceSpy, prefetch: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(searchString),
}))

vi.mock("../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    loading: false,
    profile: null,
    workspace: null,
    refresh: async () => {},
  }),
}))

vi.mock("../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn() }),
}))

vi.mock("../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "anonymous" }),
}))

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

// Seed a persisted chat tab WITH a thread so the THREAD composer renders on
// mount (active tab = a tab that already has a turn). Mirrors the persisted
// shape ChatScreen restores from localStorage (`sprntly_chat_tabs_${company}`).
function seedThreadTab() {
  const tabId = "tab-seed-1"
  localStorage.setItem(
    "sprntly_chat_tabs_acme",
    JSON.stringify([
      {
        id: tabId,
        title: "Seeded chat",
        thread: [
          {
            id: "turn-1",
            query: "first question",
            reply: { answer: "first answer", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" },
          },
        ],
        dbConvId: null,
        briefMeta: null,
      },
    ]),
  )
  localStorage.setItem("sprntly_chat_active_tab_acme", tabId)
}

// File constructor in jsdom does not run a real FileReader text decode reliably
// across versions; ChatScreen reads via FileReader.readAsText, which jsdom
// supports for Blob/File. A File with a string part decodes to that string.
function fakeFile(name: string, content: string): File {
  return new File([content], name, { type: "text/plain" })
}

// The landing composer's hidden <input type=file> (only one file input renders
// at a time — landing OR thread).
const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement | null

beforeEach(() => {
  localStorage.clear()
  searchString = ""
  replaceSpy.mockClear()
  askedQueries.length = 0
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen landing composer (A1 / A2)", () => {
  // A1: the landing composer (reached via ?new=1) renders a hidden file input
  // and an "Attach file" button wired to open it.
  it("renders a hidden file input and a wired 'Attach file' button on the landing", () => {
    searchString = "new=1"
    renderScreen()
    // We are on the chat landing, not the brief surface.
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()

    const input = fileInput()
    expect(input).toBeTruthy()
    expect(input!.type).toBe("file")
    // It's hidden (opened programmatically by the Attach button).
    expect(input!.style.display).toBe("none")
    // The Attach button carries the accessible label and is NOT a plain inert
    // span — it's a real button (the guarded bug: a landing Attach with no
    // onClick / no file input).
    const attach = screen.getByLabelText("Attach file")
    expect(attach.tagName).toBe("BUTTON")
    expect(attach.textContent).toMatch(/Attach/i)
  })

  // A1: firing a change on the landing file input adds the attachment, and the
  // attachment is reflected on send — the typed query carries the file content.
  // (The Toast component is mounted by AppShell, not under this isolated render,
  // and the preview chip only renders in the thread dock — so the observable
  // proof that handleFileSelect populated `attachments` is the send payload.)
  it("handleFileSelect on the landing adds an attachment that rides the outgoing query", async () => {
    searchString = "new=1"
    renderScreen()
    const input = fileInput()
    expect(input).toBeTruthy()

    // Attach a file via the wired hidden input.
    await act(async () => {
      fireEvent.change(input!, { target: { files: [fakeFile("notes.txt", "hello world")] } })
    })

    // Type into the landing composer and send.
    const textarea = document.querySelector(".chat-home-composer-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "use the notes" } })
    })
    const sendBtn = within(document.querySelector(".chat-home-composer") as HTMLElement).getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    await waitFor(() => {
      expect(askedQueries.length).toBeGreaterThan(0)
    })
    const sent = askedQueries[askedQueries.length - 1]
    expect(sent).toContain("use the notes")
    expect(sent).toContain("[Attached files]")
    expect(sent).toContain("notes.txt")
    expect(sent).toContain("hello world")
  })

  // A2: NO Voice affordance on the landing composer.
  it("renders NO Voice affordance on the landing composer", () => {
    searchString = "new=1"
    renderScreen()
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    // No aria-labelled voice control…
    expect(screen.queryByLabelText("Voice input")).toBeNull()
    // …and no "Voice"/"Stop" tool button text anywhere on the chat surface.
    expect(screen.queryByText(/^Voice$/)).toBeNull()
    expect(screen.queryByText(/^Stop$/)).toBeNull()
    // (The brief tab's BriefChat HAS a "Voice" tool button, so this guards that
    // the CHAT composer specifically does not — the brief surface isn't mounted
    // in the ?new=1 landing state.) Match the BriefChat <section class="briefx">
    // by class: the sidebar rail item also carries the "Weekly brief" name, so a
    // getByLabelText would be ambiguous.
    expect(document.querySelector("section.briefx")).toBeNull()
  })
})

describe("ChatScreen thread composer (A2 / A3 / A4)", () => {
  // A3: the thread composer renders a hidden file input + a wired Attach button.
  it("renders a hidden file input and an Attach button on the thread composer", () => {
    seedThreadTab()
    renderScreen()
    // The seeded thread is showing (user bubble + assistant reply).
    expect(screen.getByText("first question")).toBeTruthy()

    const input = fileInput()
    expect(input).toBeTruthy()
    expect(input!.style.display).toBe("none")
    // The thread Attach button lives in `.bc-composer-tools` as a `.bc-tool`.
    const dock = document.querySelector(".bc-dock") as HTMLElement
    expect(dock).toBeTruthy()
    const attach = within(dock).getByText(/Attach/i)
    expect(attach.closest("button")).toBeTruthy()
  })

  // A2: NO Voice affordance on the thread composer either.
  it("renders NO Voice affordance on the thread composer", () => {
    seedThreadTab()
    renderScreen()
    expect(screen.getByText("first question")).toBeTruthy()
    const dock = document.querySelector(".bc-dock") as HTMLElement
    expect(dock).toBeTruthy()
    expect(within(dock).queryByLabelText("Voice input")).toBeNull()
    expect(within(dock).queryByText(/^Voice$/)).toBeNull()
    expect(within(dock).queryByText(/^Stop$/)).toBeNull()
  })

  // A1/A3: firing a change on the thread file input renders the attachment
  // preview chip (the chip row only renders in the thread dock).
  it("shows an attachment preview chip after selecting a file in the thread composer", async () => {
    seedThreadTab()
    renderScreen()
    const input = fileInput()
    expect(input).toBeTruthy()

    await act(async () => {
      fireEvent.change(input!, { target: { files: [fakeFile("spec.md", "# spec body") ] } })
    })

    // The preview chip shows the attached file name.
    await waitFor(() => {
      const dock = document.querySelector(".bc-dock") as HTMLElement
      expect(within(dock).getByText("spec.md")).toBeTruthy()
    })
  })

  // A4: an attached file's content is appended to the outgoing query on send.
  it("appends the attached file content to the outgoing query on send", async () => {
    seedThreadTab()
    renderScreen()
    const input = fileInput()
    expect(input).toBeTruthy()

    // Attach a file.
    await act(async () => {
      fireEvent.change(input!, { target: { files: [fakeFile("data.csv", "a,b,c\n1,2,3")] } })
    })
    await waitFor(() => {
      const dock = document.querySelector(".bc-dock") as HTMLElement
      expect(within(dock).getByText("data.csv")).toBeTruthy()
    })

    // Type into the thread composer and send.
    const textarea = document.querySelector(".bc-composer-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "summarize this" } })
    })
    const sendBtn = within(document.querySelector(".bc-composer") as HTMLElement).getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    // runAskGeneration was called with the typed query PLUS the attached content.
    await waitFor(() => {
      expect(askedQueries.length).toBeGreaterThan(0)
    })
    const sent = askedQueries[askedQueries.length - 1]
    expect(sent).toContain("summarize this")
    expect(sent).toContain("[Attached files]")
    expect(sent).toContain("data.csv")
    expect(sent).toContain("a,b,c")
  })
})
