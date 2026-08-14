// @vitest-environment jsdom
//
// Tests for the group-chat-centric detail SHELL: the pure `ProjectDetailView`
// (rail sections, agent member, artifacts/memory/task cards, chat-row
// active-state + composer swap, a11y) and the `ProjectDetailScreen`
// container's fetch + 403/404 state machine. Same View/Screen split posture
// as `ProjectsView`/`ProjectsScreen`'s own test file; all context boundaries
// mocked, not re-implemented.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { renderToStaticMarkup } from "react-dom/server"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const getMock = vi.fn()
const artifactsMock = vi.fn()
const memorySummaryMock = vi.fn()
const memoryInsightMock = vi.fn()
const openModalMock = vi.fn()
const removeMemberMock = vi.fn()
const individualUnreadMock = vi.fn()
const markIndividualReadMock = vi.fn()
const ledgerCountsMock = vi.fn()
const ledgerMock = vi.fn()
const emitDelegationEventMock = vi.fn()
// Default: a viewer who is neither PROJECT's creator ("u1") nor either
// listed human member — lets the container-level tests exercise the
// "removable" branch (u2/Shristi) without also tripping the self-removal
// suppression. Individual tests override this to prove the self-suppression
// branch (`authMock.mockReturnValue({ kind: "authed", user: { id: "u2" } })`).
const authMock = vi.fn(() => ({ kind: "authed" as const, user: { id: "current-viewer" } }))

// `ApiError` is defined INSIDE the factory (self-contained — no outer-scope
// reference) so vi.mock's hoisting-to-the-top-of-file needs nothing hoisted
// alongside it; the mocked class is retrieved for test use via a normal
// (non-type-only) `import { ApiError } from "../../../../../lib/api"` below,
// which resolves to this same mocked export at runtime.
vi.mock("../../../../../lib/api", () => {
  class ApiError extends Error {
    status: number
    body: unknown
    // Matches the real `ApiError(status, body, message?)` signature
    // (`web/app/lib/api.ts`) so calls in the tests below type-check against
    // the real class's declaration.
    constructor(status: number, body: unknown, message?: string) {
      super(message ?? String(status))
      this.status = status
      this.body = body
    }
  }
  return {
    ApiError,
    projectsApi: {
      get: (...a: unknown[]) => getMock(...a),
      artifacts: (...a: unknown[]) => artifactsMock(...a),
      memorySummary: (...a: unknown[]) => memorySummaryMock(...a),
      memoryInsight: (...a: unknown[]) => memoryInsightMock(...a),
      removeMember: (...a: unknown[]) => removeMemberMock(...a),
      individualUnread: (...a: unknown[]) => individualUnreadMock(...a),
      markIndividualRead: (...a: unknown[]) => markIndividualReadMock(...a),
      ledgerCounts: (...a: unknown[]) => ledgerCountsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      emitDelegationEvent: (...a: unknown[]) => emitDelegationEventMock(...a),
    },
    // Real implementation (no API call, no side effect) — mirrors
    // lib/api.ts's own five-value check, kept here rather than importing the
    // real module so this mock stays self-contained. Needed by both this
    // screen's own artifact grouping and the ArtifactsModal it mounts.
    isProjectArtifactType: (t: string) =>
      ["prd", "evidence", "prototype", "report", "ticket_set"].includes(t),
  }
})
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => authMock(),
}))
vi.mock("../../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({ openModal: openModalMock }),
}))
// The container mounts `<ArtifactsModal>`, whose redesign reads `useRouter` for
// its legacy deep-link fallback. Stub it — no Next app-router provider exists in
// jsdom — or the shell throws on mount. `onOpenInPlace` is always wired here, so
// the router `push` is never actually reached.
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
// `ProjectMainThread` pulls in `ProjectIndividualChat`'s ask/poll wiring on
// its individual-chat branch plus `ProjectGroupChat`'s network wiring on the
// group branch. Both drag in a dependency graph (CompanyContext, the shared
// ask lib, projectsApi network calls…) this file has no reason to boot just
// to test the SHELL (top bar, rail, cards, state machine) — the same
// isolation reason `AppLayout`/`NavigationContext` are mocked above. The
// mount itself (which props it receives) is what THIS file verifies; the
// real thread/composer/swap behaviour is `ProjectMainThread.test.tsx`,
// `ProjectGroupChat.test.tsx`, and `ProjectIndividualChat.test.tsx`'s job.
vi.mock("../ProjectMainThread", () => ({
  ProjectMainThread: (props: {
    projectId: number | string
    activeChat: string
    insightNote?: { by: string; text: string } | null
  }) =>
    React.createElement("div", {
      "data-testid": "main-thread-stub",
      "data-project-id": String(props.projectId),
      "data-active-chat": props.activeChat,
      // Reflects whether/what insightNote this container passed through —
      // ProjectMainThread's OWN rendering of it is out of this file's scope
      // (ProjectMainThread.test.tsx/ProjectIndividualChat.test.tsx's job);
      // this file only proves the container fed the right value in.
      "data-has-insight": props.insightNote ? "true" : "false",
      "data-insight-text": props.insightNote?.text ?? "",
    }),
}))

import { ProjectDetailView, ProjectDetailScreen, type ProjectDetailViewProps } from "../ProjectDetailScreen"
// Regular (non-type-only) import: resolves to the mocked `ApiError` above,
// the SAME class reference the component's `instanceof` checks compare
// against — required for the 403/404 container tests below.
import { ApiError } from "../../../../../lib/api"
import type { ArtifactItem, ProjectDetail, ProjectMemoryInsight, ProjectMemorySummary } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const PROJECT: ProjectDetail = {
  id: 101,
  company_id: "c1",
  workspace_id: "w1",
  name: "Instant-quote flow",
  origin: "manual",
  created_by: "u1",
  created_at: hoursAgo(48),
  updated_at: hoursAgo(2),
  group_chat_id: 55,
  members: [
    {
      kind: "agent",
      user_id: null,
      name: "Sprntly",
      role_label: "Agent coworker · dispatches tasks",
      status: "working",
    },
    {
      kind: "human",
      user_id: "u1",
      name: "David M.",
      email: "david@example.com",
      avatar_url: null,
      job_role: "PM",
      added_at: hoursAgo(48),
    },
    {
      kind: "human",
      user_id: "u2",
      name: "Shristi",
      email: "shristi@example.com",
      avatar_url: null,
      job_role: "Design",
      added_at: hoursAgo(40),
    },
  ],
}

const ARTIFACTS: ArtifactItem[] = [
  {
    type: "prd",
    id: 1,
    title: "Instant-quote flow — v3",
    status: "ready",
    created_at: hoursAgo(2),
    source: { brief_id: 1, week_label: null, insight_index: null },
    open: { brief_id: 1, insight_index: null, prd_id: 1 },
  } as ArtifactItem,
  {
    type: "evidence",
    id: 2,
    title: "Xometry call",
    status: "ready",
    created_at: hoursAgo(70),
    source: { brief_id: 1, week_label: null, insight_index: null },
    open: { brief_id: 1, insight_index: null, evidence_id: 2 },
  } as ArtifactItem,
  {
    type: "evidence",
    id: 3,
    title: "Pricing latency benchmark",
    status: "ready",
    created_at: hoursAgo(5),
    source: { brief_id: 1, week_label: null, insight_index: null },
    open: { brief_id: 1, insight_index: null, evidence_id: 3 },
  } as ArtifactItem,
]

const MEMORY: ProjectMemorySummary = {
  summary_md:
    "A Xometry-driven redesign of on-demand quoting — a priced quote in under 60 seconds. It also covers the guest path for first-time buyers.",
  entry_count: 24,
  stale: false,
}

const INSIGHT: ProjectMemoryInsight = {
  by: "Sprntly",
  text: "The pricing model changed last week — flat rate, not tiered.",
}

const noop = () => {}

function viewProps(overrides: Partial<ProjectDetailViewProps> = {}): ProjectDetailViewProps {
  return {
    project: PROJECT,
    artifacts: ARTIFACTS,
    memory: MEMORY,
    railCollapsed: false,
    onToggleRail: noop,
    activeChat: "group",
    onSelectChat: noop,
    individualUnread: false,
    ledgerCounts: { assigned_to_me_open: 0, waiting_on_open: 0 },
    ledgerRows: [],
    onOpenArtifacts: noop,
    onOpenArtifactInPlace: noop,
    openArtifact: null,
    onCloseArtifactDrawer: noop,
    onAddExistingArtifact: noop,
    onOpenMemory: noop,
    onAddMemory: noop,
    onOpenTasks: noop,
    onInvite: noop,
    currentUserId: "current-viewer",
    onRemoveMember: noop,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  getMock.mockReset()
  artifactsMock.mockReset()
  memorySummaryMock.mockReset()
  memoryInsightMock.mockReset()
  openModalMock.mockReset()
  removeMemberMock.mockReset()
  individualUnreadMock.mockReset()
  individualUnreadMock.mockResolvedValue({ unread: false, latest_turn_id: null, last_read_turn_id: 0 })
  markIndividualReadMock.mockReset()
  markIndividualReadMock.mockResolvedValue({ last_read_turn_id: 0 })
  ledgerCountsMock.mockReset()
  ledgerCountsMock.mockResolvedValue({ assigned_to_me_open: 0, waiting_on_open: 0 })
  ledgerMock.mockReset()
  ledgerMock.mockResolvedValue([])
  emitDelegationEventMock.mockReset()
  emitDelegationEventMock.mockResolvedValue({ delegation_id: 1, status: "accepted" })
  authMock.mockReset()
  authMock.mockReturnValue({ kind: "authed", user: { id: "current-viewer" } })
})

describe("ProjectDetailView — top bar", () => {
  it("renders the back-link, serif project name, member avatars, and no status pill", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const back = screen.getByTestId("back-to-projects")
    expect(back.getAttribute("href")).toBe("/projects")
    expect(screen.getByTestId("project-name").textContent).toContain("Instant-quote flow")
    expect(screen.getByTestId("topbar-avatars")).toBeTruthy()
    expect(screen.queryByText(/status/i)).toBeNull()
    expect(screen.queryByTestId("status-pill")).toBeNull()
  })
})

describe("ProjectDetailView — right rail structure", () => {
  it("renders the chat switcher, then ARTIFACTS / PROJECT / MEMBERS sections in order, with no Overview card", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    // Redesign: the mockup leads the rail with Artifacts, so the group⇆private
    // Chat switch is now a compact segmented control at the top of the rail
    // (`rail-chat-toggle`, a role="tablist") rather than its own "Chats"
    // rail-section-label. The remaining labelled sections keep their order.
    expect(screen.getByTestId("rail-chat-toggle")).toBeTruthy()
    const labels = screen
      .getAllByTestId("rail-section-label")
      .map((el) => (el.textContent?.trim().match(/^[A-Za-z]+/) ?? [""])[0])
    expect(labels).toEqual(["Artifacts", "Project", "Members"])
    expect(screen.queryByText("Overview")).toBeNull()
  })

  it("renders the Sprntly AGENT member row with the green working status, from the virtual member", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const agentRow = screen.getByTestId("member-row-agent")
    expect(agentRow.textContent).toContain("Sprntly")
    expect(within(agentRow).getByText("Agent")).toBeTruthy()
    expect(within(agentRow).getByText("Agent coworker · dispatches tasks")).toBeTruthy()
    expect(within(agentRow).getByText("working")).toBeTruthy()
  })

  it("test_agent_pill_has_status_role — the agent pill is exposed to assistive tech as a live status with an accessible name referencing the status string", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const pill = screen.getByTestId("agent-working-status")
    expect(pill.getAttribute("role")).toBe("status")
    const accessibleName = pill.getAttribute("aria-label") ?? pill.textContent ?? ""
    expect(accessibleName).toContain("Sprntly")
    expect(accessibleName).toContain(PROJECT.members[0].kind === "agent" ? PROJECT.members[0].status : "")
  })

  it("test_agent_pill_shows_backend_status_string — the pill text equals the virtual member's status constant, no derived/overridden value", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const pill = screen.getByTestId("agent-working-status")
    expect(pill.textContent).toBe("working")
  })

  it("test_working_pill_only_for_agent — no human member row renders the agent-working-status pill", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const humanRows = screen.getAllByTestId("member-row-human")
    for (const row of humanRows) {
      expect(within(row).queryByTestId("agent-working-status")).toBeNull()
    }
    expect(screen.getAllByTestId("agent-working-status")).toHaveLength(1)
  })

  it("human member rows carry their job_role label", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const rows = screen.getAllByTestId("member-row-human")
    expect(rows).toHaveLength(2)
    expect(within(rows[0]).getByText("David M.")).toBeTruthy()
    expect(within(rows[0]).getByText("PM")).toBeTruthy()
    expect(within(rows[1]).getByText("Shristi")).toBeTruthy()
    expect(within(rows[1]).getByText("Design")).toBeTruthy()
  })

  it("renders the Remove control only on the removable row — not the creator, not the caller, not the agent", () => {
    // PROJECT: created_by="u1" (David M.), members u1/u2. currentUserId
    // defaults ("current-viewer") to neither, so only u2 (Shristi) is
    // removable — proves the creator suppression independently of self.
    render(React.createElement(ProjectDetailView, viewProps()))
    const rows = screen.getAllByTestId("member-row-human")
    expect(within(rows[0]).queryByTestId("member-remove")).toBeNull() // David M. — creator
    expect(within(rows[1]).getByTestId("member-remove")).toBeTruthy() // Shristi — removable
    expect(within(screen.getByTestId("member-row-agent")).queryByTestId("member-remove")).toBeNull()
  })

  it("withholds the Remove control on the caller's OWN row, even when not the creator", () => {
    render(React.createElement(ProjectDetailView, viewProps({ currentUserId: "u2" })))
    expect(screen.queryAllByTestId("member-remove")).toHaveLength(0)
  })

  it("clicking Remove invokes onRemoveMember with that human member", () => {
    const onRemoveMember = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onRemoveMember })))
    const rows = screen.getAllByTestId("member-row-human")
    fireEvent.click(within(rows[1]).getByTestId("member-remove"))
    expect(onRemoveMember).toHaveBeenCalledTimes(1)
    expect(onRemoveMember.mock.calls[0][0]).toMatchObject({ user_id: "u2", name: "Shristi" })
  })

  it("renders one compact card per artifact type present, sourced from the artifacts list, plus an Add-existing card", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.getByTestId("artifact-card-prd-sub").textContent).toContain("1 item")
    expect(screen.getByTestId("artifact-card-evidence-sub").textContent).toContain("2 items")
    expect(screen.queryByTestId("artifact-card-prototype")).toBeNull()
    expect(screen.queryByTestId("artifact-card-report")).toBeNull()
    const addExisting = screen.getByTestId("artifact-add-existing")
    expect(addExisting).toBeTruthy()
    expect(addExisting.textContent).toContain("Add existing artifact")
  })

  it("clicking Add existing artifact invokes onAddExistingArtifact (opens the company-library picker)", () => {
    const onAddExistingArtifact = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onAddExistingArtifact })))
    fireEvent.click(screen.getByTestId("artifact-add-existing"))
    expect(onAddExistingArtifact).toHaveBeenCalledTimes(1)
  })

  it("clicking a MULTI-item artifact card's ↗ opens the browse modal for that type", () => {
    // Evidence has 2 items in the fixture → no single artifact to open in
    // place → the card still routes to the browse modal (onOpenArtifacts).
    const onOpenArtifacts = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onOpenArtifacts })))
    fireEvent.click(screen.getByTestId("artifact-card-evidence"))
    expect(onOpenArtifacts).toHaveBeenCalledWith("evidence")
  })

  it("clicking a SINGLE in-place-type artifact card opens that artifact in the side drawer, not the modal", () => {
    // Redesign: a rail card for an in-place type (prd/evidence/prototype) that
    // maps to exactly ONE artifact opens it IN-PLACE beside the chat
    // (onOpenArtifactInPlace) rather than the browse modal. The PRD fixture is
    // a single item, so its card takes that path.
    const onOpenArtifacts = vi.fn()
    const onOpenArtifactInPlace = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onOpenArtifacts, onOpenArtifactInPlace })))
    fireEvent.click(screen.getByTestId("artifact-card-prd"))
    expect(onOpenArtifactInPlace).toHaveBeenCalledTimes(1)
    expect(onOpenArtifactInPlace.mock.calls[0][0]).toMatchObject({ type: "prd", id: 1 })
    expect(onOpenArtifacts).not.toHaveBeenCalled()
  })

  it("Project-memory card teaser is the summary's first sentence, with the entry count and View all/Add", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const card = screen.getByTestId("memory-card")
    expect(within(card).getByText(/A Xometry-driven redesign of on-demand quoting — a priced quote in under 60 seconds\./)).toBeTruthy()
    expect(within(card).getByText("24")).toBeTruthy()
    expect(screen.getByTestId("memory-view-all").textContent).toContain("24")
    expect(screen.getByTestId("memory-add")).toBeTruthy()
  })

  it("clicking View all / Add on the memory card invokes their callbacks", () => {
    const onOpenMemory = vi.fn()
    const onAddMemory = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onOpenMemory, onAddMemory })))
    fireEvent.click(screen.getByTestId("memory-view-all"))
    fireEvent.click(screen.getByTestId("memory-add"))
    expect(onOpenMemory).toHaveBeenCalledTimes(1)
    expect(onAddMemory).toHaveBeenCalledTimes(1)
  })

  it("the task-ledger rail card is un-mounted from the rail (non-destructive — see the source-scan test below)", () => {
    render(
      React.createElement(
        ProjectDetailView,
        viewProps({ ledgerCounts: { assigned_to_me_open: 3, waiting_on_open: 2 } }),
      ),
    )
    expect(screen.queryByTestId("task-ledger-card")).toBeNull()
    expect(screen.queryByTestId("task-ledger-view-all")).toBeNull()
  })
})

describe("ProjectDetailScreen — task-ledger substrate intact after the rail un-mount (AC-12)", () => {
  it("projectsApi.ledger*/TaskModal/ledgerCounts/ledgerRows/onOpenTasks remain imported/defined/importable", () => {
    const src = readFileSync(
      join(__dirname, "../ProjectDetailScreen.tsx"),
      "utf8",
    )
    expect(src).toContain('from "./TaskModal"')
    expect(src).toMatch(/<TaskModal\b/)
    expect(src).toMatch(/railModal\?\.kind === "tasks"/)
    expect(src).toContain("ledgerCounts")
    expect(src).toContain("ledgerRows")
    expect(src).toContain("onOpenTasks")
    expect(src).toContain("ledgerVersion")
  })

  it("the ledger read calls (projectsApi.ledgerCounts/.ledger) are still present in source, not deleted", () => {
    const src = readFileSync(
      join(__dirname, "../ProjectDetailScreen.tsx"),
      "utf8",
    )
    expect(src).toMatch(/projectsApi\s*\.ledgerCounts\(/)
    expect(src).toMatch(/projectsApi\.ledger\(projectId, "assigned_to_me"\)/)
    expect(src).toMatch(/projectsApi\.ledger\(projectId, "waiting_on"\)/)
  })
})

describe("ProjectDetailView — state", () => {
  it("Hide panel / Show panel toggles the rail via railCollapsed", () => {
    const onToggleRail = vi.fn()
    const { rerender } = render(React.createElement(ProjectDetailView, viewProps({ onToggleRail })))
    expect(screen.getByTestId("project-rail")).toBeTruthy()
    fireEvent.click(screen.getByTestId("rail-toggle"))
    expect(onToggleRail).toHaveBeenCalledTimes(1)

    rerender(React.createElement(ProjectDetailView, viewProps({ railCollapsed: true })))
    expect(screen.queryByTestId("project-rail")).toBeNull()
    expect(screen.getByTestId("rail-toggle").textContent).toContain("Show panel")
  })

  it("selecting the individual CHATS row sets activeChat and marks it active", () => {
    const onSelectChat = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onSelectChat })))
    const groupRow = screen.getByTestId("chat-row-group")
    const indivRow = screen.getByTestId("chat-row-individual")
    // Redesign: the chat switch is a segmented tablist, so active-state is
    // exposed via aria-selected on role="tab" (not aria-pressed).
    expect(groupRow.getAttribute("aria-selected")).toBe("true")
    expect(indivRow.getAttribute("aria-selected")).toBe("false")
    fireEvent.click(indivRow)
    expect(onSelectChat).toHaveBeenCalledWith("individual")
  })

  it("the individual chat row renders active when activeChat='individual'", () => {
    render(React.createElement(ProjectDetailView, viewProps({ activeChat: "individual" })))
    expect(screen.getByTestId("chat-row-individual").getAttribute("aria-selected")).toBe("true")
    expect(screen.getByTestId("chat-row-group").getAttribute("aria-selected")).toBe("false")
  })

  it("test_rail_badge_shows_and_clears — the individual row renders an unread dot when individualUnread is true, and none when false", () => {
    const { rerender } = render(React.createElement(ProjectDetailView, viewProps({ individualUnread: false })))
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()

    rerender(React.createElement(ProjectDetailView, viewProps({ individualUnread: true })))
    expect(screen.getByTestId("individual-chat-unread-dot")).toBeTruthy()

    rerender(React.createElement(ProjectDetailView, viewProps({ individualUnread: false })))
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()
  })

  it("the chat note bar swaps group ⇆ individual copy", () => {
    const { rerender } = render(React.createElement(ProjectDetailView, viewProps({ activeChat: "group" })))
    expect(screen.getByTestId("chat-note").textContent).toContain("smart interjection")

    rerender(React.createElement(ProjectDetailView, viewProps({ activeChat: "individual" })))
    expect(screen.getByTestId("chat-note").textContent).toContain("feeds project memory")
  })

  // ProjectMainThread OWNS the composer for whichever chat is active — the
  // SAME extracted composer on both sides — this shell mounts it once, per
  // `activeChat`, and stops there (its own composer/thread behaviour is out
  // of THIS file's scope, per the isolation mock above).
  it("mounts ProjectMainThread once, keyed on activeChat and the project id", () => {
    const { rerender } = render(React.createElement(ProjectDetailView, viewProps({ activeChat: "group" })))
    const host = screen.getByTestId("main-thread-stub")
    expect(host.getAttribute("data-active-chat")).toBe("group")
    expect(host.getAttribute("data-project-id")).toBe("101")

    rerender(React.createElement(ProjectDetailView, viewProps({ activeChat: "individual" })))
    expect(screen.getByTestId("main-thread-stub").getAttribute("data-active-chat")).toBe("individual")
  })
})

describe("ProjectDetailView — accessibility", () => {
  it("every rail control is a real interactive element with an aria-label on icon-only affordances", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.getByTestId("rail-toggle").tagName).toBe("BUTTON")
    expect(screen.getByTestId("chat-row-group").tagName).toBe("BUTTON")
    expect(screen.getByTestId("chat-row-individual").tagName).toBe("BUTTON")
    expect(screen.getByTestId("artifact-card-prd").tagName).toBe("BUTTON")
    // The single-item PRD card opens in place → its accessible name is "Open
    // PRD"; a multi-item type keeps the "Browse … artifacts" browse label.
    expect(screen.getByLabelText("Open PRD")).toBeTruthy()
    expect(screen.getByLabelText("Browse Evidence artifacts")).toBeTruthy()
    expect(screen.getByLabelText("Invite by email")).toBeTruthy()
  })

  it("static markup renders (SSR-safe) with no interactive element disabled by default", () => {
    const html = renderToStaticMarkup(React.createElement(ProjectDetailView, viewProps()))
    expect(html).toContain("Instant-quote flow")
    expect(html).not.toContain("disabled")
  })
})

describe("ProjectDetailScreen module CSS — tokens only", () => {
  it("resolves every color/spacing/radius/shadow to a globals.css custom property — no new palette", () => {
    const css = readFileSync(join(__dirname, "../ProjectDetailScreen.module.css"), "utf8")
    // Only exception: `#fff` for on-accent/on-dark button text — the SAME
    // exception `ProjectsScreen.module.css`'s `.newBtn` already takes (no
    // `--on-*`-text token exists for it in globals.css).
    const found = css.match(/#[0-9A-Fa-f]{3,8}/g) ?? []
    const disallowed = found.filter((hex) => hex.toLowerCase() !== "#fff")
    expect(disallowed).toEqual([])
  })

  it("the artifact-type badge palette in the .tsx matches ArtifactsScreen's real hexes, never the design mockup's purple", () => {
    const src = readFileSync(join(__dirname, "../ProjectDetailScreen.tsx"), "utf8")
    expect(src).toContain("#DBEAFE")
    expect(src).toContain("#1E40AF")
    expect(src).not.toContain("634AB0")
  })
})

describe("ProjectDetailScreen — agent working-pill pulse (presentational polish)", () => {
  it("test_pulse_keyframe_in_module_not_globals — the module defines the pulse keyframe + a prefers-reduced-motion guard; globals.css is untouched", () => {
    const css = readFileSync(join(__dirname, "../ProjectDetailScreen.module.css"), "utf8")
    expect(css).toContain("@keyframes projectAgentPulse")
    expect(css).toMatch(/@media\s*\(prefers-reduced-motion:\s*reduce\)/)
    expect(css).toMatch(/\.workingPill::before\s*{[^}]*animation:\s*none/)

    const globals = readFileSync(join(__dirname, "../../../../../globals.css"), "utf8")
    expect(globals).not.toContain("projectAgentPulse")
  })

  it("test_no_new_state_for_status — no new useState/fetch is introduced for the agent status (source-scan guard against accidental activity-wiring)", () => {
    const src = readFileSync(join(__dirname, "../ProjectDetailScreen.tsx"), "utf8")
    // Baseline declaration count was 8 (state/rail/activeChat/railModal/
    // removeTarget/removeBusy/removeError/individualUnread). The ledger-UI
    // work added `ledgerCounts` (9) and the ledger-liveness work added
    // `ledgerVersion` (10). The Projects screen redesign adds exactly TWO more,
    // each legitimately in its own declared scope: `ledgerRows` (a small
    // OPEN-rows preview for the Task-ledger rail card — best-effort, party-
    // filtered reads mirrored the same way `ledgerCounts` is) and `openArtifact`
    // (the artifact opened IN-PLACE in the side-by-side drawer beside the chat;
    // a pure local UI toggle, never the URL / never stored derived-state — 12).
    // The project invite modal adds ONE more (13): `inviteOpen`, the same shape
    // of pure local open/close toggle as `openArtifact` — it replaces the rail
    // Invite button's call into the shared `useNavigation().openModal("invite")`
    // mechanics with mounting `<ProjectInviteModal>` directly. The guard this
    // test protects — no NEW state for the AGENT STATUS pulse specifically —
    // still holds: `posting` (the ask-composer wiring this guard was written
    // against) is still absent.
    const useStateDeclarations = src.match(/useState\s*[<(]/g) ?? []
    expect(useStateDeclarations).toHaveLength(13)
    expect(src).not.toContain("posting")
  })
})

describe("ProjectDetailScreen source — never touches ChatScreen.tsx", () => {
  it("contains no import of or reference to ChatScreen", () => {
    const src = readFileSync(join(__dirname, "../ProjectDetailScreen.tsx"), "utf8")
    expect(src).not.toContain("ChatScreen")
  })
})

// ── ProjectDetailScreen — loading state (skeleton, not bare text) ──
describe("ProjectDetailScreen — loading state", () => {
  it("test_loading_renders_skeleton_not_text — the loading branch renders a skeleton node under project-detail-loading, not the literal 'Loading…' string", async () => {
    // A never-resolving fetch keeps the container in the "loading" branch
    // for the duration of this assertion.
    getMock.mockReturnValue(new Promise(() => {}))
    artifactsMock.mockReturnValue(new Promise(() => {}))
    memorySummaryMock.mockReturnValue(new Promise(() => {}))
    memoryInsightMock.mockReturnValue(new Promise(() => {}))
    render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    const wrap = screen.getByTestId("project-detail-loading")
    expect(wrap.getAttribute("aria-busy")).toBe("true")
    expect(screen.getByTestId("project-detail-loading-skeleton")).toBeTruthy()
    expect(wrap.textContent).not.toContain("Loading…")
  })

  it("test_detail_error_branches_unchanged — 403 -> forbidden, 404 -> not_found, else -> error still render their existing EmptyPane copy (regression)", async () => {
    getMock.mockRejectedValue(new ApiError(403, "Not a member of this project"))
    artifactsMock.mockResolvedValue([])
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-detail-forbidden")).toBeTruthy())
    expect(screen.getByText("You're not a member of this project")).toBeTruthy()
    expect(screen.getByText("Ask a project member to add you, then come back.")).toBeTruthy()
  })

  it("a non-ApiError / generic rejection renders the generic error branch, unchanged", async () => {
    getMock.mockRejectedValue(new Error("boom"))
    artifactsMock.mockResolvedValue([])
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-detail-error")).toBeTruthy())
    expect(screen.getByText("Couldn't load this project")).toBeTruthy()
  })
})

// ── ProjectDetailScreen — container fetch + membership-gate state machine ──
describe("ProjectDetailScreen — data fetch", () => {
  it("fetches project/artifacts/memory for the given id and renders the shell", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-name")).toBeTruthy())
    expect(getMock).toHaveBeenCalledWith("101")
    expect(artifactsMock).toHaveBeenCalledWith("101")
    expect(memorySummaryMock).toHaveBeenCalledWith("101")
    expect(memoryInsightMock).toHaveBeenCalledWith("101")
  })

  it("renders a graceful 'not a member' state on a 403, never a crash", async () => {
    getMock.mockRejectedValue(new ApiError(403, "Not a member of this project"))
    artifactsMock.mockResolvedValue([])
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-detail-forbidden")).toBeTruthy())
    expect(screen.getByText("You're not a member of this project")).toBeTruthy()
  })

  it("renders a graceful 'not found' state on a 404, never a crash", async () => {
    getMock.mockRejectedValue(new ApiError(404, "Project not found"))
    artifactsMock.mockResolvedValue([])
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "999" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-detail-not_found")).toBeTruthy())
    expect(screen.getByText("Project not found")).toBeTruthy()
  })

  it("invite button opens the project-scoped invite modal, NOT the global mock InviteModal", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("invite-button")).toBeTruthy())
    expect(screen.queryByTestId("project-invite-modal")).toBeNull()

    fireEvent.click(screen.getByTestId("invite-button"))

    // The project-scoped surface opens...
    expect(screen.getByTestId("project-invite-modal")).toBeTruthy()
    // ...and the global mock modal mechanics are never touched.
    expect(openModalMock).not.toHaveBeenCalled()
  })

  it("the project invite modal lists the project's current members", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("invite-button")).toBeTruthy())
    fireEvent.click(screen.getByTestId("invite-button"))

    const rows = screen.getAllByTestId("project-invite-member-row")
    expect(rows.map((r) => r.textContent)).toEqual(
      expect.arrayContaining([expect.stringContaining("David M."), expect.stringContaining("Shristi")]),
    )
    expect(screen.getByTestId("project-invite-member-row-agent").textContent).toContain("Sprntly")
  })

  // ── Remove member: confirm → DELETE call → roster refetch (AC3) ──
  describe("removing a member", () => {
    const PROJECT_AFTER_REMOVE = { ...PROJECT, members: PROJECT.members.filter((m) => m.user_id !== "u2") }

    it("Remove → confirm calls removeMember and refetches the roster in place (no full reload)", async () => {
      getMock.mockResolvedValueOnce(PROJECT).mockResolvedValueOnce(PROJECT_AFTER_REMOVE)
      artifactsMock.mockResolvedValue(ARTIFACTS)
      memorySummaryMock.mockResolvedValue(MEMORY)
      memoryInsightMock.mockResolvedValue(null)
      removeMemberMock.mockResolvedValue({ removed: true })
      await act(async () => {
        render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
      })
      await waitFor(() => expect(screen.getAllByTestId("member-row-human")).toHaveLength(2))

      const rows = screen.getAllByTestId("member-row-human")
      fireEvent.click(within(rows[1]).getByTestId("member-remove")) // Shristi (u2)

      // The confirm dialog opens, naming the member, and does NOT call the
      // API until confirmed.
      expect(screen.getByText("Remove Shristi?")).toBeTruthy()
      expect(removeMemberMock).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole("button", { name: "Remove" }))

      await waitFor(() => expect(removeMemberMock).toHaveBeenCalledWith("101", "u2"))
      // Refetches the project (roster) — never re-enters the full-screen
      // loading state (AC3 "without a full reload"): the shell/thread host
      // stays mounted throughout.
      expect(screen.getByTestId("main-thread-stub")).toBeTruthy()
      await waitFor(() => expect(screen.getAllByTestId("member-row-human")).toHaveLength(1))
      expect(screen.queryByText("Shristi")).toBeNull()
      expect(getMock).toHaveBeenCalledTimes(2)
    })

    it("Cancel closes the dialog without calling removeMember", async () => {
      getMock.mockResolvedValue(PROJECT)
      artifactsMock.mockResolvedValue(ARTIFACTS)
      memorySummaryMock.mockResolvedValue(MEMORY)
      memoryInsightMock.mockResolvedValue(null)
      await act(async () => {
        render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
      })
      await waitFor(() => expect(screen.getAllByTestId("member-row-human")).toHaveLength(2))
      const rows = screen.getAllByTestId("member-row-human")
      fireEvent.click(within(rows[1]).getByTestId("member-remove"))
      expect(screen.getByText("Remove Shristi?")).toBeTruthy()

      fireEvent.click(screen.getByRole("button", { name: "Cancel" }))
      expect(screen.queryByText("Remove Shristi?")).toBeNull()
      expect(removeMemberMock).not.toHaveBeenCalled()
    })

    it("a failed removal shows the error inline and keeps the dialog open, roster unchanged", async () => {
      getMock.mockResolvedValue(PROJECT)
      artifactsMock.mockResolvedValue(ARTIFACTS)
      memorySummaryMock.mockResolvedValue(MEMORY)
      memoryInsightMock.mockResolvedValue(null)
      removeMemberMock.mockRejectedValue(
        new ApiError(409, { detail: "The project creator can't be removed" }, "The project creator can't be removed"),
      )
      await act(async () => {
        render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
      })
      await waitFor(() => expect(screen.getAllByTestId("member-row-human")).toHaveLength(2))
      const rows = screen.getAllByTestId("member-row-human")
      fireEvent.click(within(rows[1]).getByTestId("member-remove"))
      fireEvent.click(screen.getByRole("button", { name: "Remove" }))

      await waitFor(() => expect(removeMemberMock).toHaveBeenCalled())
      await waitFor(() => expect(screen.getByText("The project creator can't be removed")).toBeTruthy())
      // Only the initial fetch happened — a failed removal does not refetch.
      expect(getMock).toHaveBeenCalledTimes(1)
      expect(screen.getAllByTestId("member-row-human")).toHaveLength(2)
    })
  })
})

// ── ProjectDetailScreen — assignee-awareness unread badge (AD-P3/AD-P20) ──
describe("ProjectDetailScreen — individual chat unread badge", () => {
  it("test_rail_badge_shows_and_clears — fetches unread on mount, renders the dot, and clears it (via POST /individual/read) once the individual row is selected", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    individualUnreadMock.mockResolvedValue({ unread: true, latest_turn_id: 7, last_read_turn_id: 0 })
    markIndividualReadMock.mockResolvedValue({ last_read_turn_id: 7 })

    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(individualUnreadMock).toHaveBeenCalledWith("101"))
    await waitFor(() => expect(screen.getByTestId("individual-chat-unread-dot")).toBeTruthy())
    expect(markIndividualReadMock).not.toHaveBeenCalled()

    fireEvent.click(screen.getByTestId("chat-row-individual"))

    await waitFor(() => expect(markIndividualReadMock).toHaveBeenCalledWith("101"))
    await waitFor(() => expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull())
    // Selecting the row still switches the active thread — the badge-clear
    // is additive to the existing swap, not a replacement for it.
    expect(screen.getByTestId("main-thread-stub").getAttribute("data-active-chat")).toBe("individual")
  })

  it("selecting the GROUP row does not call markIndividualRead", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    individualUnreadMock.mockResolvedValue({ unread: true, latest_turn_id: 3, last_read_turn_id: 0 })

    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("individual-chat-unread-dot")).toBeTruthy())

    fireEvent.click(screen.getByTestId("chat-row-group"))
    expect(markIndividualReadMock).not.toHaveBeenCalled()
    // The badge is untouched by a group-row click.
    expect(screen.getByTestId("individual-chat-unread-dot")).toBeTruthy()
  })

  it("a failed unread fetch leaves the badge unset (best-effort), with no error surfaced", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    individualUnreadMock.mockRejectedValue(new ApiError(500, "unread backend down"))

    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-name")).toBeTruthy())
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()
    expect(screen.queryByTestId("project-detail-error")).toBeNull()
  })
})

// ── ProjectDetailScreen — cross-chat insight fetch (top-of-chain wiring) ───
describe("ProjectDetailScreen — cross-chat insight fetch", () => {
  it("fetches the insight on load and passes a non-null result through as insightNote", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(INSIGHT)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("main-thread-stub")).toBeTruthy())
    expect(memoryInsightMock).toHaveBeenCalledWith("101")
    const host = screen.getByTestId("main-thread-stub")
    expect(host.getAttribute("data-has-insight")).toBe("true")
    expect(host.getAttribute("data-insight-text")).toBe(INSIGHT.text)
  })

  it("a null insight response renders no insight turn, with no error surfaced", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-name")).toBeTruthy())
    expect(screen.getByTestId("main-thread-stub").getAttribute("data-has-insight")).toBe("false")
    expect(screen.queryByTestId("project-detail-error")).toBeNull()
    expect(screen.queryByTestId("project-detail-forbidden")).toBeNull()
    expect(screen.queryByTestId("project-detail-not_found")).toBeNull()
  })

  it("a failed insight fetch (best-effort) renders no insight turn, with no error surfaced", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockRejectedValue(new ApiError(500, "insight backend down"))
    await act(async () => {
      render(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-name")).toBeTruthy())
    expect(screen.getByTestId("main-thread-stub").getAttribute("data-has-insight")).toBe("false")
    expect(screen.queryByTestId("project-detail-error")).toBeNull()
  })
})
