// View tests for the "upload your own documents" modal.
// Same node-env SSR pattern as the other connector modal tests
// (ApiKeyPromptModal / CredentialsPromptModal).
import * as React from "react"
import { renderToStaticMarkup } from "react-dom/server"
import { describe, expect, it } from "vitest"
;(globalThis as typeof globalThis & { React?: typeof React }).React = React

import { UploadSourceModalView } from "../UploadSourceModal"

function noop() {}

function file(name: string): File {
  return { name } as File
}

function render(
  override: Partial<React.ComponentProps<typeof UploadSourceModalView>> = {},
): string {
  const defaults: React.ComponentProps<typeof UploadSourceModalView> = {
    open: true,
    name: "",
    description: "",
    files: [],
    submitting: false,
    error: null,
    onNameChange: noop,
    onDescriptionChange: noop,
    onFilesChange: noop,
    onSubmit: noop,
    onClose: noop,
  }
  return renderToStaticMarkup(
    React.createElement(UploadSourceModalView, { ...defaults, ...override }),
  )
}

describe("UploadSourceModalView", () => {
  it("renders nothing when closed", () => {
    expect(render({ open: false })).toBe("")
  })

  it("asks for a name and an OPTIONAL description", () => {
    const html = render()
    expect(html).toContain("Source name")
    expect(html).toContain("What are these documents?")
    expect(html).toContain("(optional)")
  })

  it("explains that the description reaches the agents", () => {
    expect(render()).toContain("Your agents read this alongside the documents")
  })

  it("accepts any file type (no accept filter on the input)", () => {
    const html = render()
    expect(html).toContain("Any file type")
    expect(html).not.toContain("accept=")
  })

  it("disables Add source until a name AND at least one file are present", () => {
    expect(render()).toContain("disabled=\"\"")
    expect(render({ name: "Research" })).toContain("disabled=\"\"")
    expect(render({ files: [file("a.pdf")] })).toContain("disabled=\"\"")
    const ready = render({ name: "Research", files: [file("a.pdf")] })
    expect(ready).toContain("Add source")
    expect(ready).not.toContain("disabled=\"\"")
  })

  it("lists the picked files and their count", () => {
    const html = render({ files: [file("a.pdf"), file("b.csv")] })
    expect(html).toContain("2 files selected")
    expect(html).toContain("a.pdf")
    expect(html).toContain("b.csv")
  })

  it("shows a busy label while uploading", () => {
    const html = render({ name: "R", files: [file("a.pdf")], submitting: true })
    expect(html).toContain("Uploading…")
  })

  it("surfaces an inline error", () => {
    expect(render({ error: "File exceeds 20MB limit" })).toContain(
      "File exceeds 20MB limit",
    )
  })

  it("hides the name/description step when adding to an existing source", () => {
    const html = render({ addingToSourceName: "Q3 interviews" })
    expect(html).toContain("Add documents to Q3 interviews")
    expect(html).not.toContain("Source name")
    // Files alone are enough here — the source is already named.
    const ready = render({
      addingToSourceName: "Q3 interviews",
      files: [file("a.pdf")],
    })
    expect(ready).toContain("Add documents")
    expect(ready).not.toContain("disabled=\"\"")
  })
})
