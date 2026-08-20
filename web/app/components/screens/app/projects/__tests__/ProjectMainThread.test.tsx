// @vitest-environment jsdom
//
// ProjectMainThread — the group⇆individual swap host, POST-REWRITE.
//
// The prior per-surface components (`ProjectGroupChat` / `ProjectPrivateChat`)
// were DELETED and rebuilt as a SINGLE configurable mount of main's actual
// chat: every surface mounts the shared `useProjectConversation` controller and
// renders the shared `ConversationView`. This file verifies the SWAP/mount
// wiring against that single-shared-engine shape — that both the group and the
// individual arm mount the SAME controller (differing only by `surface`), that
// the swap is react-state-only (AD-P14, no route change), that toggling
// remounts each side fresh (surface-keyed, no cross-chat state leak), and that
// the host never imports the ChatScreen monolith (AD-P13). It does NOT exercise
// the controller's own behaviour — that is `useProjectConversation.actions.dom.
// test.tsx`'s job.
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// The shared engine seam. Every project chat surface mounts THIS hook — capture
// each call's (projectId, surface) so the test can prove both arms use it.
const useProjectConversationMock = vi.fn(
  (projectId: number | string, surface: "group" | "individual") => {
    useProjectConversationCalls.push({ projectId, surface })
    return {
      viewerAttachment: null,
      setViewerAttachment: vi.fn(),
      mentionPickerNode: null,
      mentionPickerOpen: false,
      composerRef: { current: null },
    }
  },
)
const useProjectConversationCalls: { projectId: number | string; surface: string }[] = []
vi.mock("../useProjectConversation", () => ({
  useProjectConversation: (projectId: number | string, surface: "group" | "individual") =>
    useProjectConversationMock(projectId, surface),
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
vi.mock("../MentionPickerOverlay", () => ({
  MentionPickerOverlay: () => null,
}))
vi.mock("../useMentionNotifications", () => ({
  useMentionNotifications: () => ({ unreadCount: 0, clear: vi.fn() }),
}))

import { ProjectMainThread } from "../ProjectMainThread"

afterEach(() => {
  cleanup()
  useProjectConversationMock.mockClear()
  conversationViewMock.mockClear()
  useProjectConversationCalls.length = 0
})

describe("ProjectMainThread — single-shared-engine mount", () => {
  it("mounts the shared useProjectConversation controller + ConversationView on the GROUP arm", () => {
    render(
      React.createElement(ProjectMainThread, { projectId: 101, activeChat: "group", openPrdId: null }),
    )
    expect(screen.getByTestId("main-thread-group")).toBeTruthy()
    expect(screen.queryByTestId("main-thread-individual")).toBeNull()
    // The shared engine is mounted, configured for the group surface — and the
    // shared renderer main uses per tab is what draws it.
    expect(useProjectConversationCalls).toEqual([{ projectId: 101, surface: "group" }])
    expect(conversationViewMock).toHaveBeenCalledTimes(1)
  })

  it("mounts the SAME controller on the INDIVIDUAL arm, differing only by surface", () => {
    render(
      React.createElement(ProjectMainThread, { projectId: 202, activeChat: "individual", openPrdId: null }),
    )
    expect(screen.getByTestId("main-thread-individual")).toBeTruthy()
    expect(screen.queryByTestId("main-thread-group")).toBeNull()
    expect(useProjectConversationCalls).toEqual([{ projectId: 202, surface: "individual" }])
    expect(conversationViewMock).toHaveBeenCalledTimes(1)
  })

  it("renders exactly one arm per activeChat; toggling remounts each side fresh (surface-keyed, no state leak)", () => {
    const { rerender } = render(
      React.createElement(ProjectMainThread, { projectId: 303, activeChat: "group", openPrdId: null }),
    )
    expect(screen.getByTestId("main-thread-group")).toBeTruthy()

    rerender(React.createElement(ProjectMainThread, { projectId: 303, activeChat: "individual", openPrdId: null }))
    expect(screen.queryByTestId("main-thread-group")).toBeNull()
    expect(screen.getByTestId("main-thread-individual")).toBeTruthy()

    rerender(React.createElement(ProjectMainThread, { projectId: 303, activeChat: "group", openPrdId: null }))
    expect(screen.queryByTestId("main-thread-individual")).toBeNull()
    expect(screen.getByTestId("main-thread-group")).toBeTruthy()

    // Two distinct group mounts + one individual mount: the surface is keyed on
    // project+surface, so each toggle unmounts and re-resolves rather than
    // reusing the subtree (scroll/draft/focus cannot leak across the swap).
    const surfaces = useProjectConversationCalls.map((c) => c.surface)
    expect(surfaces.filter((s) => s === "group").length).toBe(2)
    expect(surfaces.filter((s) => s === "individual").length).toBe(1)
  })
})

describe("ProjectMainThread — invariants held through the rewrite", () => {
  it("the swap is state-only — no route/URL API is touched (AD-P14)", () => {
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
