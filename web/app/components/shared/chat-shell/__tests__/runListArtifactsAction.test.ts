// runListArtifactsAction — "just show me the prd's that i created" answers with
// the PRDs, not with a sentence about them.
//
// Two things are pinned here, and both come from the same reported turn: the
// answer said "Here are your 12 newest PRDs — click one to open it with its
// chat." The rows are a CAPPED page (`chat_envelope._MAX_CHAT_ARTIFACTS`), and
// nothing on this side can tell a page of twelve from a library of exactly
// twelve — so that "12" reported a cap as a total, to a reader who had asked
// to be shown their PRDs and never asked how many there were. And the rows
// themselves must ride the turn, because they are the answer.
import { describe, expect, it, vi } from "vitest"
import { runListArtifactsAction } from "../conversation/actions"
import type { ChatArtifactItem, ChatIntentEnvelope } from "../../../../lib/api"

vi.mock("../../../../lib/api", () => ({
  projectsApi: {},
  slackShareApi: {},
  ticketDataApi: {},
}))

const row = (id: number, title: string): ChatArtifactItem =>
  ({
    id, type: "prd", title, created_at: "2026-08-24T16:12:00Z",
    open: { prd_id: id }, source: { conversation_id: id, conversation_title: title },
  }) as ChatArtifactItem

function envelope(items: ChatArtifactItem[], overrides = {}): ChatIntentEnvelope {
  return {
    intent: "list_artifacts",
    confidence: 0.9,
    list_kind: "prd",
    artifact_list: items,
    ...overrides,
  } as unknown as ChatIntentEnvelope
}

function emitted(items: ChatArtifactItem[], overrides = {}) {
  const emitTurn = vi.fn()
  runListArtifactsAction("show me my prds", envelope(items, overrides), { emitTurn } as never)
  return emitTurn.mock.calls[0][0]
}

describe("runListArtifactsAction", () => {
  it("carries the rows on the turn, so the answer IS the PRDs", () => {
    const items = [row(3827, "Checkout margin fix"), row(3828, "AI SOC collaboration")]
    const turn = emitted(items)
    expect(turn.artifactList).toEqual(items)
  })

  it("does not report the capped page as a count", () => {
    const items = Array.from({ length: 12 }, (_, i) => row(3800 + i, `PRD ${i}`))
    const answer = emitted(items).reply.answer
    expect(answer).not.toContain("12")
    expect(answer).toContain("your most recent PRDs")
    expect(answer).toContain("click one to open it with its chat")
  })

  it("still says 'most recent' in the singular when there is exactly one", () => {
    const turn = emitted([row(3827, "Checkout margin fix")])
    expect(turn.reply.answer).toContain("Here's your most recent PRD")
    expect(turn.artifactList).toHaveLength(1)
  })

  it("says plainly that there are none, and carries no empty card list", () => {
    const turn = emitted([])
    expect(turn.reply.answer).toContain("You haven't created any PRDs yet")
    expect(turn.artifactList).toBeUndefined()
  })

  it("leads a how-many ask with the server-computed numbers", () => {
    // A COUNT ask is a different question and keeps its numbers — they are
    // computed over the whole library, not counted off the capped page.
    const items = [row(3827, "Checkout margin fix")]
    const answer = emitted(items, {
      list_mode: "count",
      artifact_counts: { today: 2, yesterday: 1, total: 9 },
    }).reply.answer
    expect(answer).toContain("You've created 2 PRDs today and 1 yesterday")
    expect(answer).toContain("9 in total")
  })
})
