// A sub-header inside a chat answer reads as prose, not as a document title.
//
// `.ai-bar-reply-answer` is the markdown-rendered answer body, shared by the
// chat thread (14px) and the AI bar (13px). Two things were wrong with its
// heading scale:
//
//   * `h2` was pinned to 18px/`--font-display` and `h3` to 15px, so a `##` in
//     an answer arrived as a title dropped into the conversation; and
//   * `h1`, `h4`, `h5` and `h6` were never styled AT ALL, so they fell through
//     to the browser's defaults — an `h1` is 2em bold — which is the loudest
//     version of the same problem and the easiest to hit, since a model writing
//     a structured answer often opens with `#`.
//
// Read off globals.css rather than rendered, because this is a pure CSS rule
// with no component to mount; jsdom applies no stylesheet cascade anyway, so a
// DOM test here would assert nothing.
import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const HERE = dirname(fileURLToPath(import.meta.url))
// __tests__ → shared → components → app
const GLOBALS = join(HERE, "..", "..", "..", "globals.css")

/** The declaration block of the first rule whose selector list matches. */
function ruleBody(css: string, selectorMatch: RegExp): string | null {
  for (const m of css.matchAll(/([^{}]+)\{([^}]*)\}/g)) {
    if (selectorMatch.test(m[1])) return m[2]
  }
  return null
}

describe("chat answer sub-headers", () => {
  const css = readFileSync(GLOBALS, "utf8")

  it("styles ALL SIX heading levels, not just h2/h3", () => {
    // The gap that let an `h1` render at browser-default 2em.
    for (const level of ["h1", "h2", "h3", "h4", "h5", "h6"]) {
      expect(
        css.includes(`.ai-bar-reply-answer ${level},`) ||
          css.includes(`.ai-bar-reply-answer ${level} {`),
        `.ai-bar-reply-answer ${level} is unstyled — it will fall to the browser default`,
      ).toBe(true)
    }
  })

  it("renders them at the surface's own body size and weight", () => {
    const body = ruleBody(css, /\.ai-bar-reply-answer h1,[\s\S]*?\.ai-bar-reply-answer h6\s*$/m)
    expect(body, "the unified heading rule is gone").not.toBeNull()
    // `inherit`, not a number: the block is shared by two surfaces at two body
    // sizes, and a hard value would silently un-scale one of them.
    expect(body).toMatch(/font-size:\s*inherit/)
    expect(body).toMatch(/font-family:\s*inherit/)
    expect(body).toMatch(/font-weight:\s*600/)
  })

  it("no longer pins a display font or an 18px scale", () => {
    // The two declarations that made a `##` look like a title. Asserted on the
    // heading rule itself rather than on a sliced region of the file — the
    // first draft of this test sliced from `.ai-bar-reply-answer {`, which also
    // matches `.bc-agent-body .ai-bar-reply-answer {` hundreds of lines earlier
    // and swept in unrelated CSS that legitimately uses 18px.
    const body = ruleBody(css, /\.ai-bar-reply-answer h1,[\s\S]*?\.ai-bar-reply-answer h6\s*$/m)
    expect(body).not.toMatch(/font-size:\s*18px/)
    expect(body).not.toMatch(/var\(--font-display\)/)
    // And the per-level rules that carried them are gone, not merely overridden
    // by a later block (which would leave the cascade decided by file order).
    expect(css).not.toMatch(/\.ai-bar-reply-answer h2\s*\{/)
    expect(css).not.toMatch(/\.ai-bar-reply-answer h3\s*\{/)
  })

  it("separates them by space instead, and never indents the first one", () => {
    const body = ruleBody(css, /\.ai-bar-reply-answer h1,[\s\S]*?\.ai-bar-reply-answer h6\s*$/m)
    expect(body).toMatch(/margin:/)
    expect(css).toContain(".ai-bar-reply-answer h1:first-child")
    expect(css).toContain(".ai-bar-reply-answer h6:first-child")
  })
})
