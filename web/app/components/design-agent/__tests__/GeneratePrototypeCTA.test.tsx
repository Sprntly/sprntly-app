// @vitest-environment jsdom
//
// Unit tests for <GeneratePrototypeCTA>, the render-prop wrapper around
// useGeneratePrototype() + <GenerateModal> + <GenerationLoadingScreen>. The
// hook itself is mocked here (its own branching is covered exhaustively by
// useGeneratePrototype.test.tsx) so these tests stay scoped to the
// component's own contract: passing `disabled` through to the render prop,
// and mounting the two child surfaces exactly once regardless of re-renders.

import * as React from "react"
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import type { PrototypeRecord } from "../../../lib/api"
import type {
  GeneratePrototypeCtaState,
  UseGeneratePrototypeResult,
} from "../useGeneratePrototype"
import { GeneratePrototypeCTA } from "../GeneratePrototypeCTA"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

let mockResult: UseGeneratePrototypeResult
const useGeneratePrototypeSpy = vi.fn((..._args: unknown[]) => mockResult)
vi.mock("../useGeneratePrototype", () => ({
  useGeneratePrototype: (...args: unknown[]) => useGeneratePrototypeSpy(...args),
}))

vi.mock("../GenerateModal", () => ({
  GenerateModal: () => React.createElement("div", { "data-testid": "generate-modal-mount" }),
}))
vi.mock("../GenerationLoadingScreen", () => ({
  GenerationLoadingScreen: () =>
    React.createElement("div", { "data-testid": "loading-screen-mount" }),
}))

function makeResult(
  overrides: Partial<UseGeneratePrototypeResult> & { cta: GeneratePrototypeCtaState },
): UseGeneratePrototypeResult {
  return {
    existing: null,
    isLoadingExisting: false,
    ctaLabel: "Generate Prototype",
    handleCtaClick: vi.fn(async () => {}),
    openGenerateModal: vi.fn(),
    deleteExisting: vi.fn(async () => {}),
    refetchExisting: vi.fn(),
    generateModalProps: {
      open: false,
      onClose: vi.fn(),
      prdId: 1,
      figmaFileKey: null,
      onGenStart: vi.fn(),
      onKickoff: vi.fn(),
      onGenDone: vi.fn(),
      savedPreference: null,
      onSavePreference: vi.fn(async () => {}),
    },
    loadingScreenProps: {
      open: false,
      figmaFileKey: null,
      githubRepo: null,
      prototypeId: null,
      onCancel: vi.fn(),
      onNotifyWhenReady: vi.fn(),
    },
    ...overrides,
  }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("GeneratePrototypeCTA — disabled state", () => {
  it("passes disabled true while the existence check is in flight, false once resolved", () => {
    mockResult = makeResult({ cta: "loading", isLoadingExisting: true, ctaLabel: "Loading…" })
    let capturedDisabled: boolean | undefined
    const { rerender } = render(
      <GeneratePrototypeCTA
        prdId={1}
        render={(state) => {
          capturedDisabled = state.disabled
          return <div data-testid="trigger">{state.label}</div>
        }}
      />,
    )
    expect(capturedDisabled).toBe(true)

    mockResult = makeResult({ cta: "generate", isLoadingExisting: false })
    rerender(
      <GeneratePrototypeCTA
        prdId={1}
        render={(state) => {
          capturedDisabled = state.disabled
          return <div data-testid="trigger">{state.label}</div>
        }}
      />,
    )
    expect(capturedDisabled).toBe(false)
  })
})

describe("GeneratePrototypeCTA — child mounts", () => {
  it("mounts exactly one GenerateModal and one GenerationLoadingScreen across repeated re-renders", () => {
    mockResult = makeResult({ cta: "generate" })
    const renderTrigger = () => <div data-testid="trigger">trigger</div>

    const { rerender } = render(
      <GeneratePrototypeCTA prdId={1} render={renderTrigger} />,
    )
    expect(screen.getAllByTestId("generate-modal-mount")).toHaveLength(1)
    expect(screen.getAllByTestId("loading-screen-mount")).toHaveLength(1)

    mockResult = makeResult({ cta: "loading", isLoadingExisting: true })
    rerender(<GeneratePrototypeCTA prdId={1} render={renderTrigger} />)
    expect(screen.getAllByTestId("generate-modal-mount")).toHaveLength(1)
    expect(screen.getAllByTestId("loading-screen-mount")).toHaveLength(1)

    mockResult = makeResult({ cta: "view", existing: { id: 1 } as PrototypeRecord })
    rerender(<GeneratePrototypeCTA prdId={1} render={renderTrigger} />)
    expect(screen.getAllByTestId("generate-modal-mount")).toHaveLength(1)
    expect(screen.getAllByTestId("loading-screen-mount")).toHaveLength(1)
  })
})
