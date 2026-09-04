// Pure-function coverage for the individual-chat realtime append contract:
// parsing an unknown broadcast payload, the id/local-echo dedupe gate, and
// shaping a whitelisted DTO into the bare ThreadTurn the live thread appends.
// No DOM/React needed — these are plain module-scope helpers.
import { describe, expect, it } from "vitest"

import {
  applyRealtimeTurn,
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

  it("test_rejects_blank_content_on_either_role", () => {
    // Mirrors hydrate's own `content.trim()` guard on a standalone assistant
    // row — an empty/whitespace-only body never renders anything real and
    // must never reach the append/merge logic at all.
    expect(parseRealtimeTurnPayload({ id: 1, role: "assistant", content: "" })).toBeNull()
    expect(parseRealtimeTurnPayload({ id: 2, role: "assistant", content: "   " })).toBeNull()
    expect(parseRealtimeTurnPayload({ id: 3, role: "user", content: "\n\t" })).toBeNull()
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

describe("applyRealtimeTurn", () => {
  it("test_observer_tab_phantom_is_gone_user_then_assistant_produces_one_paired_bubble", () => {
    // THE bug this fix closes: on a tab with no optimistic echo (the
    // observer case — nobody there typed the ask), the user's turn.created
    // used to render as its own reply-less bubble ("No response was
    // generated for this message.") directly above the assistant's OWN
    // separate, headless bubble. After the fix, the pair collapses into ONE
    // ThreadTurn carrying both `query` and `reply`.
    let thread: ThreadTurn[] = []
    thread = applyRealtimeTurn(thread, { id: 4001, role: "user", content: "what changed since Friday?" })
    thread = applyRealtimeTurn(thread, { id: 4002, role: "assistant", content: "Three tickets moved to Done." })

    expect(thread).toHaveLength(1)
    expect(thread[0].query).toBe("what changed since Friday?")
    expect(thread[0].reply?.answer).toBe("Three tickets moved to Done.")
    // The user row's id is the one carried forward (needed for rewind) —
    // the assistant row's own id is intentionally NOT threaded onto the
    // merged turn (see the dedicated redelivery test below for how that
    // gap is still covered).
    expect(thread[0].dbTurnId).toBe(4001)
  })

  it("test_assistant_with_no_unpaired_user_turn_appends_standalone", () => {
    // A `brief.delivered` (or any assistant turn.created with no matching
    // ask already on this thread) has nothing to pair with — it must still
    // append as its own turn, exactly like before.
    const existing: ThreadTurn[] = [
      { id: "resumed-1-0", query: "status?", dbTurnId: 10, reply: { answer: "All green.", key_points: [], citations: [], confidence: 1, unanswered: "" } },
    ]
    const next = applyRealtimeTurn(existing, { id: 11, role: "assistant", content: "Delegated brief delivered." })
    expect(next).toHaveLength(2)
    expect(next[1].query).toBe("")
    expect(next[1].reply?.answer).toBe("Delegated brief delivered.")
  })

  it("test_assistant_on_an_empty_thread_appends_standalone", () => {
    const next = applyRealtimeTurn([], { id: 1, role: "assistant", content: "Hi there." })
    expect(next).toHaveLength(1)
    expect(next[0].reply?.answer).toBe("Hi there.")
  })

  it("test_user_turn_created_always_appends_bare_never_merges", () => {
    // A user turn.created never merges into anything — its pairing partner
    // (the reply) has not arrived yet, so it must always land as its own
    // reply-less turn, same as before.
    const existing: ThreadTurn[] = [
      { id: "resumed-1-0", query: "status?", dbTurnId: 10, reply: { answer: "All green.", key_points: [], citations: [], confidence: 1, unanswered: "" } },
    ]
    const next = applyRealtimeTurn(existing, { id: 11, role: "user", content: "what about the PRD?" })
    expect(next).toHaveLength(2)
    expect(next[1].query).toBe("what about the PRD?")
    expect(next[1].reply).toBeUndefined()
  })

  it("test_a_deduped_delivery_is_a_true_no_op", () => {
    // shouldAppendRealtimeTurn's own gate (id-dedupe) still blocks a repeat
    // delivery of an already-rendered row — applyRealtimeTurn must return
    // the SAME array reference (not just an equal one) on that path, since
    // its caller (`applyIncomingTurn`) uses reference equality to decide
    // whether a merge just happened.
    const existing: ThreadTurn[] = [
      { id: "resumed-1-0", query: "q", dbTurnId: 501, reply: { answer: "a", key_points: [], citations: [], confidence: 1, unanswered: "" } },
    ]
    const next = applyRealtimeTurn(existing, { id: 501, role: "assistant", content: "a" })
    expect(next).toBe(existing)
  })

  it("test_mergedReplyIds_blocks_a_redelivery_of_an_already_merged_assistant_row", () => {
    // The merged turn's `dbTurnId` stays pinned to the USER row's id (never
    // the assistant's), so the plain dbTurnId dedupe alone can't catch a
    // second delivery of that SAME assistant row. `mergedReplyIds` is the
    // second, independent guard — the caller is expected to add a payload's
    // id to it the moment a merge (not an append) happens.
    let thread: ThreadTurn[] = [{ id: "t1", query: "q", dbTurnId: 200 }]
    const mergedReplyIds = new Set<number>()
    thread = applyRealtimeTurn(thread, { id: 201, role: "assistant", content: "the answer" }, mergedReplyIds)
    mergedReplyIds.add(201) // what applyIncomingTurn's post-hoc detection would do
    expect(thread[0].reply?.answer).toBe("the answer")

    // Same assistant row redelivered (e.g. a flaky reconnect) — must be a
    // true no-op, not a second merge attempt or a stray standalone append.
    const redelivered = applyRealtimeTurn(thread, { id: 201, role: "assistant", content: "the answer" }, mergedReplyIds)
    expect(redelivered).toBe(thread)
    expect(redelivered).toHaveLength(1)
  })
})
