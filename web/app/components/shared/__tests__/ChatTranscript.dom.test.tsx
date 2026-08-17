// @vitest-environment jsdom
//
// ChatTranscript composes ChatBubble over an already-computed turn list —
// orchestration (which turns, which signals) is the caller's job; this only
// renders the list, plus its reply-adjacent card slot inside each turn.
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
  if (typeof window !== "undefined" && !window.matchMedia) {
    window.matchMedia = ((q: string) => ({
      matches: false, media: q, onchange: null,
      addEventListener() {}, removeEventListener() {},
      addListener() {}, removeListener() {}, dispatchEvent() { return false },
    })) as unknown as typeof window.matchMedia
  }
})

import { ChatTranscript, type ChatTranscriptTurn } from "../ChatTranscript"
import type { ChatArtifactItem } from "../../../lib/api"

afterEach(cleanup)

const REPLY = {
  answer: "Answered.", key_points: [], citations: [], confidence: 1, unanswered: "",
}

describe("ChatTranscript", () => {
  it("renders every turn in order, plus a reply-adjacent card slot", () => {
    const turns: ChatTranscriptTurn[] = [
      { turnId: "a", agentName: "Sprntly", user: { name: "Reader", query: "First question" }, reply: REPLY },
      {
        turnId: "b",
        agentName: "Sprntly",
        user: { name: "Reader", query: "What are my PRDs?" },
        reply: REPLY,
        artifactList: [
          {
            id: 1, type: "prd", title: "Onboarding PRD", created_at: "2026-08-01T00:00:00Z",
            source: { conversation_id: null, conversation_title: null },
          } as unknown as ChatArtifactItem,
        ],
        onOpenArtifactItem: () => {},
      },
    ]
    const { container, getByText } = render(<ChatTranscript turns={turns} />)
    expect(getByText("First question")).not.toBeNull()
    expect(getByText("What are my PRDs?")).not.toBeNull()
    expect(container.querySelector('[data-testid="artifact-list-cards"]')).not.toBeNull()
    expect(getByText("Onboarding PRD")).not.toBeNull()
  })

  it("renders the leading and trailing slots around the turn list", () => {
    const turns: ChatTranscriptTurn[] = [
      { turnId: "a", agentName: "Sprntly", user: { name: "Reader", query: "Hi" }, reply: REPLY },
    ]
    const { container } = render(
      <ChatTranscript
        turns={turns}
        leading={<div data-testid="leading-slot" />}
        trailing={<div data-testid="trailing-slot" />}
      />,
    )
    const leading = container.querySelector('[data-testid="leading-slot"]')
    const trailing = container.querySelector('[data-testid="trailing-slot"]')
    expect(leading).not.toBeNull()
    expect(trailing).not.toBeNull()
    // Leading precedes the turn list, trailing follows it.
    expect(leading!.compareDocumentPosition(trailing!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it("renders nothing extra when turns is empty", () => {
    const { container } = render(<ChatTranscript turns={[]} />)
    expect(container.querySelectorAll(".bc-turn").length).toBe(0)
  })
})
