import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it, vi } from "vitest"
import {
  GenerationErrorBanner,
  isRetryableFailure,
  iterateFailureCopy,
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

  it("renders the calm composition — art tile + serif title (test_banner_composition)", () => {
    const html = renderToStaticMarkup(
      React.createElement(GenerationErrorBanner, {
        reason: "The prototype failed to build. Try regenerating.",
        onRetry: noop,
      }),
    )
    // Art tile (danger icon lives inside; the tile itself is decorative).
    expect(html).toContain('class="da-gen-error-art"')
    // Serif calming title.
    expect(html).toContain('class="da-gen-error-title"')
    expect(html).toContain("Generation")
    expect(html).toContain("finish")
    // Reassurance line copy is preserved.
    expect(html).toContain("Your PRD and brief are saved")
  })

  it("renders the in-banner Back affordance only when onBack is provided (test_banner_back_optional)", () => {
    const withoutBack = renderToStaticMarkup(
      React.createElement(GenerationErrorBanner, {
        reason: "Generation failed. Try regenerating.",
        onRetry: noop,
      }),
    )
    expect(withoutBack).not.toContain('data-testid="prototype-route-gen-error-back"')

    const withBack = renderToStaticMarkup(
      React.createElement(GenerationErrorBanner, {
        reason: "Generation failed. Try regenerating.",
        onRetry: noop,
        onBack: noop,
      }),
    )
    expect(withBack).toContain('data-testid="prototype-route-gen-error-back"')
    expect(withBack).toContain("Back to brief")
  })

  it("the Back button's onClick calls onBack (test_back_callback)", () => {
    const onBack = vi.fn()
    const tree = GenerationErrorBanner({
      reason: "Generation failed. Try regenerating.",
      onRetry: noop,
      onBack,
    }) as React.ReactElement
    const children = React.Children.toArray(
      (tree.props as { children: React.ReactNode }).children,
    ) as React.ReactElement[]
    const actions = children.find(
      (c) =>
        typeof c.props === "object" &&
        (c.props as { className?: string }).className ===
          "generation-error-actions",
    ) as React.ReactElement
    // Back is the SECOND child of the actions row (Retry is first).
    const back = React.Children.toArray(
      (actions.props as { children: React.ReactNode }).children,
    )[1] as React.ReactElement
    expect(back.type).toBe("button")
    ;(back.props as { onClick: () => void }).onClick()
    expect(onBack).toHaveBeenCalledTimes(1)
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

describe("reasonCopy — provider error classes", () => {
  it("maps each PROVIDER_* class to curated copy (test_reasoncopy_provider_classes)", () => {
    // Billing/auth: reassuring, non-blaming, never leaks the credit/auth cause.
    expect(reasonCopy("error_class=PROVIDER_BILLING | error_message=x")).toBe(
      "Something went wrong on our end — we've been notified.",
    )
    expect(reasonCopy("error_class=PROVIDER_AUTH | error_message=x")).toBe(
      "Something went wrong on our end — we've been notified.",
    )
    // Interpolates a support reference when a refId is given.
    expect(reasonCopy("error_class=PROVIDER_BILLING", 252)).toBe(
      "Something went wrong on our end — we've been notified. (Ref: 252)",
    )
    expect(reasonCopy("error_class=PROVIDER_CAPACITY | error_message=x")).toBe(
      "High demand right now — try again in a few minutes.",
    )
    expect(reasonCopy("error_class=PROVIDER_UNAVAILABLE | error_message=x")).toBe(
      "The prototype service is temporarily unavailable. Try again shortly.",
    )
  })

  it("falls back to the generic line for an unknown class (test_reasoncopy_unknown_generic_fallback)", () => {
    // Regression: the existing generic fallback is unchanged.
    expect(reasonCopy("SomeUnclassifiedError: whatever")).toBe(
      "Generation failed. Try regenerating.",
    )
  })

  it("classifies retryability from the raw wire string (isRetryableFailure)", () => {
    expect(isRetryableFailure("error_class=PROVIDER_BILLING")).toBe(false)
    expect(isRetryableFailure("error_class=PROVIDER_AUTH")).toBe(false)
    expect(isRetryableFailure("error_class=PROVIDER_CAPACITY")).toBe(true)
    expect(isRetryableFailure("error_class=PROVIDER_UNAVAILABLE")).toBe(true)
    expect(isRetryableFailure("ViteBuildError: exit=1")).toBe(true)
  })
})

describe("iterateFailureCopy — iterate-context curated copy", () => {
  it("test_iterate_copy_maps_provider_billing_and_auth_to_reassuring_ref_line", () => {
    expect(
      iterateFailureCopy(
        "iterate agent_loop ended with status=error iters=1 | error_message=x | error_class=PROVIDER_BILLING",
        42,
      ),
    ).toBe("Something went wrong on our end — we've been notified. (Ref: 42)")
    expect(
      iterateFailureCopy("error_class=PROVIDER_AUTH | error_message=x", 42),
    ).toBe("Something went wrong on our end — we've been notified. (Ref: 42)")
    // Omitting refId omits the "(Ref: ...)" suffix.
    expect(iterateFailureCopy("error_class=PROVIDER_BILLING")).toBe(
      "Something went wrong on our end — we've been notified.",
    )
  })

  it("test_iterate_copy_maps_provider_unavailable_and_capacity", () => {
    expect(iterateFailureCopy("error_class=PROVIDER_UNAVAILABLE | error_message=x")).toBe(
      "The prototype service is temporarily unavailable. Try again shortly.",
    )
    expect(iterateFailureCopy("error_class=PROVIDER_CAPACITY | error_message=x")).toBe(
      "High demand right now — try again in a few minutes.",
    )
  })

  it("test_iterate_copy_maps_build_failure_without_leaking_raw_exception_text", () => {
    const viteRaw =
      "ViteBuildError: vite build exit=1: src/App.tsx(12,3): error TS2304"
    const viteCopy = iterateFailureCopy(viteRaw)
    expect(viteCopy).toBe(
      "The change couldn't be built. Try again, or adjust your comment and resubmit.",
    )
    expect(viteCopy).not.toContain("ViteBuildError")
    expect(viteCopy).not.toContain("exit=1")
    expect(viteCopy).not.toContain("TS2304")

    const typeCheckRaw = "TypeCheckError: TS2322 in App.tsx"
    const typeCheckCopy = iterateFailureCopy(typeCheckRaw)
    expect(typeCheckCopy).toBe(
      "The change couldn't be built. Try again, or adjust your comment and resubmit.",
    )
    expect(typeCheckCopy).not.toContain("TypeCheckError")
    expect(typeCheckCopy).not.toContain("TS2322")
  })

  it("test_iterate_copy_maps_emitted_no_files", () => {
    expect(
      iterateFailureCopy("iterate agent_loop completed but emitted no files"),
    ).toBe("The agent didn't produce a change. Try again.")
  })

  it("test_iterate_copy_falls_back_to_generic_for_unrecognized_text", () => {
    expect(iterateFailureCopy("build blew up")).toBe(
      "Couldn't apply the change. Try again.",
    )
  })
})

describe("GenerationErrorBanner — retry suppression on non-retryable failures", () => {
  const noop = () => {}

  it("suppresses the Retry control when retryable=false, keeps it when true (test_retry_cta_suppressed_for_billing_auth)", () => {
    const suppressed = renderToStaticMarkup(
      React.createElement(GenerationErrorBanner, {
        reason: "Something went wrong on our end — we've been notified.",
        onRetry: noop,
        onBack: noop,
        retryable: false,
      }),
    )
    expect(suppressed).not.toContain('data-testid="generation-error-retry"')
    // Back to brief becomes the primary action when retry is gone.
    expect(suppressed).toContain('data-testid="prototype-route-gen-error-back"')

    const retryable = renderToStaticMarkup(
      React.createElement(GenerationErrorBanner, {
        reason: "The prototype failed to build. Try regenerating.",
        onRetry: noop,
        retryable: true,
      }),
    )
    expect(retryable).toContain('data-testid="generation-error-retry"')
  })
})
