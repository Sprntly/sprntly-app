// @vitest-environment jsdom
//
// Ledger liveness — the ledger surfaces (Task-ledger modal, rail counts, the
// assignee's inline brief-turn affordance) going live on the caller's own
// per-user `delegation.event` broadcast. `useRealtimeChannel` itself is
// mocked (its subscribe/reconnect/degrade lifecycle is covered by
// useRealtimeChannel.dom.test.tsx) — this file asserts the CONSUMER wiring:
// one per-user channel per surface (never the group channel), a
// `delegation.event` refetching counts + re-reading the open modal + patching
// the brief-turn status, the reconnect reconcile, the degraded fallback, and
// non-breakage of `brief.delivered` + the send path.
import * as React from "react"
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
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

// ── projectsApi mock (covers both surfaces) ──
const getMock = vi.fn()
const artifactsMock = vi.fn()
const memorySummaryMock = vi.fn()
const memoryInsightMock = vi.fn()
const individualUnreadMock = vi.fn()
const markIndividualReadMock = vi.fn()
const ledgerCountsMock = vi.fn()
const ledgerMock = vi.fn()
const emitDelegationEventMock = vi.fn()
const individualTurnsMock = vi.fn()
const individualChatMock = vi.fn()
const runAskGenerationMock = vi.fn()
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
      individualUnread: (...a: unknown[]) => individualUnreadMock(...a),
      markIndividualRead: (...a: unknown[]) => markIndividualReadMock(...a),
      ledgerCounts: (...a: unknown[]) => ledgerCountsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      emitDelegationEvent: (...a: unknown[]) => emitDelegationEventMock(...a),
      individualTurns: (...a: unknown[]) => individualTurnsMock(...a),
      individualChat: (...a: unknown[]) => individualChatMock(...a),
    },
  }
})
vi.mock("../../../../../lib/auth", () => ({ useAuth: () => authState }))
vi.mock("../../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openModal: openModalMock }),
}))
vi.mock("../../../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme", setActiveCompany: vi.fn(), activeCompanyDisplayName: "Acme" }),
}))
// The component now reads the classifier flag (`chatIntentEnvelopeOn`) to
// decide whether to classify-then-dispatch at all. Explicit OFF here keeps
// every assertion in this file byte-identical to pre-classifier behaviour —
// same stub shape `ProjectIndividualChat.test.tsx` uses.
vi.mock("../../../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    loading: false, profile: null,
    workspace: { feature_flags: { chat_intent_envelope: false } },
    refresh: async () => {},
  }),
}))
// The detail screen mounts `<ArtifactsModal>`, whose redesign reads
// `useRouter` for its legacy deep-link fallback — no Next app-router
// provider exists in jsdom, so the shell throws on mount without this.
// `onOpenInPlace` is always wired here, so the router `push` is never
// actually reached. Same stub `ProjectDetailScreen.test.tsx` uses.
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
}))
vi.mock("../../../../../lib/runAskGeneration", async () => {
  const actual = await vi.importActual<typeof import("../../../../../lib/runAskGeneration")>(
    "../../../../../lib/runAskGeneration",
  )
  return {
    ...actual,
    runAskGeneration: (...a: unknown[]) => runAskGenerationMock(...a),
    resumeAskGeneration: vi.fn(),
    getPendingAsk: () => null,
  }
})
vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: React.PropsWithChildren<{ href: string } & Record<string, unknown>>) =>
    React.createElement("a", { href, ...rest }, children),
}))
// The detail screen's thread host renders its own subscribing children — stub
// it so the ONLY per-user subscription under test is the container's own.
vi.mock("../ProjectMainThread", () => ({
  ProjectMainThread: () => React.createElement("div", { "data-testid": "main-thread-stub" }),
}))

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
import { ProjectIndividualChat } from "../ProjectIndividualChat"
import type {
  ProjectDetail,
  ArtifactItem,
  ProjectMemorySummary,
  DelegationLedgerRow,
  DelegationCounts,
  IndividualTurn,
} from "../../../../../lib/api"

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
const MEMORY: ProjectMemorySummary = { summary_md: null, entry_count: 0, stale: false }
const ARTIFACTS: ArtifactItem[] = []

const row = (over: Partial<DelegationLedgerRow>): DelegationLedgerRow => ({
  delegation_id: 5,
  task_summary: "Draft the pricing page",
  status: "assigned",
  status_at: hoursAgo(1),
  bucket: "open",
  other_party_user_id: "owner",
  other_party_name: "Owner",
  delivered_conversation_id: 9001,
  delivered_turn_id: 7,
  ...over,
})

// Mutable ledger state the mocks read, so a refetch reflects a new status.
let assignedRows: DelegationLedgerRow[] = []
let waitingRows: DelegationLedgerRow[] = []
let counts: DelegationCounts = { assigned_to_me_open: 0, waiting_on_open: 0 }

const iturn = (over: Partial<IndividualTurn>): IndividualTurn => ({
  id: 7,
  role: "assistant",
  content: "a delegated brief",
  created_at: new Date().toISOString(),
  ...over,
})

function lastHandlers() {
  const call = realtimeSpy.mock.calls[realtimeSpy.mock.calls.length - 1]
  return call[1] as { onEvent: (event: string, payload: unknown) => void; onReconcile: () => void }
}

async function renderDetailReady() {
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
  vi.clearAllMocks()
  individualUnreadMock.mockResolvedValue({ unread: false, latest_turn_id: null, last_read_turn_id: 0 })
  markIndividualReadMock.mockResolvedValue({ last_read_turn_id: 0 })
  ledgerCountsMock.mockImplementation(() => Promise.resolve(counts))
  ledgerMock.mockImplementation((_id: unknown, view: string) =>
    Promise.resolve(view === "assigned_to_me" ? assignedRows : waitingRows),
  )
  emitDelegationEventMock.mockResolvedValue({ delegation_id: 5, status: "accepted" })
  individualTurnsMock.mockResolvedValue([])
  individualChatMock.mockImplementation((id: number) =>
    Promise.resolve({ id: 9001, project_id: id, user_id: "u1", kind: "individual", created_at: "", updated_at: "" }),
  )
  runAskGenerationMock.mockResolvedValue({ answer: "ok", key_points: [], citations: [], confidence: 1, unanswered: "" })
  authState = { kind: "authed", user: { id: "u1" } }
  realtimeState.degraded = false
  assignedRows = []
  waitingRows = []
  counts = { assigned_to_me_open: 0, waiting_on_open: 0 }
})
afterEach(() => cleanup())

// ── AC-4: open modal re-reads on a delegation.event ──
describe("ledger modal — live update (AC-4)", () => {
  it("test_modal_updates_on_delegation_event: an open modal refetches and the row buttons update", async () => {
    assignedRows = [row({ delegation_id: 5, status: "assigned", bucket: "open" })]
    await renderDetailReady()

    fireEvent.click(screen.getByTestId("task-ledger-view-all"))
    // Initial open read → assignee/assigned shows Accept.
    expect(await screen.findByTestId("delegation-action-accepted")).toBeTruthy()
    expect(screen.queryByTestId("delegation-action-completed")).toBeNull()

    // The assignee accepts elsewhere; the live event drives an in-place re-read.
    assignedRows = [row({ delegation_id: 5, status: "accepted", bucket: "open" })]
    await act(async () => {
      lastHandlers().onEvent("delegation.event", { delegation_id: 5, status: "accepted" })
      await Promise.resolve()
    })

    // accepted → assignee now has In progress / Mark done, no manual reload.
    expect(await screen.findByTestId("delegation-action-completed")).toBeTruthy()
    expect(screen.getByTestId("delegation-action-in_progress")).toBeTruthy()
  })
})

// ── AC-5: rail counts go live ──
describe("rail counts — live update (AC-5)", () => {
  it("test_rail_counts_update_on_delegation_event: counts refetch and re-render", async () => {
    counts = { assigned_to_me_open: 0, waiting_on_open: 0 }
    await renderDetailReady()
    await waitFor(() =>
      expect(screen.getByTestId("task-ledger-counts").textContent).toContain("0 assigned to you"),
    )

    counts = { assigned_to_me_open: 1, waiting_on_open: 0 }
    await act(async () => {
      lastHandlers().onEvent("delegation.event", { delegation_id: 5, status: "assigned" })
      await Promise.resolve()
    })

    await waitFor(() =>
      expect(screen.getByTestId("task-ledger-counts").textContent).toContain("1 assigned to you"),
    )
  })

  it("ignores unrelated events (brief.delivered does not refetch counts)", async () => {
    await renderDetailReady()
    const callsAfterMount = ledgerCountsMock.mock.calls.length
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", { assignee_user_id: "u1" })
      await Promise.resolve()
    })
    expect(ledgerCountsMock.mock.calls.length).toBe(callsAfterMount)
  })
})

// ── AC-7: reconnect reconcile ──
describe("reconnect reconcile (AC-7)", () => {
  it("test_reconnect_refetches_once: onReconcile refetches the rail counts once", async () => {
    await renderDetailReady()
    const callsAfterMount = ledgerCountsMock.mock.calls.length

    await act(async () => {
      lastHandlers().onReconcile()
      await Promise.resolve()
    })

    expect(ledgerCountsMock.mock.calls.length).toBe(callsAfterMount + 1)
  })
})

// ── AC-8 / AC-9: degraded fallback + single own channel ──
describe("degradation + channel scoping (AC-8, AC-9)", () => {
  it("test_subscribes_only_own_per_user_channel: one channel on project:{id}:user:{uid}, never the group channel", async () => {
    await renderDetailReady()
    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics.every((t) => t === "project:101:user:u1")).toBe(true)
    expect(new Set(topics).size).toBe(1)
    expect(topics).not.toContain("project:101")
  })

  it("test_degraded_falls_back_to_l03_behaviour: degraded channel, no error, no second channel, refetch-on-emit still works", async () => {
    realtimeState.degraded = true
    await renderDetailReady()

    // No duplicate channel, never the group channel.
    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(new Set(topics).size).toBe(1)
    expect(topics).not.toContain("project:101")
    // No error surfaced anywhere in the shell.
    expect(screen.queryByRole("alert")).toBeNull()

    // The refetch-on-emit path still works while degraded.
    counts = { assigned_to_me_open: 2, waiting_on_open: 0 }
    await act(async () => {
      lastHandlers().onEvent("delegation.event", { delegation_id: 5, status: "assigned" })
      await Promise.resolve()
    })
    await waitFor(() =>
      expect(screen.getByTestId("task-ledger-counts").textContent).toContain("2 assigned to you"),
    )
  })
})

// ── AC-6: inline brief-turn affordance goes live ──
describe("brief-turn affordance — live update (AC-6)", () => {
  it("test_brief_turn_actions_update_on_delegation_event: the matched brief turn reflects the new status", async () => {
    individualTurnsMock.mockResolvedValue([iturn({ id: 7, content: "Ship onboarding by Friday." })])
    // Completed → the assignee has no actions yet.
    assignedRows = [row({ delegation_id: 5, delivered_turn_id: 7, status: "completed", bucket: "done" })]

    render(React.createElement(ProjectIndividualChat, { projectId: 101 }))
    await waitFor(() => expect(screen.getByTestId("ic-history-agent")).toBeTruthy())
    await waitFor(() => expect(ledgerMock).toHaveBeenCalled())
    expect(screen.queryByTestId("delegation-action-accepted")).toBeNull()

    // The assigner reopens → the live event flips this turn's affordance open.
    await act(async () => {
      lastHandlers().onEvent("delegation.event", { delegation_id: 5, status: "reopened" })
      await Promise.resolve()
    })

    expect(await screen.findByTestId("delegation-action-accepted")).toBeTruthy()
  })

  it("ignores a delegation.event for a turn not rendered in this thread", async () => {
    individualTurnsMock.mockResolvedValue([iturn({ id: 7 })])
    assignedRows = [row({ delegation_id: 5, delivered_turn_id: 7, status: "assigned", bucket: "open" })]

    render(React.createElement(ProjectIndividualChat, { projectId: 101 }))
    await waitFor(() => expect(screen.getByTestId("ic-history-agent")).toBeTruthy())
    await waitFor(() => expect(screen.getByTestId("delegation-action-accepted")).toBeTruthy())

    await act(async () => {
      // A different delegation, not on any rendered turn — must not throw or
      // mutate the rendered affordance.
      lastHandlers().onEvent("delegation.event", { delegation_id: 999, status: "completed" })
      await Promise.resolve()
    })
    // The rendered turn's Accept affordance is untouched.
    expect(screen.getByTestId("delegation-action-accepted")).toBeTruthy()
  })
})

// ── AC-10: non-breakage of brief.delivered + send path ──
describe("non-breakage (AC-10)", () => {
  it("test_send_path_and_brief_delivered_unchanged: brief.delivered still appends and send still runs", async () => {
    individualTurnsMock.mockResolvedValue([])
    render(React.createElement(ProjectIndividualChat, { projectId: 101 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledWith(101))

    // R1-05 brief.delivered still appends a live turn.
    await act(async () => {
      lastHandlers().onEvent("brief.delivered", iturn({ id: 42, content: "A brand-new brief." }))
    })
    expect(await screen.findByText("A brand-new brief.")).toBeTruthy()

    // The send path is untouched.
    const textarea = document.querySelector(".cx-input") as HTMLTextAreaElement
    await act(async () => {
      fireEvent.change(textarea, { target: { value: "still a normal question" } })
    })
    await act(async () => {
      fireEvent.click(screen.getByLabelText("Send"))
    })
    expect(runAskGenerationMock).toHaveBeenCalledWith(
      "still a normal question",
      "acme",
      "project-individual-101",
      expect.objectContaining({ project_id: 101, conversation_id: 9001 }),
    )
  })

  it("ProjectIndividualChat subscribes only its own per-user channel (AC-9)", async () => {
    render(React.createElement(ProjectIndividualChat, { projectId: 101 }))
    await waitFor(() => expect(individualTurnsMock).toHaveBeenCalledWith(101))
    const topics = realtimeSpy.mock.calls.map((c) => c[0])
    expect(topics.every((t) => t === "project:101:user:u1")).toBe(true)
    expect(topics).not.toContain("project:101")
  })
})
