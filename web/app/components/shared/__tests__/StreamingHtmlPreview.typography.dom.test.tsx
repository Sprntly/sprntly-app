// @vitest-environment jsdom
//
// A generating artifact reads in the product's own typography, not the
// browser's.
//
// The defect: an artifact's HTML carries its own `<style>`, and mid-stream that
// block has usually not arrived — so the opening paragraphs of a generating PRD
// rendered as Times on a bare page, then reflowed when the stylesheet landed.
// A base stylesheet is now written into the iframe ahead of any model markup.
//
// The ordering is the part worth pinning: written FIRST, so the artifact's own
// design still wins on source order the moment it exists. This is a floor for
// the unstyled window, never an override of the finished document.
import * as React from "react"
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { StreamingHtmlPreview } from "../StreamingHtmlPreview"

afterEach(cleanup)

const frame = () => document.querySelector("iframe") as HTMLIFrameElement
const docHtml = () => frame().contentDocument?.documentElement.innerHTML ?? ""

function renderPreview(html: string) {
  return render(
    <StreamingHtmlPreview html={html} title="PRD draft (generating)" testId="prd-streaming-preview" />,
  )
}

describe("a generating artifact's typography", () => {
  it("styles the document before a single byte of model markup", () => {
    renderPreview("<p>The first paragraph to arrive.</p>")
    const styles = frame().contentDocument?.querySelectorAll("style") ?? []
    expect(styles.length).toBeGreaterThan(0)
    const base = styles[0].textContent ?? ""
    expect(base).toContain("Geist")
    // Not Times: the whole point is that the body has a font at all.
    expect(base).toMatch(/body\s*\{[^}]*font-family/)
  })

  it("puts the base style AHEAD of the artifact's own, so the artifact still wins", () => {
    renderPreview("<style>body{font-family:'Fancy Serif';}</style><p>Body.</p>")
    const html = docHtml()
    const basePos = html.indexOf("Geist")
    const modelPos = html.indexOf("Fancy Serif")
    expect(basePos).toBeGreaterThanOrEqual(0)
    expect(modelPos).toBeGreaterThanOrEqual(0)
    expect(basePos).toBeLessThan(modelPos)
  })

  it("renders sub-headers at body size, the same rule the chat answer follows", () => {
    renderPreview("<h1>Problem statement</h1><p>Body.</p>")
    const base = frame().contentDocument?.querySelector("style")?.textContent ?? ""
    // All six levels, and at inherited size — an unstyled <h1> is 2em bold,
    // which is the loudest version of exactly this defect.
    expect(base).toMatch(/h1,\s*h2,\s*h3,\s*h4,\s*h5,\s*h6/)
    expect(base).toMatch(/font-size:\s*inherit/)
  })

  it("keeps the model's markup intact — the style is added, nothing is replaced", () => {
    renderPreview("<h2>Scope</h2><p>The body text.</p>")
    const html = docHtml()
    expect(html).toContain("Scope")
    expect(html).toContain("The body text.")
  })

  it("re-injects the base style when the stream restarts from zero", () => {
    // A backend retry re-emits from the top; the component reopens the document,
    // which would otherwise drop the styling for the rest of the run.
    const view = renderPreview("<p>First attempt, partway through.</p>")
    view.rerender(
      <StreamingHtmlPreview
        html="<p>Second attempt from the top.</p>"
        title="PRD draft (generating)"
        testId="prd-streaming-preview"
      />,
    )
    expect(frame().contentDocument?.querySelectorAll("style").length).toBeGreaterThan(0)
    expect(docHtml()).toContain("Second attempt from the top.")
  })

  it("does not re-inject on an ordinary append", () => {
    // The suffix path must stay a pure append — a second base block mid-document
    // would re-assert itself over markup the model had already styled.
    const view = renderPreview("<p>One.</p>")
    view.rerender(
      <StreamingHtmlPreview
        html="<p>One.</p><p>Two.</p>"
        title="PRD draft (generating)"
        testId="prd-streaming-preview"
      />,
    )
    const bases = Array.from(frame().contentDocument?.querySelectorAll("style") ?? [])
      .filter((s) => (s.textContent ?? "").includes("Geist"))
    expect(bases.length).toBe(1)
    expect(docHtml()).toContain("Two.")
  })
})
