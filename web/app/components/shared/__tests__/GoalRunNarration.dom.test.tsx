// @vitest-environment jsdom
//
// The run narrating itself instead of spinning.
//
// What is guarded here is not layout. Every one of these is a way the funnel
// could read as authoritative while being false:
//
//   1. A NUMBER THE RUN NEVER MEASURED. The fields arrive in three writes and
//      a poll can land between any two of them, so absent must render as
//      absent — never as zero. A narration that revises its own numbers
//      teaches a reader to distrust all of them.
//   2. A CHECK THAT DID NOT RUN, rendered as a check that found nothing. When
//      the corpus is dated by ingest the echo rule is skipped entirely, and
//      "0 set aside" would claim it passed.
//   3. GROUPS AND CLAIMS COUNTED UNDER ONE NOUN. Every rule sets aside a
//      GROUP; ungroupable counts individual CLAIMS. One column, one noun, and
//      the number is quietly wrong.
//   4. The engine's name reaching the screen.
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { GoalRunNarration } from "../GoalRunNarration"

afterEach(cleanup)

const FULL = {
  step: "done" as const,
  signals_read: 2777,
  claims: 2410,
  sources: 4,
  themed: 1502,
  unthemed: 242,
  groups: 1744,
  findings: 168,
  conflicts: 3,
  deep: 5,
  dropped: {
    anecdote: 1576,
    echo: 9,
    single_account: 41,
    no_authority: 2,
    uncausal: 2,
    ungroupable: 0,
  },
  echo_check_skipped: false,
}

describe("the funnel", () => {
  it("shows how a corpus became a ranking, per rule", () => {
    render(<GoalRunNarration progress={FULL} />)
    const text = screen.getByTestId("goal-narration").textContent || ""

    expect(text).toContain("2,410")   // claims read
    expect(text).toContain("1,744")   // groups
    expect(text).toContain("1,576")   // anecdotes
    expect(text).toContain("168")     // findings

    const drops = screen.getByTestId("goal-narration-drops").textContent || ""
    // Each rule named in the reader's terms, not the engine's codes.
    expect(drops).toContain("anecdotes, not findings")
    expect(drops).toContain("one conversation, not a pattern")
    expect(drops).toContain("one account")
    expect(drops).toContain("no source allowed to speak")
    expect(drops).toContain("asserting a cause")
    // Codes are for counting, never for reading.
    expect(drops).not.toContain("single_account")
    expect(drops).not.toContain("no_authority")
  })

  it("names the conflicts, because they outrank everything", () => {
    render(<GoalRunNarration progress={FULL} />)
    expect(screen.getByTestId("goal-narration").textContent).toContain(
      "3 where your sources disagree",
    )
  })

  it("never says Crucible", () => {
    render(<GoalRunNarration progress={FULL} />)
    expect(
      (screen.getByTestId("goal-narration").textContent || "").toLowerCase(),
    ).not.toContain("crucible")
  })
})

describe("what it refuses to state", () => {
  it("renders nothing at all before the run has measured anything", () => {
    const { container } = render(<GoalRunNarration progress={{}} />)
    expect(container.innerHTML).toBe("")
  })

  it("shows only what has landed, mid-run", () => {
    // The first write: claims and sources, no grouping and no funnel yet.
    render(
      <GoalRunNarration
        progress={{ step: "grouping", claims: 2410, sources: 4 }}
      />,
    )
    const text = screen.getByTestId("goal-narration").textContent || ""
    expect(text).toContain("2,410")
    // NOT a zero-finding funnel. The run has not decided that yet, and
    // rendering it would be a number the run never measured.
    expect(screen.queryByTestId("goal-narration-drops")).toBeNull()
    expect(text).not.toContain("0 finding")
  })

  it("omits a rule that dropped nothing rather than listing a zero", () => {
    render(
      <GoalRunNarration
        progress={{ ...FULL, dropped: { ...FULL.dropped, echo: 0 } }}
      />,
    )
    const drops = screen.getByTestId("goal-narration-drops").textContent || ""
    expect(drops).not.toContain("one conversation, not a pattern")
    expect(drops).toContain("anecdotes, not findings")
  })
})

describe("a check that could not see", () => {
  it("says the echo rule was skipped rather than showing it found nothing", () => {
    render(
      <GoalRunNarration
        progress={{
          ...FULL,
          echo_check_skipped: true,
          dropped: { ...FULL.dropped, echo: 0 },
        }}
      />,
    )
    const note = screen.getByTestId("goal-narration-echo-skipped").textContent || ""
    expect(note).toContain("did not run")
    expect(note).toContain("dated by")
    // The distinction the whole note exists for.
    const drops = screen.getByTestId("goal-narration-drops").textContent || ""
    expect(drops).not.toContain("one conversation, not a pattern")
  })
})

describe("units", () => {
  it("counts ungroupable in CLAIMS, not in the groups every other rule counts", () => {
    render(
      <GoalRunNarration
        progress={{ ...FULL, dropped: { ...FULL.dropped, ungroupable: 312 } }}
      />,
    )
    const drops = screen.getByTestId("goal-narration-drops").textContent || ""
    expect(drops).toContain("312")
    // The noun is what stops this being a quietly wrong number.
    expect(drops).toContain("claims never grouped at all")
  })
})
