// @vitest-environment jsdom
//
// DOM companion to PostGenerationResult.test.tsx — the cases that depend on the
// VIEWER rendering, which now requires the async view-grant mint to resolve
// first (the container gates `bundle_url` behind useViewGrant). renderToStaticMarkup
// (the node-env file) is synchronous and can't drive the effect, so these moved
// here where @testing-library + waitFor drive it.
//
// What these prove (plan §1.2 / §11 / §16-1, authed mount path):
//   1. The grant POST (designAgentApi.viewGrant) FIRES, and the authed iframe
//      `src` is NOT set until it resolves (gating order).
//   2. Once the grant resolves the inline viewer + MarkOverlay mount inside the
//      container's center stage (container→view→leaf wiring intact).
//   3. defaultFullscreen + a granted bundle renders the fullscreen overlay.
import * as React from "react"
import { render, waitFor } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

// Mock ONLY designAgentApi.viewGrant observably; the rest of the api surface the
// container touches (createComment via usePinMarking, etc.) is interaction-driven
// and never fires on mount, so stubs suffice.
vi.mock("../../../lib/api", () => {
  return {
    designAgentApi: {
      viewGrant: vi.fn<(viewGrantUrl: string) => Promise<void>>().mockResolvedValue(undefined),
      createComment: vi.fn().mockResolvedValue({}),
    },
    withAuthRetry: <T,>(fn: () => Promise<T>) => fn(),
  }
})

import { designAgentApi } from "../../../lib/api"
import { PostGenerationResult } from "../PostGenerationResult"
import type { PrototypeRecord } from "../../../lib/api"

const BUNDLE = "https://app.test/_da-bundle/v1/design-agent/42/bundle/index.html"
const viewGrant = designAgentApi.viewGrant as unknown as ReturnType<typeof vi.fn>

function proto(over: Partial<PrototypeRecord> = {}): PrototypeRecord {
  return { id: 42, status: "ready", bundle_url: null, error: null, ...over }
}

beforeEach(() => {
  viewGrant.mockReset()
  viewGrant.mockResolvedValue(undefined)
  // jsdom implements neither requestFullscreen nor exitFullscreen; the
  // FullscreenOverlay calls them on mount/close. Test-only no-op stubs (real
  // browsers provide them natively) — the component already .catch()es rejections,
  // but the methods must EXIST to be called.
  if (!Element.prototype.requestFullscreen) {
    Element.prototype.requestFullscreen = function requestFullscreen() {
      return Promise.resolve()
    }
  }
  if (!document.exitFullscreen) {
    ;(document as Document & { exitFullscreen: () => Promise<void> }).exitFullscreen =
      function exitFullscreen() {
        return Promise.resolve()
      }
  }
})
afterEach(() => {
  vi.clearAllMocks()
})

describe("PostGenerationResult — authed view-grant gates the iframe (DOM)", () => {
  it("mints the grant BEFORE the iframe src is set, then mounts the viewer + MarkOverlay", async () => {
    // Hold the mint pending so we can observe the pre-grant state.
    let resolveMint: (() => void) | null = null
    viewGrant.mockImplementation(
      () => new Promise<void>((res) => { resolveMint = () => res() }),
    )

    const { container } = render(
      React.createElement(PostGenerationResult, { prototype: proto({ bundle_url: BUNDLE }) }),
    )

    // Grant POST fired for the prototype, via the app-origin /_da-bundle/
    // view-grant path derived from the bundle URL (Option A)...
    expect(viewGrant).toHaveBeenCalledTimes(1)
    expect(viewGrant).toHaveBeenCalledWith("https://app.test/_da-bundle/v1/design-agent/42/view-grant")
    // ...and BEFORE it resolves there is no iframe (src never set without a grant).
    expect(container.querySelector("iframe.da-prototype-iframe")).toBeNull()

    // Resolve the mint → the viewer + overlay mount.
    resolveMint?.()
    await waitFor(() => {
      expect(container.querySelector("iframe.da-prototype-iframe")).not.toBeNull()
    })
    const iframe = container.querySelector("iframe.da-prototype-iframe") as HTMLIFrameElement
    expect(iframe.getAttribute("src")).toBe(BUNDLE)
    // container→view→leaf wiring: the MarkOverlay mounts inside the center stage.
    expect(container.querySelector('[data-testid="da-canvas-center"]')).not.toBeNull()
    expect(container.querySelector('[data-testid="da-mark-overlay"]')).not.toBeNull()
  })

  it("defaultFullscreen=true + a granted bundle renders the fullscreen overlay", async () => {
    const { container } = render(
      React.createElement(PostGenerationResult, {
        prototype: proto({ bundle_url: BUNDLE }),
        defaultFullscreen: true,
      }),
    )
    await waitFor(() => {
      expect(container.querySelector('[data-testid="proto-fullscreen"]')).not.toBeNull()
    })
    expect(container.querySelector('[data-testid="proto-fullscreen-close"]')).not.toBeNull()
  })

  it("surfaces a grant error (no iframe) when the mint fails", async () => {
    viewGrant.mockRejectedValueOnce(new Error("401"))
    const { container } = render(
      React.createElement(PostGenerationResult, { prototype: proto({ bundle_url: BUNDLE }) }),
    )
    await waitFor(() => {
      expect(container.querySelector('[data-testid="da-grant-error"]')).not.toBeNull()
    })
    expect(container.querySelector("iframe.da-prototype-iframe")).toBeNull()
  })
})
