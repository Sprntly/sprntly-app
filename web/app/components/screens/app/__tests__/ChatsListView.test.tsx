// @vitest-environment jsdom
//
// Tests for the chats list's presentational surface (`ChatsListView`), the pure
// component extracted from ChatsScreen so it is testable without the app's
// context stack — same View-export pattern as `ArtifactsView`, plus a jsdom
// interaction pass for clicks (rows, pin/unpin, and the weekly-brief pin).
//
// Focus: the current weekly brief is surfaced as an always-pinned entry at the
// very TOP of the list (above per-conversation pins and all dated groups), it
// links to the brief surface when clicked, and it's absent when there's no
// current brief — without breaking the existing per-conversation pin feature.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ChatsListView, isMirroredBrief, type BriefEntry } from "../ChatsScreen"
import type { ConversationRow } from "../../../../types/content"

// A conversation row, with the private `_pinned` / `_dbId` markers the screen
// attaches before handing rows to the view.
type Row = ConversationRow & { _pinned?: boolean; _dbId?: number }

const now = new Date()
const iso = (d: Date) => d.toISOString()

const TODAY: Row = {
  id: "1",
  title: "Cohort breakdown analytics",
  time: iso(now),
  savedTurn: { id: "1", query: "Break down churn by cohort" },
  _dbId: 1,
}
const PINNED_CONV: Row = {
  id: "2",
  title: "On-call SEV-2 incident",
  time: iso(new Date(now.getTime() - 3 * 86400000)),
  savedTurn: { id: "2", query: "Investigate the latency spike" },
  _pinned: true,
  _dbId: 2,
}
const EARLIER: Row = {
  id: "3",
  title: "OKR planning doc",
  time: iso(new Date(now.getTime() - 30 * 86400000)),
  savedTurn: { id: "3", query: "Draft Q3 OKRs" },
  _dbId: 3,
}

const ROWS: Row[] = [TODAY, PINNED_CONV, EARLIER]

const BRIEF: BriefEntry = {
  id: 42,
  weekLabel: "Week of May 20",
  headline: "Handoff threshold is costing 8% retention.",
  generatedAt: iso(now),
}

const noop = () => {}

type Props = React.ComponentProps<typeof ChatsListView>

function defaults(override: Partial<Props> = {}): Props {
  return {
    rows: ROWS,
    briefEntry: BRIEF,
    onRowClick: noop,
    onPin: noop,
    onDelete: noop,
    onOpenBrief: noop,
    ...override,
  }
}

function markup(override: Partial<Props> = {}): string {
  return renderToStaticMarkup(React.createElement(ChatsListView, defaults(override)))
}

afterEach(cleanup)

describe("ChatsListView — weekly-brief pin (static render)", () => {
  it("renders the weekly-brief entry with its week label + headline when a current brief exists", () => {
    const html = markup()
    // Static markup HTML-escapes the apostrophe ("This week&#x27;s brief"), so
    // match on a stable substring of the title.
    expect(html).toContain("This week")
    expect(html).toContain("brief")
    expect(html).toContain('data-brief-pin="true"')
    expect(html).toContain("Week of May 20")
    expect(html).toContain("Handoff threshold is costing 8% retention.")
    // It carries the PM agent pill, like brief-derived rows.
    expect(html).toContain(">PM AGENT<")
  })

  it("renders the brief entry inside the Pinned group", () => {
    const html = markup()
    // The Pinned header must be present (the brief alone forces it on).
    expect(html).toContain(">Pinned<")
  })

  it("omits the brief entry entirely when there's no current brief", () => {
    const html = markup({ briefEntry: null })
    expect(html).not.toContain("This week's brief")
    expect(html).not.toContain("Handoff threshold is costing 8% retention.")
  })

  it("still shows the Pinned group for a pinned conversation when there's no brief", () => {
    const html = markup({ briefEntry: null })
    // PINNED_CONV is _pinned, so the Pinned group still renders.
    expect(html).toContain(">Pinned<")
    expect(html).toContain("On-call SEV-2 incident")
  })

  it("does not render a Pinned group when there's neither a brief nor a pinned conversation", () => {
    const html = markup({ briefEntry: null, rows: [TODAY, EARLIER] })
    expect(html).not.toContain(">Pinned<")
  })
})

describe("ChatsListView — brief sorts above everything", () => {
  it("places the brief entry before all conversations (pinned + dated)", () => {
    const { container } = render(React.createElement(ChatsListView, defaults()))
    expect(container.querySelector('[data-brief-pin="true"]')).not.toBeNull()

    const html = container.innerHTML
    const briefIdx = html.indexOf("This week's brief")
    expect(briefIdx).toBeGreaterThanOrEqual(0)

    // Every conversation title must appear AFTER the brief pin in the markup.
    for (const title of ["On-call SEV-2 incident", "Cohort breakdown analytics", "OKR planning doc"]) {
      expect(html.indexOf(title)).toBeGreaterThan(briefIdx)
    }
  })

  it("renders the brief pin above the pinned conversation within the Pinned group", () => {
    const { container } = render(React.createElement(ChatsListView, defaults()))
    const html = container.innerHTML
    expect(html.indexOf("This week's brief")).toBeLessThan(html.indexOf("On-call SEV-2 incident"))
  })
})

describe("isMirroredBrief — brief de-duplication", () => {
  // A persisted row, with the private markers the screen attaches.
  type DbRow = ConversationRow & { _agentType?: string; _dbId?: number }
  const dbRow = (over: Partial<DbRow>): DbRow => ({
    id: "x",
    title: "Some chat",
    time: iso(now),
    savedTurn: { id: "x", query: "hi" },
    ...over,
  })

  it("suppresses a conversation tagged with the brief agent_type (structural signal)", () => {
    // Even with a totally generic title, the structural signal wins.
    expect(isMirroredBrief(dbRow({ title: "Untitled", _agentType: "brief" }), BRIEF)).toBe(true)
    expect(isMirroredBrief(dbRow({ title: "Untitled", _agentType: "BRIEF" }), BRIEF)).toBe(true)
  })

  it("suppresses a conversation whose title is a canonical brief pin title", () => {
    expect(isMirroredBrief(dbRow({ title: "This week's brief" }), BRIEF)).toBe(true)
    expect(isMirroredBrief(dbRow({ title: "Monday Brief" }), BRIEF)).toBe(true)
    expect(isMirroredBrief(dbRow({ title: "  monday brief  " }), BRIEF)).toBe(true)
  })

  it("suppresses a conversation whose title exactly matches the live brief week label or headline", () => {
    expect(isMirroredBrief(dbRow({ title: "Week of May 20" }), BRIEF)).toBe(true)
    expect(isMirroredBrief(dbRow({ title: "handoff threshold is costing 8% retention." }), BRIEF)).toBe(true)
  })

  it("does NOT suppress a legitimate chat that merely MENTIONS 'brief' (no substring match)", () => {
    expect(isMirroredBrief(dbRow({ title: "Brief me on the latency regression" }), BRIEF)).toBe(false)
    expect(isMirroredBrief(dbRow({ title: "Debrief from the Cerner call" }), BRIEF)).toBe(false)
    expect(isMirroredBrief(dbRow({ title: "Brainstorm a brief PRD" }), BRIEF)).toBe(false)
  })

  it("does NOT match the brief week label / headline as a substring", () => {
    // A title that contains the headline but isn't equal to it must survive.
    expect(
      isMirroredBrief(dbRow({ title: "Follow-up: Handoff threshold is costing 8% retention. — next steps" }), BRIEF),
    ).toBe(false)
  })

  it("returns false for unrelated chats and tolerates a null brief", () => {
    expect(isMirroredBrief(dbRow({ title: "Cohort breakdown analytics" }), BRIEF)).toBe(false)
    expect(isMirroredBrief(dbRow({ title: "Cohort breakdown analytics" }), null)).toBe(false)
    // Pin titles still suppress even without a live brief entry.
    expect(isMirroredBrief(dbRow({ title: "Monday Brief" }), null)).toBe(true)
  })
})

describe("ChatsListView — interaction (jsdom)", () => {
  it("fires onOpenBrief when the brief pin is clicked", () => {
    const onOpenBrief = vi.fn()
    const { container } = render(
      React.createElement(ChatsListView, defaults({ onOpenBrief })),
    )
    const briefPin = container.querySelector('[data-brief-pin="true"]') as HTMLDivElement
    fireEvent.click(briefPin)
    expect(onOpenBrief).toHaveBeenCalledTimes(1)
  })

  it("does not fire onOpenBrief when there's no brief (no pin rendered)", () => {
    const onOpenBrief = vi.fn()
    const { container } = render(
      React.createElement(ChatsListView, defaults({ briefEntry: null, onOpenBrief })),
    )
    expect(container.querySelector('[data-brief-pin="true"]')).toBeNull()
    expect(onOpenBrief).not.toHaveBeenCalled()
  })

  it("keeps the per-conversation pin feature working (onPin fires for a row)", () => {
    const onPin = vi.fn()
    const { container } = render(
      React.createElement(ChatsListView, defaults({ onPin })),
    )
    // The pin toggle buttons carry a Pin/Unpin title.
    const pinBtn = container.querySelector('button[title="Unpin"]') as HTMLButtonElement
    expect(pinBtn).not.toBeNull()
    fireEvent.click(pinBtn)
    expect(onPin).toHaveBeenCalledTimes(1)
    // The brief pin must NOT expose a pin/unpin toggle (it can't be unpinned).
    const briefPin = container.querySelector('[data-brief-pin="true"]') as HTMLDivElement
    expect(briefPin.querySelector('button[title="Unpin"]')).toBeNull()
    expect(briefPin.querySelector('button[title="Pin"]')).toBeNull()
  })

  it("fires onRowClick for a conversation row (and the brief click doesn't count as a row click)", () => {
    const onRowClick = vi.fn()
    const { container } = render(
      React.createElement(ChatsListView, defaults({ onRowClick })),
    )
    const briefPin = container.querySelector('[data-brief-pin="true"]') as HTMLDivElement
    fireEvent.click(briefPin)
    expect(onRowClick).not.toHaveBeenCalled()
  })
})
