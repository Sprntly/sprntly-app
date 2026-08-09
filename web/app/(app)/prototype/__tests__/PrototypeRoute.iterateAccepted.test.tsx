// @vitest-environment jsdom
//
// Narrow passthrough test: `runCanvasIterate` (wired to PostGenerationResult's
// `onPinIterate`) and `runCommentIterate` (wired to CommentsPanel's
// `onIterateComment`) must resolve to EXACTLY what the shared
// `iterateRun.runIterate` resolves to — never swallowed to `undefined`
// (the old `void iterateRun.runIterate(...)` shape), never inverted. The
// heavy leaf components (PostGenerationResult, CommentsPanel, IterateComposer)
// are stubbed to expose the wired callbacks directly, mirroring
// PrototypeRoute.notify.test.tsx's own stubbing convention in this directory —
// this test owns the passthrough wiring, not the leaves' own rendering (each
// has its own test file for that).

import * as React from "react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: vi.fn(),
    push: vi.fn(),
    prefetch: vi.fn(),
    back: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams("prd=42"),
  usePathname: () => "/prototype",
}))

vi.mock("../../../context/NavigationContext", () => ({
  useNavigation: () => ({ goTo: vi.fn(), showToast: vi.fn() }),
}))
vi.mock("../../../context/ContentContext", () => ({
  useContent: () => ({ content: { prd: null, userName: null } }),
}))
vi.mock("../../../context/WorkspaceContext", () => ({
  useWorkspace: () => ({ workspace: null }),
}))
vi.mock("../../../components/screens/app/AppLayout", () => ({
  AppLayout: ({ children }: { children: React.ReactNode }) =>
    React.createElement("div", { "data-testid": "app-layout" }, children),
}))

// The shared runner is mocked directly: this test owns the PASSTHROUGH wiring
// (does the route forward the runner's own resolved value untouched?), not
// the runner's internal behaviour (useIterateRun.test.tsx's job).
const mockRunIterate = vi.fn<(instruction: string, appliedCommentId?: number | null) => Promise<boolean>>()
vi.mock("../../../components/design-agent/useIterateRun", () => ({
  useIterateRun: () => ({
    running: false,
    activity: [],
    pendingQuestion: null,
    error: null,
    runIterate: mockRunIterate,
    answerQuestion: vi.fn(),
    dismissQuestion: vi.fn(),
    appendActivity: vi.fn(),
  }),
}))

// Stub PostGenerationResult: exposes a button that invokes the wired
// `onPinIterate` (== runCanvasIterate) and renders through the `comments` slot
// (a CommentsPanel element) unmodified, so the comment-Apply passthrough can
// be exercised through the SAME mounted tree.
vi.mock("../../../components/design-agent/PostGenerationResult", () => ({
  PostGenerationResult: (props: Record<string, unknown>) =>
    React.createElement(
      "div",
      { "data-testid": "stub-post-gen-result" },
      React.createElement(
        "button",
        {
          type: "button",
          "data-testid": "trigger-pin-iterate",
          onClick: () => {
            const onPinIterate = props.onPinIterate as (
              instruction: string,
              appliedCommentId?: number | null,
            ) => Promise<boolean>
            void onPinIterate("pin instruction", null).then((r) => {
              ;(window as unknown as Record<string, unknown>).__pinIterateResult = r
            })
          },
        },
        "pin apply",
      ),
      props.comments as React.ReactNode,
    ),
}))

// Stub CommentsPanel: exposes a button that invokes the wired
// `onIterateComment` (== runCommentIterate).
vi.mock("../../../components/design-agent/CommentsPanel", () => ({
  CommentsPanel: (props: Record<string, unknown>) =>
    React.createElement(
      "button",
      {
        type: "button",
        "data-testid": "trigger-comment-iterate",
        onClick: () => {
          const onIterateComment = props.onIterateComment as (comment: {
            id: number
            anchor_id: string
            body: string
            author: string
            status: "open"
            created_at: string
            resolved_at: string | null
          }) => Promise<boolean>
          void onIterateComment({
            id: 5,
            anchor_id: "a",
            body: "make it bigger",
            author: "external",
            status: "open",
            created_at: "2026-07-01T00:00:00Z",
            resolved_at: null,
          }).then((r) => {
            ;(window as unknown as Record<string, unknown>).__commentIterateResult = r
          })
        },
      },
      "comment apply",
    ),
}))

vi.mock("../../../components/design-agent/IterateComposer", () => ({
  IterateComposer: () => null,
}))

const getActiveByPrd = vi.fn(async (_id: number): Promise<unknown> => ({
  id: 42,
  status: "ready",
  bundle_url: "https://bundle.test/v1",
  error: null,
  share_token: "tok-1",
}))
vi.mock("../../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../../lib/api")>("../../../lib/api")
  return {
    ...actual,
    designAgentApi: {
      ...actual.designAgentApi,
      getActiveByPrd: (id: number) => getActiveByPrd(id),
    },
  }
})

import { PrototypeRoute } from "../PrototypeRoute"

beforeEach(() => {
  mockRunIterate.mockReset()
  getActiveByPrd.mockClear()
  delete (window as unknown as Record<string, unknown>).__pinIterateResult
  delete (window as unknown as Record<string, unknown>).__commentIterateResult
})

afterEach(() => {
  cleanup()
})

async function mountReady() {
  render(React.createElement(PrototypeRoute))
  await screen.findByTestId("stub-post-gen-result")
}

describe("PrototypeRoute — runCanvasIterate / runCommentIterate passthrough", () => {
  it("test_prototype_route_iterate_passthrough_returns_true: both wrappers resolve to exactly true, not undefined", async () => {
    mockRunIterate.mockResolvedValue(true)
    await mountReady()

    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger-pin-iterate"))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect((window as unknown as Record<string, unknown>).__pinIterateResult).toBe(true)

    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger-comment-iterate"))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect((window as unknown as Record<string, unknown>).__commentIterateResult).toBe(true)
  })

  it("test_prototype_route_iterate_passthrough_returns_false: both wrappers resolve to exactly false, not swallowed/inverted", async () => {
    mockRunIterate.mockResolvedValue(false)
    await mountReady()

    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger-pin-iterate"))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect((window as unknown as Record<string, unknown>).__pinIterateResult).toBe(false)

    await act(async () => {
      fireEvent.click(screen.getByTestId("trigger-comment-iterate"))
      await Promise.resolve()
      await Promise.resolve()
    })
    expect((window as unknown as Record<string, unknown>).__commentIterateResult).toBe(false)
  })
})
