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

import { IterateActivityStream } from "../IterateActivityStream"
import type { ActivityEvent } from "../useIterateRun"

// Sprntly components carry no `import React`; expose it globally.
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

const HERE = dirname(fileURLToPath(import.meta.url))
const ACTIVITY_STREAM_PATH = join(HERE, "..", "IterateActivityStream.tsx")

let _id = 0
function makeEvent<T extends Omit<ActivityEvent, "id">>(e: T): T & { id: number } {
  return { ...e, id: ++_id } as T & { id: number }
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
    expect(html).toContain("Design Agent asks")
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
// Source-marker guard
// ---------------------------------------------------------------------------

describe("IterateActivityStream — source marker guard", () => {
  it("test_no_ux_explore_marker_in_activity_stream: the source file contains no UX-EXPLORE marker", () => {
    const source = readFileSync(ACTIVITY_STREAM_PATH, "utf8")
    expect(source).not.toContain("UX-EXPLORE")
  })
})
