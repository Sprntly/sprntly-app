// @vitest-environment jsdom
//
// ProjectDetailScreen — the caller's per-user realtime channel + the group
// artifact-invalidation channel (#9-count).
//
// The private-chat UNREAD BADGE this file used to cover was REMOVED
// (planner decision, follow-on to the group-chat-removal ticket): the
// Group⇆Private toggle's removal left the badge with no reachable clear
// path, and in the single-surface model the badge no longer earns its
// place. `projectsApi.individualUnread`/`markIndividualRead` are gone from
// `web/app/lib/api.ts` too. What SURVIVES here: the per-user channel
// (`project:{id}:user:{uid}`) still exists — it now carries
// `delegation.event` (the ledger ticker) and `member.added` (the cross-
// project landing signal) — and the group artifact channel
// (`project:{id}`, `artifact.added` -> refetch) is untouched.
//
// `useRealtimeChannel` itself is mocked here (its own subscribe/reconnect/
// degrade lifecycle is covered by useRealtimeChannel.dom.test.tsx) — this
// file asserts the CONSUMER wiring (which topics get subscribed, which
// events route where).
import * as React from "react"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const getMock = vi.fn()
const artifactsMock = vi.fn()
const memorySummaryMock = vi.fn()
const memoryInsightMock = vi.fn()
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
// The container also reads `useSearchParams` (to preserve the other query
// params on nav) — provide it alongside `useRouter`, an empty param set by
// default. A mock missing it does not fail one assertion, it throws on
// every mount in this file.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
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
  ProjectMainThread: (props: { projectId: number | string }) =>
    React.createElement("div", {
      "data-testid": "main-thread-stub",
      "data-project-id": String(props.projectId),
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
import { ContentProvider } from "../../../../../context/ContentContext"
import type { ProjectDetail, ArtifactItem, ProjectMemorySummary } from "../../../../../lib/api"

// `ProjectDetailScreen` now consumes `useContent()`, so it must render under a
// real `ContentProvider` (mirroring the reference DOM tests, e.g.
// `useArtifactUrlSync.dom.test.tsx`). `useNavigation()` is already satisfied by
// this file's module-level `NavigationContext` mock above, so only the content
// provider needs standing up here.
function renderWithContent(node: React.ReactElement) {
  return render(React.createElement(ContentProvider, null, node))
}

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

// The container wires TWO channels: the group `project:{id}` artifact-
// invalidation subscription (#9-count) and the per-user `project:{id}:user:
// {uid}` one (ledger + member.added). Resolve by topic explicitly.
function handlersForTopic(topic: string) {
  const call = [...realtimeSpy.mock.calls].reverse().find((c) => c[0] === topic)
  if (!call) throw new Error(`no useRealtimeChannel subscription for topic ${topic}`)
  return call[1] as { onEvent: (event: string, payload: unknown) => void; onReconcile: () => void }
}
function lastHandlers() {
  return handlersForTopic("project:101:user:u1")
}

async function renderReady() {
  getMock.mockResolvedValue(PROJECT)
  artifactsMock.mockResolvedValue(ARTIFACTS)
  memorySummaryMock.mockResolvedValue(MEMORY)
  memoryInsightMock.mockResolvedValue(null)
  await act(async () => {
    renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
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
    expect(new Set(topics.filter((t) => t === "project:101:user:u1")).size).toBe(1)
  })

  it("test_also_subscribes_the_group_artifact_channel: project:{id} (#9-count), independent of the per-user topic", async () => {
    await renderReady()

    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics).toContain("project:101")
    expect(topics).toContain("project:101:user:u1")
  })

  it("test_artifact_added_refetches_the_list: a server-side attach broadcast refetches artifacts.length", async () => {
    await renderReady()
    const callsAfterMount = artifactsMock.mock.calls.length

    artifactsMock.mockResolvedValueOnce([
      { id: 9, type: "prd", title: "New PRD", updated_at: hoursAgo(0) },
    ] as unknown as ArtifactItem[])
    await act(async () => {
      handlersForTopic("project:101").onEvent("artifact.added", {
        project_id: 101, artifact_type: "prd", artifact_id: 9,
      })
      await Promise.resolve()
    })

    expect(artifactsMock.mock.calls.length).toBeGreaterThan(callsAfterMount)
    await waitFor(() => expect(screen.getByTestId("topbar-artifacts").textContent).toContain("1"))
  })

  it("ignores an unrelated event on the group artifact channel", async () => {
    await renderReady()
    const callsAfterMount = artifactsMock.mock.calls.length

    await act(async () => {
      handlersForTopic("project:101").onEvent("delegation.event", {})
    })
    expect(artifactsMock.mock.calls.length).toBe(callsAfterMount)
  })

  it("a null current-user id yields a null per-user topic (hook degrades); the group artifact channel is unaffected", async () => {
    authState = { kind: "anonymous" }
    await renderReady()

    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics).toContain(null)
    expect(topics).toContain("project:101")
  })
})

describe("ProjectDetailScreen — per-user channel routes non-unread events (AC-2/AC-3)", () => {
  it("a member.added signal for the SAME project is a no-op (there is only the always-visible private chat) — no crash, no unread badge", async () => {
    await renderReady()

    // Same project → memberAddedLandingTarget resolves null (alreadyInPrivateChat
    // is always true in the single-surface model) → nothing happens.
    await act(async () => {
      lastHandlers().onEvent("member.added", { project_id: 101 })
    })
    expect(screen.getByTestId("project-name")).toBeTruthy()
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()

    // ignores an unknown broadcast event name — no crash.
    await act(async () => {
      lastHandlers().onEvent("presence.sync", { anything: true })
    })
    expect(screen.getByTestId("project-name")).toBeTruthy()
  })

  // The cross-project member.added ROUTING itself (a genuinely different
  // project pushes `projectPath(target, { chat: "individual" })`) is the
  // pure-function decision `memberAddedLanding.test.ts` already covers
  // end-to-end; this file's job is the channel/event wiring, not re-proving
  // that decision under a real Next router mock.
})

describe("ProjectDetailScreen — non-breakage (AC-5, AC-6, AC-7)", () => {
  it("test_unmount_tears_down_channel: stale handlers post-unmount do not throw or trigger further reads", async () => {
    vi.useFakeTimers()
    const hasFocusSpy = vi.spyOn(document, "hasFocus").mockReturnValue(true)
    realtimeState.degraded = true

    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    const { unmount } = renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    await act(async () => {
      await Promise.resolve()
    })
    const handlers = lastHandlers()
    const callsAtUnmount = getMock.mock.calls.length

    unmount()

    expect(() => handlers.onEvent("delegation.event", {})).not.toThrow()
    await act(async () => {
      vi.advanceTimersByTime(20_000)
    })
    // A stale-handler event post-unmount never re-triggers the project fetch.
    expect(getMock.mock.calls.length).toBe(callsAtUnmount)
    hasFocusSpy.mockRestore()
  })

  it("test_badge_removed: no private-unread badge testid renders under any per-user channel event, and projectsApi has no unread methods to call", async () => {
    await renderReady()
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()

    await act(async () => {
      lastHandlers().onEvent("delegation.event", {})
    })
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()

    const api = await import("../../../../../lib/api")
    expect("individualUnread" in api.projectsApi).toBe(false)
    expect("markIndividualRead" in api.projectsApi).toBe(false)
  })
})
