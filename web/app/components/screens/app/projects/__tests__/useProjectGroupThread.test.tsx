// @vitest-environment jsdom
//
// useProjectGroupThread — the group-thread transport engine. `useRealtimeChannel`
// is mocked (its own lifecycle is covered elsewhere); this file asserts the
// engine's transport contract: since-reconcile dedup, optimistic send +
// rollback + same-content guard, cross-turn invokedBy precompute,
// posting/stayed-out state, presence/typing exposure (the review-flagged), the
// engine-owned failure-restore CAS, and the two named intended fixes — the
// gap-burning cursor and generation-safety (realtime-before-load, merge-sorted,
// stale-project-drop).
import * as React from "react"
import { act, cleanup, render } from "@testing-library/react"
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

const groupTurnsMock = vi.fn()
const postGroupTurnMock = vi.fn()
let authState:
  | { kind: "authed"; user: { id: string; user_metadata?: unknown; email?: string | null } }
  | { kind: "anonymous" } = {
  kind: "authed",
  user: { id: "u1" },
}

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      groupTurns: (...a: unknown[]) => groupTurnsMock(...a),
      postGroupTurn: (...a: unknown[]) => postGroupTurnMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({ useAuth: () => authState }))

const { realtimeSpy, realtimeState } = vi.hoisted(() => ({
  realtimeSpy: vi.fn(),
  realtimeState: {
    degraded: true,
    presenceMembers: [] as { userId: string; name: string }[],
    typers: [] as { userId: string; name: string }[],
    sendTyping: vi.fn(),
  },
}))
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (
    topic: string | null,
    handlers: { onEvent?: (e: string, p: unknown) => void; onReconcile?: () => void },
  ) => {
    realtimeSpy(topic, handlers)
    return {
      status: realtimeState.degraded ? "degraded" : "live",
      degraded: realtimeState.degraded,
      presenceMembers: realtimeState.presenceMembers,
      typers: realtimeState.typers,
      sendTyping: realtimeState.sendTyping,
    }
  },
}))

import { useProjectGroupThread, type UseProjectGroupThread } from "../useProjectGroupThread"
import type { ComposerDraftApi } from "../../../../shared/chat-shell/types"
import type { GroupTurn } from "../../../../../lib/api"

const gt = (o: Partial<GroupTurn> & { id: number }): GroupTurn => ({
  role: "user",
  content: `c${o.id}`,
  author_user_id: "u1",
  author_name: "Ada",
  author_job_role: null,
  created_at: new Date(1_700_000_000_000 + o.id * 1000).toISOString(),
  ...o,
})

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

let latest: UseProjectGroupThread | null = null
let draftValue = ""
const setValueSpy = vi.fn((text: string) => {
  draftValue = text
})
const draftApiRef: { current: ComposerDraftApi | null } = {
  current: {
    getValue: () => draftValue,
    getCaret: () => draftValue.length,
    setValue: setValueSpy,
  },
}

function Harness({ projectId }: { projectId: number }) {
  const engine = useProjectGroupThread({ projectId, draftApiRef })
  latest = engine
  return React.createElement("div", { "data-testid": "n" }, engine.turns.length)
}

function handlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (e: string, p: unknown) => void; onReconcile: () => void }
}
const flush = async () => {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

beforeEach(() => {
  groupTurnsMock.mockReset()
  postGroupTurnMock.mockReset()
  realtimeSpy.mockClear()
  realtimeState.degraded = true
  realtimeState.presenceMembers = []
  realtimeState.typers = []
  realtimeState.sendTyping = vi.fn()
  authState = { kind: "authed", user: { id: "u1" } }
  latest = null
  draftValue = ""
  setValueSpy.mockClear()
})
afterEach(() => cleanup())

describe("useProjectGroupThread — transport", () => {
  it("test_group_engine_since_reconcile_dedups_via_known_ids (AC1)", async () => {
    groupTurnsMock.mockResolvedValueOnce([gt({ id: 1 }), gt({ id: 2 })]) // initial
    groupTurnsMock.mockResolvedValueOnce([gt({ id: 2 }), gt({ id: 3 })]) // reconcile (overlap)
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    await act(async () => {
      handlers().onReconcile()
    })
    await flush()
    expect(latest!.turns.map((t) => t.id)).toEqual(["1", "2", "3"])
  })

  it("test_group_engine_optimistic_negative_id_send_then_rollback_on_failure (AC1/AC3)", async () => {
    groupTurnsMock.mockResolvedValueOnce([gt({ id: 1 })])
    const post = deferred<void>()
    postGroupTurnMock.mockReturnValueOnce(post.promise)
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    act(() => latest!.post("hello team"))
    // Optimistic turn appears immediately with a NEGATIVE id.
    expect(latest!.turns.some((t) => Number(t.id) < 0 && t.content === "hello team")).toBe(true)
    await act(async () => {
      post.reject(new Error("boom"))
      await Promise.resolve()
    })
    await flush()
    // Rolled back — the ghost is gone, an error is surfaced.
    expect(latest!.turns.some((t) => Number(t.id) < 0)).toBe(false)
    expect(latest!.error).toBeTruthy()
  })

  it("test_group_engine_same_content_guard_blocks_only_identical_inflight (AC3)", async () => {
    groupTurnsMock.mockResolvedValue([gt({ id: 1 })])
    const post = deferred<void>()
    postGroupTurnMock.mockReturnValue(post.promise) // never resolves during the test
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    act(() => latest!.post("same text"))
    act(() => latest!.post("same text")) // identical in-flight — blocked
    expect(postGroupTurnMock).toHaveBeenCalledTimes(1)
    act(() => latest!.post("different text")) // different — goes through
    expect(postGroupTurnMock).toHaveBeenCalledTimes(2)
  })

  it("test_group_engine_precomputes_invokedBy_from_previous_turn (AC1)", async () => {
    groupTurnsMock.mockResolvedValueOnce([
      gt({ id: 1, role: "user", content: "@Sprntly do x", author_user_id: "u2", author_name: "Bo" }),
      gt({ id: 2, role: "assistant", content: "on it", author_user_id: null, author_name: null }),
      gt({ id: 3, role: "user", content: "@Sprntly mine", author_user_id: "u1", author_name: "Ada" }),
      gt({ id: 4, role: "assistant", content: "sure", author_user_id: null, author_name: null }),
    ])
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    const byId = Object.fromEntries(latest!.turns.map((t) => [t.id, t]))
    expect(byId["2"].invokedBy).toBe("Bo")
    expect(byId["2"].invokedByMe).toBe(false)
    expect(byId["4"].invokedBy).toBe("Ada")
    expect(byId["4"].invokedByMe).toBe(true)
  })

  it("test_group_engine_posting_and_stayed_out_state (AC5)", async () => {
    groupTurnsMock.mockResolvedValueOnce([gt({ id: 1, role: "user" })])
    const post = deferred<void>()
    postGroupTurnMock.mockReturnValue(post.promise)
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    // Last turn is a human turn, nothing posting → stayed-out arm shows.
    expect(latest!.showStayedOut).toBe(true)
    act(() => latest!.post("another"))
    // While posting: stayed-out suppressed, posting-wait node present.
    expect(latest!.posting).toBe(true)
    expect(latest!.showStayedOut).toBe(false)
    expect(latest!.postingWaitNode).toBeTruthy()
  })

  it("test_group_engine_exposes_presence_members_and_typers (AC1 — review-flagged)", async () => {
    realtimeState.presenceMembers = [{ userId: "u1", name: "Ada" }, { userId: "u2", name: "Bo" }]
    realtimeState.typers = [{ userId: "u2", name: "Bo" }]
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    expect(latest!.presenceMembers.map((m) => m.userId)).toEqual(["u1", "u2"])
    expect(latest!.typers.map((t) => t.userId)).toEqual(["u2"])
    expect(latest!.typingIndicator).toBeTruthy()
  })

  it("test_group_engine_sendTyping_fires_on_input (AC1)", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    act(() => latest!.sendTyping())
    // `myName` derives from user_metadata; a metadata-less mock user → "You".
    expect(realtimeState.sendTyping).toHaveBeenCalledWith({ userId: "u1", name: "You" })
  })

  it("test_display_name_prefers_full_name_over_email (AC6)", async () => {
    authState = {
      kind: "authed",
      user: { id: "u1", user_metadata: { full_name: "David Mumuni" }, email: "david@x.com" },
    }
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    act(() => latest!.sendTyping())
    expect(realtimeState.sendTyping).toHaveBeenCalledWith({ userId: "u1", name: "David Mumuni" })
  })

  it("test_display_name_falls_back_to_email_local (AC6)", async () => {
    authState = { kind: "authed", user: { id: "u1", email: "david@x.com" } }
    groupTurnsMock.mockResolvedValueOnce([])
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    act(() => latest!.sendTyping())
    expect(realtimeState.sendTyping).toHaveBeenCalledWith({ userId: "u1", name: "david" })
  })

  it("test_group_engine_restores_draft_on_send_failure_only_if_empty (AC4)", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    const post = deferred<void>()
    postGroupTurnMock.mockReturnValueOnce(post.promise)
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    draftValue = "" // composer cleared by the shell on send
    act(() => latest!.post("recover me"))
    await act(async () => {
      post.reject(new Error("net"))
      await Promise.resolve()
    })
    await flush()
    // Composer still empty at failure → restore the failed draft (CAS).
    expect(setValueSpy).toHaveBeenCalledWith("recover me")
  })

  it("test_group_engine_restore_does_not_clobber_newer_text (AC4)", async () => {
    groupTurnsMock.mockResolvedValueOnce([])
    const post = deferred<void>()
    postGroupTurnMock.mockReturnValueOnce(post.promise)
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    draftValue = "a brand new message" // user typed after the clear
    act(() => latest!.post("failed one"))
    await act(async () => {
      post.reject(new Error("net"))
      await Promise.resolve()
    })
    await flush()
    // Composer non-empty → the CAS declines to restore, no clobber.
    expect(setValueSpy).not.toHaveBeenCalled()
  })
})

describe("useProjectGroupThread — named intended fixes (AC8)", () => {
  it("test_group_engine_dropped_broadcast_then_reconcile_recovers_turn (AC8)", async () => {
    groupTurnsMock.mockResolvedValueOnce([gt({ id: 1 }), gt({ id: 2 })]) // initial, cursor=2
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    // A newer broadcast (id 5) lands; ids 3,4 were dropped (at-most-once).
    await act(async () => {
      handlers().onEvent("turn.created", gt({ id: 5 }))
    })
    // Cursor must NOT have jumped to 5 — the reconcile still asks since 2.
    groupTurnsMock.mockResolvedValueOnce([gt({ id: 3 }), gt({ id: 4 }), gt({ id: 5 })])
    await act(async () => {
      handlers().onReconcile()
    })
    await flush()
    // The reconcile fetched from cursor 2 (the gap-burning fix), recovering 3,4.
    const lastCall = groupTurnsMock.mock.calls[groupTurnsMock.mock.calls.length - 1]
    expect(lastCall[1]).toBe(2)
    expect(latest!.turns.map((t) => t.id)).toEqual(["1", "2", "3", "4", "5"])
  })

  it("test_group_engine_realtime_before_load_not_clobbered (AC8)", async () => {
    const load = deferred<GroupTurn[]>()
    groupTurnsMock.mockReturnValueOnce(load.promise) // initial load in flight
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    // A realtime turn arrives BEFORE the initial load resolves.
    await act(async () => {
      handlers().onEvent("turn.created", gt({ id: 9, content: "live-first" }))
    })
    await act(async () => {
      load.resolve([gt({ id: 1 }), gt({ id: 2 })])
      await Promise.resolve()
    })
    await flush()
    // The load MERGES (not setTurns(all)) so the live turn survives.
    expect(latest!.turns.map((t) => t.id)).toEqual(["1", "2", "9"])
  })

  it("test_group_engine_out_of_order_batches_land_sorted (AC8)", async () => {
    const load = deferred<GroupTurn[]>()
    groupTurnsMock.mockReturnValueOnce(load.promise)
    postGroupTurnMock.mockReturnValue(deferred<void>().promise)
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    // A message sent DURING the load (optimistic, created "now" — newest).
    act(() => latest!.post("sent during load"))
    await act(async () => {
      // History is older (created_at well before now).
      load.resolve([
        { ...gt({ id: 1, content: "old-1" }), created_at: new Date(1_600_000_000_000).toISOString() },
        { ...gt({ id: 2, content: "old-2" }), created_at: new Date(1_600_000_001_000).toISOString() },
      ])
      await Promise.resolve()
    })
    await flush()
    // Merge SORTS by clock — the sent message renders BELOW its own history.
    const contents = latest!.turns.map((t) => t.content)
    expect(contents[contents.length - 1]).toBe("sent during load")
    expect(contents.slice(0, 2)).toEqual(["old-1", "old-2"])
  })

  it("test_group_engine_optimistic_clock_clamped_above_history_on_lagging_client (AC8, Fable #11)", async () => {
    // The client clock runs BEHIND the server: history carries `created_at`s in
    // the (client-)future. Without the clamp, `new Date()` for the optimistic
    // turn would be < history and it would sort ABOVE messages that predate it.
    const future = Date.now() + 60 * 60 * 1000 // an hour ahead of this client
    groupTurnsMock.mockResolvedValueOnce([
      { ...gt({ id: 1, content: "server-1" }), created_at: new Date(future).toISOString() },
      { ...gt({ id: 2, content: "server-2" }), created_at: new Date(future + 1000).toISOString() },
    ])
    postGroupTurnMock.mockReturnValue(deferred<void>().promise)
    render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    act(() => latest!.post("my just-sent message"))
    // Clamped to ≥ the newest known clock → it sorts to the BOTTOM, never above
    // the pre-existing (client-future) history.
    const contents = latest!.turns.map((t) => t.content)
    expect(contents).toEqual(["server-1", "server-2", "my just-sent message"])
  })

  it("test_group_engine_stale_generation_result_dropped_on_project_switch (AC8)", async () => {
    const loadA = deferred<GroupTurn[]>()
    groupTurnsMock.mockReturnValueOnce(loadA.promise) // project 7 load (stale)
    groupTurnsMock.mockResolvedValueOnce([gt({ id: 100, content: "proj8" })]) // project 8 load
    const { rerender } = render(React.createElement(Harness, { projectId: 7 }))
    await flush()
    // Switch to project 8 before project 7's load resolves.
    rerender(React.createElement(Harness, { projectId: 8 }))
    await flush()
    // The stale project-7 load resolves late — must be dropped.
    await act(async () => {
      loadA.resolve([gt({ id: 1, content: "proj7" })])
      await Promise.resolve()
    })
    await flush()
    expect(latest!.turns.map((t) => t.content)).toEqual(["proj8"])
    expect(latest!.turns.some((t) => t.content === "proj7")).toBe(false)
  })
})
