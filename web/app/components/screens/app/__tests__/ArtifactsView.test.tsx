// @vitest-environment jsdom
//
// Tests for the Artifacts tab's presentational list (`ArtifactsView`), the
// pure surface extracted from ChatsScreen so it is testable without the app's
// context stack — same View-export pattern as SlackChannelPickerView /
// LabCodeChatView, plus a jsdom interaction pass for clicks (filter chips +
// row open), mirroring the *.dom.test.tsx files in this repo.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ArtifactsView } from "../ChatsScreen"
import type { ArtifactItem } from "../../../../lib/api"

const PRD: ArtifactItem = {
  type: "prd",
  id: 1,
  title: "Handoff Threshold PRD",
  status: "ready",
  created_at: new Date().toISOString(),
  source: { brief_id: 10, week_label: "Week of May 20", insight_index: 0 },
  open: { brief_id: 10, insight_index: 0, prd_id: 1 },
}
const PROTO: ArtifactItem = {
  type: "prototype",
  id: 2,
  title: "Handoff Threshold PRD", // derived from parent PRD
  status: "ready",
  created_at: new Date().toISOString(),
  preview_image_url: "https://cdn/proto-thumb.png",
  source: { prd_id: 1, prd_title: "Handoff Threshold PRD" },
  open: { prototype_id: 2, prd_id: 1 },
}
const PROTO_NO_PREVIEW: ArtifactItem = {
  type: "prototype",
  id: 4,
  title: "No-thumbnail Prototype",
  status: "ready",
  created_at: new Date().toISOString(),
  preview_image_url: null,
  source: { prd_id: 1, prd_title: "Handoff Threshold PRD" },
  open: { prototype_id: 4, prd_id: 1 },
}
const EVIDENCE: ArtifactItem = {
  type: "evidence",
  id: 3,
  title: "Day-30 Retention Evidence",
  status: "ready",
  created_at: new Date().toISOString(),
  source: { brief_id: 10, week_label: "Week of May 20", insight_index: 1 },
  open: { brief_id: 10, insight_index: 1, evidence_id: 3 },
}
const ITEMS = [PROTO, PRD, EVIDENCE]

const noop = () => {}

type Props = React.ComponentProps<typeof ArtifactsView>

function markup(override: Partial<Props> = {}): string {
  const defaults: Props = {
    items: ITEMS,
    filter: "all",
    loading: false,
    onFilterChange: noop,
    onOpen: noop,
  }
  return renderToStaticMarkup(React.createElement(ArtifactsView, { ...defaults, ...override }))
}

afterEach(cleanup)

describe("ArtifactsView — chrome", () => {
  it("renders all four filter chips", () => {
    const html = markup()
    expect(html).toContain("All")
    expect(html).toContain("PRDs")
    expect(html).toContain("Prototypes")
    expect(html).toContain("Evidence")
  })

  it("renders a row per artifact with a type badge (prd + evidence)", () => {
    const html = markup()
    expect(html).toContain("Handoff Threshold PRD")
    expect(html).toContain("Day-30 Retention Evidence")
    expect(html).toContain(">PRD<")
    expect(html).toContain(">EVIDENCE<")
  })

  it("renders the source/meta line for prd/evidence rows", () => {
    const html = markup()
    // prd/evidence → "from Brief <week_label>"
    expect(html).toContain("from Brief Week of May 20")
  })

  it("shows the empty state when there are no artifacts", () => {
    const html = markup({ items: [] })
    expect(html).toContain("No artifacts yet")
    expect(html.toLowerCase()).toContain("generate a prd")
  })

  it("shows a loading skeleton (no empty state) while loading", () => {
    const html = markup({ items: [], loading: true })
    expect(html).not.toContain("No artifacts yet")
    expect(html).toContain("chats-pulse")
  })
})

describe("ArtifactsView — filtering (client-side by type)", () => {
  it("renders only PRDs when filter=prd", () => {
    const html = markup({ filter: "prd" })
    expect(html).toContain("Handoff Threshold PRD")
    expect(html).not.toContain("Day-30 Retention Evidence")
    expect(html).not.toContain(">PROTOTYPE<")
  })

  it("renders only prototypes when filter=prototype", () => {
    const html = markup({ filter: "prototype" })
    // prototype-with-preview renders an image card (no PROTOTYPE badge)
    expect(html).toContain('class="fc-preview-img"')
    expect(html).not.toContain(">EVIDENCE<")
    expect(html).not.toContain(">PRD<")
  })

  it("renders only evidence when filter=evidence", () => {
    const html = markup({ filter: "evidence" })
    expect(html).toContain("Day-30 Retention Evidence")
    expect(html).not.toContain(">PRD<")
  })
})

describe("ArtifactsView — interaction (jsdom)", () => {
  it("fires onFilterChange with the chosen filter id", () => {
    const onFilterChange = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: ITEMS, filter: "all", loading: false, onFilterChange, onOpen: noop,
      }),
    )
    const prdChip = container.querySelector('[data-filter="prototype"]') as HTMLButtonElement
    fireEvent.click(prdChip)
    expect(onFilterChange).toHaveBeenCalledWith("prototype")
  })

  it("fires onOpen with the clicked artifact when a row is clicked", () => {
    const onOpen = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: ITEMS, filter: "all", loading: false, onFilterChange: noop, onOpen,
      }),
    )
    const protoRow = container.querySelector('[data-artifact-type="prototype"]') as HTMLDivElement
    fireEvent.click(protoRow)
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith(PROTO)
  })

  it("does not show rows for a filtered-out type, so its onOpen never fires", () => {
    const onOpen = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: ITEMS, filter: "prd", loading: false, onFilterChange: noop, onOpen,
      }),
    )
    expect(container.querySelector('[data-artifact-type="prototype"]')).toBeNull()
    expect(container.querySelector('[data-artifact-type="prd"]')).not.toBeNull()
  })
})

describe("ArtifactsView — prototype image cards", () => {
  it("renders a prototype WITH preview_image_url as an image card", () => {
    const html = markup({ items: [PROTO] })
    // reuses the shared fc-preview-img class + the real src
    expect(html).toContain('class="fc-preview-img"')
    expect(html).toContain('src="https://cdn/proto-thumb.png"')
    // alt for a11y
    expect(html).toContain('alt="Handoff Threshold PRD"')
    // title still rendered below the thumbnail
    expect(html).toContain("Handoff Threshold PRD")
    // no icon+text badge for the image card
    expect(html).not.toContain(">PROTOTYPE<")
  })

  it("renders the 'Prototype · …' sub-line on the image card", () => {
    const html = markup({ items: [PROTO] })
    expect(html).toContain("Prototype · ")
    // relativeTime of a just-now timestamp
    expect(html).toContain("just now")
  })

  it("renders a PRD row as icon+text (no image card)", () => {
    const html = markup({ items: [PRD] })
    expect(html).toContain(">PRD<")
    expect(html).not.toContain("fc-preview-img")
  })

  it("renders an evidence row as icon+text (no image card)", () => {
    const html = markup({ items: [EVIDENCE] })
    expect(html).toContain(">EVIDENCE<")
    expect(html).not.toContain("fc-preview-img")
  })

  it("falls back to the icon+text row for a prototype with null preview_image_url", () => {
    const html = markup({ items: [PROTO_NO_PREVIEW] })
    // no image card / no <img>
    expect(html).not.toContain("fc-preview-img")
    expect(html).not.toContain("<img")
    // falls back to the badge + source line row
    expect(html).toContain(">PROTOTYPE<")
    expect(html).toContain("from PRD Handoff Threshold PRD")
  })
})
