// @vitest-environment jsdom
//
// Internal transcript viewer (the hidden /r7k2m9x4qp8w route) — conversation
// rendering.
//
// The panel used to print turn content as raw text, so a reviewer saw literal
// `**bold**` markers and, for a generated artifact, a whole `<!DOCTYPE html>…`
// source dump. It now delegates assistant turns to AskReplyBody — the SAME
// component the chat thread renders replies with — so a transcript reads the
// way the customer saw it. Covers:
//   - a markdown prose turn renders formatted (a real <strong>, no literal `**`),
//   - a full-HTML turn renders through the artifact path: a SANDBOXED iframe
//     carrying the document in srcdoc, never printed source,
//   - the sandbox stays scripts-off (this surface shows untrusted, cross-tenant
//     customer content to a staff viewer),
//   - USER / AI labels and turn order survive,
//   - the panel stays read-only: no composer or regenerate affordance.
//
// transcriptsApi/transcriptsAuth are mocked at the lib/api boundary (the
// adjacent StaffAdminScreen suite's convention) so mounting hits no network.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// jsdom doesn't implement window.matchMedia; AskReplyBody's typing-animation
// hook reads it on mount.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

const {
  listCompanies,
  listConversations,
  getConversation,
  transcriptsLogin,
  transcriptsLogout,
  transcriptsHasToken,
  FakeApiError,
} = vi.hoisted(() => {
  class FakeApiError extends Error {
    status: number
    body: unknown
    constructor(status: number) {
      super(`Request failed (${status})`)
      this.status = status
      this.body = null
    }
  }
  return {
    listCompanies: vi.fn(),
    listConversations: vi.fn(),
    getConversation: vi.fn(),
    transcriptsLogin: vi.fn(),
    transcriptsLogout: vi.fn(),
    transcriptsHasToken: vi.fn(),
    FakeApiError,
  }
})

vi.mock("../../../../lib/api", () => ({
  ApiError: FakeApiError,
  transcriptsApi: { listCompanies, listConversations, getConversation },
  transcriptsAuth: {
    login: transcriptsLogin,
    logout: transcriptsLogout,
    hasToken: transcriptsHasToken,
  },
}))

import { TranscriptsScreen } from "../TranscriptsScreen"

const CONV = {
  id: 7,
  company_id: "co-1",
  company_name: "Acme Corp",
  user_id: "u-1",
  user_label: "Dana PM",
  title: "Churn drivers",
  preview: "why are we losing…",
  agent_type: "pm",
  prd_id: null,
  turn_count: 2,
  created_at: "2026-07-18T10:00:00Z",
  updated_at: "2026-07-18T10:05:00Z",
}

const MARKDOWN_TURN = [
  "**Methodology note:** this blends two sources.",
  "",
  "> Pricing came up in 4 of 9 calls.",
  "",
  "---",
  "",
  "[Source: customer_voice/finding]",
].join("\n")

const HTML_ARTIFACT = `<!DOCTYPE html><html lang="en"><head><style>body{font:14px sans-serif}</style></head><body><h1>Checkout PRD</h1></body></html>`

function detailWith(turns: { id: number; role: "user" | "assistant"; content: string }[]) {
  return {
    conversation: { ...CONV, query: "", reply: "" },
    turns: turns.map((t) => ({ ...t, created_at: null })),
  }
}

beforeEach(() => {
  transcriptsHasToken.mockReturnValue(true)
  listConversations.mockResolvedValue({ conversations: [CONV], has_more: false })
  listCompanies.mockResolvedValue({
    companies: [{ id: "co-1", display_name: "Acme Corp" }],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

/** Mount signed-in and open the one conversation row. */
async function openDrawer() {
  await act(async () => {
    render(<TranscriptsScreen />)
  })
  await act(async () => {
    fireEvent.click(screen.getByText("Churn drivers"))
  })
}

function drawer(): HTMLElement {
  return screen.getByRole("dialog", { name: "Conversation" })
}

describe("TranscriptsScreen conversation rendering", () => {
  it("renders a markdown prose turn formatted, not as literal source", async () => {
    getConversation.mockResolvedValue(
      detailWith([
        { id: 1, role: "user", content: "Why are customers churning?" },
        { id: 2, role: "assistant", content: MARKDOWN_TURN },
      ]),
    )
    await openDrawer()

    const panel = drawer()
    // The markdown ran through the renderer: real elements, not asterisks.
    expect(panel.querySelector("strong")?.textContent).toBe("Methodology note:")
    expect(panel.querySelector("blockquote")).toBeTruthy()
    expect(panel.querySelector("hr")).toBeTruthy()
    expect(panel.textContent).not.toContain("**")
    expect(panel.textContent).not.toContain("> Pricing")
    // Prose stays in the DOM (not iframed) — this is not an artifact.
    expect(panel.querySelector("iframe")).toBeNull()
    // The chat's own markdown wrapper class, i.e. the shared renderer ran.
    expect(panel.querySelector(".ai-bar-reply-answer")).toBeTruthy()
  })

  it("renders a full-HTML turn through the sandboxed artifact iframe", async () => {
    getConversation.mockResolvedValue(
      detailWith([
        { id: 1, role: "user", content: "Write the checkout PRD" },
        { id: 2, role: "assistant", content: HTML_ARTIFACT },
      ]),
    )
    await openDrawer()

    const panel = drawer()
    const frame = panel.querySelector("iframe")
    expect(frame).toBeTruthy()
    expect(frame?.getAttribute("srcdoc")).toContain("<!DOCTYPE html>")
    // The source is NOT printed into the page.
    expect(panel.textContent).not.toContain("<!DOCTYPE")
    expect(panel.textContent).not.toContain("<style>")
    // Untrusted cross-tenant HTML: scripts must stay off. allow-same-origin
    // alone lets the frame be measured; it cannot execute anything.
    expect(frame?.getAttribute("sandbox")).toBe("allow-same-origin")
    expect(frame?.getAttribute("sandbox")).not.toContain("allow-scripts")
    // The artifact's markup exists only inside the frame's srcdoc — it was never
    // parsed into the host document (no dangerouslySetInnerHTML path).
    expect(panel.querySelector("h1")).toBeNull()
    expect(panel.querySelector("style")).toBeNull()
  })

  it("keeps role labels, turn order, and a read-only panel", async () => {
    getConversation.mockResolvedValue(
      detailWith([
        { id: 1, role: "user", content: "First question" },
        { id: 2, role: "assistant", content: "An **answer**." },
      ]),
    )
    await openDrawer()

    const panel = drawer()
    const roles = Array.from(panel.querySelectorAll(".tvw-turn-role")).map(
      (n) => n.textContent,
    )
    expect(roles).toEqual(["User", "AI"])
    // The member's words are shown verbatim.
    expect(screen.getByText("First question")).toBeTruthy()

    // Read-only: nothing to type into, nothing that would mutate a customer's
    // conversation.
    expect(panel.querySelector("textarea")).toBeNull()
    expect(panel.querySelector("input")).toBeNull()
    const labels = Array.from(panel.querySelectorAll("button")).map((b) =>
      (b.textContent || "").toLowerCase(),
    )
    expect(labels).toEqual(["close"])
  })

  it("renders legacy query/reply rows (no turn rows) through the same path", async () => {
    getConversation.mockResolvedValue({
      conversation: { ...CONV, query: "Old question", reply: "Old **answer**." },
      turns: [],
    })
    await openDrawer()

    const panel = drawer()
    expect(screen.getByText("Old question")).toBeTruthy()
    expect(panel.querySelector("strong")?.textContent).toBe("answer")
  })
})
