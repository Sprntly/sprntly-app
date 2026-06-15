// Rendering tests for IterateActivityStream — each event kind, the running
// trailing indicator, and the null-on-empty guard.
// Uses renderToStaticMarkup (the repo convention for pure presentational
// components with no hooks/I/O).
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

// ---------------------------------------------------------------------------
// Null / empty
// ---------------------------------------------------------------------------

describe("IterateActivityStream — empty list", () => {
  it("test_empty_activity_renders_null: returns null when activity is empty", () => {
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, { activity: [], running: false }),
    )
    expect(html).toBe("")
  })
})

// ---------------------------------------------------------------------------
// Individual event kinds
// ---------------------------------------------------------------------------

describe("IterateActivityStream — event kinds", () => {
  it("test_user_event_renders_bubble: a user event renders da-activity-user testid", () => {
    const event = makeEvent({ kind: "user" as const, text: "make the hero blue" })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
      }),
    )
    expect(html).toContain('data-testid="da-activity-user"')
    expect(html).toContain("make the hero blue")
  })

  it("test_step_active_renders_spinner: a step with state=active renders da-activity-spinner", () => {
    const event = makeEvent({
      kind: "step" as const,
      text: "Analyzing",
      state: "active" as const,
    })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
      }),
    )
    expect(html).toContain('data-state="active"')
    expect(html).toContain("da-activity-spinner")
  })

  it("test_step_done_renders_check: a step with state=done renders the check mark", () => {
    const event = makeEvent({
      kind: "step" as const,
      text: "Reading",
      state: "done" as const,
    })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
      }),
    )
    expect(html).toContain('data-state="done"')
    expect(html).toContain("✓")
  })

  it("test_question_event_renders_agent-asks-label: a question event renders da-activity-question and the agent-asks label", () => {
    const event = makeEvent({
      kind: "question" as const,
      question: "Which color scheme?",
    })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
      }),
    )
    expect(html).toContain('data-testid="da-activity-question"')
    // The question turn now carries the unified author label ("Design Agent · {ago}")
    // rather than the old static "Design Agent asks" heading.
    expect(html).toContain("Design Agent")
    expect(html).toContain('class="da-activity-agent-label"')
    expect(html).toContain("Which color scheme?")
  })

  it("test_done_event_renders_done-icon: a done event renders da-activity-done", () => {
    const event = makeEvent({ kind: "done" as const, text: "Change applied" })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
      }),
    )
    expect(html).toContain('data-testid="da-activity-done"')
    expect(html).toContain("Change applied")
  })

  it("test_error_event_renders-error: an error event renders da-activity-error with role=alert", () => {
    const event = makeEvent({ kind: "error" as const, text: "Something went wrong" })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
      }),
    )
    expect(html).toContain('data-testid="da-activity-error"')
    expect(html).toContain('role="alert"')
    expect(html).toContain("Something went wrong")
  })
})

// ---------------------------------------------------------------------------
// Running indicator
// ---------------------------------------------------------------------------

describe("IterateActivityStream — running trailing indicator", () => {
  it("test_running_trailing_indicator_shown_when_running_true: running=true shows da-activity-running", () => {
    const event = makeEvent({ kind: "user" as const, text: "hi" })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: true,
      }),
    )
    expect(html).toContain('data-testid="da-activity-running"')
  })

  it("test_no_running_indicator_when_false: running=false hides da-activity-running", () => {
    const event = makeEvent({ kind: "user" as const, text: "hi" })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
      }),
    )
    expect(html).not.toContain('data-testid="da-activity-running"')
  })
})

// ---------------------------------------------------------------------------
// Author + relative-timestamp labels (the named-turn conversation thread)
// ---------------------------------------------------------------------------

describe("IterateActivityStream — named turns (author + relative timestamp)", () => {
  const NOW = 1_700_000_000_000
  // 5 minutes earlier → shortRelativeTime → "5m".
  const FIVE_MIN_AGO = NOW - 5 * 60 * 1000

  it("turnLabel: a user turn is labelled '{userName} · {ago}'", () => {
    expect(turnLabel("user", FIVE_MIN_AGO, "Ada Lovelace", NOW)).toBe(
      "Ada Lovelace · 5m",
    )
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

  it("test_user_turn_renders_author_label: a user turn renders the user-name label", () => {
    const event = makeEvent({ kind: "user" as const, text: "make it blue" }, FIVE_MIN_AGO)
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
        userName: "Ada Lovelace",
      }),
    )
    expect(html).toContain('class="da-activity-agent-label"')
    expect(html).toContain("Ada Lovelace")
  })

  it("test_agent_turn_renders_design_agent_label: an agent (done) turn renders the 'Design Agent' label", () => {
    const event = makeEvent({ kind: "done" as const, text: "Made the hero blue." }, FIVE_MIN_AGO)
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, {
        activity: [event],
        running: false,
        userName: "Ada",
      }),
    )
    expect(html).toContain('class="da-activity-agent-label"')
    expect(html).toContain("Design Agent")
  })
})

// ---------------------------------------------------------------------------
// Done turn shows the agent's summary
// ---------------------------------------------------------------------------

describe("IterateActivityStream — done turn summary", () => {
  it("test_done_turn_renders_summary_text: the done turn renders whatever summary text it carries", () => {
    const event = makeEvent({
      kind: "done" as const,
      text: "Swapped the hero background to brand blue and tightened the spacing.",
    })
    const html = renderToStaticMarkup(
      React.createElement(IterateActivityStream, { activity: [event], running: false }),
    )
    expect(html).toContain('data-testid="da-activity-done"')
    expect(html).toContain("Swapped the hero background to brand blue")
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
