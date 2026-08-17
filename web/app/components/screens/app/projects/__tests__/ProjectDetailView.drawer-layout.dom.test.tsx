// @vitest-environment jsdom
//
// ProjectDetailView — the in-place artifact drawer LAYOUT. The redesign makes
// the open artifact a right-hand layout COLUMN that replaces the rail while the
// group-chat column to its left keeps rendering (and stays interactive) — the
// opposite of a modal that would cover the chat. These tests isolate the shell's
// placement wiring (real ProjectDetailView; the drawer + thread are stubbed) so
// the assertion is purely: drawer beside chat, rail hidden, chat still mounted.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: React.PropsWithChildren<{ href: string } & Record<string, unknown>>) =>
    React.createElement("a", { href, ...rest }, children),
}))
// The thread host stub — proves the chat column is still MOUNTED beside the
// drawer (not unmounted/covered as a modal would). Interactivity: a real button
// the test can click while the drawer is open.
vi.mock("../ProjectMainThread", () => ({
  ProjectMainThread: () =>
    React.createElement(
      "button",
      { type: "button", "data-testid": "thread-interactive", onClick: () => {} },
      "compose",
    ),
}))
// Drawer stub — the real drawer's own semantics are covered in
// ProjectArtifactDrawer.dom.test.tsx; here we only need to see WHERE the shell
// mounts it and that its close handler is wired.
vi.mock("../ProjectArtifactDrawer", () => ({
  ProjectArtifactDrawer: ({ artifact, onClose }: { artifact: { title?: string } | null; onClose: () => void }) =>
    artifact
      ? React.createElement(
          "aside",
          { role: "region", "data-testid": "drawer-stub" },
          React.createElement("button", { type: "button", "data-testid": "drawer-stub-close", onClick: onClose }, "close"),
        )
      : null,
}))

import { ProjectDetailView, type ProjectDetailViewProps } from "../ProjectDetailScreen"
import type { ArtifactItem, ProjectDetail, ProjectMemorySummary } from "../../../../../lib/api"

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
    { kind: "agent", user_id: null, name: "Sprntly", role_label: "Agent coworker", status: "working" },
    { kind: "human", user_id: "u1", name: "David M.", email: "d@x.com", avatar_url: null, job_role: "PM", added_at: hoursAgo(48) },
  ],
}
const ARTIFACTS: ArtifactItem[] = [
  { type: "prd", id: 1, title: "Instant-quote flow — v3", status: "ready", created_at: hoursAgo(2), source: { brief_id: 1, week_label: null, insight_index: null }, open: { brief_id: 1, insight_index: null, prd_id: 1 } } as ArtifactItem,
]
const MEMORY: ProjectMemorySummary = { summary_md: "A redesign.", entry_count: 2, stale: false }

const noop = () => {}

function viewProps(overrides: Partial<ProjectDetailViewProps> = {}): ProjectDetailViewProps {
  return {
    project: PROJECT,
    artifacts: ARTIFACTS,
    memory: MEMORY,
    activeChat: "group",
    onSelectChat: noop,
    individualUnread: false,
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

afterEach(cleanup)

describe("ProjectDetailView — in-place drawer layout", () => {
  it("with no artifact open, the chat is full-bleed and no rail or drawer is mounted", () => {
    render(React.createElement(ProjectDetailView, viewProps({ openArtifact: null })))
    expect(screen.queryByTestId("project-rail")).toBeNull()
    expect(screen.getByTestId("thread-interactive")).toBeTruthy()
    expect(screen.queryByTestId("drawer-stub")).toBeNull()
  })

  it("with an artifact open, the drawer renders as a side column, the rail is replaced, and the chat stays mounted + interactive", () => {
    render(React.createElement(ProjectDetailView, viewProps({ openArtifact: ARTIFACTS[0] })))
    // Drawer present as a role=region column…
    const drawer = screen.getByTestId("drawer-stub")
    expect(drawer.getAttribute("role")).toBe("region")
    // …the rail it replaced is gone…
    expect(screen.queryByTestId("project-rail")).toBeNull()
    // …but the chat column is STILL mounted beside it (not covered/unmounted)…
    const chat = screen.getByTestId("thread-interactive")
    expect(chat).toBeTruthy()
    fireEvent.click(chat) // interactive: no throw
    // …and the body grid carries the side-by-side layout modifier.
    const body = drawer.parentElement as HTMLElement
    expect(body.className).toMatch(/bodyDrawerOpen/)
  })

  it("the drawer's close handler is wired to onCloseArtifactDrawer", () => {
    const onCloseArtifactDrawer = vi.fn()
    render(React.createElement(ProjectDetailView, viewProps({ openArtifact: ARTIFACTS[0], onCloseArtifactDrawer })))
    fireEvent.click(screen.getByTestId("drawer-stub-close"))
    expect(onCloseArtifactDrawer).toHaveBeenCalledTimes(1)
  })
})
