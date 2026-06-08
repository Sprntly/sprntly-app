// View tests for onboarding step 04 — "Share your business context."
// renderToStaticMarkup pattern (node-env, no jsdom, no hooks): the stateful
// container wires hooks, while ContextUploadView is pure and renders to
// static markup directly. buildPastedContextBody is a pure helper tested
// alongside it.
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"

;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import {
  ContextUploadView,
  buildPastedContextBody,
  type ContextUploadViewProps,
  type StagedDoc,
} from "../Onboarding4"
import type { UploadFilesResponse } from "../../../../lib/api"

function noop() {}

function render(override: Partial<ContextUploadViewProps> = {}): string {
  const defaults: ContextUploadViewProps = {
    staged: [],
    pastedText: "",
    links: "",
    uploading: false,
    error: null,
    result: null,
    dragging: false,
    hasAnything: false,
    onPickFiles: noop,
    onRemoveStaged: noop,
    onChangePastedText: noop,
    onChangeLinks: noop,
    onDragStateChange: noop,
    onDrop: noop,
  }
  return renderToStaticMarkup(
    React.createElement(ContextUploadView, { ...defaults, ...override }),
  )
}

describe("ContextUploadView — upload area", () => {
  it("renders a drag-and-drop file area with the supported extensions", () => {
    const html = render()
    expect(html).toContain("ob-ctx-dropzone")
    expect(html).toContain("drag-and-drop")
    expect(html).toContain(".pdf")
    expect(html).toContain(".docx")
    // accepts the corpus-supported formats on the input
    expect(html).toMatch(/accept="[^"]*\.pdf/)
  })

  it("offers the documents, paste-text, and links inputs", () => {
    const html = render()
    expect(html).toContain("Documents")
    expect(html).toContain("Paste context")
    expect(html).toContain("Links")
    // two optional textareas (paste + links)
    expect((html.match(/<textarea/g) ?? []).length).toBe(2)
  })

  it("shows the uploading state on the dropzone", () => {
    expect(render({ uploading: true })).toContain("Uploading…")
  })
})

describe("ContextUploadView — staged + results", () => {
  it("lists staged documents with a remove control each", () => {
    const staged: StagedDoc[] = [
      { id: "a-1-0", name: "strategy.pdf", size: 1 },
      { id: "b-2-1", name: "deck.pptx", size: 2 },
    ]
    const html = render({ staged })
    expect(html).toContain("strategy.pdf")
    expect(html).toContain("deck.pptx")
    expect((html.match(/Remove [^<]+/g) ?? []).length).toBeGreaterThanOrEqual(2)
  })

  it("surfaces ingest results (ok + error rows)", () => {
    const result: UploadFilesResponse = {
      slug: "acme",
      ingested: [{ filename: "strategy.pdf", md_path: "x", md_chars: 10 }],
      errors: [{ filename: "broken.xyz", error: "unsupported" }],
    }
    const html = render({ result })
    expect(html).toContain("strategy.pdf")
    expect(html).toContain("broken.xyz")
    expect(html).toContain("unsupported")
  })

  it("renders an error banner when error is set", () => {
    const html = render({ error: "Upload failed" })
    expect(html).toContain("Upload failed")
    expect(html).toContain('role="alert"')
  })
})

describe("buildPastedContextBody — paste/links → corpus markdown", () => {
  it("returns null when there's nothing to send (skip path)", () => {
    expect(buildPastedContextBody("", "")).toBeNull()
    expect(buildPastedContextBody("   ", "\n  \n")).toBeNull()
  })

  it("builds markdown from pasted text without a links section", () => {
    const text = buildPastedContextBody("we serve clinicians", "")
    expect(text).not.toBeNull()
    expect(text).toContain("## Pasted notes")
    expect(text).toContain("we serve clinicians")
    expect(text).not.toContain("## Links")
  })

  it("includes links as a bulleted list and drops blank lines", () => {
    const text = buildPastedContextBody("", "https://a.com\n\n  https://b.com  \n")
    expect(text).toContain("## Links")
    expect(text).toContain("- https://a.com")
    expect(text).toContain("- https://b.com")
    expect(text).not.toContain("## Pasted notes")
  })
})
