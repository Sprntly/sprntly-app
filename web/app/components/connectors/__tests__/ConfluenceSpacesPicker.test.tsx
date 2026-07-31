// Same node-env SSR pattern as the other connector component tests.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import type { ConfluenceSpace } from "../../../lib/api"
import { ConfluenceSpacesPickerView } from "../ConfluenceSpacesPicker"

const SPACES: ConfluenceSpace[] = [
  { id: "1", key: "ENG", name: "Engineering", type: "global" },
  { id: "2", key: "PROD", name: "Product", type: "global" },
]

const noop = () => {}

type Props = React.ComponentProps<typeof ConfluenceSpacesPickerView>

function render(override: Partial<Props> = {}): string {
  const defaults: Props = {
    spaces: SPACES,
    loading: false,
    error: null,
    selectedIds: new Set<string>(),
    savedCount: 0,
    isSaving: false,
    onToggle: noop,
    onSave: noop,
  }
  return renderToStaticMarkup(
    React.createElement(ConfluenceSpacesPickerView, { ...defaults, ...override }),
  )
}

describe("ConfluenceSpacesPickerView", () => {
  it("renders a checkbox per space, labelled name + key", () => {
    const html = render()
    expect(html).toContain("Engineering (ENG)")
    expect(html).toContain("Product (PROD)")
    expect(html.match(/type="checkbox"/g)).toHaveLength(2)
  })

  it("falls back to the key when a space has no name", () => {
    const html = render({
      spaces: [{ id: "9", key: "OPS", name: null, type: "global" }],
    })
    expect(html).toContain("OPS")
  })

  it("explains the no-selection default (every readable space)", () => {
    const html = render()
    expect(html).toContain("Spaces to sync")
    expect(html).toContain("every space the connected account can read is synced")
  })

  it("states that coverage is bounded by the connecting user's permissions", () => {
    // The single most support-ticket-preventing sentence in the feature:
    // 3LO acts as that person, so "Sprntly is missing our X docs" is almost
    // always a Confluence permission, not a bug.
    const html = render()
    expect(html).toContain(
      "Sprntly sees exactly what the person who connected Confluence can see",
    )
    expect(html).toContain("page restrictions still apply")
  })

  it("ticks the checkboxes for selected space ids", () => {
    const html = render({ selectedIds: new Set(["2"]) })
    expect(html.match(/checked/g)).toHaveLength(1)
  })

  it("shows how many spaces the persisted selection has", () => {
    const html = render({ savedCount: 3 })
    expect(html).toMatch(/Syncing\s*<strong>3<\/strong>\s*spaces/)
  })

  it("uses the singular for a one-space selection", () => {
    const html = render({ savedCount: 1 })
    expect(html).toMatch(/Syncing\s*<strong>1<\/strong>\s*space</)
  })

  it("hides the saved line when nothing is persisted (sync-everything default)", () => {
    const html = render({ savedCount: 0 })
    expect(html).not.toContain("Syncing")
  })

  it("shows 'Saving…' and disables the button while a save is in flight", () => {
    const html = render({ isSaving: true })
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Saving…<\/button>/)
  })

  it("renders an empty-state hint pointing at Confluence permissions", () => {
    const html = render({ spaces: [] })
    expect(html).toContain("No spaces visible")
    expect(html).toContain("permissions")
  })

  it("renders a loading hint while spaces are being fetched", () => {
    const html = render({ spaces: [], loading: true })
    expect(html).toContain("Loading spaces…")
  })

  it("surfaces an error message as an alert when one is set", () => {
    const html = render({
      spaces: [],
      error: "Only admins can change the spaces Sprntly syncs.",
    })
    expect(html).toContain("Only admins can change")
    expect(html).toContain('role="alert"')
  })
})
