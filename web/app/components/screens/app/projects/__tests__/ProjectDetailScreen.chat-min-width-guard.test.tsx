// @vitest-environment node
//
// ProjectDetailScreen — both-artifact-drawers-open layout guard (source-scan,
// no browser: real rendered-width behaviour at narrow viewports is the
// live-verify pass, not this file). Two things this asserts on the raw CSS
// text rather than a computed-style render (jsdom doesn't do layout, so a
// grid-track computation can't be observed here either way):
//
// 1. The project chat column has an explicit min-width so it never shrinks
//    below readability, and the list-drawer's default clamp is tightened
//    specifically for the case where the content panel is ALSO open (the
//    reported squeeze) — scoped so it can never outrank the two existing
//    narrower-viewport breakpoints.
// 2. `globals.css` (main chat's own reflow — the hot file this ticket was
//    told not to touch) is byte-identical at the exact rule this bug lives
//    beside, proving the fix is scoped to the project surface only.
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

const moduleCss = () =>
  readFileSync(join(__dirname, "../ProjectDetailScreen.module.css"), "utf8")
const globalsCss = () =>
  readFileSync(join(__dirname, "../../../../../globals.css"), "utf8")

describe("ProjectDetailScreen.module.css — both-drawers-open squeeze guard (B8)", () => {
  it("the project chat column (.main) has an explicit readable min-width", () => {
    const css = moduleCss()
    // Line-anchored — `.bodyDrawerOpen .main { display: none }` (the 960px
    // rule) also matches a bare `\.main\s*\{`-style pattern; anchoring to the
    // start of a line rules that compound selector out.
    const rule = css.match(/^\.main\s*\{[^}]*\}/m)?.[0] ?? ""
    expect(rule).toMatch(/min-width:\s*380px/)
  })

  it("the list-drawer column is capped tight so chat + list + content-panel fit without overlap, guarded off the two existing narrower breakpoints", () => {
    const css = moduleCss()
    // The base (single-drawer) rule caps the LIST column tight — a column of
    // one-line rows reads at ~240-300px; the old 420-600px band was the root
    // cause of the three-panel squeeze.
    expect(css).toMatch(
      /\.bodyDrawerOpen\s*\{\s*grid-template-columns:\s*\n\s*minmax\(360px,\s*1fr\)\s*\n\s*var\(--proj-drawer-w,\s*clamp\(240px,\s*20vw,\s*300px\)\)/,
    )
    // The three-panel override only exists nested under BOTH a `min-width`
    // media guard (never overrides the 960px/1080px narrow-viewport rules —
    // those stay a plain class selector, which the override's higher
    // specificity would otherwise always win against) AND the global
    // cpanel-open class.
    const guardedBlock = css.match(
      /@media \(min-width: 1081px\) \{\s*:global\(\.app--cpanel-open\) \.bodyDrawerOpen \{[^}]*\}\s*\}/,
    )?.[0]
    expect(guardedBlock).toBeTruthy()
    expect(guardedBlock).toMatch(/minmax\(380px,\s*1fr\)/)
    expect(guardedBlock).toMatch(/var\(--proj-drawer-w,\s*clamp\(240px,\s*20vw,\s*300px\)\)/)
    // The horizontal-scroll last-resort hack is gone — the tight cap makes a
    // clean three-column layout fit, so no `overflow-x` is needed (and it
    // would have forced overflow-y:auto, clipping this surface's menus).
    expect(guardedBlock).not.toMatch(/overflow-x/)
  })

  it("main chat's own content-panel reflow rule in globals.css is byte-identical — this fix never touches the hot file", () => {
    const css = globalsCss()
    expect(css).toContain(
      ".app--cpanel-open .main-column {\n    padding-right: clamp(420px, var(--cpanel-width, 35vw), 60vw);",
    )
    expect(css).not.toContain("bodyDrawerOpen")
    expect(css).not.toContain("proj-drawer-w")
  })
})
