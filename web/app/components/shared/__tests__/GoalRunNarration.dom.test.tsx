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

// A SHAPE THE BACKEND CAN ACTUALLY EMIT, and the previous fixture was not one:
// it set themed 1,502 + unthemed 242 = groups 1,744, but `assign_themes`
// guarantees `claims_themed + claims_unthemed === claims`, so with claims 2,410
// the writer would have sent unthemed 908. A fixture encoding the author's
// mental model instead of the writer's output is how the units error rendered
// green — see learnings/a-double-that-invents-the-api.
const FULL = {
  step: "done" as const,
  signals_read: 2777,
  claims: 2410,
  retired: 300,
  undated: 67,
  sources: 4,
  claims_themed: 1502,
  claims_unthemed: 908,   // 1502 + 908 === 2410 === claims. Not === groups.
  groups: 830,            // the BALANCING total: themes + ungroupable
  themes: 622,            // 830 - 208 ungroupable. What the headline shows.
  findings: 168,
  conflicts: 3,
  deep: 5,
  dropped: {
    anecdote: 396,
    echo: 9,
    single_account: 41,
    no_authority: 2,
    uncausal: 6,
    ungroupable: 208,
  },
  echo_check_skipped: false,
}

// The funnel has to BALANCE, and asserting it here means the fixture cannot
// drift into another impossible state unnoticed.
const GROUP_DROPS = ["anecdote", "echo", "single_account", "no_authority", "uncausal"]

describe("the funnel", () => {
  it("shows how a corpus became a ranking, per rule", () => {
    render(<GoalRunNarration progress={FULL} />)
    const text = screen.getByTestId("goal-narration").textContent || ""

    expect(text).toContain("2,410")   // claims read
    expect(text).toContain("622")     // themes (the headline)
    expect(text).toContain("396")     // anecdotes
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


describe("units — the defect two reviewers found", () => {
  it("never presents the claim split as the parts of the theme count", () => {
    render(<GoalRunNarration progress={FULL} />)
    const text = screen.getByTestId("goal-narration").textContent || ""
    // 1,502 + 908 = 2,410 claims, NOT 830 themes. The sentence must say
    // "claims" on the split so a reader cannot be invited to add them to the
    // headline and conclude the headline is wrong.
    expect(text).toContain("1,502 claims")
    expect(text).toMatch(/Grouped into\s*622\s*themes/)
    // The give-away phrasing of the old bug: the split rendered with no unit.
    expect(text).not.toContain("1,502 by your knowledge graph")
  })

  it("keeps ungroupable in claims while the other rules count groups", () => {
    render(<GoalRunNarration progress={FULL} />)
    const drops = screen.getByTestId("goal-narration-drops").textContent || ""
    expect(drops).toContain("208 claims never grouped at all")
  })

  it("puts ungroupable FIRST — those claims never entered the funnel", () => {
    render(<GoalRunNarration progress={FULL} />)
    const rows = Array.from(
      screen.getByTestId("goal-narration-drops").querySelectorAll("li"),
    ).map((li) => li.textContent || "")
    expect(rows[0]).toContain("never grouped at all")
  })

  it("the funnel balances: themes === findings + group-level drops", () => {
    const groupDrops = GROUP_DROPS.reduce(
      (t, c) => t + (FULL.dropped as Record<string, number>)[c], 0)
    expect(FULL.findings + groupDrops).toBe(FULL.themes)
  })

  it("groups is the balancing total, themes is what a reader is shown", () => {
    // `_cluster` keys every ungroupable claim as its OWN cluster, so `groups`
    // carries one pseudo-group per unembeddable claim. Showing THAT as a theme
    // count is how "Grouped into N themes" ended up directly above "N claims
    // never grouped at all".
    expect(FULL.groups).toBe(FULL.themes + FULL.dropped.ungroupable)
  })

  it("never renders the balancing total as the headline", () => {
    render(<GoalRunNarration progress={FULL} />)
    const text = screen.getByTestId("goal-narration").textContent || ""
    expect(text).not.toMatch(/Grouped into\s*830/)
  })

  it("a corpus that grouped into nothing does not claim to have themes", () => {
    render(
      <GoalRunNarration
        progress={{
          step: "done", claims: 2410, claims_themed: 0, claims_unthemed: 2410,
          groups: 2410, themes: 0, findings: 0,
          dropped: { ungroupable: 2410, anecdote: 0, echo: 0,
                     single_account: 0, no_authority: 0, uncausal: 0 },
        }}
      />,
    )
    const text = screen.getByTestId("goal-narration").textContent || ""
    expect(text).toContain("Grouped into 0 themes")
    expect(text).not.toContain("Grouped into 2,410 themes")
  })
})

describe("drop-rule drift is visible, not silent", () => {
  it("renders a rule this file does not know rather than discarding it", () => {
    render(
      <GoalRunNarration
        progress={{ ...FULL, dropped: { ...FULL.dropped, stale_bet: 400 } }}
      />,
    )
    const drops = screen.getByTestId("goal-narration-drops").textContent || ""
    // Ugly beats invisible: a funnel that silently omits a rule the engine
    // applied stops adding up with nothing going red.
    expect(drops).toContain("400")
    expect(drops).toContain("stale_bet")
  })
})

describe("what was skipped before projection", () => {
  it("names retired and undated separately rather than blaming the date", () => {
    render(<GoalRunNarration progress={FULL} />)
    const text = screen.getByTestId("goal-narration").textContent || ""
    expect(text).toContain("300 superseded")
    expect(text).toContain("67 undated")
    // The old copy attributed the whole 367 gap to a missing date, which
    // contradicted the run's own coverage note.
    expect(text).not.toContain("367")
  })
})


describe("each number is gated on itself, not on its neighbour", () => {
  it("still shows the theme count when the claim-split write was lost", () => {
    // `_progress` swallows a failed write BY DESIGN, so losing only the
    // `analysing` update is an expected state — and it used to silently drop
    // the one number this screen exists to publish.
    render(
      <GoalRunNarration
        progress={{
          step: "done", claims: 2410, themes: 622, groups: 830, findings: 168,
          dropped: { ...FULL.dropped },
        }}
      />,
    )
    const text = screen.getByTestId("goal-narration").textContent || ""
    expect(text).toContain("Grouped into 622 themes")
    expect(text).toContain("168")
  })

  it("still shows the claim split before the theme count exists", () => {
    render(
      <GoalRunNarration
        progress={{ step: "analysing", claims: 2410,
                    claims_themed: 1502, claims_unthemed: 908 }}
      />,
    )
    const text = screen.getByTestId("goal-narration").textContent || ""
    expect(text).toContain("1,502 claims")
    expect(text).not.toContain("Grouped into")
  })
})
