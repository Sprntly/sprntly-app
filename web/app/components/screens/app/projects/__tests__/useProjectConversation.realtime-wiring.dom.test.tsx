// @vitest-environment jsdom
//
// useProjectConversation — the realtime WIRING (Gap 3/4): the hook
// subscribes to its OWN per-user channel (never the group channel), appends
// `turn.created`/`brief.delivered` live, and reconciles via a since-cursor
// refetch on every (re)subscribe. The append/dedupe LOGIC itself is covered
// by the pure-function suite (`useProjectConversation.realtime-dedupe.test.ts`);
// this file proves the hook actually wires those functions to
// `useRealtimeChannel` correctly.
import * as React from "react"
import { act, cleanup, render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

type CapturedChannel = {
  topic: string | null
  onEvent?: (event: string, payload: unknown) => void
  onReconcile?: () => void
}

const h = vi.hoisted(() => ({
  runConversationAsk: vi.fn(async () => {}),
  handleStopAsk: vi.fn(),
  runActionTurnInTab: vi.fn(async () => {}),
  individualChat: vi.fn(async () => ({ id: 7 })),
  listTurns: vi.fn(async (): Promise<{ turns: Array<{ id: number; role: string; content: string; created_at?: string }> }> => ({ turns: [] })),
  individualTurns: vi.fn(async () => [] as unknown[]),
  channelCalls: [] as CapturedChannel[],
}))

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

vi.mock("../../useMainConversation", () => ({
  useMainConversation: () => ({
    runConversationAsk: h.runConversationAsk,
    handleStopAsk: h.handleStopAsk,
    runActionTurnInTab: h.runActionTurnInTab,
  }),
}))
// Capture every call — a REAL channel identity keys on topic only, so this
// hook's own re-renders (hydrating flips false, thread updates, etc.) call it
// repeatedly with the same args; tests read the LATEST capture.
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (topic: string | null, handlers: { onEvent?: (e: string, p: unknown) => void; onReconcile?: () => void }) => {
    h.channelCalls.push({ topic, onEvent: handlers.onEvent, onReconcile: handlers.onReconcile })
    return { status: topic ? "live" : "degraded", degraded: !topic, presenceMembers: [], sendTyping: () => {}, typers: [] }
  },
}))

vi.mock("../../../../../context/CompanyContext", () => ({ useCompany: () => ({ activeCompany: { id: 1 } }) }))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ profile: { id: "me-1", full_name: "Me" } }),
  profileDisplayName: () => "Me",
}))
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openContentPanel: vi.fn(), contentPanelTab: null, showToast: vi.fn() }),
}))

vi.mock("../../../../../lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../../../lib/api")>()
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      individualChat: h.individualChat,
      individualTurns: h.individualTurns,
    },
    conversationsApi: {
      ...actual.conversationsApi,
      listTurns: h.listTurns,
      update: vi.fn(async () => ({})),
      create: vi.fn(async () => ({ id: 7 })),
      addTurn: vi.fn(async () => ({ id: 1, conversation_id: 7, role: "user", content: "", created_at: "" })),
    },
    chatIntentApi: { ...actual.chatIntentApi, resolve: vi.fn(async () => null) },
    chatSuggestionsApi: { ...actual.chatSuggestionsApi, next: vi.fn(async () => ({ suggestions: [] })) },
    askApi: { ...actual.askApi, skills: vi.fn(async () => ({ skills: [] })), extractFile: vi.fn(async () => ({ markdown: "" })) },
  }
})

import { useProjectConversation, type ProjectConversationProps } from "../useProjectConversation"
import { ContentProvider } from "../../../../../context/ContentContext"

let bag: ProjectConversationProps | null = null
function Harness({ projectId, currentUserId }: { projectId: number | string; currentUserId?: string | null }) {
  bag = useProjectConversation(projectId, currentUserId)
  return null
}

async function mount(projectId: number | string, currentUserId: string | null) {
  await act(async () => {
    render(
      <ContentProvider>
        <Harness projectId={projectId} currentUserId={currentUserId} />
      </ContentProvider>,
    )
  })
  await waitFor(() => expect(h.individualChat).toHaveBeenCalled())
}

/** The latest LIVE (non-null-topic) channel subscription this mount made. */
function latestLiveChannel(): CapturedChannel {
  const live = h.channelCalls.filter((c) => c.topic != null)
  expect(live.length).toBeGreaterThan(0)
  return live[live.length - 1]
}

beforeEach(() => { bag = null; h.channelCalls.length = 0; vi.clearAllMocks() })
afterEach(cleanup)

describe("useProjectConversation — realtime wiring (Gap 3/4)", () => {
  it("test_subscribes_to_own_per_user_topic_never_the_group_channel", async () => {
    await mount(101, "user-1")
    await waitFor(() => expect(h.channelCalls.some((c) => c.topic != null)).toBe(true))
    const live = latestLiveChannel()
    expect(live.topic).toBe("project:101:user:user-1")
    // The privacy invariant: this surface never subscribes the BARE group
    // topic (`project:{id}`, no `:user:` suffix).
    expect(h.channelCalls.some((c) => c.topic === "project:101")).toBe(false)
  })

  it("test_no_channel_when_currentUserId_is_unresolved", async () => {
    await mount(101, null)
    // Give any pending microtasks a tick, then assert no LIVE (non-null)
    // subscription was ever made — unresolved auth leaves this surface
    // realtime-blind, never crashed.
    await new Promise((r) => setTimeout(r, 0))
    expect(h.channelCalls.every((c) => c.topic == null)).toBe(true)
  })

  it("test_turn_created_appends_live_to_the_thread", async () => {
    await mount(101, "user-1")
    await waitFor(() => expect(h.channelCalls.some((c) => c.topic != null)).toBe(true))
    const { onEvent } = latestLiveChannel()
    act(() => {
      onEvent!("turn.created", { id: 501, role: "assistant", content: "Fresh reply from another tab.", created_at: "2026-09-02T00:00:00Z" })
    })
    await waitFor(() => expect(bag!.thread.some((t) => t.reply?.answer === "Fresh reply from another tab.")).toBe(true))
  })

  it("test_brief_delivered_appends_live_to_the_thread", async () => {
    await mount(101, "user-1")
    await waitFor(() => expect(h.channelCalls.some((c) => c.topic != null)).toBe(true))
    const { onEvent } = latestLiveChannel()
    act(() => {
      onEvent!("brief.delivered", { id: 502, role: "assistant", content: "Here's the brief you asked for.", created_at: "2026-09-02T00:00:00Z" })
    })
    await waitFor(() => expect(bag!.thread.some((t) => t.reply?.answer === "Here's the brief you asked for.")).toBe(true))
  })

  it("test_a_repeat_delivery_of_the_same_turn_id_never_double_renders", async () => {
    await mount(101, "user-1")
    await waitFor(() => expect(h.channelCalls.some((c) => c.topic != null)).toBe(true))
    const { onEvent } = latestLiveChannel()
    act(() => { onEvent!("turn.created", { id: 700, role: "assistant", content: "Only once, please." }) })
    await waitFor(() => expect(bag!.thread.filter((t) => t.reply?.answer === "Only once, please.").length).toBe(1))
    act(() => { onEvent!("turn.created", { id: 700, role: "assistant", content: "Only once, please." }) })
    // Still exactly one — the second delivery of the SAME broadcast (a
    // shared-channel replay, or an overlapping reconcile) is a no-op.
    expect(bag!.thread.filter((t) => t.reply?.answer === "Only once, please.").length).toBe(1)
  })

  it("test_an_unrelated_event_name_is_ignored", async () => {
    await mount(101, "user-1")
    await waitFor(() => expect(h.channelCalls.some((c) => c.topic != null)).toBe(true))
    const { onEvent } = latestLiveChannel()
    const before = bag!.thread.length
    act(() => { onEvent!("delegation.event", { delegation_id: 1, status: "assigned" }) })
    expect(bag!.thread.length).toBe(before)
  })

  it("test_onReconcile_triggers_a_since_cursor_refetch_and_appends_results", async () => {
    h.individualTurns.mockResolvedValueOnce([
      { id: 9001, role: "assistant", content: "Missed while you were away.", created_at: "2026-09-02T00:00:00Z" },
    ])
    await mount(101, "user-1")
    await waitFor(() => expect(h.channelCalls.some((c) => c.topic != null)).toBe(true))
    const { onReconcile } = latestLiveChannel()
    await act(async () => { onReconcile!() })
    await waitFor(() => expect(h.individualTurns).toHaveBeenCalled())
    // `since` on the FIRST reconcile is the cursor seeded from hydrate (no
    // history here, so 0) — never omitted, so a genuinely missed broadcast
    // gap always closes via an explicit bounded read.
    expect(h.individualTurns.mock.calls[0][0]).toBe(101)
    await waitFor(() => expect(bag!.thread.some((t) => t.reply?.answer === "Missed while you were away.")).toBe(true))
  })

  it("test_hydrated_paired_exchange_is_not_duplicated_by_a_reconcile_redelivery", async () => {
    // THE observer/reload bug (live-reproduced): hydrate pairs a COMPLETED
    // user+assistant exchange into ONE turn that carries the USER row's id
    // (1960); the assistant row (1961) — written right after, so a HIGHER id —
    // lives nowhere on the thread's dbTurnIds. Pre-fix the reconcile's
    // since-cursor was seeded from the paired turn's dbTurnId (1960, the user
    // row), so the FIRST reconcile refetched row 1961 and re-appended it as a
    // second, standalone assistant bubble. Prior to this test the whole suite
    // only ever exercised realtime IN ISOLATION (empty hydrate), so the
    // hydrate↔realtime coexistence gap shipped green.
    h.listTurns.mockResolvedValueOnce({ turns: [
      { id: 1960, role: "user", content: "what changed since Friday?", created_at: "2026-09-02T00:00:00Z" },
      { id: 1961, role: "assistant", content: "Three tickets moved to Done.", created_at: "2026-09-02T00:00:01Z" },
    ] })
    // A reconcile that still hands back the already-hydrated assistant row
    // (models the pre-fix behind-cursor refetch, or a genuine redelivery).
    h.individualTurns.mockResolvedValueOnce([
      { id: 1961, role: "assistant", content: "Three tickets moved to Done.", created_at: "2026-09-02T00:00:01Z" },
    ])
    await mount(101, "user-1")
    await waitFor(() => expect(bag!.thread.some((t) => t.reply?.answer === "Three tickets moved to Done.")).toBe(true))
    const { onReconcile } = latestLiveChannel()
    await act(async () => { onReconcile!() })
    // Exactly ONE bubble carries the reply, paired with its question — no
    // duplicate standalone assistant turn, and no reply-less (phantom) turn.
    expect(bag!.thread.filter((t) => t.reply?.answer === "Three tickets moved to Done.").length).toBe(1)
    const paired = bag!.thread.find((t) => t.query === "what changed since Friday?")
    expect(paired?.reply?.answer).toBe("Three tickets moved to Done.")
    // No orphaned turn: every turn is either a real question or carries a reply.
    expect(bag!.thread.every((t) => t.query !== "" || t.reply != null)).toBe(true)
  })

  it("test_hydrated_paired_reply_is_not_duplicated_by_a_live_rebroadcast", async () => {
    // Same coexistence gap via the LIVE path: a re-broadcast of a hydrated,
    // already-paired assistant row bypasses the since-cursor entirely, so the
    // fix also seeds `mergedReplyIds` from hydrate (the merged turn keeps the
    // user row's id, so the plain dbTurnId dedupe alone can't catch it).
    h.listTurns.mockResolvedValueOnce({ turns: [
      { id: 1960, role: "user", content: "status?", created_at: "2026-09-02T00:00:00Z" },
      { id: 1961, role: "assistant", content: "All green.", created_at: "2026-09-02T00:00:01Z" },
    ] })
    await mount(101, "user-1")
    await waitFor(() => expect(bag!.thread.some((t) => t.reply?.answer === "All green.")).toBe(true))
    const { onEvent } = latestLiveChannel()
    act(() => { onEvent!("turn.created", { id: 1961, role: "assistant", content: "All green." }) })
    expect(bag!.thread.filter((t) => t.reply?.answer === "All green.").length).toBe(1)
  })

  it("test_reconcile_failure_is_best_effort_and_never_throws", async () => {
    h.individualTurns.mockRejectedValueOnce(new Error("network blip"))
    await mount(101, "user-1")
    await waitFor(() => expect(h.channelCalls.some((c) => c.topic != null)).toBe(true))
    const { onReconcile } = latestLiveChannel()
    await expect(act(async () => { onReconcile!() })).resolves.not.toThrow()
  })
})
