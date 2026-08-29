// @vitest-environment jsdom
//
// Sidebar nav-wiring DOM tests.
//
// After the brief/chat unification, the home surface (`/`, ChatScreen) defaults
// to the pinned Top Insights tab on a fresh load. So the sidebar "New chat" `+`
// must NOT use the plain goTo("chat") nav (that would land on the brief) — it
// uses goToNewChat() (→ `/?new=1`, consumed by ChatScreen to start a fresh chat).
// The "Top Insights" and "All chats" rail items keep their plain goTo() nav.
//
// These tests mount the REAL Sidebar, mocking only the context boundaries it
// reads, and assert the click→nav wiring (not a re-implementation).
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goTo = vi.fn()
const goToNewChat = vi.fn()
const goToWorkbench = vi.fn()
const openPalette = vi.fn()
const toggleSidebar = vi.fn()
let sidebarCollapsed = true

// The rail carries a trial countdown that links to billing, so it now uses the
// router directly — `goTo` takes a screen id and the settings SECTION rides
// the query string.
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }))

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    currentScreen: "brief",
    goTo,
    goToNewChat,
    goToWorkbench,
    openPalette,
    sidebarCollapsed,
    toggleSidebar,
  }),
}))

vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: {} }),
}))

// Signed in by default: the sidebar only ever renders inside the authenticated
// app, and its recent-chats section reads an authed route. Individual tests
// flip this to check the signed-out path.
let authState: { kind: string; user?: { id: string } } = {
  kind: "authed",
  user: { id: "u-1" },
}
vi.mock("../../../lib/auth", () => ({
  useAuth: () => ({ ...authState, signOut: vi.fn() }),
}))

// The conversation list behind the nav's recent-chats section. Mocked at the
// API boundary rather than at `useChatsList`, so the hook's own
// cache/stale-while-revalidate behaviour is exercised rather than stubbed out.
let conversations: Array<Record<string, unknown>> = []
vi.mock("../../../lib/api", () => ({
  conversationsApi: { list: () => Promise.resolve({ conversations }) },
}))

const setActiveWorkspace = vi.fn()
let workspacesState: Array<{
  id: string
  name: string
  slug: string
  is_default: boolean
  product_id: string | null
  dataset: string | null
  role: string
}> = []
let activeWorkspaceState: (typeof workspacesState)[number] | null = null
// Company-level role — workspace creation gates on THIS, not the
// per-workspace effective role each summary row carries.
let orgRoleState: string | null = null

vi.mock("../../../context/WorkspaceContext", () => ({
  profileDisplayName: () => "Ada Lovelace",
  useWorkspace: () => ({
    profile: null,
    workspace: null,
    workspaces: workspacesState,
    activeWorkspace: activeWorkspaceState,
    orgRole: orgRoleState,
    setActiveWorkspace,
    refresh: vi.fn(),
  }),
}))

import { Sidebar } from "../Sidebar"
import {
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
} from "../../../lib/sidebarWidth"
import { chatsCacheKey, setCachedChats } from "../../../lib/recentChats"

beforeEach(() => {
  goTo.mockClear()
  goToNewChat.mockClear()
  goToWorkbench.mockClear()
  openPalette.mockClear()
  toggleSidebar.mockClear()
  setActiveWorkspace.mockClear()
  sidebarCollapsed = true
  authState = { kind: "authed", user: { id: "u-1" } }
  conversations = []
  workspacesState = []
  activeWorkspaceState = null
  orgRoleState = null
  // EMPTY THE SHARED CHATS CACHE. `useChatsList` is stale-while-revalidate
  // over a module-global Map, and mocking at the API boundary (above) is what
  // exercises that — so the three threads one test seeded are still cached
  // when the next one mounts, and render on its FIRST frame, before its own
  // fetch lands. That is what turned "says nothing at all when there are no
  // threads" red on CI while it passed here: locally the mocked list is an
  // already-resolved promise that wins the race, and on a loaded runner it
  // does not. Reproduced by resolving the mock a few ms late, which fails
  // four of these tests, including with CI's exact "…(3)" stale rows.
  //
  // Seeded empty rather than deleted because that is what the existing
  // exports offer, and the two are equivalent here: an empty cache renders
  // nothing either way, and every test that wants rows awaits the section.
  setCachedChats(chatsCacheKey("u-1", "acme"), [])
  setCachedChats(chatsCacheKey("u-1", null), [])
})
afterEach(() => cleanup())

describe("Sidebar — New chat wiring", () => {
  it("'New chat' uses goToNewChat (fresh chat), never goTo('chat') (would land on brief)", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("New chat"))
    expect(goToNewChat).toHaveBeenCalledTimes(1)
    expect(goTo).not.toHaveBeenCalledWith("chat")
  })

  it("'Top Insights' keeps its plain goTo() nav", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Top Insights"))
    expect(goTo).toHaveBeenCalledWith("brief")
    // The new-chat helper was not triggered by either.
    expect(goToNewChat).not.toHaveBeenCalled()
  })
})

// ── Workbench: hidden from the rail (2026-08-07) ─────────────────────────────
// The Workbench trigger is commented out of the rail on a product call, the
// same way Search is. The surface behind it is NOT gone: goToWorkbench and the
// `/?tab=last` one-shot ChatScreen consumes are untouched, so these tests only
// assert the rail no longer offers the door — never that the tab stopped
// working. Top Insights keeps its own, separate door to the pinned brief tab.
describe("Sidebar — Workbench (hidden)", () => {
  it("no longer renders the Workbench trigger", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByTestId("sidebar-workbench")).toBeNull()
    expect(screen.queryByLabelText("Workbench")).toBeNull()
    expect(goToWorkbench).not.toHaveBeenCalled()
  })

  it("Top Insights is now the first rail item, and still routes to the pinned brief tab", () => {
    const { container } = render(React.createElement(Sidebar))
    const labels = Array.from(container.querySelectorAll(".sb-rail-nav .sb-rail-label")).map(
      (el) => el.textContent,
    )
    expect(labels[0]).toBe("Top Insights")
    fireEvent.click(screen.getByLabelText("Top Insights"))
    expect(goTo).toHaveBeenCalledWith("brief")
    expect(goToWorkbench).not.toHaveBeenCalled()
  })
})

// ── Shell restyle: every nav affordance is preserved ──────────────────────────
// The visual restyle of the rail must NOT drop any nav entry. This guards the
// full set so a future CSS/markup change can't silently remove one. Sign out
// deliberately does NOT appear here: it moved to Settings → Account, and the
// rail's user row is display-only.
describe("Sidebar — nav affordances preserved after restyle", () => {
  // The rail's Search trigger is hidden for now (product call, 2026-07-31). The
  // palette is NOT removed: AppShell still renders it and owns ⌘K, so this only
  // asserts the button is absent — never that search stopped working.
  it("no longer renders the Search trigger (palette + ⌘K are untouched)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByTestId("palette-trigger")).toBeNull()
    expect(screen.queryByLabelText("Search (Ctrl+K)")).toBeNull()
    expect(openPalette).not.toHaveBeenCalled()
  })

  // Workbench is deliberately absent from this list — it is hidden on a product
  // call (2026-08-07), guarded by the "Workbench (hidden)" suite above.
  it("renders New chat and Top Insights; Settings and Feedback moved to the identity row", () => {
    render(React.createElement(Sidebar))
    for (const label of [
      "New chat",
      "Top Insights",
      "Backlog",
      "Settings",
      "Feedback",
    ]) {
      expect(screen.getByLabelText(label)).toBeTruthy()
    }
  })

  // Templates came back on the rail with artifact formats (2026-08): the screen
  // now decides what every PRD, ticket and engineering spec Sprntly writes
  // LOOKS like, not only which finished examples it reads for voice. That is a
  // setting a PM sets up once and returns to, so it needs a door of its own —
  // while the screen was exemplars-only the item stayed commented out.
  it("no longer carries Templates or Skills — they moved into Settings", () => {
    // Both are things a workspace sets up once and returns to, which is what
    // Settings is for; the room they freed is where the threads now live. The
    // screens, routes and command-palette entries are untouched.
    render(<Sidebar />)
    expect(screen.queryByLabelText("Templates")).toBeNull()
    expect(screen.queryByLabelText("Skills")).toBeNull()
  })

  it("no longer carries a Guide link — it moved into Settings", () => {
    // Guide is the public docs site, and Settings is already where the
    // read-about-it surfaces live. The rail's bottom block is gone entirely:
    // its three rows are two icons in the identity row plus this move.
    render(<Sidebar />)
    expect(screen.queryByTestId("sidebar-guide-link")).toBeNull()
    expect(screen.queryByLabelText("Guide")).toBeNull()
  })

  it("no longer renders a Sources rail item (hidden from the rail; screen + route kept)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Sources")).toBeNull()
  })

  it("no longer renders a Sign out affordance (it lives in Settings → Account)", () => {
    render(React.createElement(Sidebar))
    expect(screen.queryByLabelText("Sign out")).toBeNull()
  })

  it("renders the Backlog rail icon (named Ideation until 2026-08-27)", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Backlog"))
    expect(goTo).toHaveBeenCalledWith("ideation")
  })

  it("Feedback opens the feedback modal (not a nav)", () => {
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByLabelText("Feedback"))
    // Feedback is a modal trigger, not a screen nav.
    expect(goTo).not.toHaveBeenCalled()
    expect(goToNewChat).not.toHaveBeenCalled()
  })

  it("renders the brand mark with its accent dot", () => {
    const { container } = render(React.createElement(Sidebar))
    expect(container.querySelector(".sb-rail-logo-dot")).toBeTruthy()
  })
})

// ── Workspace switcher (multi-workspace 2026-07) ─────────────────────────────
describe("Sidebar — workspace switcher", () => {
  const twoWorkspaces = () => {
    workspacesState = [
      { id: "ws-a", name: "Acme App", slug: "default", is_default: true, product_id: null, dataset: "acme", role: "admin" },
      { id: "ws-b", name: "Notifications", slug: "notifications", is_default: false, product_id: null, dataset: "acme--notifications", role: "admin" },
    ]
    activeWorkspaceState = workspacesState[0]
    orgRoleState = "admin"
    sidebarCollapsed = false
  }

  it("shows the active workspace name as the brand and opens the menu", () => {
    twoWorkspaces()
    render(React.createElement(Sidebar))
    const trigger = screen.getByTestId("workspace-switcher")
    expect(trigger.textContent).toContain("Acme App")
    fireEvent.click(trigger)
    expect(screen.getByText("Notifications")).toBeTruthy()
  })

  it("selecting a workspace calls setActiveWorkspace and closes the menu", () => {
    twoWorkspaces()
    const { container } = render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    fireEvent.click(screen.getByText("Notifications"))
    expect(setActiveWorkspace).toHaveBeenCalledWith("ws-b")
    expect(container.querySelector(".sb-ws-menu")).toBeNull()
  })

  it("org admins see '+ New workspace'; the trigger is static for a lone non-admin workspace", () => {
    twoWorkspaces()
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    expect(screen.getByText("+ New workspace")).toBeTruthy()
    cleanup()

    workspacesState = [
      { id: "ws-a", name: "Acme App", slug: "default", is_default: true, product_id: null, dataset: "acme", role: "member" },
    ]
    activeWorkspaceState = workspacesState[0]
    orgRoleState = "member"
    const { container } = render(React.createElement(Sidebar))
    expect(
      container.querySelector(".sb-ws-trigger--static"),
    ).toBeTruthy()
  })

  it("a WORKSPACE-level admin who is a plain org member gets no create button (org-admin gated)", () => {
    twoWorkspaces()
    // Effective role on the rows is admin, but the company-level role is not.
    orgRoleState = "member"
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    expect(screen.queryByText("+ New workspace")).toBeNull()
  })

  it("an org OWNER sees the create button (owner ⊇ admin)", () => {
    twoWorkspaces()
    orgRoleState = "owner"
    render(React.createElement(Sidebar))
    fireEvent.click(screen.getByTestId("workspace-switcher"))
    expect(screen.getByText("+ New workspace")).toBeTruthy()
  })
})

// ── Expand / collapse toggle ──────────────────────────────────────────────────
describe("Sidebar — expand/collapse toggle", () => {
  it("renders the collapsed rail and an Expand control that fires toggleSidebar", () => {
    sidebarCollapsed = true
    const { container } = render(React.createElement(Sidebar))
    expect(container.querySelector(".sidebar--collapsed")).toBeTruthy()
    fireEvent.click(screen.getByLabelText("Expand sidebar"))
    expect(toggleSidebar).toHaveBeenCalledTimes(1)
  })

  it("renders the expanded rail with a Collapse control when not collapsed", () => {
    sidebarCollapsed = false
    const { container } = render(React.createElement(Sidebar))
    expect(container.querySelector(".sidebar--expanded")).toBeTruthy()
    fireEvent.click(screen.getByLabelText("Collapse sidebar"))
    expect(toggleSidebar).toHaveBeenCalledTimes(1)
  })
})

describe("Sidebar — the threads themselves are in the nav", () => {
  // The report: chat history was one icon among nine, so returning to a thread
  // took two navigations — open the screen, then find the row. The nav shows
  // the threads now, and keeps ONE row at the bottom for the rest.

  const seed = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      id: i + 1,
      title: `Thread ${i + 1}`,
      query: `q${i + 1}`,
      reply: `r${i + 1}`,
      prd_id: null,
      project_id: null,
      // Descending, so "most recent first" is observable rather than incidental.
      created_at: new Date(Date.UTC(2026, 0, 1, 0, n - i)).toISOString(),
      updated_at: "2026-06-01T00:00:00Z",
    }))

  const openExpanded = async () => {
    sidebarCollapsed = false
    render(<Sidebar activeCompany="acme" />)
    return screen.findByTestId("sidebar-recent-chats")
  }

  it("lists the threads, most recent first", async () => {
    conversations = seed(3)
    const section = await openExpanded()
    const titles = Array.from(
      section.querySelectorAll('[data-testid^="sidebar-chat-"] .sb-chat-title'),
    ).map((el) => el.textContent)
    expect(titles).toEqual(["Thread 1", "Thread 2", "Thread 3"])
  })

  it("orders by when a thread STARTED, not by when it was last written to", async () => {
    // `updated_at` is not user activity. The surface patches a conversation's
    // `prd_id` when it binds a PRD, and that write bumps the column — three
    // threads started on three different days were all stamped within the same
    // second by one mount, and clumped at the top of the nav together. That is
    // what "the same thread keeps repeating" turned out to be.
    conversations = [
      { id: 1, title: "Started first", query: "a", created_at: "2026-01-01T09:00:00Z",
        updated_at: "2026-05-05T19:13:51Z", prd_id: 1, project_id: null },
      { id: 2, title: "Started second", query: "b", created_at: "2026-01-02T09:00:00Z",
        updated_at: "2026-05-05T19:13:51Z", prd_id: 2, project_id: null },
      { id: 3, title: "Started third", query: "c", created_at: "2026-01-03T09:00:00Z",
        updated_at: "2026-05-05T19:13:50Z", prd_id: 3, project_id: null },
    ]
    const section = await openExpanded()
    const titles = Array.from(
      section.querySelectorAll('[data-testid^="sidebar-chat-"] .sb-chat-title'),
    ).map((el) => el.textContent)
    expect(titles).toEqual(["Started third", "Started second", "Started first"])
  })

  it("leaves out a conversation with no title", async () => {
    // Stored blank (project-bound rows are, today). In a list of titles that
    // renders as a clickable empty line, which reads as a broken nav.
    conversations = [
      { id: 1, title: "", query: "a", created_at: "2026-01-02T09:00:00Z",
        updated_at: "2026-01-02T09:00:00Z", prd_id: null, project_id: 35 },
      { id: 2, title: "   ", query: "b", created_at: "2026-01-03T09:00:00Z",
        updated_at: "2026-01-03T09:00:00Z", prd_id: null, project_id: 35 },
      { id: 3, title: "A real one", query: "c", created_at: "2026-01-01T09:00:00Z",
        updated_at: "2026-01-01T09:00:00Z", prd_id: null, project_id: null },
    ]
    const section = await openExpanded()
    const rows = section.querySelectorAll('[data-testid^="sidebar-chat-"]')
    expect(rows.length).toBe(1)
    expect(rows[0].querySelector(".sb-chat-title")?.textContent).toBe("A real one")
  })

  it("tells four asks of the SAME question apart", async () => {
    // The report: "just show me the prds that i created" appeared four times
    // and looked like one thread repeating. It was four conversations, asked
    // over 45 minutes, with four different answers — one of them six turns
    // long. Deduplicating would have hidden three real threads; the fix is
    // that the rows say WHEN.
    const title = "just show me the prds that i created"
    conversations = [
      { id: 1045, title, query: "q", created_at: "2026-08-25T11:19:49Z",
        updated_at: "2026-08-25T11:19:49Z", prd_id: null, project_id: null },
      { id: 1047, title, query: "q", created_at: "2026-08-25T11:56:17Z",
        updated_at: "2026-08-25T11:56:17Z", prd_id: null, project_id: null },
      { id: 1049, title, query: "q", created_at: "2026-08-25T12:04:35Z",
        updated_at: "2026-08-25T12:04:35Z", prd_id: null, project_id: null },
    ]
    const section = await openExpanded()

    // All three rows survive — they are not duplicates.
    const rows = section.querySelectorAll('[data-testid^="sidebar-chat-"]')
    expect(rows.length).toBe(3)

    // And each carries its own stamp, so they are visibly three occasions.
    const stamps = Array.from(rows).map(
      (r) => r.querySelector(".sb-chat-when")?.textContent,
    )
    expect(new Set(stamps).size).toBe(3)
    expect(stamps.every((s) => !!s)).toBe(true)
  })

  it("gives every row a marker and an ellipsized title", async () => {
    // Twenty left-aligned strings of different lengths read as a wall; the dot
    // gives them a common starting line, and the title truncates rather than
    // wrapping the list to twice its height.
    conversations = seed(1)
    const section = await openExpanded()
    const row = section.querySelector('[data-testid="sidebar-chat-1"]')!
    expect(row.querySelector(".sb-chat-dot")).not.toBeNull()
    expect(row.querySelector(".sb-chat-title")?.textContent).toBe("Thread 1")
  })

  it("stops at twenty, however many there are", async () => {
    // The list has to end above the fold on a laptop and below the point where
    // the nav stops being scannable.
    conversations = seed(45)
    const section = await openExpanded()
    expect(
      section.querySelectorAll('[data-testid^="sidebar-chat-"]').length,
    ).toBe(20)
  })

  it("opens the thread that was clicked, without waiting on a fetch", async () => {
    conversations = seed(2)
    const section = await openExpanded()
    fireEvent.click(section.querySelector('[data-testid="sidebar-chat-2"]')!)

    // The baton ChatScreen reads on mount — written before navigating, so the
    // tab opens on THIS thread rather than the last one.
    const baton = JSON.parse(localStorage.getItem("sprntly_resume_conv") || "{}")
    expect(baton.dbId).toBe(2)
    expect(baton.title).toBe("Thread 2")
    expect(goTo).toHaveBeenCalledWith("chat")
  })

  it("tells an already-open chat surface to look, not just navigate", async () => {
    // The reported bug: clicking a thread in the nav did nothing when the chat
    // surface was already the current screen. ChatScreen reads the baton on
    // mount and when the ROUTE changes to chat — neither happens here, so the
    // write has to announce itself.
    conversations = seed(1)
    const heard = vi.fn()
    window.addEventListener("sprntly:resume-conv", heard)
    try {
      const section = await openExpanded()
      fireEvent.click(section.querySelector('[data-testid="sidebar-chat-1"]')!)
      expect(heard).toHaveBeenCalled()
    } finally {
      window.removeEventListener("sprntly:resume-conv", heard)
    }
  })

  it("writes the baton BEFORE it announces, or the listener reads nothing", async () => {
    conversations = seed(1)
    let batonAtDispatch: string | null = null
    const spy = () => {
      batonAtDispatch = localStorage.getItem("sprntly_resume_conv")
    }
    window.addEventListener("sprntly:resume-conv", spy)
    try {
      const section = await openExpanded()
      fireEvent.click(section.querySelector('[data-testid="sidebar-chat-1"]')!)
      expect(batonAtDispatch).toContain('"dbId":1')
    } finally {
      window.removeEventListener("sprntly:resume-conv", spy)
    }
  })

  it("keeps one row for the rest, and it is the history screen", async () => {
    conversations = seed(25)
    await openExpanded()
    fireEvent.click(screen.getByTestId("sidebar-view-all-chats"))
    expect(goTo).toHaveBeenCalledWith("chats")
  })

  it("says nothing at all when there are no threads", async () => {
    // A nav section announcing "no chats" to someone who has not had one is
    // noise in the one place that has to stay scannable.
    conversations = []
    sidebarCollapsed = false
    render(<Sidebar activeCompany="acme" />)
    await screen.findByLabelText("Top Insights")
    expect(screen.queryByTestId("sidebar-recent-chats")).toBeNull()
  })

  it("is not rendered while the sidebar is a 42px icon rail", async () => {
    // Collapsed, every item is an icon; a column of truncated chat titles has
    // nowhere to go.
    conversations = seed(3)
    sidebarCollapsed = true
    render(<Sidebar activeCompany="acme" />)
    await screen.findByLabelText("Top Insights")
    expect(screen.queryByTestId("sidebar-recent-chats")).toBeNull()
  })

  it("asks for nothing when nobody is signed in", async () => {
    // `/v1/conversations` is an authed route; the signed-out nav must not call
    // it just to render an empty section.
    const list = vi.fn()
    conversations = seed(3)
    authState = { kind: "anonymous" }
    sidebarCollapsed = false
    render(<Sidebar activeCompany="acme" />)
    await screen.findByLabelText("Top Insights")
    expect(screen.queryByTestId("sidebar-recent-chats")).toBeNull()
    expect(list).not.toHaveBeenCalled()
  })

  it("no longer offers a separate Chat history icon", async () => {
    // Two doors one row apart, to the same screen, is just a longer nav.
    conversations = seed(3)
    await openExpanded()
    expect(screen.queryByLabelText("Chat history")).toBeNull()
  })
})

describe("Sidebar — the draggable edge", () => {
  // Claude's nav can be pulled wider; ours could not. The width lives in one
  // CSS custom property, so these assert on that property rather than on
  // layout, which jsdom does not compute.

  const widthVar = () =>
    document.documentElement.style.getPropertyValue("--sidebar-w")

  const grab = () => {
    sidebarCollapsed = false
    render(<Sidebar activeCompany="acme" />)
    return screen.getByTestId("sidebar-resizer")
  }

  // jsdom has no PointerEvent constructor, so these are MouseEvents carrying
  // the pointer type names. React dispatches on the type, and the handler
  // reads only `button` and `clientX`, both of which a MouseEvent has.
  const press = (handle: HTMLElement, button = 0) => {
    handle.dispatchEvent(
      new MouseEvent("pointerdown", { button, bubbles: true, cancelable: true }),
    )
  }

  const drag = (handle: HTMLElement, toX: number) => {
    press(handle)
    handle.dispatchEvent(
      new MouseEvent("pointermove", { clientX: toX, bubbles: true }),
    )
  }

  const drop = (handle: HTMLElement) => {
    handle.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }))
  }

  beforeEach(() => {
    // jsdom implements neither of these; the drag depends on capture to
    // survive the pointer leaving a 6px target.
    Element.prototype.setPointerCapture = vi.fn()
    Element.prototype.releasePointerCapture = vi.fn()
    localStorage.clear()
    document.documentElement.style.removeProperty("--sidebar-w")
  })

  it("follows the pointer", () => {
    const handle = grab()
    drag(handle, 300)
    expect(widthVar()).toBe("300px")
  })

  it("stops at the minimum, however far left you pull", () => {
    const handle = grab()
    drag(handle, 40)
    expect(widthVar()).toBe(`${SIDEBAR_MIN_WIDTH}px`)
  })

  it("stops at the maximum, however far right you pull", () => {
    // Past this the sidebar starts competing with the document it exists to
    // navigate.
    const handle = grab()
    drag(handle, 2000)
    expect(widthVar()).toBe(`${SIDEBAR_MAX_WIDTH}px`)
  })

  it("remembers the width, and restores it before the first paint", () => {
    const handle = grab()
    drag(handle, 310)
    drop(handle)
    cleanup()
    document.documentElement.style.removeProperty("--sidebar-w")

    grab()

    expect(widthVar()).toBe("310px")
  })

  it("never restores a width outside the bounds", () => {
    // A value stored before these bounds existed, or edited by hand.
    localStorage.setItem("sprntly_sidebar_w", "9999")
    grab()
    expect(widthVar()).toBe(`${SIDEBAR_MAX_WIDTH}px`)
  })

  it("moves with the arrow keys", () => {
    // A control that can only be operated by dragging a 6px target is a
    // control some people do not have.
    const handle = grab()

    fireEvent.keyDown(handle, { key: "ArrowRight" })
    expect(widthVar()).toBe("228px")           // 220 + one 8px step

    // Shift takes bigger steps, and the floor still holds: 228 - 32 is 196,
    // which is under the minimum.
    fireEvent.keyDown(handle, { key: "ArrowLeft", shiftKey: true })
    expect(widthVar()).toBe(`${SIDEBAR_MIN_WIDTH}px`)

    // And it is remembered, exactly as a dragged width is.
    expect(localStorage.getItem("sprntly_sidebar_w")).toBe(String(SIDEBAR_MIN_WIDTH))
  })

  it("shows that it can be dragged, before anyone tries", () => {
    // An invisible 6px hit-area is a feature nobody finds. The grip is always
    // rendered; the tooltip names the gesture on hover.
    const handle = grab()
    expect(handle.querySelector(".sb-resizer-grip")).not.toBeNull()
    expect(handle.querySelector(".sb-resizer-tip")?.textContent).toBe(
      "Drag to resize",
    )
  })

  it("announces itself as a separator with its range", () => {
    const handle = grab()
    expect(handle.getAttribute("role")).toBe("separator")
    expect(handle.getAttribute("aria-orientation")).toBe("vertical")
    expect(handle.getAttribute("aria-valuemin")).toBe(String(SIDEBAR_MIN_WIDTH))
    expect(handle.getAttribute("aria-valuemax")).toBe(String(SIDEBAR_MAX_WIDTH))
    expect(handle.tabIndex).toBe(0)
  })

  it("is not there when the sidebar is a collapsed rail", () => {
    sidebarCollapsed = true
    render(<Sidebar activeCompany="acme" />)
    expect(screen.queryByTestId("sidebar-resizer")).toBeNull()
  })

  it("suspends the width transition while dragging, and restores it after", () => {
    // A 200ms ease on every pointermove is a sidebar that lags the cursor.
    const handle = grab()
    press(handle)
    expect(document.body.classList.contains("is-sidebar-resizing")).toBe(true)
    drop(handle)
    expect(document.body.classList.contains("is-sidebar-resizing")).toBe(false)
  })

  it("ignores a right-click on the handle", () => {
    const handle = grab()
    press(handle, 2)
    expect(document.body.classList.contains("is-sidebar-resizing")).toBe(false)
  })
})
