// @vitest-environment jsdom
//
// EvidenceHtmlBrief renders the v3 evidence artifact — a self-contained HTML
// visual brief — inside a SANDBOXED iframe. The security contract is the point:
// sandbox="allow-same-origin" WITHOUT allow-scripts, so model-generated HTML
// can render its inline CSS/SVG but can never execute a <script> or inline
// handler. These tests pin that contract + that the HTML reaches srcDoc, and
// that the markdown→state adapter routes an HTML brief here (not :::block).
import { cleanup, render } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

// Components compile JSX to React.createElement under the repo's classic JSX
// runtime, so a global React must exist before the component module evaluates.
vi.hoisted(() => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  ;(globalThis as Record<string, unknown>).React = require("react")
})

import { EvidenceHtmlBrief, looksLikeHtmlBrief } from "../EvidenceHtmlBrief"

afterEach(cleanup)

const BRIEF =
  '<meta charset="utf-8"><style>.wrap{max-width:820px}</style>' +
  '<div class="wrap"><h1>Beginners Plateau</h1><svg viewBox="0 0 720 250"></svg></div>'

describe("EvidenceHtmlBrief", () => {
  it("renders an iframe carrying the brief HTML in srcDoc", () => {
    const { container } = render(<EvidenceHtmlBrief html={BRIEF} />)
    const iframe = container.querySelector("iframe")
    expect(iframe).not.toBeNull()
    expect(iframe!.getAttribute("srcdoc")).toBe(BRIEF)
  })

  it("sandboxes the iframe: allow-same-origin but NOT allow-scripts (XSS-safe)", () => {
    const { container } = render(<EvidenceHtmlBrief html={BRIEF} />)
    const sandbox = container.querySelector("iframe")!.getAttribute("sandbox")
    expect(sandbox).toBe("allow-same-origin")
    expect(sandbox).not.toContain("allow-scripts")
  })

  it("strips a wrapping ```html code fence before rendering (no literal backticks)", () => {
    const { container } = render(<EvidenceHtmlBrief html={"```html\n" + BRIEF + "\n```"} />)
    const srcdoc = container.querySelector("iframe")!.getAttribute("srcdoc")!
    expect(srcdoc).toBe(BRIEF)
    expect(srcdoc).not.toContain("```")
  })
})

describe("looksLikeHtmlBrief", () => {
  it.each([
    "<!doctype html><html></html>",
    '  <div class="wrap"></div>',
    '<meta charset="utf-8">',
    "<style>.x{}</style>",
  ])("is true for HTML opener %s", (s) => {
    expect(looksLikeHtmlBrief(s)).toBe(true)
  })

  it.each([":::hero\n[]\n:::", "# Heading\n\nbody", "", null, undefined])(
    "is false for non-HTML %s",
    (s) => {
      expect(looksLikeHtmlBrief(s as string | null | undefined)).toBe(false)
    },
  )
})
