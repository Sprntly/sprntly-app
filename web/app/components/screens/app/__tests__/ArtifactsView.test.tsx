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
  source: { prd_id: 1, prd_title: "Handoff Threshold PRD" },
  open: { prototype_id: 2, prd_id: 1 },
  is_complete: true,
  preview_image_url: "https://cdn.example.com/proto-2.png",
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

  it("renders a row per artifact with a type badge", () => {
    const html = markup()
    expect(html).toContain("Handoff Threshold PRD")
    expect(html).toContain("Day-30 Retention Evidence")
    expect(html).toContain(">PRD<")
    expect(html).toContain(">PROTOTYPE<")
    expect(html).toContain(">EVIDENCE<")
  })

  it("renders the source/meta line per type", () => {
    const html = markup()
    // prd/evidence → "from Brief <week_label>"
    expect(html).toContain("from Brief Week of May 20")
    // prototype → "from PRD <title>"
    expect(html).toContain("from PRD Handoff Threshold PRD")
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
    expect(html).toContain(">PROTOTYPE<")
    expect(html).not.toContain(">EVIDENCE<")
  })

  it("renders only evidence when filter=evidence", () => {
    const html = markup({ filter: "evidence" })
    expect(html).toContain("Day-30 Retention Evidence")
    expect(html).not.toContain(">PRD<")
  })
})

describe("ArtifactsView — prototype card states", () => {
  const BUILDING: ArtifactItem = {
    type: "prototype",
    id: 4,
    title: "Generating PRD",
    status: "generating",
    created_at: new Date().toISOString(),
    source: { prd_id: 5, prd_title: "Generating PRD" },
    open: { prototype_id: 4, prd_id: 5 },
    is_complete: false,
    preview_image_url: null,
  }
  const READY_COMPLETE: ArtifactItem = {
    type: "prototype",
    id: 6,
    title: "Done PRD",
    status: "ready",
    created_at: new Date().toISOString(),
    source: { prd_id: 7, prd_title: "Done PRD" },
    open: { prototype_id: 6, prd_id: 7 },
    is_complete: true,
    preview_image_url: "https://cdn.example.com/proto-6.png",
  }
  const READY_DRAFT: ArtifactItem = {
    type: "prototype",
    id: 8,
    title: "Draft PRD",
    status: "ready",
    created_at: new Date().toISOString(),
    source: { prd_id: 9, prd_title: "Draft PRD" },
    open: { prototype_id: 8, prd_id: 9 },
    is_complete: false,
    preview_image_url: "https://cdn.example.com/proto-8.png",
  }
  const READY_NO_PREVIEW: ArtifactItem = {
    ...READY_COMPLETE,
    id: 10,
    open: { prototype_id: 10, prd_id: 7 },
    preview_image_url: null,
  }

  it("(a) generating → 'Building' label + shimmer present + NOT clickable", () => {
    const onOpen = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: [BUILDING], filter: "all", loading: false, onFilterChange: noop, onOpen,
      }),
    )
    const row = container.querySelector('[data-artifact-type="prototype"]') as HTMLDivElement
    expect(row.textContent).toContain("Building")
    expect(row.textContent).not.toContain("Completed")
    // shimmer placeholder rendered over the image slot
    expect(container.querySelector('[data-proto-thumb="building"]')).not.toBeNull()
    expect(container.querySelector('[data-proto-shimmer]')).not.toBeNull()
    // not clickable: marked non-clickable and clicking fires nothing
    expect(row.getAttribute("data-clickable")).toBe("false")
    expect(row.getAttribute("role")).toBeNull()
    fireEvent.click(row)
    expect(onOpen).not.toHaveBeenCalled()
  })

  it("(b) ready + is_complete → 'Completed', clickable, real preview image", () => {
    const onOpen = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: [READY_COMPLETE], filter: "all", loading: false, onFilterChange: noop, onOpen,
      }),
    )
    const row = container.querySelector('[data-artifact-type="prototype"]') as HTMLDivElement
    expect(row.textContent).toContain("Completed")
    expect(row.getAttribute("data-clickable")).toBe("true")
    const img = container.querySelector('[data-proto-thumb="image"] img') as HTMLImageElement
    expect(img).not.toBeNull()
    expect(img.getAttribute("src")).toBe("https://cdn.example.com/proto-6.png")
    fireEvent.click(row)
    expect(onOpen).toHaveBeenCalledWith(READY_COMPLETE)
  })

  it("(c) ready + !is_complete → 'Draft', clickable", () => {
    const onOpen = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: [READY_DRAFT], filter: "all", loading: false, onFilterChange: noop, onOpen,
      }),
    )
    const row = container.querySelector('[data-artifact-type="prototype"]') as HTMLDivElement
    expect(row.textContent).toContain("Draft")
    expect(row.getAttribute("data-clickable")).toBe("true")
    fireEvent.click(row)
    expect(onOpen).toHaveBeenCalledWith(READY_DRAFT)
  })

  it("(d) ready + null preview → SVG glyph fallback (no img)", () => {
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: [READY_NO_PREVIEW], filter: "all", loading: false, onFilterChange: noop, onOpen: noop,
      }),
    )
    expect(container.querySelector('[data-proto-thumb="fallback"]')).not.toBeNull()
    expect(container.querySelector('[data-proto-thumb="fallback"] svg')).not.toBeNull()
    expect(container.querySelector('[data-proto-thumb="image"]')).toBeNull()
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
