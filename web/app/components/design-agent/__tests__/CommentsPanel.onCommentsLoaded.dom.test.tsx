// @vitest-environment jsdom
//
// dedup seam — the CommentsPanel container publishes the canonical server
// comment ids via `onCommentsLoaded` after each successful list load. The host
// (DesignAgentLauncher / PublicTokenViewer) lifts these to suppress local
// saved-pin cards already represented in the server list.
//
// jsdom + @testing-library/react with the api module mocked so the list load is
// deterministic.
import * as React from "react"
import { render, cleanup, waitFor } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

vi.mock("../../../lib/api", () => ({
  designAgentApi: {
    listComments: vi.fn(),
    listCommentsByToken: vi.fn(),
  },
}))

import { CommentsPanel } from "../CommentsPanel"
import { designAgentApi } from "../../../lib/api"

const listComments = designAgentApi.listComments as unknown as ReturnType<typeof vi.fn>

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function rec(id: number) {
  return {
    id,
    anchor_id: `pin-${id}`,
    body: "x",
    author: "demo",
    status: "open" as const,
    created_at: "2026-06-06T08:00:00Z",
    resolved_at: null,
  }
}

describe("CommentsPanel — onCommentsLoaded publishes server ids", () => {
  it("fires with the loaded comment ids after a successful list load", async () => {
    listComments.mockResolvedValue([rec(7), rec(8)])
    const onCommentsLoaded = vi.fn()
    render(
      React.createElement(CommentsPanel, {
        token: "tok",
        prototypeId: 1,
        onCommentsLoaded,
      }),
    )
    await waitFor(() => expect(onCommentsLoaded).toHaveBeenCalled())
    expect(onCommentsLoaded).toHaveBeenCalledWith([7, 8])
  })

  it("is a safe no-op surface when omitted (load still succeeds, no throw)", async () => {
    listComments.mockResolvedValue([rec(1)])
    expect(() =>
      render(
        React.createElement(CommentsPanel, { token: "tok", prototypeId: 1 }),
      ),
    ).not.toThrow()
    await waitFor(() => expect(listComments).toHaveBeenCalled())
  })
})
