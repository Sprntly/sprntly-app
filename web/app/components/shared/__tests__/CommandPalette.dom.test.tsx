// @vitest-environment jsdom
//
// Mount + behavior tests for the global search palette (CommandPalette.tsx):
// open renders the focused combobox with the pages index; typing surfaces
// settings panes (breadcrumb + URL shown) and dynamic entities (skills, chats)
// from the mocked list endpoints; keyboard nav (arrows/Enter/Escape) and the
// action dispatch (router.push / goTo / resume-chat handoff / workspace
// switch) all behave; selections land in per-workspace recents.
//
// Matchers: native DOM only (no @testing-library/jest-dom).
import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const goToMock = vi.fn()
const goToNewChatMock = vi.fn()
const openPrdTabMock = vi.fn()
const setActiveWorkspaceMock = vi.fn()
const routerMock = { push: vi.fn() }
const onCloseMock = vi.fn()

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({
    goTo: goToMock,
    goToNewChat: goToNewChatMock,
    openPrdTab: openPrdTabMock,
  }),
}))
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({
    workspaces: [
      { id: "ws-1", name: "Acme", slug: "acme", is_default: true, product_id: null, dataset: "acme", role: "admin" },
      { id: "ws-2", name: "Beta Works", slug: "beta", is_default: false, product_id: null, dataset: "beta", role: "member" },
    ],
    activeWorkspace: { id: "ws-1", name: "Acme", slug: "acme", is_default: true, product_id: null, dataset: "acme", role: "admin" },
    setActiveWorkspace: setActiveWorkspaceMock,
  }),
}))
vi.mock("../../../context/CompanyContext", () => ({
  useCompany: () => ({ activeCompany: "acme" }),
}))
vi.mock("next/navigation", () => ({ useRouter: () => routerMock }))
vi.mock("../../../lib/api", () => ({
  askApi: {
    skills: () =>
      Promise.resolve({
        skills: [
          {
            id: "journey-map",
            label: "Journey map",
            trigger: "/journey",
            description: "Map the end-to-end user journey",
            category: "Discovery & Research",
          },
        ],
      }),
  },
  conversationsApi: {
    list: () =>
      Promise.resolve({
        conversations: [
          {
            id: 7,
            company_id: "c1",
            user_id: "u1",
            title: "Pricing experiments",
            preview: "we compared three pricing tiers",
            agent_type: "qa",
            query: "",
            reply: "",
            pinned: false,
            prd_id: 88,
            created_at: "2026-07-01",
            updated_at: "2026-07-01",
          },
        ],
      }),
  },
  artifactsApi: { list: () => Promise.resolve([]) },
  companyDocsApi: { list: () => Promise.resolve([]) },
  templatesApi: { list: () => Promise.resolve([]) },
  teamApi: { list: () => Promise.resolve({ members: [] }) },
  connectorsApi: { list: () => Promise.resolve({ connections: [] }) },
}))

import { CommandPalette } from "../CommandPalette"
import { invalidateSearchCache } from "../../../lib/search/providers"

function input(): HTMLInputElement {
  return screen.getByRole("combobox") as HTMLInputElement
}

/** Find a result row by its full title. Highlighting wraps matched substrings
 *  in <mark>, so plain findByText can't see the full string — match on the
 *  title span's textContent instead. */
function findRowByTitle(title: string) {
  return screen.findByText(
    (_content, el) =>
      el !== null &&
      el.classList.contains("cmdp-item-title") &&
      el.textContent === title,
  )
}

/** Flush the provider fan-out (allSettled over already-resolved promises). */
async function flushDynamic() {
  await act(async () => {
    await Promise.resolve()
  })
}

async function openPalette() {
  const utils = render(<CommandPalette open onClose={onCloseMock} />)
  await flushDynamic()
  return utils
}

beforeEach(() => {
  localStorage.clear()
  invalidateSearchCache()
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("CommandPalette", () => {
  it("renders nothing when closed", () => {
    const { container } = render(<CommandPalette open={false} onClose={onCloseMock} />)
    expect(container.firstChild).toBeNull()
  })

  it("opens with the pages index and a listbox", async () => {
    await openPalette()
    expect(input()).not.toBeNull()
    expect(screen.getByRole("listbox")).not.toBeNull()
    // Pages group shows the app's surfaces with their URLs.
    expect(screen.getByText("Weekly brief")).not.toBeNull()
    expect(screen.getByText("/brief")).not.toBeNull()
    expect(screen.getByText("New chat")).not.toBeNull()
  })

  it("typing 'settings' lists the settings panes with breadcrumb and URL", async () => {
    await openPalette()
    fireEvent.change(input(), { target: { value: "settings" } })
    expect(screen.getByText("/settings?section=connectors")).not.toBeNull()
    const trails = screen.getAllByText("Settings › Data & Integrations")
    expect(trails.length).toBeGreaterThan(0)
  })

  it("clicking a settings result pushes its deep link and closes", async () => {
    await openPalette()
    fireEvent.change(input(), { target: { value: "connectors" } })
    fireEvent.click(screen.getByText("Connectors").closest("button")!)
    expect(routerMock.push).toHaveBeenCalledWith("/settings?section=connectors")
    expect(onCloseMock).toHaveBeenCalled()
  })

  it("surfaces dynamic skills by label and deep-links /skills?q=", async () => {
    await openPalette()
    fireEvent.change(input(), { target: { value: "journey" } })
    const row = (await findRowByTitle("Journey map")).closest("button")!
    fireEvent.click(row)
    expect(routerMock.push).toHaveBeenCalledWith("/skills?q=Journey%20map")
  })

  it("resumes a chat via the sprntly_resume_conv handoff", async () => {
    await openPalette()
    fireEvent.change(input(), { target: { value: "pricing" } })
    const row = (await findRowByTitle("Pricing experiments")).closest("button")!
    fireEvent.click(row)
    const handoff = JSON.parse(localStorage.getItem("sprntly_resume_conv")!)
    // The PRD binding (prd_id) must ride along so the resumed tab reopens its
    // content panel and shows the "View PRD" button.
    expect(handoff).toEqual({ dbId: 7, title: "Pricing experiments", fallbackTurns: [], prdId: 88 })
    expect(goToMock).toHaveBeenCalledWith("chat")
  })

  it("switches workspace from a workspace result", async () => {
    await openPalette()
    fireEvent.change(input(), { target: { value: "beta" } })
    const row = (await findRowByTitle("Beta Works")).closest("button")!
    fireEvent.click(row)
    expect(setActiveWorkspaceMock).toHaveBeenCalledWith("ws-2")
    expect(onCloseMock).toHaveBeenCalled()
  })

  it("navigates with ArrowDown + Enter", async () => {
    await openPalette()
    // Empty query: flat list starts [New chat, Weekly brief, …].
    fireEvent.keyDown(input(), { key: "ArrowDown" })
    fireEvent.keyDown(input(), { key: "Enter" })
    expect(goToMock).toHaveBeenCalledWith("brief")
    expect(onCloseMock).toHaveBeenCalled()
  })

  it("closes on Escape", async () => {
    await openPalette()
    fireEvent.keyDown(input(), { key: "Escape" })
    expect(onCloseMock).toHaveBeenCalled()
  })

  it("records selections as per-workspace recents", async () => {
    await openPalette()
    fireEvent.change(input(), { target: { value: "history" } })
    fireEvent.keyDown(input(), { key: "Enter" })
    const stored = JSON.parse(localStorage.getItem("sprntly_palette_recents:ws-1")!)
    expect(stored[0].id).toBe("page:/history")
  })
})
