// @vitest-environment jsdom
//
// Tests the SCOPED generate-surface error boundary: a child that THROWS during
// render degrades to the in-surface fallback (message + Retry) instead of
// propagating to the framework's whole-page error screen; Retry re-mounts the
// guarded subtree.

import * as React from "react"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { GenerateSurfaceErrorBoundary } from "../GenerateSurfaceErrorBoundary"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

/** A child that throws on its first render, then (after `shouldThrow` flips)
 *  renders fine — used to prove Retry re-mounts to a healthy subtree. */
function Bomb({ shouldThrow }: { shouldThrow: { current: boolean } }) {
  if (shouldThrow.current) throw new Error("boom in generate surface")
  return React.createElement("div", { "data-testid": "healthy-child" }, "ok")
}

describe("GenerateSurfaceErrorBoundary", () => {
  it("renders the scoped fallback (with Retry) when a child throws, not the raw error", () => {
    // React logs the caught error to console.error; silence it for a clean run.
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    const shouldThrow = { current: true }

    render(
      React.createElement(
        GenerateSurfaceErrorBoundary,
        null,
        React.createElement(Bomb, { shouldThrow }),
      ),
    )

    // Fallback surfaced (boundary caught the throw — nothing propagated).
    expect(
      screen.getByTestId("generate-surface-boundary-fallback"),
    ).toBeTruthy()
    expect(
      screen.getByTestId("generate-surface-boundary-retry"),
    ).toBeTruthy()
    // The raw thrown message is NOT rendered to the DOM (curated copy only).
    expect(
      screen.getByTestId("generate-surface-boundary-fallback").textContent,
    ).not.toContain("boom in generate surface")
    // The healthy child is gone.
    expect(screen.queryByTestId("healthy-child")).toBeNull()

    errSpy.mockRestore()
  })

  it("Retry re-mounts the guarded subtree (recovers when the throw condition clears)", () => {
    const errSpy = vi.spyOn(console, "error").mockImplementation(() => {})
    const shouldThrow = { current: true }
    const onReset = vi.fn()

    render(
      React.createElement(GenerateSurfaceErrorBoundary, {
        onReset,
        children: React.createElement(Bomb, { shouldThrow }),
      }),
    )

    // In fallback. Clear the throw condition, then Retry.
    expect(
      screen.getByTestId("generate-surface-boundary-fallback"),
    ).toBeTruthy()
    shouldThrow.current = false
    act(() => {
      fireEvent.click(screen.getByTestId("generate-surface-boundary-retry"))
    })

    // Recovered: the healthy child renders, fallback gone, onReset fired.
    expect(screen.getByTestId("healthy-child")).toBeTruthy()
    expect(
      screen.queryByTestId("generate-surface-boundary-fallback"),
    ).toBeNull()
    expect(onReset).toHaveBeenCalledTimes(1)

    errSpy.mockRestore()
  })

  it("renders children untouched when nothing throws (no fallback)", () => {
    const shouldThrow = { current: false }
    render(
      React.createElement(
        GenerateSurfaceErrorBoundary,
        null,
        React.createElement(Bomb, { shouldThrow }),
      ),
    )
    expect(screen.getByTestId("healthy-child")).toBeTruthy()
    expect(
      screen.queryByTestId("generate-surface-boundary-fallback"),
    ).toBeNull()
  })
})
