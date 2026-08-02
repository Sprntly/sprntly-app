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

import { ArtifactsView } from "../ArtifactsScreen"
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
// Typed as the report MEMBER (not the union) so the spread-and-override cases
// below narrow to a report instead of a union of every artifact's `source`.
const REPORT: Extract<ArtifactItem, { type: "report" }> = {
  type: "report",
  id: 4,
  title: "Voice of Customer Report · Q2",
  status: "",
  created_at: new Date().toISOString(),
  skill: "voice-of-customer-report",
  share_mode: "private",
  source: {
    skill: "voice-of-customer-report",
    question: "what are customers saying?",
    conversation_id: 77,
    conversation_title: "Q2 customer themes",
    prd_id: null,
    prd_title: null,
  },
  open: { report_id: 4 },
}
const ITEMS = [PROTO, PRD, EVIDENCE, REPORT]

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
  it("renders all five filter chips", () => {
    const html = markup()
    expect(html).toContain("All")
    expect(html).toContain("Reports")
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

  it("(e) ready + preview that fails to load → onError → SVG glyph fallback", () => {
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: [READY_COMPLETE], filter: "all", loading: false, onFilterChange: noop, onOpen: noop,
      }),
    )
    const img = container.querySelector('[data-proto-thumb="image"] img') as HTMLImageElement
    expect(img).not.toBeNull()
    // Simulate the preview URL 404ing (the #354 broken-link case, not just null).
    fireEvent.error(img)
    expect(container.querySelector('[data-proto-thumb="image"]')).toBeNull()
    expect(container.querySelector('[data-proto-thumb="fallback"]')).not.toBeNull()
    expect(container.querySelector('[data-proto-thumb="fallback"] svg')).not.toBeNull()
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

describe("ArtifactsView — reports", () => {
  it("renders a report row with its REPORT badge and kind", () => {
    const html = markup()
    expect(html).toContain("Voice of Customer Report · Q2")
    expect(html).toContain(">REPORT<")
    // The source line leads with the report's KIND, humanised from the skill id.
    expect(html).toContain("Voice of Customer report")
  })

  it("names the chat room a report is attached to", () => {
    const html = markup()
    expect(html).toContain("from Q2 customer themes")
  })

  it("names the PRD a report is attached to", () => {
    const attached: typeof REPORT = {
      ...REPORT,
      source: { ...REPORT.source, prd_id: 1, prd_title: "Checkout revamp" },
    }
    const html = markup({ items: [attached] })
    expect(html).toContain("on PRD Checkout revamp")
  })

  it("omits the attachment entirely when the report stands alone", () => {
    const alone: typeof REPORT = {
      ...REPORT,
      source: {
        ...REPORT.source,
        conversation_id: null, conversation_title: null,
        prd_id: null, prd_title: null,
      },
    }
    const html = markup({ items: [alone] })
    expect(html).toContain("Voice of Customer report")
    expect(html).not.toContain("from ")
    expect(html).not.toContain("on PRD")
  })

  it("shows no label for an attachment whose chat was deleted", () => {
    // `on delete set null` leaves the id but no title — the row must not invent
    // a name, and must still render.
    const orphaned: typeof REPORT = {
      ...REPORT,
      source: { ...REPORT.source, conversation_id: 999, conversation_title: null },
    }
    const html = markup({ items: [orphaned] })
    expect(html).toContain("Voice of Customer Report · Q2")
    expect(html).not.toContain("from ")
  })

  it("renders only reports when filter=report", () => {
    const html = markup({ filter: "report" })
    expect(html).toContain(">REPORT<")
    expect(html).not.toContain(">PRD<")
    expect(html).not.toContain(">EVIDENCE<")
    expect(html).not.toContain(">PROTOTYPE<")
  })

  it("opens a report row on click, passing the report_id", () => {
    const onOpen = vi.fn()
    const { container } = render(
      React.createElement(ArtifactsView, {
        items: [REPORT], filter: "all", loading: false,
        onFilterChange: noop, onOpen,
      }),
    )
    const row = container.querySelector('[data-artifact-type="report"]') as HTMLElement
    expect(row).toBeTruthy()
    fireEvent.click(row)
    expect(onOpen).toHaveBeenCalledTimes(1)
    expect(onOpen.mock.calls[0][0].open).toEqual({ report_id: 4 })
  })
})
