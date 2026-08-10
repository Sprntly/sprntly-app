// @vitest-environment jsdom
//
// ChatScreen COMPOSER DOM tests.
//
// The unified home surface (ChatScreen) renders two distinct composers:
//   • the LANDING composer — the fresh-chat state shown when an active chat tab
//     has an empty thread (reached via `?new=1` / the "+" New chat button). It
//     lives in `.cx`.
//   • the THREAD composer — `.cx` inside `.bc-dock`, shown once the
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
//   A2. NO microphone in EITHER composer WHERE THE BROWSER HAS NO WEB SPEECH
//       API — which is jsdom's situation here, and Firefox's in the wild. This
//       used to assert no microphone unconditionally; dictation is wired now
//       (see ChatScreen.voice.dom.test.tsx for the supported-browser half), and
//       what survives from that rule is the part still true: an unsupported
//       browser is offered nothing rather than a button that does nothing.
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
    // askApi.skills IS the slash palette now: it serves the company's own
    // uploaded skills (category "Custom"). It used to serve the vendored
    // built-in catalog, which ChatScreen merged behind a second skillsApi.list
    // fetch — one list, one fetch, since a chat turn can no longer invoke a
    // built-in at all.
    askApi: {
      ask: vi.fn(),
      skills: vi.fn().mockResolvedValue({
        skills: [
          { id: "my-estimator", label: "My Estimator", trigger: "/my-estimator", description: "Scores features", category: "Custom" },
        ],
      }),
    },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: { create: vi.fn(), addTurn: vi.fn() },
  }
})

// runAskGeneration is the send path's network call. We mock it to (a) keep the
// ask off the network and (b) capture the query string ChatScreen sends so A4
// can assert the attached file content was folded in.
const askedQueries: string[] = []
const askedOpts: Array<Record<string, unknown> | undefined> = []
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(async (query: string, _company: string, _tabId: string, opts?: Record<string, unknown>) => {
    askedQueries.push(query)
    askedOpts.push(opts)
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
import { askApi } from "../../../../lib/api"

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
// shape ChatScreen restores from sessionStorage (`sprntly_chat_tabs_${company}`).
function seedThreadTab() {
  const tabId = "tab-seed-1"
  sessionStorage.setItem(
    "sprntly_chat_tabs_anon_acme",
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
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", tabId)
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
  askedOpts.length = 0
})
afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe("ChatScreen landing composer (A1 / A2)", () => {
  // A1: the landing composer (reached via ?new=1) renders a hidden file input
  // and an "Attach file" button wired to open it.
  it("renders a hidden file input reachable from the + menu on the landing", async () => {
    searchString = "new=1"
    renderScreen()
    // We are on the chat landing, not the brief surface.
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()

    const input = fileInput()
    expect(input).toBeTruthy()
    expect(input!.type).toBe("file")
    // It's hidden (opened programmatically from the + menu).
    expect(input!.style.display).toBe("none")

    // The lone Attach button became a `+` action menu, so skills are reachable
    // with a mouse too. It is a real button with menu semantics.
    const plus = screen.getByLabelText("Add attachment or skill")
    expect(plus.tagName).toBe("BUTTON")
    expect(plus.getAttribute("aria-haspopup")).toBe("menu")
    expect(plus.getAttribute("aria-expanded")).toBe("false")

    const clickSpy = vi.spyOn(input!, "click")
    await act(async () => { fireEvent.click(plus) })
    expect(plus.getAttribute("aria-expanded")).toBe("true")
    const menu = screen.getByRole("menu")
    expect(within(menu).getByText("Attach a file")).toBeTruthy()
    expect(within(menu).getByText("Browse skills")).toBeTruthy()

    // "Attach a file" opens the hidden input — the wiring the old Attach button
    // owned, now behind the menu.
    await act(async () => { fireEvent.click(within(menu).getByText("Attach a file")) })
    expect(clickSpy).toHaveBeenCalled()
    expect(screen.queryByRole("menu")).toBeNull()
  })

  // The `+` menu's other item opens the skills palette — the whole point of the
  // menu, since 78 skills were previously reachable only by typing "/".
  it("opens the skills palette from the + menu", async () => {
    searchString = "new=1"
    renderScreen()
    await act(async () => { fireEvent.click(screen.getByLabelText("Add attachment or skill")) })
    await act(async () => {
      fireEvent.click(within(screen.getByRole("menu")).getByText("Browse skills"))
    })
    const palette = await screen.findByRole("listbox", { name: "Skills" })
    expect(within(palette).getAllByRole("option").length).toBeGreaterThan(0)
  })

  // ⌘/ is advertised in both composers' footers and, before this, nothing in the
  // app listened for it. The hint is now true.
  it("opens the skills palette on ⌘/", async () => {
    searchString = "new=1"
    renderScreen()
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.keyDown(textarea, { key: "/", metaKey: true })
    })
    const palette = await screen.findByRole("listbox", { name: "Skills" })
    expect(within(palette).getAllByRole("option").length).toBeGreaterThan(0)
  })

  // Selecting a skill pins a removable CHIP instead of pasting
  // "/my-estimator " into the draft as raw text the user must not delete — and
  // the trigger is re-attached to the query on send, so the backend's
  // deterministic slash fast-path is unchanged. That fast-path is CUSTOM-ONLY
  // now (`qa_agent._routable` refuses every vendored id), which is exactly what
  // this palette offers — so the wire protocol behind the chip still resolves.
  it("pins a skill chip instead of pasting the trigger, and sends the trigger", async () => {
    searchString = "new=1"
    renderScreen()
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "/my-est" } })
    })
    const palette = await screen.findByRole("listbox", { name: "Skills" })
    await act(async () => {
      fireEvent.mouseDown(within(palette).getAllByRole("option")[0])
    })

    // The draft is clear (no "/my-estimator " text) and a chip names the skill.
    expect((document.querySelector(".cx-input") as HTMLTextAreaElement).value).toBe("")
    const chip = document.querySelector('[data-testid="skill-chip"]') as HTMLElement
    expect(chip).toBeTruthy()
    expect(chip.textContent).toContain("My Estimator")
    expect(within(chip).getByLabelText(/Remove the .* skill/)).toBeTruthy()

    await act(async () => {
      fireEvent.change(document.querySelector(".cx-input")!, { target: { value: "rank these ideas" } })
    })
    await act(async () => {
      fireEvent.click(within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send"))
    })
    await waitFor(() => expect(askedQueries.length).toBeGreaterThan(0))
    expect(askedQueries[askedQueries.length - 1]).toContain("rank these ideas")
    expect(askedQueries[askedQueries.length - 1].startsWith("/")).toBe(true)
  })

  // Removing the chip un-pins the skill; the next send carries no trigger.
  it("removes the pinned skill chip", async () => {
    searchString = "new=1"
    renderScreen()
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => { fireEvent.change(textarea, { target: { value: "/my-est" } }) })
    const palette = await screen.findByRole("listbox", { name: "Skills" })
    await act(async () => { fireEvent.mouseDown(within(palette).getAllByRole("option")[0]) })
    const chip = document.querySelector('[data-testid="skill-chip"]') as HTMLElement
    await act(async () => {
      fireEvent.click(within(chip).getByLabelText(/Remove the .* skill/))
    })
    expect(document.querySelector('[data-testid="skill-chip"]')).toBeNull()
  })

  // The send button is OFF below the backend's own min_length=3, and says why
  // rather than sitting there inert with no explanation.
  it("disables Send below 3 characters and titles it with the reason", async () => {
    searchString = "new=1"
    renderScreen()
    const send = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send") as HTMLButtonElement
    expect(send.disabled).toBe(true)
    expect(send.getAttribute("title")).toBe("Type at least 3 characters")
    await act(async () => {
      fireEvent.change(document.querySelector(".cx-input")!, { target: { value: "hi" } })
    })
    expect((within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send") as HTMLButtonElement).disabled).toBe(true)
    await act(async () => {
      fireEvent.change(document.querySelector(".cx-input")!, { target: { value: "hey" } })
    })
    const ready = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send") as HTMLButtonElement
    expect(ready.disabled).toBe(false)
    expect(ready.getAttribute("title")).toBeNull()
  })

  // Custom skills (PRD 1854): typing "/" opens the slash palette with the
  // company's uploaded skills, and filtering by slug narrows to them. There is
  // no built-in catalog to be listed ahead of any more — the palette is the
  // company's own library, which is the only thing a slash trigger can invoke.
  it("lists the company's custom skills in the slash palette and filters by slug", async () => {
    searchString = "new=1"
    renderScreen()
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()

    await act(async () => {
      fireEvent.change(textarea, { target: { value: "/" } })
    })
    const palette = await screen.findByRole("listbox", { name: "Skills" })
    const rows = within(palette).getAllByRole("option")
    expect(rows[0].textContent).toContain("/my-estimator")
    expect(rows[0].textContent).toContain("My Estimator")

    await act(async () => {
      fireEvent.change(textarea, { target: { value: "/my-est" } })
    })
    const narrowed = within(screen.getByRole("listbox", { name: "Skills" })).getAllByRole("option")
    expect(narrowed).toHaveLength(1)
    expect(narrowed[0].textContent).toContain("/my-estimator")
  })

  // No-override (PRD 1854 revision): a skill named after another replaces
  // nothing — both are listed, each with its own trigger and its own
  // description, because the description is what tells them apart.
  it("lists BOTH skills when two uploads share a name", async () => {
    vi.mocked(askApi.skills).mockResolvedValueOnce({
      skills: [
        { id: "prioritize-2", label: "Prioritize", trigger: "/prioritize-2", description: "House ranking rules", category: "Custom" },
        { id: "prioritize-3", label: "Prioritize", trigger: "/prioritize-3", description: "Rank ideas", category: "Custom" },
      ],
    })
    searchString = "new=1"
    renderScreen()
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement

    await act(async () => {
      fireEvent.change(textarea, { target: { value: "/prior" } })
    })
    const palette = await screen.findByRole("listbox", { name: "Skills" })
    const rows = within(palette).getAllByRole("option")
    expect(rows).toHaveLength(2)
    // Each row carries the trigger that invokes IT.
    expect(rows[0].textContent).toContain("/prioritize-2")
    expect(rows[0].textContent).toContain("House ranking rules")
    expect(rows[1].textContent).toContain("/prioritize-3")
    expect(rows[1].textContent).toContain("Rank ideas")
    // Same name on both rows — the descriptions are the distinguisher.
    expect(rows.every((r) => r.textContent?.includes("Prioritize"))).toBe(true)
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
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "use the notes" } })
    })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
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

  // A2: no microphone on the landing composer in a browser without the API.
  // jsdom implements neither `SpeechRecognition` nor `webkitSpeechRecognition`,
  // so this render stands in for Firefox — the feature detection must resolve to
  // "not supported" and render nothing, not a dead button.
  it("renders NO microphone on the landing composer without the Web Speech API", () => {
    expect((window as unknown as { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition).toBeUndefined()
    searchString = "new=1"
    renderScreen()
    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    expect(screen.queryByLabelText("Dictate your question")).toBeNull()
    expect(screen.queryByLabelText("Stop dictating")).toBeNull()
    expect(document.querySelector(".cx-mic")).toBeNull()
    // (The brief surface isn't mounted in the ?new=1 landing state.) Match the
    // BriefChat <section class="briefx"> by class rather than by its "Top
    // Insights" accessible name, which is not a label the UI surfaces anywhere
    // anymore.
    expect(document.querySelector("section.briefx")).toBeNull()
  })
})

describe("ChatScreen thread composer (A2 / A3 / A4)", () => {
  // A sent question must scroll the thread to the newest turn, so a long
  // conversation doesn't hide the question / thinking / answer below the fold.
  // jsdom has no layout, so we stub Element.scrollTo and assert the thread
  // followed (scrollTo invoked again after the new turn renders).
  it("auto-scrolls the thread to the newest turn when a question is sent", async () => {
    const orig = (HTMLElement.prototype as unknown as { scrollTo?: unknown }).scrollTo
    const scrollSpy = vi.fn()
    ;(HTMLElement.prototype as unknown as { scrollTo: unknown }).scrollTo = scrollSpy
    try {
      seedThreadTab()
      renderScreen()
      await screen.findByText("first question")
      const before = scrollSpy.mock.calls.length

      const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
      await act(async () => {
        fireEvent.change(textarea, { target: { value: "a brand new question" } })
      })
      await act(async () => {
        fireEvent.click(within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send"))
      })

      // The new user turn renders…
      await screen.findByText("a brand new question")
      // …and the thread followed it to the bottom (scrollTo called again).
      await waitFor(() => {
        expect(scrollSpy.mock.calls.length).toBeGreaterThan(before)
      })
    } finally {
      ;(HTMLElement.prototype as unknown as { scrollTo?: unknown }).scrollTo = orig
    }
  })

  // A3: the thread composer renders a hidden file input reachable from the same
  // `+` menu as the landing — one composer component, so the two cannot drift.
  it("renders a hidden file input and a + menu on the thread composer", async () => {
    seedThreadTab()
    renderScreen()
    // The seeded thread is showing (user bubble + assistant reply).
    expect(screen.getByText("first question")).toBeTruthy()

    const input = fileInput()
    expect(input).toBeTruthy()
    expect(input!.style.display).toBe("none")
    const dock = document.querySelector(".bc-dock") as HTMLElement
    expect(dock).toBeTruthy()
    const plus = within(dock).getByLabelText("Add attachment or skill")
    expect(plus.tagName).toBe("BUTTON")
    await act(async () => { fireEvent.click(plus) })
    expect(within(dock).getByText("Attach a file")).toBeTruthy()
    expect(within(dock).getByText("Browse skills")).toBeTruthy()
  })

  // The ⌘/ hint used to render only on the dock — the landing is where a new
  // user starts, so it renders on both now.
  it("shows the ⌘/ hint on the thread composer AND the landing", () => {
    seedThreadTab()
    renderScreen()
    expect((document.querySelector(".cx") as HTMLElement).querySelector(".cx-kbd")).toBeTruthy()
    cleanup()
    sessionStorage.clear()
    searchString = "new=1"
    renderScreen()
    expect((document.querySelector(".cx--home") as HTMLElement).querySelector(".cx-kbd")).toBeTruthy()
  })

  // A2: the same on the thread composer. One component, two mount points — this
  // is here so a future change that renders the mic unconditionally fails on
  // BOTH surfaces rather than only the one someone happened to test.
  it("renders NO microphone on the thread composer without the Web Speech API", () => {
    seedThreadTab()
    renderScreen()
    expect(screen.getByText("first question")).toBeTruthy()
    const dock = document.querySelector(".bc-dock") as HTMLElement
    expect(dock).toBeTruthy()
    expect(within(dock).queryByLabelText("Dictate your question")).toBeNull()
    expect(within(dock).queryByLabelText("Stop dictating")).toBeNull()
    expect(dock.querySelector(".cx-mic")).toBeNull()
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

  // Regression: after sending, the composer must snap back to its CSS resting
  // height — NOT a hardcoded inline height shorter than the textarea's own
  // vertical padding, which clipped the placeholder ("Ask Sprntly anything…"
  // showing only halfway). The fix clears the inline height so CSS governs.
  it("clears the composer's inline height on send (no clipped resting box)", async () => {
    seedThreadTab()
    renderScreen()
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()

    // Simulate a grown composer: the input handler measures scrollHeight (0 in
    // jsdom), so stamp a non-empty inline height to stand in for a multi-line
    // draft that had expanded the box.
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "a long draft that grew the composer" } })
    })
    textarea.style.height = "96px"
    expect(textarea.style.height).toBe("96px")

    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    // Inline height is cleared (falls back to CSS min-height + padding), never
    // left at the old too-short "24px" that crushed the box below one line.
    expect(textarea.style.height).toBe("")
    expect(textarea.style.height).not.toBe("24px")
  })

  // Regression (multi-turn context): a follow-up in a PLAIN chat tab that
  // already has a conversation id MUST forward that conversation_id so the
  // backend replays the prior turns (history). The old code only sent it for
  // PRD tabs, so normal chats silently lost all context — a follow-up like
  // "get all in to-do status" reached the model with no thread, and the scope
  // gate refused it as out-of-scope.
  it("forwards conversation_id on a follow-up in a plain (non-PRD) chat", async () => {
    const tabId = "tab-conv-100"
    sessionStorage.setItem(
      "sprntly_chat_tabs_anon_acme",
      JSON.stringify([
        {
          id: tabId,
          title: "Jira chat",
          thread: [
            {
              id: "turn-1",
              query: "can you get me tickets on jira?",
              reply: { answer: "Sure — give me an issue key or a status.", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" },
            },
          ],
          dbConvId: 100, // a conversation already exists (created on the first turn)
          briefMeta: null,
        },
      ]),
    )
    sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", tabId)
    renderScreen()

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "get all in to do status" } })
    })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    await waitFor(() => {
      expect(askedOpts.length).toBeGreaterThan(0)
    })
    const opts = askedOpts[askedOpts.length - 1]
    expect(opts?.conversation_id).toBe(100)
    // A plain chat tab carries no PRD id.
    expect(opts).not.toHaveProperty("prd_id")
  })

  // Regression (staging P1, 2026-07-30): the FIRST message of a tab must carry
  // a conversation_id too. The conversation row is created fire-and-forget by
  // pushPendingConversation, so the old code — which read `dbConvId` off the tab
  // synchronously — always sent the first ask with no conversation_id. Harmless
  // for history (there is none yet), but the id is what ATTACHES a captured HTML
  // report to the thread, and it is fixed at request time with nothing to
  // backfill it. A DS report generated as an opening message therefore landed
  // with conversation_id NULL and its "View report" opened an empty panel.
  // Every report skill was equally affected; VoC only looked healthy because
  // those runs happened to be follow-ups (see the test above, which pre-seeds
  // dbConvId and so never exercised this).
  it("forwards conversation_id on the FIRST message, awaiting the pending create", async () => {
    const { conversationsApi } = await import("../../../../lib/api")
    vi.mocked(conversationsApi.create).mockResolvedValueOnce({ id: 777 } as never)

    // The real first-message flow: a fresh chat, nothing persisted yet.
    searchString = "new=1"
    renderScreen()

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "analyze my data" } })
    })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    await waitFor(() => {
      expect(askedOpts.length).toBeGreaterThan(0)
    })
    expect(askedOpts[askedOpts.length - 1]?.conversation_id).toBe(777)
    // NB: awaiting shares the in-flight create rather than starting one — the
    // number of create calls in this harness is identical with and without the
    // await (measured), so the fix adds no conversation churn.
  })

  it("still sends the first ask when the conversation create fails", async () => {
    const { conversationsApi } = await import("../../../../lib/api")
    vi.mocked(conversationsApi.create).mockRejectedValueOnce(new Error("supabase down"))

    searchString = "new=1"
    renderScreen()

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "analyze my data" } })
    })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    await waitFor(() => {
      expect(askedOpts.length).toBeGreaterThan(0)
    })
    // The answer still happens — attachment is best-effort, never a gate on it.
    expect(askedOpts[askedOpts.length - 1]).not.toHaveProperty("conversation_id")
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
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    expect(textarea).toBeTruthy()
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "summarize this" } })
    })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
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

  // The thread renders the ASK + a file chip (like Claude's chat) — NOT the raw
  // document dump. The full content still rides the outgoing query to the
  // backend (asserted above / here), so this is purely a display change.
  it("shows the ask + a file chip in the thread, never the raw document content", async () => {
    seedThreadTab()
    renderScreen()
    const input = fileInput()
    expect(input).toBeTruthy()

    // A file whose content is a distinctive marker we can assert never renders.
    const MARKER = "TOP_SECRET_DOC_BODY_9137"
    await act(async () => {
      fireEvent.change(input!, { target: { files: [fakeFile("report.txt", `intro\n${MARKER}\noutro`)] } })
    })
    await waitFor(() => {
      const dock = document.querySelector(".bc-dock") as HTMLElement
      expect(within(dock).getByText("report.txt")).toBeTruthy()
    })

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "summarize the attached report" } })
    })
    const sendBtn = within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send")
    await act(async () => {
      fireEvent.click(sendBtn)
    })

    // The newest user turn shows the ask text (the seeded thread already has a
    // "first question" bubble, so assert on the LAST bubble)…
    const bubble = await waitFor(() => {
      const els = document.querySelectorAll(".bc-user-bubble")
      const last = els[els.length - 1] as HTMLElement | undefined
      expect(last?.textContent).toBe("summarize the attached report")
      return last as HTMLElement
    })
    expect(bubble.textContent).toBe("summarize the attached report")

    // …and a read-only attachment chip naming the file (in the turn, not the
    // composer dock — the dock's chip clears on send).
    const turnChip = document.querySelector('[data-testid="turn-attachment-chip"]')
    expect(turnChip).toBeTruthy()
    expect(turnChip!.textContent).toContain("report.txt")

    // The raw document body and the "[Attached files]" scaffolding are NOT
    // rendered anywhere in the thread — only the backend query carries them.
    expect(document.body.textContent).not.toContain(MARKER)
    expect(document.body.textContent).not.toContain("[Attached files]")

    // Backend still received the full content (display change only).
    await waitFor(() => expect(askedQueries.length).toBeGreaterThan(0))
    const sent = askedQueries[askedQueries.length - 1]
    expect(sent).toContain("summarize the attached report")
    expect(sent).toContain(MARKER)
    expect(sent).toContain("[Attached files]")
  })

  // Clicking a file card opens a viewer that renders the document content; the
  // content is NOT in the thread until then (proving the card, not a dump).
  it("opens a content viewer when the file card is clicked, and closes it", async () => {
    seedThreadTab()
    renderScreen()
    const input = fileInput()

    const MARKER = "VIEWER_CONTENT_MARKER_5521"
    await act(async () => {
      fireEvent.change(input!, { target: { files: [fakeFile("brief.txt", `line one\n${MARKER}\nline three`)] } })
    })
    await waitFor(() => {
      const dock = document.querySelector(".bc-dock") as HTMLElement
      expect(within(dock).getByText("brief.txt")).toBeTruthy()
    })

    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "read this please" } })
    })
    await act(async () => {
      fireEvent.click(within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send"))
    })

    // The card is present; the content is hidden until clicked.
    const card = await waitFor(() => {
      const c = document.querySelector('[data-testid="turn-attachment-chip"]') as HTMLButtonElement
      expect(c).toBeTruthy()
      return c
    })
    expect(document.body.textContent).not.toContain(MARKER)
    expect(document.querySelector('[role="dialog"]')).toBeNull()

    // Click → the viewer dialog opens and renders the content.
    await act(async () => {
      fireEvent.click(card)
    })
    const dialog = await waitFor(() => {
      const d = document.querySelector('[role="dialog"]') as HTMLElement
      expect(d).toBeTruthy()
      return d
    })
    expect(dialog.textContent).toContain(MARKER)
    expect(dialog.textContent).toContain("brief.txt")

    // Close via the close button → dialog gone, content hidden again.
    await act(async () => {
      fireEvent.click(within(dialog).getByLabelText("Close"))
    })
    await waitFor(() => expect(document.querySelector('[role="dialog"]')).toBeNull())
    expect(document.body.textContent).not.toContain(MARKER)
  })
})

// Wherever the composer is on screen, it holds the cursor — arriving on the page
// counts, not just clicking a tab. Before this, focus sat on the document body,
// so every visit cost a second click in the text box before you could type a
// single character.
describe("ChatScreen focuses the composer whenever it is on screen", () => {
  // The rule itself: no click anywhere, the composer still has the cursor.
  it("puts the cursor in the thread composer on arrival", async () => {
    seedThreadTab()
    renderScreen()

    await waitFor(() => {
      const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
      expect(ta).toBeTruthy()
      expect(document.activeElement).toBe(ta)
    })
  })

  // Same on the empty-chat landing, which is a DIFFERENT mount of the composer
  // (`.home-landing-composer`, not `.bc-dock`).
  it("puts the cursor in the landing composer on arrival", async () => {
    searchString = "new=1"
    renderScreen()

    expect(screen.getByText(/Welcome back/i)).toBeTruthy()
    await waitFor(() => {
      const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
      expect(document.activeElement).toBe(ta)
    })
  })

  it("puts the cursor back in the thread composer when a chat tab is clicked", async () => {
    seedThreadTab()
    renderScreen()

    // Drop focus first, so what this asserts is the CLICK's doing and not the
    // arrival focus above — a tab click reusing the same mount point does not
    // remount the composer, so it needs its own handler.
    const before = await waitFor(() => {
      const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
      expect(document.activeElement).toBe(ta)
      return ta
    })
    await act(async () => { before.blur() })
    expect(document.activeElement).not.toBe(before)

    const strip = screen.getByTestId("chat-tab-bar")
    await act(async () => { fireEvent.click(within(strip).getByText("Seeded chat")) })

    // Focus lands a frame after the click (the composer can remount as the view
    // swaps between the landing and the thread dock), hence waitFor.
    await waitFor(() => {
      const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
      expect(document.activeElement).toBe(ta)
    })
  })

  it("puts the cursor in the landing composer when + opens a new tab", async () => {
    seedThreadTab()
    renderScreen()

    // Scoped to the strip: the sidebar advertises a "New chat" control too.
    const strip = screen.getByTestId("chat-tab-bar")
    await act(async () => { fireEvent.click(within(strip).getByLabelText("New chat")) })

    // The fresh tab has no thread, so this is the LANDING composer — a different
    // mount point than the one that was on screen when the button was clicked.
    await waitFor(() => {
      expect(screen.getByText(/Welcome back/i)).toBeTruthy()
      const ta = document.querySelector(".cx-input") as HTMLTextAreaElement
      expect(document.activeElement).toBe(ta)
    })
  })
})
