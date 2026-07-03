// @vitest-environment jsdom
//
// (Glitch A) — the opt-in load mask: a NEUTRAL surface-colored cover
// sits over the iframe until its first `load` fires, then is removed, so the
// black initial-paint / grant-mint gap is never shown. Exercised on the real DOM
// (jsdom) because it depends on the iframe `load` event lifting the cover.
import * as React from "react"
import { cleanup, fireEvent, render } from "@testing-library/react"
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

  it("renders no placeholder at all when masking is not opted in (public path unchanged)", () => {
    const { container } = render(
      <PrototypeViewer bundleUrl={BUNDLE} isComplete={false} />,
    )
    expect(
      container.querySelector('[data-testid="da-viewer-placeholder"]'),
    ).toBeNull()
  })
})
