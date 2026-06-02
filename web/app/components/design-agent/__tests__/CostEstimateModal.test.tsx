// P3-11 — CostEstimateModal tests. Node-env vitest (no DOM, no testing-library),
// so — following the PrdPatchBanner / CompletionBar convention — we SSR-render the
// pure view via renderToStaticMarkup and unit-test the extracted orchestration
// helpers with injected deps. The iterate helper is injected (P3-14 owns the real
// `designAgentApi.iterate`), so AC9 is asserted against a spy.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  CostEstimateModalView,
  runCancel,
  runContinue,
} from "../CostEstimateModal"
import type { IterateCostEstimate } from "../../../lib/api"

afterEach(() => {
  vi.restoreAllMocks()
})

const UNDER_CAP: IterateCostEstimate = {
  cached_input_tokens: 1200,
  new_input_tokens: 8,
  expected_output_tokens: 2000,
  est_cost_usd: 0.03,
  soft_cap_usd: 0.5,
  exceeds_soft_cap: false,
  model: "claude-sonnet-4-6",
}

const OVER_CAP: IterateCostEstimate = {
  ...UNDER_CAP,
  cached_input_tokens: 2_000_000,
  est_cost_usd: 0.72,
  exceeds_soft_cap: true,
}

function render(props: React.ComponentProps<typeof CostEstimateModalView>): string {
  return renderToStaticMarkup(React.createElement(CostEstimateModalView, props))
}

describe("CostEstimateModalView — rendering", () => {
  it("renders the dollar estimate + Continue + Cancel (AC8)", () => {
    const html = render({
      estimate: UNDER_CAP,
      loading: false,
      onContinue: () => {},
      onCancel: () => {},
    })
    expect(html).toContain("~$0.03")
    expect(html).toContain('data-testid="cost-estimate-continue"')
    expect(html).toContain('data-testid="cost-estimate-cancel"')
    expect(html).toContain("Continue")
    expect(html).toContain("Cancel")
    // The under-cap estimate must NOT render the soft-cap warning.
    expect(html).not.toContain('data-testid="cost-estimate-soft-cap-warning"')
  })

  it("renders a soft-cap warning element when exceeds_soft_cap (AC8)", () => {
    const html = render({
      estimate: OVER_CAP,
      loading: false,
      onContinue: () => {},
      onCancel: () => {},
    })
    expect(html).toContain('data-testid="cost-estimate-soft-cap-warning"')
    expect(html).toContain("above the $0.50 guide")
  })

  it("renders an error message when errorMsg is set", () => {
    const html = render({
      estimate: null,
      loading: false,
      errorMsg: "Could not estimate cost",
      onContinue: () => {},
      onCancel: () => {},
    })
    expect(html).toContain('data-testid="cost-estimate-error"')
    expect(html).toContain("Could not estimate cost")
  })
})

describe("CostEstimateModal — orchestration helpers", () => {
  it("runContinue calls the injected iterate helper once with the body (AC9)", async () => {
    const iterate = vi.fn().mockResolvedValue({})
    await runContinue(iterate, { prototypeId: 7, prompt: "make it blue", appliedCommentId: 5 })
    expect(iterate).toHaveBeenCalledTimes(1)
    expect(iterate).toHaveBeenCalledWith(7, { prompt: "make it blue", applied_comment_id: 5 })
  })

  it("runContinue defaults applied_comment_id to null when absent", async () => {
    const iterate = vi.fn().mockResolvedValue({})
    await runContinue(iterate, { prototypeId: 7, prompt: "tweak" })
    expect(iterate).toHaveBeenCalledWith(7, { prompt: "tweak", applied_comment_id: null })
  })

  it("runCancel does NOT call the iterate helper; it only closes (AC9)", () => {
    const iterate = vi.fn()
    const onClose = vi.fn()
    runCancel(onClose)
    expect(iterate).not.toHaveBeenCalled()
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
