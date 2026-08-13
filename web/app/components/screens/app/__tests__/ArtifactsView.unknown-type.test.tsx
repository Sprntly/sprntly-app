// @vitest-environment jsdom
//
// THE LIBRARY MUST SURVIVE AN ARTIFACT TYPE THIS BUNDLE HAS NEVER HEARD OF.
//
// The two sides of this feature deploy separately: a backend that starts
// listing a new type is live minutes before the web bundle that renders it,
// and a user sitting on a cached bundle can be behind for much longer. Before
// this guard, `ARTIFACT_BADGE[a.type].bg` was an unguarded dereference on a
// fixed-key Record — one unknown row threw during render, and with no
// ErrorBoundary near this screen that took the WHOLE library down: every PRD,
// prototype, evidence brief and report with it.
//
// These tests use a deliberately fictional type, so they keep testing the
// unknown-type path forever rather than accidentally testing a real one.
import * as React from "react"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ArtifactsView } from "../ArtifactsScreen"
import type { ArtifactItem } from "../../../../lib/api"

// A shape the client has no branch for — what a newer server would send.
const FROM_THE_FUTURE = {
  type: "something_not_invented_yet",
  id: 99,
  title: "A thing from a newer server",
  status: "ready",
  created_at: new Date().toISOString(),
  source: {},
  open: {},
} as unknown as ArtifactItem

const PRD: ArtifactItem = {
  type: "prd", id: 1, title: "Handoff Threshold PRD", status: "ready",
  created_at: new Date().toISOString(),
  source: { brief_id: 10, week_label: "Week of May 20", insight_index: 0 },
  open: { brief_id: 10, insight_index: 0, prd_id: 1 },
}

const noop = () => {}

afterEach(cleanup)

describe("an artifact type the bundle does not know", () => {
  it("does not crash the library, and the known rows still render", () => {
    const { container } = render(
      <ArtifactsView
        items={[PRD, FROM_THE_FUTURE]}
        filter="all"
        loading={false}
        onFilterChange={noop}
        onOpen={noop}
      />,
    )
    // The whole point: everything else is still on screen.
    expect(container.textContent).toContain("Handoff Threshold PRD")
    expect(container.textContent).toContain("A thing from a newer server")
  })

  it("renders it as a plain document rather than inventing a label", () => {
    const { container } = render(
      <ArtifactsView
        items={[FROM_THE_FUTURE]}
        filter="all"
        loading={false}
        onFilterChange={noop}
        onOpen={noop}
      />,
    )
    expect(container.textContent).toContain("DOC")
  })

  it("does not offer to open something it cannot open", () => {
    // There is no handler for an unknown type, so a click would either do
    // nothing (a dead affordance) or throw. The row is inert instead.
    const onOpen = vi.fn()
    const { container } = render(
      <ArtifactsView
        items={[FROM_THE_FUTURE]}
        filter="all"
        loading={false}
        onFilterChange={noop}
        onOpen={onOpen}
      />,
    )
    const row = container.querySelector('[data-artifact-type="something_not_invented_yet"]')!
    expect(row.getAttribute("data-clickable")).toBe("false")
    fireEvent.click(row)
    expect(onOpen).not.toHaveBeenCalled()
  })
})
