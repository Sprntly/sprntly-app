// @vitest-environment jsdom
//
// The Documents section of the Artifacts library — team documents of any kind.
//
// What these pin is the behaviour that is specific to a document rather than
// to the rows around it: the KIND leads the source line (because "leadership
// update" vs "postmortem" is the real type, while the badge only says DOC), an
// unnamed document says so instead of rendering a blank line, a document still
// being written is not clickable, and — the one that matters for a shared
// library — an unrecognised kind renders as itself rather than being matched
// against a list that does not exist.
import * as React from "react"
import { cleanup, fireEvent, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { ArtifactsView } from "../ArtifactsScreen"
import type { ArtifactItem } from "../../../../lib/api"

function doc(over: Partial<Extract<ArtifactItem, { type: "custom_artifact" }>> = {}) {
  const now = new Date().toISOString()
  return {
    type: "custom_artifact",
    id: 7,
    title: "Q3 reliability update",
    status: "ready",
    created_at: now,
    updated_at: now,
    born_at: now,
    kind: "leadership update",
    source: { kind: "leadership update", conversation_id: null, conversation_title: null },
    open: { custom_artifact_id: 7 },
    ...over,
  } as ArtifactItem
}

const noop = () => {}

function view(items: ArtifactItem[], onOpen = noop, filter: "all" | "custom_artifact" = "all") {
  return render(
    <ArtifactsView
      items={items}
      filter={filter}
      loading={false}
      onFilterChange={noop}
      onOpen={onOpen}
    />,
  )
}

afterEach(cleanup)

describe("Documents / custom artifacts", () => {
  it("offers a Documents filter chip", () => {
    // Named after the thing, not after what it is NOT. "Others" asked people to
    // find a leadership update by elimination, and a filter named that way is a
    // filter people report as missing.
    const { container } = view([doc()])
    const chip = container.querySelector('[data-filter="custom_artifact"]')
    expect(chip).not.toBeNull()
    expect(chip?.textContent).toBe("Documents")
  })

  it("filters to only documents when Documents is selected", () => {
    const prd: ArtifactItem = {
      type: "prd", id: 1, title: "A PRD", status: "ready",
      created_at: new Date().toISOString(),
      source: { brief_id: 1, week_label: null, insight_index: 0 },
      open: { brief_id: 1, insight_index: 0, prd_id: 1 },
    }
    const { container } = view([prd, doc()], noop, "custom_artifact")
    expect(container.textContent).toContain("Q3 reliability update")
    expect(container.textContent).not.toContain("A PRD")
  })

  it("leads the source line with the document's own kind", () => {
    const { container } = view([doc({ kind: "leadership update" })])
    expect(container.textContent).toContain("leadership update")
  })

  it("renders an unheard-of kind as itself", () => {
    // `kind` is FREE TEXT — there is no list of kinds, by design. A row that
    // only knew a fixed vocabulary would render nothing (or a wrong label) for
    // the first document type someone invents, which is the whole promise of
    // the feature.
    const { container } = view([doc({ kind: "incident retro for the board" })])
    expect(container.textContent).toContain("incident retro for the board")
  })

  it("names an untitled document instead of leaving a blank line", () => {
    // A document is named by being typed into, so it is legitimately untitled
    // until then — the state a new Google Doc sits in.
    const { container } = view([doc({ title: "" })])
    expect(container.textContent).toContain("Untitled document")
  })

  it("labels the timestamp as an edit, not a creation", () => {
    // This row's time is the LAST EDIT. An unlabelled date beside a document
    // reads as when it was created, which for a living document is wrong most
    // of the time.
    const { container } = view([doc()])
    expect(container.textContent).toContain("Edited")
  })

  it("names the chat a document was born in, and omits it when that chat is gone", () => {
    const { container } = view([
      doc({ source: { kind: "memo", conversation_id: 4, conversation_title: "Board prep" } }),
    ])
    expect(container.textContent).toContain("from Board prep")

    cleanup()
    // `on delete set null` leaves the id but no row: no title resolved.
    const gone = view([
      doc({ source: { kind: "memo", conversation_id: 99, conversation_title: null } }),
    ])
    expect(gone.container.textContent).not.toContain("from")
  })

  it("opens a ready document", () => {
    const onOpen = vi.fn()
    const { container } = view([doc()], onOpen)
    const row = container.querySelector('[data-artifact-type="custom_artifact"]')!
    expect(row.getAttribute("data-clickable")).toBe("true")
    fireEvent.click(row)
    expect(onOpen).toHaveBeenCalledTimes(1)
  })

  it("does not open a document that is still being written", () => {
    // A generation exists as a row BEFORE its content does. Opening it would
    // show an empty page that looks like a broken document rather than one
    // that is not finished — so the row says "Writing" and refuses the click,
    // the same treatment a building prototype gets.
    const onOpen = vi.fn()
    const { container } = view([doc({ status: "generating", title: "" })], onOpen)
    const row = container.querySelector('[data-artifact-type="custom_artifact"]')!
    expect(container.textContent).toContain("Writing")
    expect(row.getAttribute("data-clickable")).toBe("false")
    fireEvent.click(row)
    expect(onOpen).not.toHaveBeenCalled()
  })
})
