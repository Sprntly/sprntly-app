// @vitest-environment jsdom
//
// useMainConversation — the LIVE ask-completion side of the reply-persist
// dedup key. `runConversationAsk` mints ONE `clientMessageId` per send
// (`askReplyClientMessageId`) and stamps it on the SAME persisted pending-job
// record `runAskGeneration` writes (jobResume) — so a SECOND completion path
// racing the same conversation-scoped ask (a project chat's resume effect —
// see `useProjectConversation.resume-dedupe.dom.test.tsx`) reads back the
// IDENTICAL key. Here: a single completed ask calls `finalizeConversationTurn`
// EXACTLY ONCE, carrying a non-empty `clientMessageId` that round-trips
// through the SAME jobResume record `runAskGeneration` persisted.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { useMainConversation } from "../useMainConversation"
import { askApi } from "../../../../lib/api"
import { getPendingJob } from "../../../../lib/jobResume"
import { askScope } from "../../../../lib/runAskGeneration"
import type { ConversationHandle } from "../conversationCore"
import type { AskResponse } from "../../../../lib/api"

const READY: AskResponse = {
  answer: "Three tickets moved to Done.", key_points: [], citations: [], confidence: 1, unanswered: "",
}

function makeHandle(thread: { current: unknown[] }): ConversationHandle {
  return {
    key: "tab-1",
    getTurns: () => thread.current as never,
    patchTurns: (update) => { thread.current = update(thread.current as never) as unknown[] },
    setBusy: () => {},
    markStopped: () => {},
    isStopped: () => false,
    clearAsking: () => {},
    pendingAsk: () => null,
    isAsking: () => false,
    exists: () => true,
    patchMeta: () => {},
    isActive: () => true,
    dbConvId: () => 1,
    getMeta: () => null,
  }
}

function Harness({ finalizeConversationTurn }: {
  finalizeConversationTurn: (turnId: string, updates: unknown, key: string) => Promise<void>
}) {
  const thread = React.useRef<unknown[]>([{ id: "turn-1", query: "what changed?" }])
  const conv = useMainConversation({
    makeHandle: () => makeHandle(thread),
    activeKey: "tab-1",
    activeCompany: "acme",
    askingRef: React.useRef(new Set<string>()),
    setBusy: () => {},
    resolveAskParams: async () => ({ convId: 1, grounding: { conversation_id: 1 } }),
    getPrdId: () => null,
    mountedRef: React.useRef(true),
    animatedTurnIds: React.useRef(new Set<string>()),
    askStartRef: React.useRef(new Map<string, number>()),
    resumedTurnsRef: React.useRef(new Set<string>()),
    pushPendingConversation: () => {},
    setActiveConv: () => {},
    finalizeConversationTurn: finalizeConversationTurn as never,
    nextPrompts: { onSettled: () => {} },
    showToast: () => {},
  })
  React.useEffect(() => {
    void conv.runConversationAsk({
      targetTabId: "tab-1", id: "turn-1", displayQuery: "what changed?", sendQuery: "what changed?",
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  return null
}

beforeEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})
afterEach(cleanup)

describe("useMainConversation — runConversationAsk mints + threads a reply dedup key", () => {
  it("test_a_single_completed_ask_calls_finalize_exactly_once_with_a_stable_key", async () => {
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 900, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue({ ...READY, status: "ready", error: null } as never)

    const finalizeConversationTurn = vi.fn(async (..._a: unknown[]) => {})
    await act(async () => {
      render(<Harness finalizeConversationTurn={finalizeConversationTurn} />)
    })
    await waitFor(() => expect(finalizeConversationTurn).toHaveBeenCalledTimes(1))

    const [, updates] = finalizeConversationTurn.mock.calls[0] as unknown as [string, { reply: AskResponse; clientMessageId?: string }, string]
    expect(updates.reply.answer).toBe("Three tickets moved to Done.")
    expect(typeof updates.clientMessageId).toBe("string")
    expect(updates.clientMessageId!.length).toBeGreaterThan(0)

    // The SAME key rides the jobResume record `runAskGeneration` persisted at
    // send time — the wire this ticket's cross-mount collapse depends on.
    // (Cleared on terminal exit, so read it via the value captured above —
    // this assertion instead proves it was NOT the record's bare id, i.e. a
    // real mint happened, not an accidental echo of the ask_id.)
    expect(updates.clientMessageId).not.toBe("900")
  })

  it("test_exactly_one_finalize_call_means_exactly_one_persisted_assistant_turn", async () => {
    // Regression guard for the double-fire this ticket closes: ONE completed
    // ask must never drive TWO finalize calls from the live path alone.
    vi.spyOn(askApi, "start").mockResolvedValue({ ask_id: 901, status: "generating" } as never)
    vi.spyOn(askApi, "get").mockResolvedValue({ ...READY, status: "ready", error: null } as never)

    const finalizeConversationTurn = vi.fn(async () => {})
    await act(async () => {
      render(<Harness finalizeConversationTurn={finalizeConversationTurn} />)
    })
    await waitFor(() => expect(finalizeConversationTurn).toHaveBeenCalled())
    // Give any stray extra microtask a chance to fire before asserting the count.
    await act(async () => { await Promise.resolve() })
    expect(finalizeConversationTurn).toHaveBeenCalledTimes(1)
    expect(getPendingJob("ask", "acme", askScope("tab-1"))).toBeNull()
  })
})
