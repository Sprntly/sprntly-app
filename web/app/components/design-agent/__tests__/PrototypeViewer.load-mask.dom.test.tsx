// @vitest-environment jsdom
//
// (Glitch A) — the opt-in load mask: a NEUTRAL surface-colored cover
// sits over the iframe until its first `load` fires, then is removed, so the
// black initial-paint / grant-mint gap is never shown. Exercised on the real DOM
// (jsdom) because it depends on the iframe `load` event lifting the cover.
import * as React from "react"
import { act, cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { PrototypeViewer } from "../PrototypeViewer"

afterEach(() => cleanup())

const BUNDLE = "https://cdn/x/bundle/index.html"

describe("load mask lifts on first paint", () => {
  it("shows the neutral placeholder before load, then removes it after the iframe load", () => {
    const { container } = render(
      <PrototypeViewer bundleUrl={BUNDLE} isComplete={false} maskUntilLoaded />,
    )
    const placeholder = () =>
      container.querySelector('[data-testid="da-viewer-placeholder"]')
    // Present before the iframe paints — and it is NOT the black / apply copy.
    expect(placeholder()).not.toBeNull()
    expect(container.textContent).not.toContain("Applying changes")

    const iframe = container.querySelector("iframe.da-prototype-iframe")!
    fireEvent.load(iframe)
    // Gone once the bundle painted.
    expect(placeholder()).toBeNull()
  })

  it("still forwards onBundleLoad on the first load while masking", () => {
    const onBundleLoad = vi.fn()
    const { container } = render(
      <PrototypeViewer
        bundleUrl={BUNDLE}
        isComplete={false}
        maskUntilLoaded
        onBundleLoad={onBundleLoad}
      />,
    )
    fireEvent.load(container.querySelector("iframe.da-prototype-iframe")!)
    expect(onBundleLoad).toHaveBeenCalledTimes(1)
  })

  it("renders no placeholder at all when masking is not opted in (default non-masking path)", () => {
    const { container } = render(
      <PrototypeViewer bundleUrl={BUNDLE} isComplete={false} />,
    )
    expect(
      container.querySelector('[data-testid="da-viewer-placeholder"]'),
    ).toBeNull()
  })
})

// A readiness-aware onBundleLoad (e.g. useViewGrant's notifyBundleLoaded) can
// return a Promise that resolves only once the async readiness decision is
// in. The mask must stay up until then, not clear on the raw `load` event.
describe("load mask bridges an async onBundleLoad readiness signal", () => {
  const placeholderIn = (container: HTMLElement) =>
    container.querySelector('[data-testid="da-viewer-placeholder"]')

  it("test_prototype_viewer_mask_stays_up_while_async_onBundleLoad_pending", () => {
    const { container } = render(
      <PrototypeViewer
        bundleUrl={BUNDLE}
        isComplete={false}
        maskUntilLoaded
        onBundleLoad={() => new Promise<void>(() => {})}
      />,
    )
    fireEvent.load(container.querySelector("iframe.da-prototype-iframe")!)
    // The returned promise never resolves within this test — the mask must
    // remain up rather than clearing on the raw `load` event.
    expect(placeholderIn(container)).not.toBeNull()
  })

  it("test_prototype_viewer_mask_clears_once_pending_onBundleLoad_promise_resolves", async () => {
    let resolveReadiness: () => void = () => {}
    const readiness = new Promise<void>((resolve) => {
      resolveReadiness = resolve
    })
    const { container } = render(
      <PrototypeViewer
        bundleUrl={BUNDLE}
        isComplete={false}
        maskUntilLoaded
        onBundleLoad={() => readiness}
      />,
    )
    fireEvent.load(container.querySelector("iframe.da-prototype-iframe")!)
    expect(placeholderIn(container)).not.toBeNull()
    await act(async () => {
      resolveReadiness()
      await readiness
    })
    expect(placeholderIn(container)).toBeNull()
  })

  it("test_prototype_viewer_mask_clears_synchronously_for_non_promise_onBundleLoad", () => {
    const onBundleLoad = vi.fn(() => undefined)
    const { container } = render(
      <PrototypeViewer
        bundleUrl={BUNDLE}
        isComplete={false}
        maskUntilLoaded
        onBundleLoad={onBundleLoad}
      />,
    )
    fireEvent.load(container.querySelector("iframe.da-prototype-iframe")!)
    // No act(async ...) needed — the mask clears in the same tick.
    expect(placeholderIn(container)).toBeNull()
  })
})

// A stalled bundle (hung signed-URL fetch, dead asset host) never fires `load`,
// which would otherwise leave the neutral cover up forever. The timeout is a
// FALLBACK that lifts the cover only — it must not masquerade as a real load
// (onBundleLoad stays a load-event signal).
describe("mask timeout fallback", () => {
  const placeholderIn = (container: HTMLElement) =>
    container.querySelector('[data-testid="da-viewer-placeholder"]')

  it("test_mask_lifts_after_timeout_without_load: the cover is removed after 8000ms with no load event", () => {
    vi.useFakeTimers()
    try {
      const { container } = render(
        <PrototypeViewer bundleUrl={BUNDLE} isComplete={false} maskUntilLoaded />,
      )
      act(() => {
        vi.advanceTimersByTime(7999)
      })
      // Still covered just under the deadline…
      expect(placeholderIn(container)).not.toBeNull()
      act(() => {
        vi.advanceTimersByTime(1)
      })
      // …lifted at 8000ms even though `load` never fired.
      expect(placeholderIn(container)).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it("test_mask_timeout_does_not_fire_on_bundle_load: onBundleLoad is NOT called by the timeout path", () => {
    vi.useFakeTimers()
    try {
      const onBundleLoad = vi.fn()
      const { container } = render(
        <PrototypeViewer
          bundleUrl={BUNDLE}
          isComplete={false}
          maskUntilLoaded
          onBundleLoad={onBundleLoad}
        />,
      )
      act(() => {
        vi.advanceTimersByTime(20_000)
      })
      // The timeout lifts the cover only; it never synthesizes a load signal.
      expect(placeholderIn(container)).toBeNull()
      expect(onBundleLoad).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it("test_mask_timeout_cleared_on_unmount: unmounting before the timeout leaves no pending timer side-effects", () => {
    vi.useFakeTimers()
    // React reports a setState-after-unmount (act warning) via console.error —
    // a leaked timeout would surface there when the timers advance below.
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    try {
      const { unmount } = render(
        <PrototypeViewer bundleUrl={BUNDLE} isComplete={false} maskUntilLoaded />,
      )
      unmount()
      vi.advanceTimersByTime(20_000)
      expect(errorSpy).not.toHaveBeenCalled()
    } finally {
      errorSpy.mockRestore()
      vi.useRealTimers()
    }
  })
})
