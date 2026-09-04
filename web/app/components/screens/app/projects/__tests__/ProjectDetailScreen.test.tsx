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
const ledgerCountsMock = vi.fn()
const ledgerMock = vi.fn()
const emitDelegationEventMock = vi.fn()
// The container mounts the real `ArtifactsModal` (unmocked), which folds the
// real `AddArtifactPanel` in as its in-modal "add" view; the panel's
// company-library fetch effect only fires once that add view becomes active
// (Artifacts modal open + list→add swap) — a safe empty-list default keeps
// every other test in this file, which never opens that state, unaffected.
const artifactsListMock = vi.fn()
const addArtifactMock = vi.fn()
// The invite modal (mounted, unmocked) now fetches on open — default to an
// empty candidate list so tests that never touch the invite surface aren't
// affected; the one test that opens it sets its own resolved value.
const candidateSearchMock = vi.fn()
const tagCandidateMock = vi.fn()
// The settings-gear modal's Instructions tab GET-on-open effect fires
// whenever `ProjectSettingsModal` mounts (any tab) — give it a safe default
// up front, same reasoning as `ledgerCountsMock` etc. above.
const instructionsMock = vi.fn().mockResolvedValue({ instructions: null })
const setInstructionsMock = vi.fn()
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
      ledgerCounts: (...a: unknown[]) => ledgerCountsMock(...a),
      ledger: (...a: unknown[]) => ledgerMock(...a),
      emitDelegationEvent: (...a: unknown[]) => emitDelegationEventMock(...a),
      candidateSearch: (...a: unknown[]) => candidateSearchMock(...a),
      tagCandidate: (...a: unknown[]) => tagCandidateMock(...a),
      instructions: (...a: unknown[]) => instructionsMock(...a),
      setInstructions: (...a: unknown[]) => setInstructionsMock(...a),
      addArtifact: (...a: unknown[]) => addArtifactMock(...a),
    },
    // `AddArtifactModal` (mounted, unmocked) reads this for its
    // company-library fetch — see `artifactsListMock` above.
    artifactsApi: { list: (...a: unknown[]) => artifactsListMock(...a) },
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
// The container also reads `useSearchParams` (to preserve the other query
// params when it writes `?chat=…` on a surface switch) — provide it alongside
// `useRouter`, an empty param set by default.
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
// `ProjectMainThread` pulls in the shared chat engine's ask/poll wiring — a
// dependency graph (CompanyContext, the shared ask lib, projectsApi network
// calls…) this file has no reason to boot just to test the SHELL (top bar,
// rail, cards, state machine) — the same isolation reason
// `AppLayout`/`NavigationContext` are mocked above. The mount itself (which
// props it receives) is what THIS file verifies; the real thread/composer
// behaviour is `ProjectMainThread.test.tsx`'s job.
vi.mock("../ProjectMainThread", () => ({
  ProjectMainThread: (props: {
    projectId: number | string
    insightNote?: { by: string; text: string } | null
  }) =>
    React.createElement("div", {
      "data-testid": "main-thread-stub",
      "data-project-id": String(props.projectId),
      // Reflects whether/what insightNote this container passed through —
      // ProjectMainThread's OWN rendering of it is out of this file's scope
      // (ProjectMainThread.test.tsx's job); this file only proves the
      // container fed the right value in.
      "data-has-insight": props.insightNote ? "true" : "false",
      "data-insight-text": props.insightNote?.text ?? "",
    }),
}))
// The in-place artifact drawer's own fetch/render semantics are covered by
// `ProjectArtifactDrawer.dom.test.tsx` and (for its LAYOUT placement beside
// the chat) `ProjectDetailView.drawer-layout.dom.test.tsx` — stubbed here,
// same isolation reason as `ProjectMainThread` above, so this file only
// proves the shell still mounts the chat host while an artifact is open.
vi.mock("../ProjectArtifactDrawer", () => ({
  ProjectArtifactDrawer: () => React.createElement("aside", { "data-testid": "drawer-stub" }),
}))

import { ProjectDetailView, ProjectDetailScreen, type ProjectDetailViewProps } from "../ProjectDetailScreen"
import { ContentProvider } from "../../../../../context/ContentContext"

// The `ProjectDetailScreen` CONTAINER now consumes `useContent()`, so it must
// render under a real `ContentProvider` (`useNavigation()` is already satisfied
// by this file's module-level `NavigationContext` mock). The presentational
// `ProjectDetailView` takes everything as props and needs no provider — only
// the container renders route through this helper.
function renderWithContent(node: React.ReactElement) {
  return render(React.createElement(ContentProvider, null, node))
}
// Regular (non-type-only) import: resolves to the mocked `ApiError` above,
// the SAME class reference the component's `instanceof` checks compare
// against — required for the 403/404 container tests below.
import { ApiError, projectsApi } from "../../../../../lib/api"
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
    title: "Contoso call",
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
    "A Contoso-driven redesign of on-demand quoting — a priced quote in under 60 seconds. It also covers the guest path for first-time buyers.",
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
    ledgerCounts: { assigned_to_me_open: 0, waiting_on_open: 0 },
    ledgerRows: [],
    onOpenArtifacts: noop,
    onOpenArtifactInPlace: noop,
    openArtifact: null,
    onCloseArtifactDrawer: noop,
    onOpenTasks: noop,
    onOpenSettings: noop,
    onOpenInvite: noop,
    currentUserId: "current-viewer",
    onRemoveMember: noop,
    refetchArtifacts: noop,
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
  ledgerCountsMock.mockReset()
  ledgerCountsMock.mockResolvedValue({ assigned_to_me_open: 0, waiting_on_open: 0 })
  ledgerMock.mockReset()
  ledgerMock.mockResolvedValue([])
  emitDelegationEventMock.mockReset()
  emitDelegationEventMock.mockResolvedValue({ delegation_id: 1, status: "accepted" })
  candidateSearchMock.mockReset()
  candidateSearchMock.mockResolvedValue({ candidates: [], pending_invites: [] })
  tagCandidateMock.mockReset()
  instructionsMock.mockReset()
  instructionsMock.mockResolvedValue({ instructions: null })
  setInstructionsMock.mockReset()
  artifactsListMock.mockReset()
  artifactsListMock.mockResolvedValue([])
  addArtifactMock.mockReset()
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

  it("test_avatar_tooltip_name — a member avatar's hover tooltip (native title) shows the member's NAME when present", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    // David M. + Shristi are the two human members in PROJECT — the native
    // `title` is the hover tooltip, addressable via getByTitle.
    expect(screen.getByTitle("David M.")).toBeTruthy()
    expect(screen.getByTitle("Shristi")).toBeTruthy()
  })

  it("test_avatar_tooltip_email_fallback — a member with NO name falls back to their EMAIL in the tooltip", () => {
    const emailOnlyProject = {
      ...PROJECT,
      members: [
        {
          kind: "human" as const,
          user_id: "u9",
          name: null,
          email: "newbie@example.com",
          avatar_url: null,
          job_role: null,
          added_at: hoursAgo(1),
        },
      ],
    }
    render(React.createElement(ProjectDetailView, viewProps({ project: emailOnlyProject })))
    expect(screen.getByTitle("newbie@example.com")).toBeTruthy()
    // The generic "Member" fallback is NOT used when an email is available.
    expect(screen.queryByTitle("Member")).toBeNull()
  })
})

// The former "right rail structure" describe — the redesign removes the
// standing rail entirely (AC2). Every assertion that targeted rail-only DOM
// either moved verbatim to `ProjectSettingsModal.dom.test.tsx` (the Members
// tab now owns the member/agent-row assertions) or is superseded by the
// new top-bar layout tests below — nothing here is lost, see the ticket's
// disposition table.
describe("ProjectDetailView — top-bar layout (redesign)", () => {
  it("test_detail_no_standing_rail — project-rail and rail-toggle are absent in the ready state", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.queryByTestId("project-rail")).toBeNull()
    expect(screen.queryByTestId("rail-toggle")).toBeNull()
  })

  it("test_detail_topbar_has_settings_gear_opens_modal — the gear renders and calls onOpenSettings", () => {
    const onOpenSettings = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onOpenSettings })))
    const gear = screen.getByTestId("project-settings-gear")
    expect(gear.getAttribute("aria-label")).toBe("Project settings")
    fireEvent.click(gear)
    expect(onOpenSettings).toHaveBeenCalledTimes(1)
  })

  it("test_detail_topbar_artifacts_button_shows_count_and_opens_modal — topbar-artifacts shows artifacts.length and calls onOpenArtifacts", () => {
    const onOpenArtifacts = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onOpenArtifacts })))
    const btn = screen.getByTestId("topbar-artifacts")
    expect(btn.textContent).toContain(String(ARTIFACTS.length))
    fireEvent.click(btn)
    expect(onOpenArtifacts).toHaveBeenCalledTimes(1)
  })

  it("test_detail_topbar_has_no_add_existing_trigger — the top bar renders no artifact-add-existing testid and no 'Add existing artifact' text (relocated into the Artifacts modal)", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.queryByTestId("artifact-add-existing")).toBeNull()
    expect(screen.queryByText("Add existing artifact")).toBeNull()
  })

  it("test_detail_view_props_has_no_add_existing_field — viewProps() (no onAddExistingArtifact) satisfies ProjectDetailViewProps; a clean type-check is the proof PlusIcon and the prop were fully removed", () => {
    const props: ProjectDetailViewProps = viewProps()
    expect("onAddExistingArtifact" in props).toBe(false)
  })

  it("test_detail_no_chat_toggle — no Group⇆Private tablist renders; the private thread is the only mounted surface", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.queryByTestId("topbar-chat-toggle")).toBeNull()
    expect(screen.queryByTestId("chat-row-group")).toBeNull()
    expect(screen.queryByTestId("chat-row-individual")).toBeNull()
    expect(screen.getByTestId("main-thread-stub")).toBeTruthy()
  })

  it("test_detail_chat_full_bleed_without_artifact — with openArtifact null, project-main-thread-host renders and no rail column exists", () => {
    render(React.createElement(ProjectDetailView, viewProps({ openArtifact: null })))
    expect(screen.getByTestId("project-main-thread-host")).toBeTruthy()
    expect(screen.queryByTestId("project-rail")).toBeNull()
  })

  it("test_detail_artifact_open_keeps_chat_mounted — with an artifact open, the chat host stays rendered", () => {
    render(React.createElement(ProjectDetailView, viewProps({ openArtifact: ARTIFACTS[0] })))
    expect(screen.getByTestId("project-main-thread-host")).toBeTruthy()
  })

  it("test_thread_host_only_bounds_the_mounted_chat — .threadHost (the group⇆private swap host's wrapper) just BOUNDS the mounted ChatShell to its flex track (flex/min-height/display:flex) and must NOT be a second scroll container: the ChatShell already scrolls internally and pins its own composer, so an outer overflow-y + padding here let the user drag past the pinned composer into a dead grey band during the working state. It also never carries the deleted .threadPlaceholder's center/center alignment, so the shell fills the box on the flex default (stretch) (project-scroll fix)", () => {
    render(React.createElement(ProjectDetailView, viewProps({ openArtifact: null })))
    expect(screen.getByTestId("project-main-thread-host").className).toMatch(/threadHost/)
    const css = readFileSync(join(__dirname, "../ProjectDetailScreen.module.css"), "utf8")
    const rule = css.match(/\.threadHost\s*\{[^}]*\}/)?.[0] ?? ""
    expect(rule).toMatch(/flex:\s*1/)
    expect(rule).toMatch(/min-height:\s*0/)
    expect(rule).toMatch(/display:\s*flex/)
    // The redundant outer scroller + its padding are gone — the shell is the
    // only scroller and the composer stays pinned (no dead band below it).
    expect(rule).not.toMatch(/overflow-y/)
    expect(rule).not.toMatch(/padding/)
    expect(rule).not.toMatch(/align-items/)
    expect(rule).not.toMatch(/justify-content/)
  })

  it("test_body_grid_grows_so_short_chat_composer_stays_pinned — .body carries a filling grid row (minmax(0,1fr)) so the chat column (.main, a grid ITEM whose own flex is inert) fills the viewport height even when the transcript is SHORTER than the view; without it the row is auto/content-sized and the composer floats above dead space (project-scroll fix, short-content case)", () => {
    const css = readFileSync(join(__dirname, "../ProjectDetailScreen.module.css"), "utf8")
    const rule = css.match(/\.body\s*\{[^}]*\}/)?.[0] ?? ""
    expect(rule).toMatch(/display:\s*grid/)
    expect(rule).toMatch(/grid-template-rows:\s*minmax\(0,\s*1fr\)/)
    expect(rule).toMatch(/min-height:\s*0/)
  })

  it("test_main_clips_so_composer_stays_pinned_during_working — .main is overflow:hidden so the mounted ChatShell's scroll-content height can't leak past this bounded column up to the app's .main-column (overflow:auto) during the answer render. Without the clip, .main-column's scrollHeight inflated to ~full-transcript height, became scrollable, and an auto-scroll shifted the whole column up — the composer floated above dead space for the entire 'Working' state (measured live: .main-column 20180/855 → 855/855 after the clip)", () => {
    const css = readFileSync(join(__dirname, "../ProjectDetailScreen.module.css"), "utf8")
    const rule = css.match(/^\.main\s*\{[^}]*\}/m)?.[0] ?? ""
    expect(rule).toMatch(/overflow:\s*hidden/)
    expect(rule).toMatch(/min-height:\s*0/)
  })
})

describe("ProjectDetailView — top-bar tasks trigger removed", () => {
  it("test_no_see_all_tasks_trigger_in_topbar — the top-bar 'See all tasks' affordance is gone (the ledger is reached conversationally now)", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    // The tasks entry point was removed from the top bar — no trigger, no copy.
    expect(screen.queryByTestId("tasks-see-all")).toBeNull()
    expect(screen.queryByText("See all tasks")).toBeNull()
  })
})

describe("ProjectDetailView — top-bar invite affordance", () => {
  it("test_topbar_invite_renders_and_calls_on_open_invite — the bare '+' invite icon renders next to the avatars and calls onOpenInvite", () => {
    const onOpenInvite = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ onOpenInvite })))
    const invite = screen.getByTestId("topbar-invite")
    expect(invite.tagName).toBe("BUTTON")
    expect(invite.getAttribute("aria-label")).toBe("Invite members")
    fireEvent.click(invite)
    expect(onOpenInvite).toHaveBeenCalledTimes(1)
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
  it("renders no private-unread badge — the badge was removed with the toggle it depended on (no reachable clear path in the single-surface model)", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()
    // No `individualUnread` field on the props type any more — a clean
    // type-check is the proof the prop was fully removed.
    expect("individualUnread" in viewProps()).toBe(false)
  })

  it("the chat note bar always renders the private copy — there is no other surface to swap to", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const note = screen.getByTestId("chat-note")
    expect(note.textContent).toContain("feeds project memory")
    expect(note.textContent).not.toContain("Open to all members")
    expect(note.querySelector('[data-surface="group"]')).toBeNull()
    expect(note.querySelector('[data-surface="individual"]')).toBeNull()
  })

  // ProjectMainThread OWNS the composer for the private chat — the shell
  // mounts it once, keyed on the project id (its own composer/thread
  // behaviour is out of THIS file's scope, per the isolation mock above).
  it("mounts ProjectMainThread once, on the project id", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    const host = screen.getByTestId("main-thread-stub")
    expect(host.getAttribute("data-project-id")).toBe("101")
  })
})

describe("ProjectDetailView — accessibility", () => {
  it("test_topbar_controls_interactive_with_labels — project-settings-gear/topbar-artifacts/topbar-invite are BUTTONs with accessible names", () => {
    render(React.createElement(ProjectDetailView, viewProps()))
    expect(screen.getByTestId("project-settings-gear").tagName).toBe("BUTTON")
    expect(screen.getByLabelText("Project settings")).toBeTruthy()
    expect(screen.getByTestId("topbar-artifacts").tagName).toBe("BUTTON")
    // The top-bar tasks trigger was removed; the invite affordance replaces it
    // as the newest labeled top-bar control.
    expect(screen.getByTestId("topbar-invite").tagName).toBe("BUTTON")
    expect(screen.getByLabelText("Invite members")).toBeTruthy()
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

  it("the artifact-type badge palette (now in ArtifactsModal.tsx — the rail's own badge/TYPE_BADGE is gone with the rail) matches ArtifactsScreen's real hexes, never the design mockup's purple", () => {
    // Redesign: `TYPE_BADGE`/`ArtifactTypeIcon` left `ProjectDetailScreen.tsx`
    // with the removed rail — the surviving per-type badge palette lives in
    // `ArtifactsModal.tsx` (already mounted, unmodified by this ticket).
    const src = readFileSync(join(__dirname, "../ArtifactsModal.tsx"), "utf8")
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
    // mechanics with mounting `<ProjectInviteModal>` directly.
    // The layout redesign (Projects panel demotion) then removes TWO states —
    // `railCollapsed` (the standing rail + its collapse toggle are gone) and
    // `inviteOpen` (the standalone `<ProjectInviteModal>` mount is gone;
    // Invite is now a tab inside `<ProjectSettingsModal>` reached via the
    // top-bar gear) — and adds exactly ONE: `settingsTab` (`SettingsTab |
    // null`; `null` = the settings modal is closed, a tab value = open on
    // that tab — open-ness is derived, no separate `settingsOpen` boolean).
    // Net 13 − 2 + 1 = 12; the chat rewrite then folds one further state out
    // (the chat surface now owns its own state via the shared controller),
    // leaving 11. The group-chat removal then folds ONE MORE state out —
    // `activeChat` (the Group⇆Private toggle's own state; there is no
    // toggle to hold state for any more, the private surface is the only
    // one and is always mounted) — leaving 10. The private-unread-badge
    // removal (the toggle's removal left it with no reachable clear path,
    // so the planner cut the badge outright rather than leave a dead-end
    // affordance) folds out `individualUnread` too — leaving 9. The guard
    // this test protects — no NEW state for the AGENT STATUS pulse
    // specifically — still holds: `posting` (the ask-composer wiring this
    // guard was written against) is still absent.
    const useStateDeclarations = src.match(/useState\s*[<(]/g) ?? []
    expect(useStateDeclarations).toHaveLength(9)
    expect(src).not.toContain("posting")
  })
})

describe("ProjectDetailScreen source — never imports ChatScreen.tsx", () => {
  it("contains no IMPORT of ChatScreen", () => {
    // Loosened from "no reference to ChatScreen" to "no IMPORT": the rewritten
    // source legitimately mentions ChatScreen in an explanatory comment
    // ("byte-for-byte main's chat evidence-open content-set (ChatScreen's …)").
    // The load-bearing invariant is that the container never imports/mounts the
    // monolith.
    const src = readFileSync(join(__dirname, "../ProjectDetailScreen.tsx"), "utf8")
    expect(src).not.toMatch(/from\s+["'][^"']*ChatScreen["']/)
    expect(src).not.toMatch(/import\s*\{[^}]*\bChatScreen\b[^}]*\}\s*from/)
  })
})

// ── ProjectDetailScreen — loading state (spinner, not skeleton/bare text) ──
describe("ProjectDetailScreen — loading state", () => {
  it("test_detail_loading_renders_spinner_not_skeleton — the loading branch renders project-detail-loading + aria-busy with a spinner (auth-btn-spin), no skeleton", async () => {
    // A never-resolving fetch keeps the container in the "loading" branch
    // for the duration of this assertion.
    getMock.mockReturnValue(new Promise(() => {}))
    artifactsMock.mockReturnValue(new Promise(() => {}))
    memorySummaryMock.mockReturnValue(new Promise(() => {}))
    memoryInsightMock.mockReturnValue(new Promise(() => {}))
    renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    const wrap = screen.getByTestId("project-detail-loading")
    expect(wrap.getAttribute("aria-busy")).toBe("true")
    expect(wrap.querySelector(".auth-btn-spin")).toBeTruthy()
    expect(screen.queryByTestId("project-detail-loading-skeleton")).toBeNull()
    expect(wrap.textContent).not.toContain("Loading…")
  })

  it("test_detail_error_branches_unchanged — 403 -> forbidden, 404 -> not_found, else -> error still render their existing EmptyPane copy (regression)", async () => {
    getMock.mockRejectedValue(new ApiError(403, "Not a member of this project"))
    artifactsMock.mockResolvedValue([])
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
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
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
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
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-name")).toBeTruthy())
    expect(getMock).toHaveBeenCalledWith("101")
    expect(artifactsMock).toHaveBeenCalledWith("101")
    expect(memorySummaryMock).toHaveBeenCalledWith("101")
    expect(memoryInsightMock).toHaveBeenCalledWith("101")
  })

  it("test_detail_add_existing_swaps_drawer_to_add_view — clicking the in-drawer Add control swaps the open artifacts DRAWER in place to the folded add-artifact view (same drawer), not a separate modal; the list is replaced by the add-host + a back control, and Back returns to the list", async () => {
    // Retargeted from the deleted in-modal `ArtifactsModal` swap to its
    // replacement, `ProjectArtifactsDrawer` (the topbar now opens artifacts in
    // the shared side-panel drawer — commit "open chat artifacts in main's
    // shared side-panel; delete the fork"). Same invariant: the browse surface
    // swaps list ⇆ add IN PLACE with a back control, no second dialog.
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    artifactsListMock.mockResolvedValue([])
    await act(async () => {
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("topbar-artifacts")).toBeTruthy())

    // Open the artifacts drawer — the LIST view, with its in-drawer "+ Add"
    // split-menu control.
    fireEvent.click(screen.getByTestId("topbar-artifacts"))
    await waitFor(() => expect(screen.getByTestId("artifacts-drawer-add")).toBeTruthy())
    expect(screen.getByTestId("artifacts-drawer-body")).toBeTruthy()

    // The "+ Add" control opens a menu; "Add existing artifact" swaps the
    // drawer's internal view list → add IN PLACE: the folded AddArtifactPanel
    // renders under an add-host with a "← Back" control, the list body is gone,
    // and NO separate add dialog is mounted.
    fireEvent.click(screen.getByTestId("artifacts-drawer-add"))
    fireEvent.click(screen.getByTestId("artifacts-drawer-menu-existing"))
    await waitFor(() => expect(screen.getByTestId("artifacts-drawer-add-host")).toBeTruthy())
    expect(screen.getByTestId("artifacts-drawer-back")).toBeTruthy()
    expect(screen.queryByTestId("artifacts-drawer-body")).toBeNull()
    expect(screen.queryByTestId("add-artifact-modal")).toBeNull()

    // "← Back" returns to the list view within the SAME drawer.
    fireEvent.click(screen.getByTestId("artifacts-drawer-back"))
    await waitFor(() => expect(screen.getByTestId("artifacts-drawer-body")).toBeTruthy())
    expect(screen.queryByTestId("artifacts-drawer-add-host")).toBeNull()
  })

  it("renders a graceful 'not a member' state on a 403, never a crash", async () => {
    getMock.mockRejectedValue(new ApiError(403, "Not a member of this project"))
    artifactsMock.mockResolvedValue([])
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    await act(async () => {
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
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
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "999" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-detail-not_found")).toBeTruthy())
    expect(screen.getByText("Project not found")).toBeTruthy()
  })

  it("the settings gear's Invite tab opens the project-scoped picker, NOT the global mock InviteModal", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    candidateSearchMock.mockResolvedValue({ candidates: [], pending_invites: [] })
    await act(async () => {
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-settings-gear")).toBeTruthy())
    expect(screen.queryByTestId("project-settings-modal")).toBeNull()

    fireEvent.click(screen.getByTestId("project-settings-gear"))
    expect(screen.getByTestId("project-settings-modal")).toBeTruthy()
    fireEvent.click(screen.getByTestId("settings-tab-invite"))

    // The project-scoped picker body renders inside the settings modal...
    await waitFor(() => expect(screen.getByTestId("project-invite-search")).toBeTruthy())
    // ...and the global mock modal mechanics are never touched.
    expect(openModalMock).not.toHaveBeenCalled()
  })

  it("the top-bar '+' invite affordance opens the settings modal directly on the Invite tab", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    candidateSearchMock.mockResolvedValue({ candidates: [], pending_invites: [] })
    await act(async () => {
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("topbar-invite")).toBeTruthy())
    // Modal is closed until the affordance is clicked.
    expect(screen.queryByTestId("project-settings-modal")).toBeNull()

    fireEvent.click(screen.getByTestId("topbar-invite"))

    // The SAME settings modal the gear opens, but landing on the Invite tab
    // (onOpenInvite → settingsTab "invite"), not the gear's default
    // Instructions tab.
    expect(screen.getByTestId("project-settings-modal")).toBeTruthy()
    await waitFor(() => expect(screen.getByTestId("project-invite-search")).toBeTruthy())
    expect(screen.getByTestId("settings-panel-invite")).toBeTruthy()
    expect(openModalMock).not.toHaveBeenCalled()
  })

  it("the Invite tab renders no 'On this project' current-members block", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)
    candidateSearchMock.mockResolvedValue({ candidates: [], pending_invites: [] })
    await act(async () => {
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-settings-gear")).toBeTruthy())
    fireEvent.click(screen.getByTestId("project-settings-gear"))
    fireEvent.click(screen.getByTestId("settings-tab-invite"))
    await waitFor(() => expect(screen.getByTestId("project-invite-search")).toBeTruthy())

    expect(screen.queryByTestId("project-invite-members-label")).toBeNull()
    expect(screen.queryByTestId("project-invite-members")).toBeNull()
    expect(screen.queryByTestId("project-invite-member-row")).toBeNull()
    expect(screen.queryByTestId("project-invite-member-row-agent")).toBeNull()
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
        renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
      })
      await waitFor(() => expect(screen.getByTestId("project-settings-gear")).toBeTruthy())
      fireEvent.click(screen.getByTestId("project-settings-gear"))
      fireEvent.click(screen.getByTestId("settings-tab-members"))
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
        renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
      })
      await waitFor(() => expect(screen.getByTestId("project-settings-gear")).toBeTruthy())
      fireEvent.click(screen.getByTestId("project-settings-gear"))
      fireEvent.click(screen.getByTestId("settings-tab-members"))
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
        renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
      })
      await waitFor(() => expect(screen.getByTestId("project-settings-gear")).toBeTruthy())
      fireEvent.click(screen.getByTestId("project-settings-gear"))
      fireEvent.click(screen.getByTestId("settings-tab-members"))
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

// ── ProjectDetailScreen — private-unread badge REMOVED ──────────────────────
//
// The badge (AD-P3/AD-P20) is gone entirely — planner decision: with the
// Group⇆Private toggle removed, the badge's only clear-path trigger (the
// toggle's Private-tab click) no longer exists, and in the single-surface
// model the per-project private-unread badge no longer earns its place.
// `projectsApi.individualUnread`/`markIndividualRead` were removed from
// `web/app/lib/api.ts` alongside it (the dot was their only FE consumer);
// the backend `/individual/unread` + `/individual/read` routes are now
// orphaned server-side (a separate, backend-only follow-up).
describe("ProjectDetailScreen — private-unread badge removed", () => {
  it("renders no unread badge, and the container never calls the removed unread endpoints", async () => {
    getMock.mockResolvedValue(PROJECT)
    artifactsMock.mockResolvedValue(ARTIFACTS)
    memorySummaryMock.mockResolvedValue(MEMORY)
    memoryInsightMock.mockResolvedValue(null)

    await act(async () => {
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("main-thread-stub")).toBeTruthy())
    expect(screen.queryByTestId("individual-chat-unread-dot")).toBeNull()
    // Closed-world: the mocked `projectsApi` object has no such methods to
    // even accidentally call any more.
    expect("individualUnread" in projectsApi).toBe(false)
    expect("markIndividualRead" in projectsApi).toBe(false)
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
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
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
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
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
      renderWithContent(React.createElement(ProjectDetailScreen, { projectId: "101" }))
    })
    await waitFor(() => expect(screen.getByTestId("project-name")).toBeTruthy())
    expect(screen.getByTestId("main-thread-stub").getAttribute("data-has-insight")).toBe("false")
    expect(screen.queryByTestId("project-detail-error")).toBeNull()
  })
})
