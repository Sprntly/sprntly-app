// @vitest-environment jsdom
//
// (Glitch B) — the prototype iframe must SURVIVE the fullscreen toggle
// in-place: ONE iframe node, no reparent, no `src` re-fetch, so there is no
// blank/white gap and no misleading "Applying changes…" on the fullscreen on↔off
// transition. This exercises the REAL rendered tree (jsdom), toggling
// `fullscreenOpen` via a testing-library rerender and asserting the SAME iframe
// DOM node persists — it FAILS against the old conditional-mount (which unmounted
// the inline viewer and mounted a separate overlay viewer → a brand-new iframe).
//
// It ALSO re-asserts #572's fullscreen presentation on the refactored tree (the
// device pill / labeled Close pill / live toggle / single-device gating / mobile
// default), guarding that the persistence refactor did not undo #572.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  PostGenerationResultView,
  type PostGenerationResultViewProps,
} from "../PostGenerationResult"

afterEach(() => cleanup())

const BUNDLE = "https://cdn/x/bundle/index.html"

function baseProps(
  over: Partial<PostGenerationResultViewProps> = {},
): PostGenerationResultViewProps {
  return {
    prototypeId: 42,
    isComplete: false,
    shareMode: "private",
    shareToken: null,
    bundleUrl: BUNDLE,
    ...over,
  } as PostGenerationResultViewProps
}

function iframeOf(container: HTMLElement): HTMLIFrameElement | null {
  return container.querySelector("iframe.da-prototype-iframe")
}

describe("persistent iframe across fullscreen (AC1)", () => {
  it("keeps the SAME iframe node — no remount, no new src — across a fullscreen ON↔OFF toggle", () => {
    const { container, rerender } = render(
      <PostGenerationResultView {...baseProps({ fullscreenOpen: false })} />,
    )
    const before = iframeOf(container)
    expect(before).not.toBeNull()
    const beforeSrc = before!.getAttribute("src")

    // Enter fullscreen.
    rerender(
      <PostGenerationResultView {...baseProps({ fullscreenOpen: true })} />,
    )
    const during = iframeOf(container)
    // SAME DOM node object (identity), and its src was never re-set.
    expect(during).toBe(before)
    expect(during!.getAttribute("src")).toBe(beforeSrc)
    // still exactly one iframe (no shadow second instance)
    expect(container.querySelectorAll("iframe.da-prototype-iframe")).toHaveLength(1)

    // Leave fullscreen — the very same node returns inline, still not reloaded.
    rerender(
      <PostGenerationResultView {...baseProps({ fullscreenOpen: false })} />,
    )
    const after = iframeOf(container)
    expect(after).toBe(before)
    expect(after!.getAttribute("src")).toBe(beforeSrc)
  })

  it("does NOT show 'Applying changes…' when returning from fullscreen with no iterate in flight (AC2)", () => {
    const { container, rerender } = render(
      <PostGenerationResultView
        {...baseProps({ fullscreenOpen: true, iterateRunning: false })}
      />,
    )
    rerender(
      <PostGenerationResultView
        {...baseProps({ fullscreenOpen: false, iterateRunning: false })}
      />,
    )
    expect(container.textContent).not.toContain("Applying changes…")
  })
})

describe("#572 fullscreen presentation preserved on the refactored tree (AC4)", () => {
  it("single-device (mobile-only) fullscreen → device pill + labeled Close pill, toggle gated, opens on mobile", () => {
    const { container } = render(
      <PostGenerationResultView
        {...baseProps({
          fullscreenOpen: true,
          showDesktop: false,
          showMobile: true,
          platform: "mobile",
        })}
      />,
    )
    // fullscreen dialog + always-present labeled Close pill (the exit)
    const dialog = container.querySelector('[data-testid="proto-fullscreen"]')
    expect(dialog).not.toBeNull()
    expect(dialog!.getAttribute("role")).toBe("dialog")
    const close = container.querySelector('[data-testid="proto-fullscreen-close"]')
    expect(close).not.toBeNull()
    expect(close!.querySelector(".proto-fs-close-label")?.textContent).toBe("Close")
    // device indicator pill present; in-frame toggle gated away (nothing to toggle)
    const pill = container.querySelector(".proto-fs-device")
    expect(pill).not.toBeNull()
    expect(pill!.textContent).toContain("Mobile")
    expect(container.querySelector('[aria-label="Preview platform"]')).toBeNull()
    // opens on the single (mobile) device
    expect(container.querySelector(".proto-stage.mobile")).not.toBeNull()
    expect(container.querySelector(".proto-stage.desktop")).toBeNull()
  })

  it("both-device fullscreen → live Desktop/Mobile toggle, no device pill, same Close pill", () => {
    const { container } = render(
      <PostGenerationResultView
        {...baseProps({
          fullscreenOpen: true,
          showDesktop: true,
          showMobile: true,
          platform: "desktop",
        })}
      />,
    )
    expect(
      container.querySelector('[data-testid="proto-fullscreen-close"]'),
    ).not.toBeNull()
    expect(
      container.querySelector('[aria-label="Preview platform"]'),
    ).not.toBeNull()
    expect(container.querySelector(".proto-fs-device")).toBeNull()
  })

  it("the labeled Close pill keeps z-index:2 (never occluded / trapped)", () => {
    // The z-index:2 stacking (#572) lives in the scoped stylesheet — assert the
    // rule is intact so the persistence refactor can't silently drop it.
    const fs = require("node:fs") as typeof import("node:fs")
    const path = require("node:path") as typeof import("node:path")
    const cssPath = path.join(__dirname, "..", "design-agent.css")
    const css = fs.readFileSync(cssPath, "utf8")
    const block = css.match(
      /\.design-agent-surface\s+\.proto-fullscreen-close\s*\{([^}]*)\}/,
    )
    expect(block).not.toBeNull()
    expect(block![1]).toMatch(/z-index:\s*2/)
  })
})
