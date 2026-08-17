// @vitest-environment jsdom
//
// ProjectMainThread — the group⇆individual swap host. Renders exactly one of
// the two chats per `activeChat`, in place (AD-P14 — no route change).
// Neither side imports the app's existing multi-tab chat container (AD-P13);
// both `ProjectGroupChat` and `ProjectPrivateChat` are mocked here — this
// file verifies the SWAP/mount wiring, not either component's own internal
// behaviour (that's `ProjectGroupChat.test.tsx` and
// `ProjectPrivateChat.test.tsx`'s job).
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const privateChatMock = vi.fn((props: unknown) =>
  React.createElement("div", { "data-testid": "real-private-chat", "data-props": JSON.stringify(props) }),
)
vi.mock("../ProjectPrivateChat", () => ({
  ProjectPrivateChat: (props: unknown) => privateChatMock(props),
}))

const groupChatMock = vi.fn((props: unknown) =>
  React.createElement("div", { "data-testid": "real-group-chat", "data-props": JSON.stringify(props) }),
)
vi.mock("../ProjectGroupChat", () => ({
  ProjectGroupChat: (props: unknown) => groupChatMock(props),
}))

import { ProjectMainThread } from "../ProjectMainThread"

afterEach(() => {
  cleanup()
  privateChatMock.mockClear()
  groupChatMock.mockClear()
})

describe("ProjectMainThread — swap", () => {
  it("renders exactly one of group/individual per activeChat", () => {
    const { rerender } = render(
      React.createElement(ProjectMainThread, { projectId: 101, activeChat: "group", openPrdId: null }),
    )
    expect(screen.getByTestId("main-thread-group")).toBeTruthy()
    expect(screen.queryByTestId("main-thread-individual")).toBeNull()
    expect(groupChatMock).toHaveBeenCalledTimes(1)
    expect(privateChatMock).not.toHaveBeenCalled()

    rerender(React.createElement(ProjectMainThread, { projectId: 101, activeChat: "individual", openPrdId: null }))
    expect(screen.queryByTestId("main-thread-group")).toBeNull()
    expect(screen.getByTestId("main-thread-individual")).toBeTruthy()
    expect(privateChatMock).toHaveBeenCalledTimes(1)
  })

  it("the swap is state-only — no route/URL API is touched", () => {
    const src = readFileSync(join(__dirname, "../ProjectMainThread.tsx"), "utf8")
    expect(src).not.toContain("useRouter")
    expect(src).not.toContain("useSearchParams")
    expect(src).not.toContain("window.location")
  })
})

describe("ProjectMainThread — neither side forks or imports the chat monolith (AD-P13)", () => {
  it("mounts the thin ProjectPrivateChat on the individual side, with the project id threaded through", () => {
    render(React.createElement(ProjectMainThread, { projectId: 202, activeChat: "individual", openPrdId: null }))
    const host = screen.getByTestId("main-thread-individual")
    expect(host.getAttribute("data-project-id")).toBe("202")
    expect(screen.getByTestId("real-private-chat")).toBeTruthy()
    expect(privateChatMock).toHaveBeenCalledWith(expect.objectContaining({ projectId: 202 }))
  })

  it("group chat receives the project id + the artifact-open callback", () => {
    const onOpenArtifact = vi.fn()
    render(
      React.createElement(ProjectMainThread, { projectId: 303, activeChat: "group", onOpenArtifact, openPrdId: null }),
    )
    expect(groupChatMock).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: 303, onOpenArtifact }),
    )
  })

  it("neither ProjectMainThread nor ProjectPrivateChat imports or mounts the chat monolith container (AD-P13a)", () => {
    // Migrated to the post-amendment invariant: AD-P13a explicitly allows the
    // private surface to consume the shared `dispatchChatIntent` PRIMITIVE
    // (now in its engine hook, where the classify → dispatch → ask pipeline
    // lives) while the container import/mount prohibition still holds for both
    // the host and the engine.
    const mainThreadSrc = readFileSync(join(__dirname, "../ProjectMainThread.tsx"), "utf8")
    const hostSrc = readFileSync(join(__dirname, "../ProjectPrivateChat.tsx"), "utf8")
    const engineSrc = readFileSync(join(__dirname, "../useProjectPrivateThread.ts"), "utf8")
    expect(mainThreadSrc).not.toContain("ChatScreen")
    expect(hostSrc).not.toContain("ChatScreen")
    expect(engineSrc).not.toContain("from \"../ChatScreen\"")
    expect(engineSrc).not.toMatch(/import\s*\{[^}]*\bChatScreen\b[^}]*\}\s*from/)
    expect(engineSrc).toContain('from "../../../../lib/chat/dispatchChatIntent"')
  })

  it("passes the insightNote prop through to ProjectPrivateChat unchanged", () => {
    const insightNote = { by: "Shristi", text: "the pricing model changed" }
    render(
      React.createElement(ProjectMainThread, { projectId: 202, activeChat: "individual", insightNote, openPrdId: null }),
    )
    expect(privateChatMock).toHaveBeenCalledWith(expect.objectContaining({ insightNote }))
  })
})

describe("ProjectMainThread — private host + surface-keyed toggle", () => {
  it("test_main_thread_mounts_private_chat_host (AC1/AC11): the private arm mounts ProjectPrivateChat and the AD-P13a monolith-import assertion holds for the host", () => {
    render(React.createElement(ProjectMainThread, { projectId: 202, activeChat: "individual", openPrdId: null }))
    // The private arm mounts the thin ProjectPrivateChat host with the id threaded.
    expect(screen.getByTestId("real-private-chat")).toBeTruthy()
    expect(privateChatMock).toHaveBeenCalledWith(expect.objectContaining({ projectId: 202 }))
    // AD-P13a: the retargeted monolith-import assertion holds for the new host.
    const hostSrc = readFileSync(join(__dirname, "../ProjectPrivateChat.tsx"), "utf8")
    expect(hostSrc).not.toContain("ChatScreen")
  })

  it("test_private_group_toggle_surface_keyed_no_state_leak (AC9): toggling group→private→group remounts each side fresh, never reusing the subtree", () => {
    const { rerender } = render(
      React.createElement(ProjectMainThread, { projectId: 101, activeChat: "group", openPrdId: null }),
    )
    expect(screen.getByTestId("main-thread-group")).toBeTruthy()

    rerender(React.createElement(ProjectMainThread, { projectId: 101, activeChat: "individual", openPrdId: null }))
    // Group unmounts; the private host mounts fresh (surface-keyed <ChatShell>).
    expect(screen.queryByTestId("main-thread-group")).toBeNull()
    expect(screen.getByTestId("main-thread-individual")).toBeTruthy()
    expect(privateChatMock).toHaveBeenCalledTimes(1)

    rerender(React.createElement(ProjectMainThread, { projectId: 101, activeChat: "group", openPrdId: null }))
    // The private host unmounts; the group side mounts fresh again — two
    // distinct group mounts prove the subtree is not reused across the toggle,
    // so scroll/draft/focus cannot leak between the two chats.
    expect(screen.queryByTestId("main-thread-individual")).toBeNull()
    expect(screen.getByTestId("main-thread-group")).toBeTruthy()
    expect(groupChatMock).toHaveBeenCalledTimes(2)

    // The private host keys its <ChatShell> by surface, the remount mechanism
    // that guarantees the isolation this test asserts at the swap boundary.
    const hostSrc = readFileSync(join(__dirname, "../ProjectPrivateChat.tsx"), "utf8")
    expect(hostSrc).toContain('key="project_private"')
  })
})
