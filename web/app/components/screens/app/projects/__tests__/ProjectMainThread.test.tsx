// @vitest-environment jsdom
//
// ProjectMainThread — the group⇆individual swap host. Renders exactly one of
// the two chats per `activeChat`, in place (AD-P14 — no route change).
// Neither side imports the app's existing multi-tab chat container (AD-P13);
// both `ProjectGroupChat` and `ProjectIndividualChat` are mocked here — this
// file verifies the SWAP/mount wiring, not either component's own internal
// behaviour (that's `ProjectGroupChat.test.tsx` and
// `ProjectIndividualChat.test.tsx`'s job).
import * as React from "react"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const individualChatMock = vi.fn((props: unknown) =>
  React.createElement("div", { "data-testid": "real-individual-chat", "data-props": JSON.stringify(props) }),
)
vi.mock("../ProjectIndividualChat", () => ({
  ProjectIndividualChat: (props: unknown) => individualChatMock(props),
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
  individualChatMock.mockClear()
  groupChatMock.mockClear()
})

describe("ProjectMainThread — swap", () => {
  it("renders exactly one of group/individual per activeChat", () => {
    const { rerender } = render(
      React.createElement(ProjectMainThread, { projectId: 101, activeChat: "group" }),
    )
    expect(screen.getByTestId("main-thread-group")).toBeTruthy()
    expect(screen.queryByTestId("main-thread-individual")).toBeNull()
    expect(groupChatMock).toHaveBeenCalledTimes(1)
    expect(individualChatMock).not.toHaveBeenCalled()

    rerender(React.createElement(ProjectMainThread, { projectId: 101, activeChat: "individual" }))
    expect(screen.queryByTestId("main-thread-group")).toBeNull()
    expect(screen.getByTestId("main-thread-individual")).toBeTruthy()
    expect(individualChatMock).toHaveBeenCalledTimes(1)
  })

  it("the swap is state-only — no route/URL API is touched", () => {
    const src = readFileSync(join(__dirname, "../ProjectMainThread.tsx"), "utf8")
    expect(src).not.toContain("useRouter")
    expect(src).not.toContain("useSearchParams")
    expect(src).not.toContain("window.location")
  })
})

describe("ProjectMainThread — neither side forks or imports the chat monolith (AD-P13)", () => {
  it("mounts the thin ProjectIndividualChat on the individual side, with the project id threaded through", () => {
    render(React.createElement(ProjectMainThread, { projectId: 202, activeChat: "individual" }))
    const host = screen.getByTestId("main-thread-individual")
    expect(host.getAttribute("data-project-id")).toBe("202")
    expect(screen.getByTestId("real-individual-chat")).toBeTruthy()
    expect(individualChatMock).toHaveBeenCalledWith(expect.objectContaining({ projectId: 202 }))
  })

  it("group chat receives the project id + the artifact-open callback", () => {
    const onOpenArtifact = vi.fn()
    render(
      React.createElement(ProjectMainThread, { projectId: 303, activeChat: "group", onOpenArtifact }),
    )
    expect(groupChatMock).toHaveBeenCalledWith(
      expect.objectContaining({ projectId: 303, onOpenArtifact }),
    )
  })

  it("neither ProjectMainThread nor ProjectIndividualChat imports or mounts the chat monolith container (AD-P13a)", () => {
    // Migrated to the post-amendment invariant: AD-P13a explicitly allows
    // BOTH sides to consume the shared `dispatchChatIntent` PRIMITIVE (proven
    // here on the individual side, which actually uses it) while the
    // container import/mount prohibition still holds.
    const mainThreadSrc = readFileSync(join(__dirname, "../ProjectMainThread.tsx"), "utf8")
    const individualSrc = readFileSync(join(__dirname, "../ProjectIndividualChat.tsx"), "utf8")
    expect(mainThreadSrc).not.toContain("ChatScreen")
    expect(individualSrc).not.toContain("from \"../ChatScreen\"")
    expect(individualSrc).not.toMatch(/import\s*\{[^}]*\bChatScreen\b[^}]*\}\s*from/)
    expect(individualSrc).toContain('from "../../../../lib/chat/dispatchChatIntent"')
  })

  it("passes the insightNote prop through to ProjectIndividualChat unchanged", () => {
    const insightNote = { by: "Shristi", text: "the pricing model changed" }
    render(
      React.createElement(ProjectMainThread, { projectId: 202, activeChat: "individual", insightNote }),
    )
    expect(individualChatMock).toHaveBeenCalledWith(expect.objectContaining({ insightNote }))
  })
})
