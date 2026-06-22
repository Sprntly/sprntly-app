// Rendering tests for IterateActivityStream — the "Focus" model: ONE live status
// line (latest step, updated in place) + a single terminal chip, the user bubble,
// and the pending-question "waiting" state.
// Uses renderToStaticMarkup (the repo convention for pure presentational
// components; the 30s ticker effect does not run under SSR).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { readFileSync } from "node:fs"

import { IterateActivityStream, turnLabel } from "../IterateActivityStream"
import type { ActivityEvent } from "../useIterateRun"

// Sprntly components carry no `import React`; expose it globally.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const HERE = dirname(fileURLToPath(import.meta.url))
const ACTIVITY_STREAM_PATH = join(HERE, "..", "IterateActivityStream.tsx")

let _id = 0
function makeEvent<T extends Omit<ActivityEvent, "id" | "createdAt">>(
  e: T,
  createdAt: number = Date.now(),
): T & { id: number; createdAt: number } {
  return { ...e, id: ++_id, createdAt } as T & { id: number; createdAt: number }
}

const render = (activity: ActivityEvent[], extra: { running?: boolean; userName?: string | null } = {}) =>
  renderToStaticMarkup(
    React.createElement(IterateActivityStream, {
      activity,
      running: extra.running ?? false,
      ...(extra.userName !== undefined ? { userName: extra.userName } : {}),
    }),
  )

/** Count non-overlapping occurrences of a substring. */
function count(haystack: string, needle: string): number {
  let n = 0
  let i = haystack.indexOf(needle)
  while (i !== -1) {
    n += 1
    i = haystack.indexOf(needle, i + needle.length)
  }
  return n
}

// ---------------------------------------------------------------------------
// Null / empty
// ---------------------------------------------------------------------------

describe("IterateActivityStream — empty list", () => {
  it("test_empty_activity_renders_null: returns null when activity is empty", () => {
    expect(render([])).toBe("")
  })
})

// ---------------------------------------------------------------------------
// THE NON-VACUITY CENTERPIECE: single-line collapse (not a wall)
// ---------------------------------------------------------------------------

describe("IterateActivityStream — single live line (collapse the step wall)", () => {
  it("test_multiple_steps_collapse_to_one_live_line_reflecting_the_latest: a run with N step events renders exactly ONE live-status node showing only the LATEST step's text — NOT a multi-row list", () => {
    // Four step events, exactly the cosmetic script the old WALL rendered as
    // four stacked rows. The Focus model must show ONE line = the LATEST.
    const activity: ActivityEvent[] = [
      makeEvent({ kind: "user" as const, text: "add confetti" }),
      makeEvent({ kind: "step" as const, text: "Reading the change request", state: "done" as const }),
      makeEvent({ kind: "step" as const, text: "Analyzing the prototype", state: "done" as const }),
      makeEvent({ kind: "step" as const, text: "Applying the change", state: "done" as const }),
      makeEvent({ kind: "step" as const, text: "Rebuilding", state: "active" as const }),
    ]
    const html = render(activity, { running: true })

    // EXACTLY ONE live-status node — proves the collapse (the old wall rendered
    // one `da-activity-step` row per event = four nodes here).
    expect(count(html, 'data-testid="da-activity-live"')).toBe(1)

    // It reflects the LATEST step only…
    expect(html).toContain("Rebuilding")
    // …and NOT the earlier steps (no per-step history / no wall).
    expect(html).not.toContain("Reading the change request")
    expect(html).not.toContain("Analyzing the prototype")
    expect(html).not.toContain("Applying the change")

    // Non-vacuity guard against a regression to the old wall: the per-step row
    // testid must be gone entirely.
    expect(html).not.toContain('data-testid="da-activity-step"')

    // The live line reuses the existing spinner + the shimmer text span.
    expect(html).toContain("da-activity-spinner")
    expect(html).toContain("da-activity-shim")
  })

  it("test_single_step_renders_one_live_line: a single step renders one live line with its text", () => {
    const html = render([
      makeEvent({ kind: "user" as const, text: "x" }),
      makeEvent({ kind: "step" as const, text: "Analyzing the prototype", state: "active" as const }),
    ])
    expect(count(html, 'data-testid="da-activity-live"')).toBe(1)
    expect(html).toContain("Analyzing the prototype")
  })

  it("test_live_line_fallback_when_no_step_yet: a running thread with only a user event shows one live line with the Working… fallback", () => {
    const html = render([makeEvent({ kind: "user" as const, text: "x" })], { running: true })
    expect(count(html, 'data-testid="da-activity-live"')).toBe(1)
    expect(html).toContain("Working…")
  })
})

// ---------------------------------------------------------------------------
// User request bubble (kept)
// ---------------------------------------------------------------------------

describe("IterateActivityStream — user request bubble", () => {
  it("test_user_event_renders_bubble: the user request renders the da-activity-user bubble + text", () => {
    const html = render([makeEvent({ kind: "user" as const, text: "make the hero blue" })], {
      running: true,
    })
    expect(html).toContain('data-testid="da-activity-user"')
    expect(html).toContain("make the hero blue")
  })

  it("test_user_bubble_still_renders_alongside_live_line: the user bubble + the live line coexist", () => {
    const html = render(
      [
        makeEvent({ kind: "user" as const, text: "make the hero blue" }),
        makeEvent({ kind: "step" as const, text: "Rebuilding", state: "active" as const }),
      ],
      { running: true },
    )
    expect(html).toContain('data-testid="da-activity-user"')
    expect(html).toContain('data-testid="da-activity-live"')
  })

  it("test_user_turn_renders_author_label: the user bubble carries the user-name label", () => {
    const FIVE_MIN_AGO = 1_700_000_000_000 - 5 * 60 * 1000
    const html = render([makeEvent({ kind: "user" as const, text: "x" }, FIVE_MIN_AGO)], {
      userName: "Ada Lovelace",
      running: true,
    })
    expect(html).toContain('class="da-activity-agent-label"')
    expect(html).toContain("Ada Lovelace")
  })
})

// ---------------------------------------------------------------------------
// Terminal chips — exactly one node each, NOT appended to a wall
// ---------------------------------------------------------------------------

describe("IterateActivityStream — terminal chips", () => {
  it("test_done_renders_one_terminal_done_chip_with_summary: done → the done chip with the summary, no live line", () => {
    const html = render([
      makeEvent({ kind: "user" as const, text: "add confetti" }),
      makeEvent({ kind: "step" as const, text: "Rebuilding", state: "done" as const }),
      makeEvent({
        kind: "done" as const,
        text: "Added a confetti burst to the Continue button.",
      }),
    ])
    expect(count(html, 'data-testid="da-activity-done"')).toBe(1)
    expect(html).toContain("Added a confetti burst to the Continue button.")
    // Reuses the existing done-icon (no duplicate icon class introduced).
    expect(html).toContain("da-activity-done-icon")
    // The done chip REPLACES the live line — no spinner/live node remains.
    expect(html).not.toContain('data-testid="da-activity-live"')
    expect(html).not.toContain('data-testid="da-activity-step"')
  })

  it("test_skipped_renders_one_skip_chip: skipped → a single neutral skip chip with the skip text", () => {
    const html = render([
      makeEvent({ kind: "user" as const, text: "x" }),
      makeEvent({
        kind: "skipped" as const,
        text: "Change skipped — prototype left unchanged",
      }),
    ])
    expect(count(html, 'data-testid="da-activity-skipped"')).toBe(1)
    expect(html).toContain("Change skipped — prototype left unchanged")
    expect(html).toContain("da-activity-terminal--skipped")
    // No live line after a terminal.
    expect(html).not.toContain('data-testid="da-activity-live"')
  })

  it("test_error_renders_one_error_chip_with_alert: error → a single error chip with role=alert", () => {
    const html = render([
      makeEvent({ kind: "user" as const, text: "x" }),
      makeEvent({ kind: "error" as const, text: "Something went wrong" }),
    ])
    expect(count(html, 'data-testid="da-activity-error"')).toBe(1)
    expect(html).toContain('role="alert"')
    expect(html).toContain("Something went wrong")
    expect(html).not.toContain('data-testid="da-activity-live"')
  })

  it("test_terminal_wins_over_steps: a terminal after several steps shows the chip, not the steps", () => {
    const html = render([
      makeEvent({ kind: "user" as const, text: "x" }),
      makeEvent({ kind: "step" as const, text: "Reading", state: "done" as const }),
      makeEvent({ kind: "step" as const, text: "Applying", state: "done" as const }),
      makeEvent({ kind: "done" as const, text: "All set." }),
    ])
    expect(html).toContain('data-testid="da-activity-done"')
    expect(html).not.toContain("Reading")
    expect(html).not.toContain("Applying")
    expect(html).not.toContain('data-testid="da-activity-live"')
  })
})

// ---------------------------------------------------------------------------
// Pending question → frozen "waiting" line (card mounts separately in the host)
// ---------------------------------------------------------------------------

describe("IterateActivityStream — pending question waiting state", () => {
  it("test_pending_question_renders_waiting_line_no_spinner: a question with no terminal after it shows the waiting line, not a spinner", () => {
    const html = render([
      makeEvent({ kind: "user" as const, text: "make it pop" }),
      makeEvent({ kind: "step" as const, text: "Analyzing", state: "done" as const }),
      makeEvent({ kind: "question" as const, question: "Which accent — coral or green?" }),
    ])
    expect(html).toContain('data-testid="da-activity-waiting"')
    expect(html).toContain("Waiting for your answer…")
    // Frozen: no live spinner line while waiting.
    expect(html).not.toContain('data-testid="da-activity-live"')
    // The user bubble still renders.
    expect(html).toContain('data-testid="da-activity-user"')
  })

  it("test_answered_question_then_terminal_shows_chip_not_waiting: once a terminal follows the question, the chip wins over the waiting line", () => {
    const html = render([
      makeEvent({ kind: "user" as const, text: "make it pop" }),
      makeEvent({ kind: "question" as const, question: "Which accent?" }),
      makeEvent({ kind: "step" as const, text: "Applying", state: "done" as const }),
      makeEvent({ kind: "done" as const, text: "Bumped the accent to green." }),
    ])
    expect(html).toContain('data-testid="da-activity-done"')
    expect(html).not.toContain('data-testid="da-activity-waiting"')
  })
})

// ---------------------------------------------------------------------------
// Author + relative-timestamp labels (the named-turn helper — unchanged)
// ---------------------------------------------------------------------------

describe("IterateActivityStream — named turns (author + relative timestamp)", () => {
  const NOW = 1_700_000_000_000
  // 5 minutes earlier → shortRelativeTime → "5m".
  const FIVE_MIN_AGO = NOW - 5 * 60 * 1000

  it("turnLabel: a user turn is labelled '{userName} · {ago}'", () => {
    expect(turnLabel("user", FIVE_MIN_AGO, "Ada Lovelace", NOW)).toBe("Ada Lovelace · 5m")
  })

  it("turnLabel: a user turn falls back to 'You' when userName is null", () => {
    expect(turnLabel("user", FIVE_MIN_AGO, null, NOW)).toBe("You · 5m")
  })

  it("turnLabel: an agent turn is labelled 'Design Agent · {ago}'", () => {
    expect(turnLabel("done", FIVE_MIN_AGO, "Ada", NOW)).toBe("Design Agent · 5m")
  })

  it("turnLabel: omits the time suffix when createdAt is missing", () => {
    expect(turnLabel("user", undefined, "Ada", NOW)).toBe("Ada")
  })

  it("test_done_turn_renders_design_agent_label: the done chip carries the 'Design Agent' label", () => {
    const html = render([
      makeEvent({ kind: "done" as const, text: "Made the hero blue." }, FIVE_MIN_AGO),
    ])
    expect(html).toContain('class="da-activity-agent-label"')
    expect(html).toContain("Design Agent")
  })
})

// ---------------------------------------------------------------------------
// Source-marker guard
// ---------------------------------------------------------------------------

describe("IterateActivityStream — source marker guard", () => {
  it("the source file carries no throwaway exploration marker (test_source_carries_no_throwaway_marker)", () => {
    const source = readFileSync(ACTIVITY_STREAM_PATH, "utf8")
    expect(source).not.toContain("UX-EXPLORE")
  })
})
