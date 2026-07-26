// @vitest-environment jsdom
//
// ChatScreen — a stored attachment renders the ORIGINAL file, not just its text.
//
// When a turn attachment carries a storage `key`, the chip is downloadable and
// its viewer fetches a fresh signed URL and renders the real document — a PDF in
// an <iframe>, an image in an <img>. This is what lets a user reopen a chat and
// view/download the file they uploaded (the reported gap: attachments saved only
// name + empty content, so nothing could be rendered back).
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

const sign = vi.fn().mockResolvedValue({
  view_url: "https://storage.example/signed/guide.pdf?token=abc",
  download_url: "https://storage.example/signed/guide.pdf?token=abc&download=guide.pdf",
  mime: "application/pdf",
})

vi.mock("../../../../lib/api", () => {
  class ApiError extends Error { status = 0; body: unknown = null }
  return {
    ApiError,
    askApi: { ask: vi.fn(), skills: vi.fn().mockResolvedValue({ skills: [] }) },
    briefApi: { current: vi.fn().mockResolvedValue({ id: 1, insights: [] }) },
    conversationsApi: {
      create: vi.fn().mockResolvedValue({ id: 1 }),
      addTurn: vi.fn().mockResolvedValue({}),
      byPrd: vi.fn().mockResolvedValue({ conversation: null, turns: [] }),
    },
    attachmentsApi: { upload: vi.fn(), sign: (...a: unknown[]) => sign(...a) },
  }
})

vi.mock("../../../../lib/runPrdGeneration", () => ({
  runPrdGeneration: vi.fn(), resumePrdGeneration: vi.fn(),
  runPrdGenerationFromIdeation: vi.fn(), loadPrdById: vi.fn(),
}))
vi.mock("../../../../lib/runAskGeneration", () => ({
  runAskGeneration: vi.fn(), resumeAskGeneration: vi.fn(), getPendingAsk: vi.fn(() => null),
}))
vi.mock("../../../../lib/usePipelineStatus", () => ({
  usePipelineStatus: () => ({ runStatus: null, isTriggering: false, showCompleted: false, triggerRun: vi.fn() }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/", useSearchParams: () => new URLSearchParams(""),
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
  useBriefPrototypeMap: () => ({ entriesByInsight: new Map(), loading: false, refetch: vi.fn() }),
}))

import { NavigationProvider } from "../../../../context/NavigationContext"
import { ContentProvider } from "../../../../context/ContentContext"
import { ChatScreen } from "../ChatScreen"

function mountApp() {
  return render(
    React.createElement(
      NavigationProvider, null,
      React.createElement(ContentProvider, null, React.createElement(ChatScreen)),
    ),
  )
}

function seedThreadWithAttachment(mime: string, name: string) {
  sessionStorage.setItem("sprntly_chat_tabs_anon_acme", JSON.stringify([{
    id: "tab-a", title: "Chat", thread: [{
      id: "t1", query: "here's the guide",
      attachments: [{ name, content: "", key: "chat-attachments/w/abc." + name.split(".").pop(), mime }],
      reply: { answer: "Got it.", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" },
    }],
    dbConvId: 1, briefMeta: null, insightBody: null, prdId: null,
  }]))
  sessionStorage.setItem("sprntly_chat_active_tab_anon_acme", "tab-a")
}

beforeEach(() => {
  localStorage.clear(); sessionStorage.clear(); sign.mockClear()
})
afterEach(() => { cleanup(); localStorage.clear(); sessionStorage.clear() })

describe("ChatScreen — stored-attachment viewer", () => {
  it("a stored attachment is downloadable and opens a PDF inline via a fresh signed URL", async () => {
    seedThreadWithAttachment("application/pdf", "guide.pdf")
    await act(async () => { mountApp() })

    // The chip is present; there is NO inline download button on the file chip.
    const chip = await screen.findByTestId("turn-attachment-chip")
    expect(screen.queryByTestId("turn-attachment-download")).toBeNull()

    // Clicking the chip opens the viewer, which signs the key and renders a PDF
    // iframe pointed at the fresh view URL.
    await act(async () => { fireEvent.click(chip) })
    await waitFor(() => expect(sign).toHaveBeenCalledWith("chat-attachments/w/abc.pdf", "guide.pdf"))
    await waitFor(() => {
      const frame = document.querySelector('[data-testid="attachment-pdf-frame"]') as HTMLIFrameElement
      expect(frame).toBeTruthy()
      expect(frame.getAttribute("src")).toContain("signed/guide.pdf")
    })
  })

  it("an image attachment renders in an <img>", async () => {
    sign.mockResolvedValueOnce({
      view_url: "https://storage.example/signed/pic.png?t=1",
      download_url: "https://storage.example/signed/pic.png?t=1&download=pic.png",
      mime: "image/png",
    })
    seedThreadWithAttachment("image/png", "pic.png")
    await act(async () => { mountApp() })

    await act(async () => { fireEvent.click(await screen.findByTestId("turn-attachment-chip")) })
    await waitFor(() => {
      const img = document.querySelector('[data-testid="attachment-image"]') as HTMLImageElement
      expect(img).toBeTruthy()
      expect(img.getAttribute("src")).toContain("signed/pic.png")
    })
  })
})
