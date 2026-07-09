import { describe, expect, it } from "vitest"
import type { PrdContent } from "../../types/content"
import {
  scopeSelector,
  scopeCss,
  splitBriefHtml,
  buildCombinedHtml,
  canExportCombined,
} from "../combinedExport"

// A PrdContent is a big shape; export only reads `title` + `html`, so cast a
// minimal literal.
const brief = (title: string, html: string): PrdContent =>
  ({ title, html }) as unknown as PrdContent

describe("scopeSelector", () => {
  it("remaps document-root selectors to the scope element", () => {
    expect(scopeSelector(":root", ".ex-prd")).toBe(".ex-prd")
    expect(scopeSelector("html", ".ex-prd")).toBe(".ex-prd")
    expect(scopeSelector("body", ".ex-prd")).toBe(".ex-prd")
  })
  it("prefixes descendant selectors, keeping a leading html/body as the scope", () => {
    expect(scopeSelector(".row", ".ex-prd")).toBe(".ex-prd .row")
    expect(scopeSelector("table th", ".ex-prd")).toBe(".ex-prd table th")
    expect(scopeSelector("body .q", ".ex-prd")).toBe(".ex-prd .q")
  })
})

describe("scopeCss", () => {
  it("scopes every top-level rule and remaps :root vars under the scope", () => {
    const out = scopeCss(":root{--g:#0a0} body{margin:0} .q{color:var(--g)}", ".ex-ev")
    expect(out).toContain(".ex-ev{--g:#0a0}")
    expect(out).toContain(".ex-ev{margin:0}")
    expect(out).toContain(".ex-ev .q{color:var(--g)}")
  })
  it("recurses into @media and passes @keyframes through unchanged", () => {
    const out = scopeCss("@media print{ .q{color:red} } @keyframes spin{to{transform:rotate(360deg)}}", ".ex-ev")
    expect(out).toContain("@media print{.ex-ev .q{color:red}}")
    expect(out).toContain("@keyframes spin{to{transform:rotate(360deg)}}")
  })
  it("strips comments", () => {
    expect(scopeCss("/* c */ .q{x:1}", ".s")).not.toContain("/*")
  })
})

describe("splitBriefHtml", () => {
  it("extracts the style CSS and body markup and drops scripts + contenteditable", () => {
    const { css, body } = splitBriefHtml(
      `<!doctype html><html><head><style>.q{x:1}</style></head><body><div class="page" contenteditable="true">hi<script>evil()</script></div></body></html>`,
    )
    expect(css).toContain(".q{x:1}")
    expect(body).toContain('<div class="page">hi</div>')
    expect(body).not.toContain("script")
    expect(body).not.toContain("contenteditable")
  })
})

describe("buildCombinedHtml", () => {
  const ev = brief("Widget", "<html><head><style>:root{--g:#0a0} .q{color:var(--g)}</style></head><body><div class='q'>evidence</div></body></html>")
  const prd = brief("Widget", "<html><head><style>:root{--g:#00f} .q{color:var(--g)}</style></head><body><div class='q'>prd</div></body></html>")

  it("combines both briefs under isolated scopes with a page break", () => {
    const html = buildCombinedHtml(ev, prd)!
    expect(html).toContain('<section class="ex-scope ex-evidence">')
    expect(html).toContain('<section class="ex-scope ex-prd">')
    expect(html).toContain('class="ex-pagebreak"')
    // Each brief's :root is remapped to its OWN scope, so --g does not collide.
    expect(html).toContain(".ex-evidence{--g:#0a0}")
    expect(html).toContain(".ex-prd{--g:#00f}")
    expect(html).toContain(".ex-evidence .q")
    expect(html).toContain(".ex-prd .q")
    // Evidence renders before the PRD.
    expect(html.indexOf("ex-evidence")).toBeLessThan(html.indexOf("ex-prd"))
  })

  it("returns null when neither doc is an HTML brief", () => {
    expect(buildCombinedHtml(brief("x", ""), brief("y", ""))).toBeNull()
    expect(buildCombinedHtml(null, null)).toBeNull()
  })

  it("emits a single section when only one doc is an HTML brief", () => {
    const html = buildCombinedHtml(null, prd)!
    expect(html).toContain("ex-prd")
    expect(html).not.toContain("ex-evidence")
    // No page-break separator element between sections (the CSS rule stays).
    expect(html).not.toContain('<div class="ex-pagebreak">')
  })
})

describe("canExportCombined", () => {
  it("is true only when both Evidence and PRD are HTML briefs", () => {
    expect(canExportCombined(brief("e", "<body>x</body>"), brief("p", "<body>y</body>"))).toBe(true)
    expect(canExportCombined(null, brief("p", "<body>y</body>"))).toBe(false)
    expect(canExportCombined(brief("e", "<body>x</body>"), brief("p", ""))).toBe(false)
    expect(canExportCombined(null, null)).toBe(false)
  })
})
