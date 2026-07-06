// @vitest-environment jsdom
//
// StoryMap renders the Jeff-Patton board over the ticket set, and storyMapSizing
// is the frontend sizing gate that decides whether the map is worth showing
// (≥2 of: >1 activity, >12 tickets, >1 release). Both derive purely from each
// ticket's `activity`/`release` — no batch metadata.
import * as React from "react"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { StoryMap, storyMapSizing } from "../StoryMap"
import type { GeneratedStory } from "../../../lib/api"

afterEach(cleanup)

function t(over: Partial<GeneratedStory>): GeneratedStory {
  return { title: "T", body: "", acceptance_criteria: [], priority: "high", route: null, ...over }
}

// A large feature: 2 activities × 2 releases.
const LARGE: GeneratedStory[] = [
  t({ title: "Create workspace", activity: "Set up", release: "Release 1 — walking skeleton" }),
  t({ title: "Connect tracker", activity: "Set up", release: "Release 1 — walking skeleton" }),
  t({ title: "Invite teammate", activity: "Grow the team", release: "Release 2" }),
  t({ title: "Templates", activity: "Grow the team", release: "Release 2" }),
]

describe("storyMapSizing", () => {
  it("builds the map when ≥2 signals fire (multi-activity + multi-release)", () => {
    const s = storyMapSizing(LARGE)
    expect(s.build).toBe(true)
    expect(s.activities).toEqual(["Set up", "Grow the team"])
    expect(s.reason).toMatch(/built/)
  })

  it("stays flat for a single-activity set", () => {
    const flat = [t({ title: "A", activity: "", release: "" }), t({ title: "B", activity: "", release: "" })]
    const s = storyMapSizing(flat)
    expect(s.build).toBe(false)
    expect(s.reason).toMatch(/not needed/)
  })

  it("orders the walking skeleton first", () => {
    const s = storyMapSizing(LARGE)
    expect(s.releases[0]).toMatch(/walking skeleton/)
  })
})

describe("StoryMap", () => {
  it("renders the backbone activities and release bands with ticket cards", () => {
    const onOpen = vi.fn()
    render(React.createElement(StoryMap, { stories: LARGE, onOpen }))
    // Backbone (activities) + release bands.
    expect(screen.getByText("Set up")).toBeTruthy()
    expect(screen.getByText("Grow the team")).toBeTruthy()
    expect(screen.getByText(/walking skeleton/)).toBeTruthy()
    expect(screen.getByText("Release 2")).toBeTruthy()
    // A card opens the right ticket by its original index.
    fireEvent.click(screen.getByText("Invite teammate"))
    expect(onOpen).toHaveBeenCalledWith(2)
  })
})
