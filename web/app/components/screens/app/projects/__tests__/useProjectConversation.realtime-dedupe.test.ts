// Pure-function coverage for the individual-chat realtime append contract:
// parsing an unknown broadcast payload, the id/local-echo dedupe gate, and
// shaping a whitelisted DTO into the bare ThreadTurn the live thread appends.
// No DOM/React needed — these are plain module-scope helpers.
import { describe, expect, it } from "vitest"

import {
  parseRealtimeTurnPayload,
  realtimeTurnToThreadTurn,
  shouldAppendRealtimeTurn,
} from "../useProjectConversation"
import type { ThreadTurn } from "../../ChatScreen"

describe("parseRealtimeTurnPayload", () => {
  it("test_parses_a_well_formed_assistant_payload", () => {
    const parsed = parseRealtimeTurnPayload({
      id: 501, role: "assistant", content: "Done.", created_at: "2026-09-02T00:00:00Z",
    })
    expect(parsed).toEqual({ id: 501, role: "assistant", content: "Done.", created_at: "2026-09-02T00:00:00Z" })
  })

  it("test_rejects_malformed_or_future_shaped_payloads", () => {
    expect(parseRealtimeTurnPayload(null)).toBeNull()
    expect(parseRealtimeTurnPayload(undefined)).toBeNull()
    expect(parseRealtimeTurnPayload({})).toBeNull()
    expect(parseRealtimeTurnPayload({ id: "501", role: "assistant", content: "x" })).toBeNull()
    expect(parseRealtimeTurnPayload({ id: 1, role: "peer", content: "x" })).toBeNull()
    expect(parseRealtimeTurnPayload({ id: 1, role: "user" })).toBeNull()
  })
})

describe("realtimeTurnToThreadTurn", () => {
  it("test_shapes_a_user_turn_as_a_bare_query", () => {
    const turn = realtimeTurnToThreadTurn({ id: 10, role: "user", content: "what happened last week?" })
    expect(turn.dbTurnId).toBe(10)
    expect(turn.query).toBe("what happened last week?")
    expect(turn.reply).toBeUndefined()
  })

  it("test_shapes_an_assistant_turn_as_a_bare_reply", () => {
    const turn = realtimeTurnToThreadTurn({ id: 11, role: "assistant", content: "Here's the update." })
    expect(turn.dbTurnId).toBe(11)
    expect(turn.query).toBe("")
    expect(turn.reply?.answer).toBe("Here's the update.")
  })

  it("test_two_shaped_turns_never_collide_on_client_id", () => {
    const a = realtimeTurnToThreadTurn({ id: 1, role: "user", content: "x" })
    const b = realtimeTurnToThreadTurn({ id: 2, role: "user", content: "y" })
    expect(a.id).not.toBe(b.id)
  })
})

describe("shouldAppendRealtimeTurn", () => {
  it("test_appends_a_brand_new_turn_to_an_empty_thread", () => {
    expect(shouldAppendRealtimeTurn([], { id: 1, role: "assistant", content: "hi" })).toBe(true)
  })

  it("test_id_dedupe_skips_a_row_already_rendered_dbTurnId_match", () => {
    const existing: ThreadTurn[] = [
      { id: "resumed-1-0", query: "q", dbTurnId: 501, reply: { answer: "a", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } },
    ]
    // A REPEAT delivery of the SAME db row (a re-broadcast, or a reconcile
    // refetching something already rendered) must not append twice.
    expect(shouldAppendRealtimeTurn(existing, { id: 501, role: "assistant", content: "a" })).toBe(false)
  })

  it("test_local_echo_race_skips_the_clients_own_optimistic_turn", () => {
    // The PRD-edit path's local echo: `emitTurn` already rendered this exact
    // instruction as a query turn (no dbTurnId yet — not reconciled with its
    // row), and the server's `turn.created` broadcast for the SAME write
    // arrives moments later. Must not double-render.
    const existing: ThreadTurn[] = [
      { id: "local-echo-1", query: "tighten requirements" },
    ]
    expect(shouldAppendRealtimeTurn(existing, { id: 900, role: "user", content: "tighten requirements" })).toBe(false)
  })

  it("test_local_echo_race_skips_the_clients_own_optimistic_reply", () => {
    const existing: ThreadTurn[] = [
      { id: "local-echo-2", query: "tighten requirements", reply: { answer: "Done — I've updated the PRD.", sources: [], follow_ups: [], key_points: [], citations: [], confidence: 1, unanswered: "" } },
    ]
    expect(shouldAppendRealtimeTurn(existing, { id: 901, role: "assistant", content: "Done — I've updated the PRD." })).toBe(false)
  })

  it("test_a_genuinely_new_turn_with_different_content_still_appends", () => {
    const existing: ThreadTurn[] = [
      { id: "local-echo-3", query: "tighten requirements" },
    ]
    expect(shouldAppendRealtimeTurn(existing, { id: 902, role: "assistant", content: "Done — I've updated the PRD." })).toBe(true)
  })

  it("test_a_reconciled_turn_no_longer_shields_a_later_repeat_of_its_content_far_back", () => {
    // Once a turn HAS a dbTurnId (already reconciled), it no longer counts as
    // an "unreconciled local echo" — only the id-dedupe check (above) can
    // still block a repeat of ITS OWN id; a distinct new id with the same
    // text is a legitimate new message (e.g. the user asks the same
    // question again) and must append.
    const existing: ThreadTurn[] = [
      { id: "resumed-1-0", query: "status?", dbTurnId: 10 },
    ]
    expect(shouldAppendRealtimeTurn(existing, { id: 99, role: "user", content: "status?" })).toBe(true)
  })

  it("test_local_echo_window_is_bounded_to_recent_turns", () => {
    // A genuine repeat message FAR EARLIER in history (well outside the
    // recent window) is never suppressed as a false-positive echo match.
    const existing: ThreadTurn[] = [
      { id: "old-1", query: "status?", dbTurnId: 1 },
      { id: "pad-1", query: "a", dbTurnId: 2 },
      { id: "pad-2", query: "b", dbTurnId: 3 },
      { id: "pad-3", query: "c", dbTurnId: 4 },
      { id: "pad-4", query: "d", dbTurnId: 5 },
      { id: "pad-5", query: "e", dbTurnId: 6 },
      { id: "pad-6", query: "f", dbTurnId: 7 },
    ]
    expect(shouldAppendRealtimeTurn(existing, { id: 8, role: "user", content: "status?" })).toBe(true)
  })
})
