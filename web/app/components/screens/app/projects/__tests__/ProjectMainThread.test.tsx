// @vitest-environment jsdom
//
// ProjectMainThread — the private chat mount host.
//
// The prior per-surface components (`ProjectGroupChat` / `ProjectPrivateChat`)
// were DELETED and rebuilt as a SINGLE configurable mount of main's actual
// chat: the surface mounts the shared `useProjectConversation` controller and
// renders the shared `ConversationView`. This file verifies the mount wiring
// against that single-shared-engine shape — that the host never imports the
// ChatScreen monolith (AD-P13), and touches no route/URL API (AD-P14). It
// does NOT exercise the controller's own behaviour — that is
// `useProjectConversation.actions.dom.test.tsx`'s job.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// The shared engine seam. The one project chat surface mounts THIS hook —
// capture each call's projectId so the test can prove the mount happened.
const useProjectConversationMock = vi.fn((projectId: number | string) => {
  useProjectConversationCalls.push({ projectId })
  return {
    viewerAttachment: null,
    setViewerAttachment: vi.fn(),
    composerRef: { current: null },
  }
})
const useProjectConversationCalls: { projectId: number | string }[] = []
vi.mock("../useProjectConversation", () => ({
  useProjectConversation: (projectId: number | string) => useProjectConversationMock(projectId),
}))

// The shared renderer main uses per tab — mocked so this file asserts the mount,
// not the renderer's internals.
const conversationViewMock = vi.fn((props: unknown) =>
  React.createElement("div", { "data-testid": "conversation-view", "data-props": JSON.stringify(props ?? {}) }),
)
vi.mock("../../ConversationView", () => ({
  ConversationView: (props: unknown) => conversationViewMock(props),
}))

vi.mock("../../../../shared/AttachmentViewer", () => ({
  AttachmentViewer: () => React.createElement("div", { "data-testid": "attachment-viewer" }),
}))

import { ProjectMainThread } from "../ProjectMainThread"

afterEach(() => {
  cleanup()
  useProjectConversationMock.mockClear()
  conversationViewMock.mockClear()
  useProjectConversationCalls.length = 0
})

describe("ProjectMainThread — single-shared-engine mount", () => {
  it("mounts the shared useProjectConversation controller + ConversationView on the private surface", () => {
    render(React.createElement(ProjectMainThread, { projectId: 202, openPrdId: null }))
    expect(screen.getByTestId("main-thread-individual")).toBeTruthy()
    // The shared engine is mounted — and the shared renderer main uses per tab
    // is what draws it.
    expect(useProjectConversationCalls).toEqual([{ projectId: 202 }])
    expect(conversationViewMock).toHaveBeenCalledTimes(1)
  })
})

describe("ProjectMainThread — invariants held through the rewrite", () => {
  it("mounting is state-only — no route/URL API is touched (AD-P14)", () => {
    const src = readFileSync(join(__dirname, "../ProjectMainThread.tsx"), "utf8")
    expect(src).not.toContain("useRouter")
    expect(src).not.toContain("useSearchParams")
    expect(src).not.toContain("window.location")
  })

  it("the host never IMPORTS the ChatScreen monolith (AD-P13)", () => {
    // Loosened from "no mention of ChatScreen" to "no IMPORT of ChatScreen":
    // the rewritten host legitimately references ChatScreen in explanatory
    // comments ("mirroring how ChatScreen mounts the same component"). The
    // load-bearing invariant is that it never mounts/imports the monolith.
    const src = readFileSync(join(__dirname, "../ProjectMainThread.tsx"), "utf8")
    expect(src).not.toMatch(/from\s+["'][^"']*ChatScreen["']/)
    expect(src).not.toMatch(/import\s*\{[^}]*\bChatScreen\b[^}]*\}\s*from/)
  })
})
