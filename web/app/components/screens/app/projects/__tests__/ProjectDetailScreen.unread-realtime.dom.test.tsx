// @vitest-environment jsdom
//
// ProjectDetailScreen — the individual-chat unread badge's live signal.
// `useRealtimeChannel` itself is mocked here (its own subscribe/reconnect/
// degrade lifecycle is covered by useRealtimeChannel.dom.test.tsx) — this
// file asserts the CONSUMER wiring: one channel for the caller's per-user
// topic, a `brief.delivered` broadcast lighting the badge with no poll, the
// reconnect reconcile re-deriving the badge from `individualUnread`, the
// poll's degrade/fallback gating, and that the existing clear-on-read path
// and the badge/component contract are unmodified.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const getMock = vi.fn()
const artifactsMock = vi.fn()
const memorySummaryMock = vi.fn()
const memoryInsightMock = vi.fn()
const individualUnreadMock = vi.fn()
const markIndividualReadMock = vi.fn()
const removeMemberMock = vi.fn()
const openModalMock = vi.fn()
let authState: { kind: "authed"; user: { id: string } } | { kind: "anonymous" } = {
  kind: "authed",
  user: { id: "u1" },
}

vi.mock("../../../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/api")>("../../../../../lib/api")
  return {
    ...actual,
    projectsApi: {
      ...actual.projectsApi,
      get: (...a: unknown[]) => getMock(...a),
      artifacts: (...a: unknown[]) => artifactsMock(...a),
      memorySummary: (...a: unknown[]) => memorySummaryMock(...a),
      memoryInsight: (...a: unknown[]) => memoryInsightMock(...a),
      removeMember: (...a: unknown[]) => removeMemberMock(...a),
      individualUnread: (...a: unknown[]) => individualUnreadMock(...a),
      markIndividualRead: (...a: unknown[]) => markIndividualReadMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => authState,
}))
vi.mock("../../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openModal: openModalMock }),
}))
// The container mounts `<ArtifactsModal>`, whose redesign reads `useRouter` for
// its legacy deep-link fallback — stub it (no Next app-router provider in jsdom)
// or the shell throws on mount.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: React.PropsWithChildren<{ href: string } & Record<string, unknown>>) =>
    React.createElement("a", { href, ...rest }, children),
}))
// Same isolation rationale as `ProjectDetailScreen.test.tsx`'s own stub —
// this file's job is the container's realtime/poll wiring, not the thread.
vi.mock("../ProjectMainThread", () => ({
  ProjectMainThread: (props: { projectId: number | string; activeChat: string }) =>
    React.createElement("div", {
      "data-testid": "main-thread-stub",
      "data-active-chat": props.activeChat,
    }),
}))

// Test-local mock of `useRealtimeChannel` — mirrors
// `ProjectGroupChat.realtime.dom.test.tsx`'s own mock exactly: the spy
// captures the topic + handlers the consumer wired up so a test can invoke
// them directly (`onEvent` / `onReconcile`) without standing up a real
// channel, and `realtimeState.degraded` is read fresh each render so a test
// can flip it + `rerender()` to simulate a channel status transition.
const { realtimeSpy, realtimeState } = vi.hoisted(() => ({
  realtimeSpy: vi.fn(),
  realtimeState: { degraded: true },
}))
vi.mock("../useRealtimeChannel", () => ({
  useRealtimeChannel: (
    topic: string | null,
    handlers: { onEvent?: (event: string, payload: unknown) => void; onReconcile?: () => void },
  ) => {
    realtimeSpy(topic, handlers)
    return { status: realtimeState.degraded ? "degraded" : "live", degraded: realtimeState.degraded }
  },
}))

import { ProjectDetailScreen } from "../ProjectDetailScreen"
import type { ProjectDetail, ArtifactItem, ProjectMemorySummary } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const PROJECT: ProjectDetail = {
  id: 101,
  company_id: "c1",
  workspace_id: "w1",
  name: "Instant-quote flow",
  origin: "manual",
  created_by: "owner",
  created_at: hoursAgo(48),
  updated_at: hoursAgo(2),
  group_chat_id: 55,
  members: [
    {
      kind: "human",
      user_id: "u1",
      name: "Ada",
      email: "ada@example.com",
      avatar_url: null,
      job_role: "PM",
      added_at: hoursAgo(48),
    },
  ],
}

const ARTIFACTS: ArtifactItem[] = []

const MEMORY: ProjectMemorySummary = {
  summary_md: null,
  entry_count: 0,
  stale: false,
}

function lastHandlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (event: string, payload: unknown) => void; onReconcile: () => void }
}

async function renderReady() {
  getMock.mockResolvedValue(PROJECT)
  artifactsMock.mockResolvedValue(ARTIFACTS)
  memorySummaryMock.mockResolvedValue(MEMORY)
  memoryInsightMock.mockResolvedValue(null)
  await act(async () => {
    render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
  })
  await waitFor(() => expect(screen.getByTestId("project-name")).toBeTruthy())
}

beforeEach(() => {
  getMock.mockReset()
  artifactsMock.mockReset()
  memorySummaryMock.mockReset()
  memoryInsightMock.mockReset()
  removeMemberMock.mockReset()
  openModalMock.mockReset()
  individualUnreadMock.mockReset()
  individualUnreadMock.mockResolvedValue({ unread: false, latest_turn_id: null, last_read_turn_id: 0 })
  markIndividualReadMock.mockReset()
  markIndividualReadMock.mockResolvedValue({ last_read_turn_id: 0 })
  realtimeSpy.mockClear()
  realtimeState.degraded = false // most tests exercise the "channel is live" path
  authState = { kind: "authed", user: { id: "u1" } }
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe("ProjectDetailScreen — live subscribe (AC-1)", () => {
  it("test_subscribes_to_per_user_topic_on_mount: one channel for project:{id}:user:{uid}", async () => {
    await renderReady()

    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics.every((t) => t === "project:101:user:u1")).toBe(true)
    expect(new Set(topics).size).toBe(1)
  })

  it("a null current-user id yields a null topic (hook degrades, poll unaffected)", async () => {
    authState = { kind: "anonymous" }
    await renderReady()

    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics.every((t) => t === null)).toBe(true)
  })
})

describe("ProjectDetailScreen — live flip (AC-2)", () => {
  it("test_brief_delivered_lights_badge: broadcast -> individualUnread true, no poll call", async () => {
    await renderReady()
    const callsAfterMount = individualUnreadMock.mock.calls.length

    await act(async () => {
      lastHandlers().onEvent("brief.delivered", { assignee_user_id: "u1" })
    })

    expect(await screen.findByTestId("individual-chat-unread-dot")).toBeTruthy()
    // No poll/reconcile fetch was needed for this to render.
    expect(individualUnreadMock.mock.calls.length).toBe(callsAfterMount)
  })

  it("ignores unknown broadcast event names", async () => {
    await renderReady()

    await act(async () => {
      lastHandlers().onEvent("presence.sync", { anything: true })
    })
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()
  })
})

describe("ProjectDetailScreen — reconnect reconcile (AC-3)", () => {
  it("test_reconnect_reconciles_unread: onReconcile -> one individualUnread(projectId) read sets the badge", async () => {
    await renderReady()
    const callsAfterMount = individualUnreadMock.mock.calls.length

    individualUnreadMock.mockResolvedValueOnce({ unread: true, latest_turn_id: 9, last_read_turn_id: 0 })
    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
    })

    expect(individualUnreadMock.mock.calls.length).toBe(callsAfterMount + 1)
    expect(individualUnreadMock).toHaveBeenLastCalledWith("101")
    expect(await screen.findByTestId("individual-chat-unread-dot")).toBeTruthy()
  })

  it("a false reconcile result clears an already-lit badge", async () => {
    await renderReady()
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", {})
    })
    expect(await screen.findByTestId("individual-chat-unread-dot")).toBeTruthy()

    individualUnreadMock.mockResolvedValueOnce({ unread: false, latest_turn_id: null, last_read_turn_id: 9 })
    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
    })
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()
  })
})

describe("ProjectDetailScreen — poll fallback / degradation (AC-4)", () => {
  it("test_poll_suppressed_while_live: degraded=false -> no 4s interval", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = false
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)

    render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    await act(async () => {
      await Promise.resolve()
    })
    const callsAfterMount = individualUnreadMock.mock.calls.length

    await act(async () => {
      vi.advanceTimersByTime(20_000)
      await Promise.resolve()
    })

    expect(individualUnreadMock.mock.calls.length).toBe(callsAfterMount)
    hasFocusSpy.mockRestore()
  })

  it("test_poll_rearms_on_degraded: degraded=true -> the 4s poll runs, badge updates", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = true
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)

    render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    await act(async () => {
      await Promise.resolve()
    })
    const callsAfterMount = individualUnreadMock.mock.calls.length

    individualUnreadMock.mockResolvedValueOnce({ unread: true, latest_turn_id: 4, last_read_turn_id: 0 })
    await act(async () => {
      vi.advanceTimersByTime(4000)
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(individualUnreadMock.mock.calls.length).toBeGreaterThan(callsAfterMount)
    expect(screen.getByTestId("individual-chat-unread-dot")).toBeTruthy()
    hasFocusSpy.mockRestore()
  })

  it("a channel that never configures (degraded from the start) leaves the poll working exactly as today", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = true
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    individualUnreadMock.mockResolvedValue({ unread: false, latest_turn_id: null, last_read_turn_id: 0 })

    render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    await act(async () => {
      await Promise.resolve()
    })
    const callsAfterMount = individualUnreadMock.mock.calls.length

    individualUnreadMock.mockResolvedValueOnce({ unread: true, latest_turn_id: 11, last_read_turn_id: 0 })
    await act(async () => {
      vi.advanceTimersByTime(4000)
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(individualUnreadMock.mock.calls.length).toBeGreaterThan(callsAfterMount)
    expect(screen.getByTestId("individual-chat-unread-dot")).toBeTruthy()
    hasFocusSpy.mockRestore()
  })
})

describe("ProjectDetailScreen — non-breakage (AC-5, AC-6, AC-7)", () => {
  it("test_clear_on_read_still_works: a lit-live badge clears via the existing read path", async () => {
    await renderReady()
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", {})
    })
    expect(await screen.findByTestId("individual-chat-unread-dot")).toBeTruthy()

    fireEvent.click(screen.getByTestId("chat-row-individual"))

    await waitFor(() => expect(markIndividualReadMock).toHaveBeenCalledWith("101"))
    await waitFor(() => expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull())
    expect(screen.getByTestId("main-thread-stub").getAttribute("data-active-chat")).toBe("individual")
  })

  it("test_unmount_tears_down_channel: stale handlers post-unmount do not throw or trigger further reads", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = true

    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    const { unmount } = render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    await act(async () => {
      await Promise.resolve()
    })
    const handlers = lastHandlers()
    const callsAtUnmount = individualUnreadMock.mock.calls.length

    unmount()

    expect(() => handlers.onEvent("brief.delivered", {})).not.toThrow()
    await act(async () => {
      vi.advanceTimersByTime(20_000)
    })
    expect(individualUnreadMock.mock.calls.length).toBe(callsAtUnmount)
    hasFocusSpy.mockRestore()
  })

  it("test_badge_prop_and_signature_unchanged: same ProjectDetailScreen({ projectId }) signature, badge testid intact, other rail content untouched", async () => {
    await renderReady()

    expect(screen.getByTestId("project-name")).toBeTruthy()
    expect(screen.getByTestId("chat-row-individual")).toBeTruthy()
    expect(screen.getByTestId("chat-row-group")).toBeTruthy()
    // No badge until the badge is actually lit — the render/clear contract
    // (`individual-chat-unread-dot`) is unchanged, not force-rendered.
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()

    await act(async () => {
      lastHandlers().onEvent("brief.delivered", {})
    })
    expect(screen.getByTestId("individual-chat-unread-dot")).toBeTruthy()
  })
})
