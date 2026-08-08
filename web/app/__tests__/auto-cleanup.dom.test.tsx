// @vitest-environment jsdom
/**
 * Nothing a test renders may survive into the next test.
 *
 * THE DEFECT THIS EXISTS FOR (2026-08-08, PR #1118 run 31228763455). vitest
 * reported 369 files / 4392 tests PASSED, zero failures — and exited 1. The
 * whole run went red on a single error raised after teardown:
 *
 *   ReferenceError: window is not defined
 *     at dispatchSetState (react-dom) ← Timeout._onTimeout ← GenerateModal.tsx
 *
 * A `GenerateModalAutoSkipLocate` test had rendered GenerateModal and never
 * unmounted it. The modal arms a 300ms `setTimeout` on open and clears it on
 * unmount — correctly — but an unmount that never happens clears nothing, so
 * the timer outlived the environment. Re-running the identical commit passed.
 *
 * WHY IT WAS POSSIBLE. React Testing Library registers its own cleanup only
 * when it can see a global `afterEach`, and `vitest.config.ts` does not set
 * `globals: true`. Every file imports its hooks from "vitest" instead, so
 * auto-cleanup never armed. 177 DOM files happened to call `cleanup()` by hand;
 * 30 did not, and nothing in the repo said they had to.
 *
 * WHAT GUARDS IT NOW. `vitest.setup.ts` runs `cleanup()` after every test. This
 * file is the alarm on that: delete the hook and this goes red with a reason,
 * rather than a random unrelated PR going red six weeks later with a stack
 * trace into a component nobody on that PR touched.
 *
 * A NOTE ON WHY THIS TEST LOOKS TRIVIAL. It has to be. The real failure is
 * timing-dependent across a 369-file run and cannot be reproduced in a single
 * file — running the original suite in isolation passes every time, because the
 * remaining tests take longer than the timer. So this does not chase the race.
 * It asserts the INVARIANT whose absence made the race reachable: after a test
 * ends, the previous test's DOM is gone. That is deterministic, and it is the
 * only part worth pinning.
 */
import * as React from "react"
import { render } from "@testing-library/react"
import { describe, expect, it } from "vitest"

/** Mounts, and arms a timer it only clears on unmount — GenerateModal's shape,
 *  reduced to the two lines that matter. If this component is still mounted
 *  when the environment goes away, the timer fires into a dead global. */
function LeavesATimerBehind() {
  const [late, setLate] = React.useState(false)
  React.useEffect(() => {
    const t = setTimeout(() => setLate(true), 300)
    return () => clearTimeout(t)
  }, [])
  return React.createElement("div", { "data-testid": "leaky" }, late ? "late" : "early")
}

describe("every test starts with an empty document", () => {
  it("renders something that would outlive the test", () => {
    render(React.createElement(LeavesATimerBehind))
    expect(document.querySelectorAll('[data-testid="leaky"]')).toHaveLength(1)
  })

  it("finds none of it still mounted", () => {
    // Fails without the cleanup hook in vitest.setup.ts — the node above is
    // still in the document, and its 300ms timer is still pending.
    expect(document.querySelectorAll('[data-testid="leaky"]')).toHaveLength(0)
    expect(document.body.textContent).toBe("")
  })
})
