// @vitest-environment jsdom
//
// ChatScreen DICTATION DOM tests — the microphone in the chat composer.
//
// The composer's no-microphone case lives in ChatScreen.composer.dom.test.tsx
// (jsdom has no speech API, which stands in for Firefox). This file covers the
// other half: a browser that DOES have the Web Speech API, with a fake
// recognizer installed on `window` and driven by hand.
//
// What is pinned here is the wiring between the engine and the draft, which is
// where a dictation feature actually goes wrong:
//   • the mic renders in BOTH composers (landing + thread dock) when supported
//   • spoken words land in the textarea, and the mic reads as recording
//   • speech APPENDS to what was already typed instead of wiping it
//   • the transcript is ASSIGNED, not accumulated — the engine re-sends the
//     whole phrase as it firms up, so appending would stutter it
//   • dictation NEVER auto-sends; Enter stays the user's
//   • sending stops the mic, so the cleared draft cannot be re-filled by the
//     tail of the question that was just sent
//   • a blocked microphone says so, in the composer, where the button is
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

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

// ── Boundary mocks (same set as the composer DOM test) ─────────────────────
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
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
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

// ── The fake speech engine ─────────────────────────────────────────────────
class FakeRecognition {
  static instances: FakeRecognition[] = []
  lang = ""
  continuous = false
  interimResults = false
  maxAlternatives = 0
  starts = 0
  stops = 0
  aborts = 0
  running = false
  onresult: ((e: { results: unknown }) => void) | null = null
  onerror: ((e: { error: string }) => void) | null = null
  onend: (() => void) | null = null

  constructor() {
    FakeRecognition.instances.push(this)
  }
  start() {
    if (this.running) throw new Error("InvalidStateError")
    this.running = true
    this.starts += 1
  }
  stop() {
    this.running = false
    this.stops += 1
  }
  abort() {
    this.running = false
    this.aborts += 1
  }
  /** The engine hands back the whole session list on every event. */
  emit(phrases: string[], isFinal = true) {
    const results = phrases.map((t) => ({ 0: { transcript: t }, isFinal, length: 1 }))
    this.onresult?.({ results: Object.assign(results, { length: results.length }) })
  }
  fail(error: string) {
    this.onerror?.({ error })
  }
}
const rec = () => FakeRecognition.instances[FakeRecognition.instances.length - 1]

function renderScreen() {
  return render(
    React.createElement(
      NavigationProvider,
      null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

function seedThreadTab() {
  sessionStorage.setItem(
    "sprntly_chat_tabs_anon_acme",
    JSON.stringify([
      {
        id: "tab-seed-1",
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
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-seed-1")
}

const textarea = () => document.querySelector(".cx-input") as HTMLTextAreaElement
const micButton = () => screen.queryByLabelText("Dictate your question") as HTMLButtonElement | null
const stopMicButton = () => screen.queryByLabelText("Stop dictating") as HTMLButtonElement | null

/** Turn the mic on and speak. Returns after React has flushed the transcript. */
async function dictate(phrases: string[]) {
  await act(async () => {
    fireEvent.click(micButton()!)
  })
  await act(async () => {
    rec().emit(phrases)
  })
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  searchString = ""
  askedQueries.length = 0
  FakeRecognition.instances = []
  // Chrome and Safari both expose the API under the webkit prefix, so that is
  // the one the composer must find.
  ;(window as unknown as Record<string, unknown>).webkitSpeechRecognition = FakeRecognition
})
afterEach(() => {
  cleanup()
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition
  localStorage.clear()
  sessionStorage.clear()
})

describe("the microphone is offered where the browser supports it", () => {
  it("renders the mic in the landing composer, on the right beside Send", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())
    expect(micButton()!.tagName).toBe("BUTTON")
    expect(micButton()!.getAttribute("aria-pressed")).toBe("false")

    // Placement is part of the ask: the mic lives in the composer's RIGHT
    // cluster, immediately before Send — not over with the `+` attach tools.
    const right = document.querySelector(".cx-right") as HTMLElement
    expect(right).toBeTruthy()
    expect(right.contains(micButton())).toBe(true)
    const order = Array.from(right.children)
    expect(order.indexOf(micButton()!)).toBe(order.findIndex((el) => el.classList.contains("cx-send")) - 1)
    expect((document.querySelector(".cx-tools") as HTMLElement).querySelector(".cx-mic")).toBeNull()
  })

  // One component, two mount points — the dock must not be able to drift.
  it("renders the mic in the thread composer", async () => {
    seedThreadTab()
    renderScreen()
    await screen.findByText("first question")
    const dock = document.querySelector(".bc-dock") as HTMLElement
    await waitFor(() => expect(within(dock).getByLabelText("Dictate your question")).toBeTruthy())
  })
})

describe("dictating into the composer", () => {
  it("puts the spoken words in the draft and shows the mic as recording", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await dictate(["why did enterprise churn rise last quarter"])

    expect(textarea().value).toBe("why did enterprise churn rise last quarter")
    // Recording must not look like idle. Three independent signals flip, and
    // this asserts all three so a restyle can't quietly drop one: the accessible
    // NAME (what a screen reader announces), the PRESSED state, and the
    // `is-recording` class (which inverts the button to solid red and starts the
    // pulse ring in globals.css).
    const stop = stopMicButton()
    expect(stop).toBeTruthy()
    expect(stop!.getAttribute("aria-pressed")).toBe("true")
    expect(stop!.classList.contains("is-recording")).toBe(true)
    expect(micButton()).toBeNull()
  })

  // The other half of "it looks different": the idle button carries none of it.
  it("carries no recording styling before the mic is switched on", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())
    expect(micButton()!.classList.contains("is-recording")).toBe(false)
    expect(micButton()!.getAttribute("aria-pressed")).toBe("false")
  })

  // The words must appear WHILE the user is still speaking, not after they stop.
  // That is what `interimResults` buys, and interim results are exactly the ones
  // a naive "only take isFinal" reader would throw away — leaving the box empty
  // for seconds at a time, which reads as a broken microphone.
  it("shows interim words in the textarea while the sentence is still being said", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await act(async () => {
      fireEvent.click(micButton()!)
    })
    // Nothing final yet — the user is mid-sentence.
    await act(async () => rec().emit(["why did"], false))
    expect(textarea().value).toBe("why did")
    await act(async () => rec().emit(["why did churn rise in"], false))
    expect(textarea().value).toBe("why did churn rise in")
    // …and the phrase firming up replaces the interim guess in place.
    await act(async () => rec().emit(["why did churn rise in June"], true))
    expect(textarea().value).toBe("why did churn rise in June")
  })

  // The engine re-sends the whole phrase as it firms up. Appending each event
  // would spell "why didwhy did churn rise".
  it("assigns the growing transcript instead of accumulating it", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await dictate(["why did"])
    expect(textarea().value).toBe("why did")
    await act(async () => rec().emit(["why did churn"]))
    expect(textarea().value).toBe("why did churn")
    await act(async () => rec().emit(["why did churn rise"]))
    expect(textarea().value).toBe("why did churn rise")
  })

  // Half-typed then finished aloud is one question. Wiping the draft on mic-on
  // would be a data-loss bug, not a styling one.
  it("appends speech to what was already typed", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await act(async () => {
      fireEvent.change(textarea(), { target: { value: "summarize" } })
    })
    await dictate(["the enterprise churn evidence"])

    expect(textarea().value).toBe("summarize the enterprise churn evidence")
  })

  it("stops dictating when the mic is clicked again, keeping the words", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await dictate(["what changed this week"])
    await act(async () => {
      fireEvent.click(stopMicButton()!)
    })

    expect(rec().stops).toBe(1)
    expect(textarea().value).toBe("what changed this week")
    expect(micButton()).toBeTruthy()
    expect(stopMicButton()).toBeNull()
  })

  // The load-bearing product rule. A transcript is close, not correct — names
  // and product nouns come back mangled — so auto-sending would spend a full
  // ask run on a question nobody asked.
  it("never sends on its own — the words only fill the draft", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await dictate(["draft a prd for the billing rewrite"])
    await act(async () => {
      fireEvent.click(stopMicButton()!)
    })

    expect(askedQueries).toHaveLength(0)
    expect(textarea().value).toBe("draft a prd for the billing rewrite")
  })

  // Regression guard: the hook's transcript is cumulative, so a mic left live
  // through a send would re-assign the whole spoken question into the draft the
  // send just cleared.
  it("stops the mic on send, and a late transcript cannot refill the cleared draft", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await dictate(["why did churn rise"])
    const engine = rec()

    await act(async () => {
      fireEvent.click(within(document.querySelector(".cx") as HTMLElement).getByLabelText("Send"))
    })
    await waitFor(() => expect(askedQueries).toHaveLength(1))
    expect(askedQueries[0]).toBe("why did churn rise")

    // The mic is off — ABORTED, not stopped. A graceful stop would still hand
    // back the phrase the engine was finalising, which is precisely the tail
    // this test is about.
    expect(engine.aborts).toBe(1)
    expect(engine.stops).toBe(0)
    await waitFor(() => expect(stopMicButton()).toBeNull())
    // …and the trailing event the engine still had in flight lands nowhere.
    await act(async () => engine.emit(["why did churn rise"]))
    expect(textarea().value).toBe("")
  })
})

describe("when the microphone is refused", () => {
  it("explains a blocked mic in the composer and turns the button off", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await act(async () => {
      fireEvent.click(micButton()!)
    })
    await act(async () => rec().fail("not-allowed"))

    const status = document.querySelector(".cx-hint") as HTMLElement
    expect(status).toBeTruthy()
    expect(status.getAttribute("role")).toBe("status")
    expect(status.textContent).toMatch(/blocked/i)
    // Back to an idle mic — not a button stuck reading "recording".
    expect(micButton()).toBeTruthy()
    expect(stopMicButton()).toBeNull()
  })

  // A pause mid-thought is not a failure, and must not put an error under the
  // composer while the user is still speaking.
  it("says nothing when the engine reports silence", async () => {
    searchString = "new=1"
    renderScreen()
    await waitFor(() => expect(micButton()).toBeTruthy())

    await act(async () => {
      fireEvent.click(micButton()!)
    })
    await act(async () => rec().fail("no-speech"))

    expect(document.querySelector(".cx-hint")).toBeNull()
    expect(stopMicButton()).toBeTruthy()
  })
})
