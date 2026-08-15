// @vitest-environment jsdom
//
// Tests for ArtifactListCards — the chat's clickable artifact listing.
// Covers the scroll cap added after a 12-PRD answer stretched the thread past
// the composer: past VISIBLE_ROWS the group scrolls INSIDE the card (rows kept,
// not dropped), while short listings stay uncapped. Also pins the row basics
// the cap must not break: every row renders, clicks reach onOpen with the row's
// artifact, and rows refuse to flex-shrink (a squished row instead of a
// scrollbar is the failure mode of a capped flex column).
import * as React from "react"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { ArtifactListCards } from "../ArtifactListCards"
import type { ChatArtifactItem } from "../../../lib/api"

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

function makeItems(n: number): ChatArtifactItem[] {
  return Array.from({ length: n }, (_, i) => ({
    type: "prd",
    id: i + 1,
    title: `PRD number ${i + 1}`,
    status: "ready",
    created_at: "2026-08-01T00:00:00Z",
    brief_anchored: false,
    source: { conversation_id: null, conversation_title: null },
    open: { prd_id: i + 1 },
  } as ChatArtifactItem))
}

const group = () =>
  document.querySelector('[data-testid="artifact-list-cards"]') as HTMLElement
const cards = () =>
  Array.from(document.querySelectorAll('[data-testid="artifact-list-card"]'))

describe("ArtifactListCards", () => {
  it("a short listing renders uncapped — no scroll container for five or fewer", () => {
    render(<ArtifactListCards items={makeItems(5)} onOpen={vi.fn()} />)

    expect(cards()).toHaveLength(5)
    expect(group().style.maxHeight).toBe("")
    expect(group().style.overflowY).toBe("")
  })

  it("a long listing caps the card and scrolls inside it, keeping every row", () => {
    render(<ArtifactListCards items={makeItems(12)} onOpen={vi.fn()} />)

    // All 12 rows exist — the cap is height, never truncation.
    expect(cards()).toHaveLength(12)
    expect(group().style.overflowY).toBe("auto")
    expect(group().style.maxHeight).not.toBe("")
    // The scroller must not chain into the thread's scroll at its ends.
    expect(group().style.overscrollBehavior).toBe("contain")
    // Rows must refuse to shrink — otherwise the flex column compresses them
    // to fit the max height and no scrollbar ever appears.
    for (const c of cards()) {
      expect((c as HTMLElement).style.flexShrink).toBe("0")
    }
  })

  it("a row click opens THAT artifact", () => {
    const onOpen = vi.fn()
    render(<ArtifactListCards items={makeItems(7)} onOpen={onOpen} />)

    fireEvent.click(cards()[6])
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen.mock.calls[0][0].id).toBe(7)
  })
})
