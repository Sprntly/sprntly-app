// @vitest-environment jsdom
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { turnAfterNode, type TurnAfterNodeAdapter } from "../turnAfterNode"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

afterEach(cleanup)

const insight = <div data-testid="insight">insight</div>
const prd = <div data-testid="prd">prd questions</div>
const share = <div data-testid="share">share</div>

function renderAfter(idx: number, adapter: TurnAfterNodeAdapter) {
  return render(<>{turnAfterNode({ id: "t" }, idx, adapter)}</>)
}

describe("turnAfterNode — shared inline card placement (AC11/AC12)", () => {
  it("test_turnAfterNode_renders_insight_card_from_envelope", () => {
    const { queryByTestId } = renderAfter(2, {
      insightCardNode: insight,
      prdQuestionsNode: null,
      inlinePrdCards: true,
      inlinePrdAnchorIdx: 2,
    })
    expect(queryByTestId("insight")).toBeTruthy()
  })

  it("test_turnAfterNode_renders_prd_questions_when_prd_present", () => {
    const { queryByTestId } = renderAfter(2, {
      insightCardNode: null,
      prdQuestionsNode: prd,
      inlinePrdCards: true,
      inlinePrdAnchorIdx: 2,
    })
    expect(queryByTestId("prd")).toBeTruthy()
  })

  it("test_turnAfterNode_returns_null_when_no_insight_no_prd", () => {
    const { container } = renderAfter(2, {
      insightCardNode: null,
      prdQuestionsNode: null,
      inlinePrdCards: false,
      inlinePrdAnchorIdx: null,
    })
    // No cards, no extra → nothing rendered.
    expect(container.textContent).toBe("")
  })

  it("test_turnAfterNode_honours_top_vs_inline_placement", () => {
    // inlinePrdCards true + idx === anchor → cards render inline (below the turn).
    const inline = renderAfter(2, {
      insightCardNode: insight,
      prdQuestionsNode: prd,
      inlinePrdCards: true,
      inlinePrdAnchorIdx: 2,
      extra: share,
    })
    expect(inline.queryByTestId("insight")).toBeTruthy()
    expect(inline.queryByTestId("prd")).toBeTruthy()
    expect(inline.queryByTestId("share")).toBeTruthy()
    cleanup()

    // inlinePrdCards false → cards belong to the top-fallback, NOT the
    // after-node; only the per-turn extra rides here.
    const top = renderAfter(2, {
      insightCardNode: insight,
      prdQuestionsNode: prd,
      inlinePrdCards: false,
      inlinePrdAnchorIdx: null,
      extra: share,
    })
    expect(top.queryByTestId("insight")).toBeNull()
    expect(top.queryByTestId("prd")).toBeNull()
    expect(top.queryByTestId("share")).toBeTruthy()
    cleanup()

    // At a NON-anchor turn even when inline, the cards do not render here.
    const nonAnchor = renderAfter(5, {
      insightCardNode: insight,
      prdQuestionsNode: prd,
      inlinePrdCards: true,
      inlinePrdAnchorIdx: 2,
      extra: share,
    })
    expect(nonAnchor.queryByTestId("insight")).toBeNull()
    expect(nonAnchor.queryByTestId("share")).toBeTruthy()
  })
})
