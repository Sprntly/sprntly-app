// @vitest-environment jsdom
//
// Project-switch isolation (AC11, an adversarial review): `<ProjectMainThread
// key={project.id}>` at `ProjectDetailScreen.tsx` so a project A→B `?id=` change
// on the flat route resets the shell + engine together (a shell-only key would
// leak engine state). Latent/defensive — no current nav path does a direct A→B
// without an unmount — so this proves the keying mechanism (a projectId change
// remounts the subtree) plus the call-site wiring.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// A mount-counting stand-in for the chat surface — a fresh mount increments the
// counter, so a remount (not just a prop re-render) is observable. The private
// surface mounts main's shared renderer (`ConversationView`) via the shared
// `useProjectConversation` controller, so the counter lives on the mocked
// `ConversationView` and the controller is stubbed to keep this a pure
// keying/remount test (no Workspace/Content providers needed).
let mountCount = 0
vi.mock("../useProjectConversation", () => ({
  useProjectConversation: () => ({
    viewerAttachment: null,
    setViewerAttachment: vi.fn(),
    composerRef: { current: null },
  }),
}))
vi.mock("../../ConversationView", () => ({
  ConversationView: () => {
    React.useEffect(() => {
      mountCount += 1
    }, [])
    return React.createElement("div", { "data-testid": "priv" }, "surface")
  },
}))
vi.mock("../../../../shared/AttachmentViewer", () => ({
  AttachmentViewer: () => null,
}))

import { ProjectMainThread } from "../ProjectMainThread"

afterEach(() => {
  cleanup()
  mountCount = 0
})

describe("Project-switch isolation (AC11)", () => {
  it("test_project_switch_remounts_thread_no_state_carry", () => {
    // The call-site pattern: the thread is keyed by projectId.
    const { rerender } = render(
      React.createElement("div", null, React.createElement(ProjectMainThread, { key: 1, projectId: 1, openPrdId: null })),
    )
    expect(screen.getByTestId("priv")).toBeTruthy()
    expect(mountCount).toBe(1)

    // A project A→B change with a DIFFERENT key remounts the whole subtree — no
    // shell/engine state carried over.
    rerender(
      React.createElement("div", null, React.createElement(ProjectMainThread, { key: 2, projectId: 2, openPrdId: null })),
    )
    expect(mountCount).toBe(2)
  })

  it("ProjectDetailScreen keys ProjectMainThread on project.id (call-site wiring)", () => {
    const src = readFileSync(join(__dirname, "../ProjectDetailScreen.tsx"), "utf8")
    // The key sits on the <ProjectMainThread> element, keyed by the project id.
    expect(/<ProjectMainThread[\s\S]*?key=\{project\.id\}/.test(src)).toBe(true)
  })
})
