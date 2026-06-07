import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"
import {
  GenerationErrorBanner,
  reasonCopy,
} from "../GenerationErrorBanner"

// PrdSections-style shim: Sprntly components have no `import React`; vitest's
// esbuild transform defaults to the classic runtime, so expose React globally
// rather than touch the shared vitest config (outside the engagement's map).
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

describe("reasonCopy — bare class-name substring → human copy (AC2)", () => {
  it("maps UnresolvedImportRepairExhausted (P6-07) to the screen-couldn't-build copy (test_reason_map_unresolved_import)", () => {
    // Arrives via the BUILD-PATH shape: bare class-name prefix, no `error_class=`.
    expect(reasonCopy("UnresolvedImportRepairExhausted: <Dashboard> never built")).toBe(
      "A referenced screen couldn't be built. Try regenerating — describe the screens you want explicitly.",
    )
  })

  it("maps ViteBuildError / TypeCheckError to the build-failed copy (test_reason_map_build_error)", () => {
    expect(reasonCopy("ViteBuildError: vite build exit=1: …")).toBe(
      "The prototype failed to build. Try regenerating.",
    )
    expect(reasonCopy("TypeCheckError: TS2322 in App.tsx")).toBe(
      "The prototype failed to build. Try regenerating.",
    )
  })

  it("maps a timeout message to the timeout copy (test_reason_map_timeout)", () => {
    expect(reasonCopy("Generation timed out (6 minutes)")).toBe(
      "Generation timed out. Try regenerating with a simpler scope.",
    )
  })

  it("maps an invalidated message to the template-changed copy (test_reason_map_invalidated)", () => {
    expect(reasonCopy("Template invalidated; retry")).toBe(
      "This prototype's template changed. Regenerate to pick up the latest.",
    )
  })

  it("maps an unrecognised string to the generic fallback (test_reason_map_generic_fallback)", () => {
    expect(reasonCopy("some unexpected backend string")).toBe(
      "Generation failed. Try regenerating.",
    )
    expect(reasonCopy("")).toBe("Generation failed. Try regenerating.")
  })

  it("prefers the more specific match when multiple substrings could apply (order)", () => {
    // A repair-exhausted failure that ALSO mentions a build error must resolve to
    // the more specific unresolved-import copy (most-specific-first ordering).
    expect(
      reasonCopy("UnresolvedImportRepairExhausted after ViteBuildError"),
    ).toBe(
      "A referenced screen couldn't be built. Try regenerating — describe the screens you want explicitly.",
    )
  })
})

describe("GenerationErrorBanner — render + retry (AC1/AC3/AC6)", () => {
  const noop = () => {}

  it("renders the mapped reason + a Retry control (test_banner_render)", () => {
    const html = renderToStaticMarkup(
      React.createElement(GenerationErrorBanner, {
        reason: "The prototype failed to build. Try regenerating.",
        onRetry: noop,
      }),
    )
    expect(html).toContain('data-testid="generation-error-banner"')
    expect(html).toContain("The prototype failed to build. Try regenerating.")
    expect(html).toContain('data-testid="generation-error-retry"')
    expect(html).toContain("Retry")
    // role=alert so assistive tech announces the failure.
    expect(html).toMatch(/role="alert"/)
  })

  it("SSR-renders without throwing — pure component, no window/effects (test_banner_ssr_renders, AC6)", () => {
    expect(() =>
      renderToStaticMarkup(
        React.createElement(GenerationErrorBanner, {
          reason: "Generation failed. Try regenerating.",
          onRetry: noop,
        }),
      ),
    ).not.toThrow()
  })

  it("the Retry button's onClick calls onRetry (test_retry_callback, AC3)", () => {
    const onRetry = vi.fn()
    // Pure component → call directly and walk the element tree to the button.
    const tree = GenerationErrorBanner({
      reason: "Generation failed. Try regenerating.",
      onRetry,
    }) as React.ReactElement
    const children = React.Children.toArray(
      (tree.props as { children: React.ReactNode }).children,
    ) as React.ReactElement[]
    // The Retry button lives inside the actions wrapper (second child).
    const actions = children.find(
      (c) =>
        typeof c.props === "object" &&
        (c.props as { className?: string }).className ===
          "generation-error-actions",
    ) as React.ReactElement
    const button = React.Children.toArray(
      (actions.props as { children: React.ReactNode }).children,
    )[0] as React.ReactElement
    expect(button.type).toBe("button")
    ;(button.props as { onClick: () => void }).onClick()
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it("never renders the raw backend error string verbatim (test_banner_never_renders_raw_error, AC2)", () => {
    // The launcher feeds the banner `reasonCopy(rawMessage)`. A raw message with a
    // stderr tail / internal path must NOT survive into the DOM — only the mapped
    // copy. We render through the real path (reasonCopy → banner reason).
    const raw =
      "ViteBuildError: vite build exit=1: /srv/internal/secret/path/App.tsx: Unexpected token at line 42 [31mstderr-tail[0m"
    const html = renderToStaticMarkup(
      React.createElement(GenerationErrorBanner, {
        reason: reasonCopy(raw),
        onRetry: noop,
      }),
    )
    expect(html).toContain("The prototype failed to build. Try regenerating.")
    expect(html).not.toContain("/srv/internal/secret/path")
    expect(html).not.toContain("stderr-tail")
    expect(html).not.toContain("exit=1")
  })
})
