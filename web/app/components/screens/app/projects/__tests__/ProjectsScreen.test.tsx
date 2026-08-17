// @vitest-environment jsdom
//
// Tests for the Projects list surface: the pure `ProjectsView` (content,
// filtering, a11y — same View/Screen split as `ArtifactsView`/
// `ArtifactsScreen`), the `ProjectsScreen` container's data + nav wiring, and
// the Sidebar rail entry it depends on. All context boundaries are mocked
// (not re-implemented) — same posture as `ArtifactsScreen.import.dom.test.tsx`
// and `Sidebar.dom.test.tsx`.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const listMock = vi.fn()
const pushMock = vi.fn()
const goTo = vi.fn()

vi.mock("../../../../../lib/api", () => ({
  projectsApi: { list: (...a: unknown[]) => listMock(...a) },
}))
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: pushMock }) }))
vi.mock("../../AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))
vi.mock("../../../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "projects",
    goTo,
    goToNewChat: vi.fn(),
    goToWorkbench: vi.fn(),
    openPalette: vi.fn(),
    sidebarCollapsed: true,
    toggleSidebar: vi.fn(),
  }),
}))
vi.mock("../../../../../context/ContentContext", () => ({
  useContent: () => ({ content: {} }),
}))
vi.mock("../../../../../lib/auth", () => ({
  useAuth: () => ({ kind: "anonymous", signOut: vi.fn() }),
}))
vi.mock("../../../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    profile: null,
    workspace: null,
    workspaces: [],
    activeWorkspace: null,
    orgRole: null,
    setActiveWorkspace: vi.fn(),
    refresh: vi.fn(),
  }),
}))

import { ProjectsView, ProjectsScreen } from "../ProjectsScreen"
import { Sidebar } from "../../../../shared/Sidebar"
import type { ProjectListItem } from "../../../../../lib/api"

const hoursAgo = (h: number) => new Date(Date.now() - h * 3600 * 1000).toISOString()

const MANUAL_PROJECT: ProjectListItem = {
  id: 101,
  company_id: "c1",
  workspace_id: "w1",
  name: "Instant-quote flow",
  origin: "manual",
  created_by: "u1",
  created_at: hoursAgo(48),
  updated_at: hoursAgo(2),
  artifact_counts: { prd: 2, ticket_set: 14, prototype: 3, evidence: 6 },
  member_count: 4,
  has_group_chat: true,
  memory_count: 24,
}

const AUTO_PROJECT: ProjectListItem = {
  id: 102,
  company_id: "c1",
  workspace_id: "w1",
  name: "Onboarding v2",
  origin: "prd_auto",
  created_by: "u1",
  created_at: hoursAgo(30),
  updated_at: hoursAgo(26),
  artifact_counts: { prd: 1, ticket_set: 9, prototype: 1 },
  member_count: 2,
  has_group_chat: false,
  memory_count: 11,
}

const noop = () => {}

type ViewProps = React.ComponentProps<typeof ProjectsView>

function viewProps(overrides: Partial<ViewProps> = {}): ViewProps {
  return {
    projects: [MANUAL_PROJECT, AUTO_PROJECT],
    loading: false,
    error: false,
    onRetry: noop,
    search: "",
    onSearchChange: noop,
    onOpen: noop,
    onNewProject: noop,
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  listMock.mockReset()
  pushMock.mockReset()
  goTo.mockReset()
})

describe("ProjectsView — card content", () => {
  it("renders a card per project from the given list", () => {
    const html = renderToStaticMarkup(React.createElement(ProjectsView, viewProps()))
    expect(html).toContain("Instant-quote flow")
    expect(html).toContain("Onboarding v2")
  })

  it("renders the serif name heading and one correctly-colored badge per non-empty type", () => {
    const html = renderToStaticMarkup(React.createElement(ProjectsView, viewProps()))
    // Serif name lives in an <h3> (the styled title element).
    expect(html).toMatch(/<h3[^>]*>Instant-quote flow<\/h3>/)
    // PRD/prototype/evidence/tickets counts for the manual project.
    expect(html).toContain("<b>2</b>")
    expect(html).toContain("<b>14</b>")
    expect(html).toContain("<b>3</b>")
    expect(html).toContain("<b>6</b>")
    // Prototype badge resolves to the app's real prototype color (ArtifactsScreen's
    // ARTIFACT_BADGE.prototype), never the design mockup's purple.
    expect(html).toContain("#DBEAFE")
    expect(html).toContain("#1E40AF")
    expect(html).not.toContain("634AB0")
  })

  it("renders the avatar stack, artifact indicator, and chats indicator", () => {
    render(React.createElement(ProjectsView, viewProps()))
    const cards = screen.getAllByTestId("project-card")
    expect(cards).toHaveLength(2)
    const manualCard = within(cards[0])
    expect(manualCard.getByTestId("av-stack").getAttribute("aria-label")).toBe("4 members")
    // Footer meta now shows total artifacts on the project (sum of the per-type
    // `artifact_counts`), not the old "N insights" memory count:
    // MANUAL = prd 2 + ticket_set 14 + prototype 3 + evidence 6 = 25.
    expect(manualCard.getByText(/25 artifacts/)).toBeTruthy()
    expect(manualCard.queryByText(/insight/)).toBeNull()
    expect(manualCard.getByText("Group chat")).toBeTruthy()

    const autoCard = within(cards[1])
    expect(autoCard.getByTestId("av-stack").getAttribute("aria-label")).toBe("2 members")
    // AUTO = prd 1 + ticket_set 9 + prototype 1 = 11 artifacts.
    expect(autoCard.getByText(/11 artifacts/)).toBeTruthy()
    expect(autoCard.getByText("No group chat yet")).toBeTruthy()
  })

  it("shows 'Auto · from PRD' iff origin==='prd_auto'", () => {
    render(React.createElement(ProjectsView, viewProps()))
    const cards = screen.getAllByTestId("project-card")
    expect(within(cards[0]).queryByText("Auto · from PRD")).toBeNull()
    expect(within(cards[1]).getByText("Auto · from PRD")).toBeTruthy()
  })
})

describe("ProjectsView — states", () => {
  it("renders EmptyPane with the specified copy when there are zero projects", () => {
    render(React.createElement(ProjectsView, viewProps({ projects: [] })))
    expect(
      screen.getByText("No projects yet — your first PRD will start one automatically."),
    ).toBeTruthy()
  })

  it("test_list_error_shows_error_not_empty — error=true renders the error surface with a retry control, not the empty state", () => {
    render(React.createElement(ProjectsView, viewProps({ projects: [], error: true })))
    expect(screen.getByTestId("projects-error")).toBeTruthy()
    expect(screen.getByTestId("projects-retry")).toBeTruthy()
    expect(screen.getByText("Couldn't load your projects")).toBeTruthy()
    expect(screen.queryByText("No projects yet — your first PRD will start one automatically.")).toBeNull()
  })

  it("test_empty_list_still_shows_empty_pane — error=false and an empty list renders the existing empty EmptyPane, not the error surface (regression)", () => {
    render(React.createElement(ProjectsView, viewProps({ projects: [], error: false })))
    expect(
      screen.getByText("No projects yet — your first PRD will start one automatically."),
    ).toBeTruthy()
    expect(screen.queryByTestId("projects-error")).toBeNull()
  })

  it("clicking projects-retry invokes onRetry", () => {
    const onRetry = vi.fn()
    render(React.createElement(ProjectsView, viewProps({ projects: [], error: true, onRetry })))
    fireEvent.click(screen.getByTestId("projects-retry"))
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it("renders no status filter tabs", () => {
    const { container } = render(React.createElement(ProjectsView, viewProps()))
    expect(container.querySelectorAll('[role="tab"]').length).toBe(0)
    expect(screen.queryByText("Shipping")).toBeNull()
    expect(screen.queryByText("In build")).toBeNull()
    expect(screen.queryByText("Scoping")).toBeNull()
  })

  it("filters the rendered card set client-side by project name", () => {
    function Harness() {
      const [search, setSearch] = React.useState("")
      return React.createElement(ProjectsView, viewProps({ search, onSearchChange: setSearch }))
    }
    render(React.createElement(Harness))
    expect(screen.getAllByTestId("project-card")).toHaveLength(2)
    fireEvent.change(screen.getByTestId("projects-search"), { target: { value: "onboarding" } })
    expect(screen.getAllByTestId("project-card")).toHaveLength(1)
    expect(screen.getByText("Onboarding v2")).toBeTruthy()
    expect(screen.queryByText("Instant-quote flow")).toBeNull()
  })
})

describe("ProjectsView — accessibility", () => {
  it("every card is a real, keyboard-reachable <button>; icon-only indicators carry a title", () => {
    render(React.createElement(ProjectsView, viewProps()))
    const cards = screen.getAllByTestId("project-card")
    for (const card of cards) {
      expect(card.tagName).toBe("BUTTON")
      expect(card.hasAttribute("disabled")).toBe(false)
    }
    // Chats indicator is meaning-bearing (icon + text) and carries a title.
    expect(within(cards[0]).getByTitle("Group chat")).toBeTruthy()
    expect(within(cards[1]).getByTitle("No group chat yet")).toBeTruthy()
    // Search input is labeled (not a bare icon-only control).
    expect(screen.getByLabelText("Search projects")).toBeTruthy()
  })

  it("invokes onOpen with the project id when a card is activated", () => {
    const onOpen = vi.fn()
    render(React.createElement(ProjectsView, viewProps({ onOpen })))
    fireEvent.click(screen.getAllByTestId("project-card")[0])
    expect(onOpen).toHaveBeenCalledWith(101)
  })
})

// ── ProjectsScreen — container wiring (data fetch + flat-route nav) ─────────
describe("ProjectsScreen — data + nav wiring", () => {
  it("renders cards from mocked projectsApi.list (no dataset arg)", async () => {
    listMock.mockResolvedValue([MANUAL_PROJECT])
    await act(async () => {
      render(React.createElement(ProjectsScreen))
    })
    await waitFor(() => expect(screen.getByText("Instant-quote flow")).toBeTruthy())
    expect(listMock).toHaveBeenCalledWith()
  })

  it("clicking a project card navigates to the flat `/projects?id=<id>` route, never a path segment", async () => {
    listMock.mockResolvedValue([MANUAL_PROJECT])
    await act(async () => {
      render(React.createElement(ProjectsScreen))
    })
    await waitFor(() => expect(screen.getByText("Instant-quote flow")).toBeTruthy())
    fireEvent.click(screen.getByTestId("project-card"))
    expect(pushMock).toHaveBeenCalledWith("/projects?id=101")
    expect(pushMock).not.toHaveBeenCalledWith("/projects/101")
  })

  it("a rejected list() renders the error surface, not the empty state", async () => {
    listMock.mockRejectedValue(new Error("network down"))
    await act(async () => {
      render(React.createElement(ProjectsScreen))
    })
    await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy())
    expect(screen.queryByText("No projects yet — your first PRD will start one automatically.")).toBeNull()
  })

  it("test_retry_refetches — clicking projects-retry re-calls list(); a subsequent success renders the list and clears the error", async () => {
    listMock.mockRejectedValueOnce(new Error("network down")).mockResolvedValueOnce([MANUAL_PROJECT])
    await act(async () => {
      render(React.createElement(ProjectsScreen))
    })
    await waitFor(() => expect(screen.getByTestId("projects-error")).toBeTruthy())

    await act(async () => {
      fireEvent.click(screen.getByTestId("projects-retry"))
    })

    await waitFor(() => expect(screen.getByText("Instant-quote flow")).toBeTruthy())
    expect(listMock).toHaveBeenCalledTimes(2)
    expect(screen.queryByTestId("projects-error")).toBeNull()
  })
})

// ── Sidebar — coexistence + nav plumbing (AC1) ───────────────────────────────
describe("Sidebar — Projects rail entry", () => {
  // The rail item is gated behind NEXT_PUBLIC_PROJECTS_ENABLED (build-time
  // cosmetic gate — see `Sidebar.projects-flag.dom.test.tsx`, which owns the
  // on/off gating behaviour itself). This test only needs the flag ON so the
  // item is present to assert coexistence + click-to-navigate against.
  afterEach(() => {
    vi.unstubAllEnvs()
  })

  it("Projects coexists with Artifacts and Ideation; clicking it navigates to the 'projects' screen", () => {
    vi.stubEnv("NEXT_PUBLIC_PROJECTS_ENABLED", "1")
    render(React.createElement(Sidebar))
    expect(screen.getByLabelText("Projects")).toBeTruthy()
    expect(screen.getByLabelText("Artifacts")).toBeTruthy()
    expect(screen.getByLabelText("Ideation")).toBeTruthy()
    fireEvent.click(screen.getByLabelText("Projects"))
    expect(goTo).toHaveBeenCalledWith("projects")
  })
})
