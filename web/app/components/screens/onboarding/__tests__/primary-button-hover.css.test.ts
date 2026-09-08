// A PRIMARY BUTTON MUST STAY VISIBLE UNDER THE CURSOR.
//
// Reported on the onboarding plan step: hovering Continue made it disappear.
// It was never a plan-step bug — `.btn primary` is used by PlanStep (Continue
// and the post-payment retry) and PaymentRequiredPrompt, and all of them had
// it.
//
// The mechanism, because it is the kind that comes back: `.btn.primary` sets a
// near-black background and white text at specificity (0,2,0). The generic
// `.btn:hover:not(:disabled)` sets only a background, at (0,3,0) — `:not()`
// carries its argument's weight, so that is three pseudo-classes against two
// classes. The hover therefore wins the background, repaints it `--surface-2`
// (#FFFFFF), and leaves the white text exactly where it was.
//
// Asserted against globals.css rather than a rendered component because jsdom
// does not apply stylesheets or resolve cascade order, so a DOM test could not
// see this at all. Matched with a whitespace-tolerant regex, NOT a byte
// comparison: a byte-identical assertion fails on a CRLF checkout while
// passing in CI, which is a test that lies depending on who runs it.
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

const raw = readFileSync(
  join(__dirname, "..", "..", "..", "..", "globals.css"),
  "utf8",
)

// Comments out first. They carry selector names (this fix's own note cites
// `.od-rail-newbtn`), and a rule preceded by a comment has no `}` in front of
// it for the anchor below to find.
const css = raw.replace(/\/\*[\s\S]*?\*\//g, "")

/**
 * The body of a rule, by exact selector. Null when the selector is absent.
 *
 * Anchored on `}` or start-of-file so a selector cannot match as the SUFFIX of
 * a longer one — `.btn.primary` must not be found inside
 * `.foo .btn.primary`, or the guard passes on a rule that never applies here.
 */
function ruleBody(selector: string): string | null {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
    .replace(/\s+/g, "\\s+")
  const m = css.match(new RegExp(`(?:^|\\})\\s*${escaped}\\s*\\{([^}]*)\\}`))
  return m ? m[1] : null
}

describe("the primary button's hover state", () => {
  it("has a hover rule at all", () => {
    // The whole bug: there wasn't one, so the generic .btn:hover applied.
    expect(ruleBody(".btn.primary:hover:not(:disabled)")).not.toBeNull()
  })

  it("keeps a dark ground instead of falling through to the light surface", () => {
    const body = ruleBody(".btn.primary:hover:not(:disabled)")!
    expect(body).toMatch(/background:\s*var\(--ink-2\)/)
    // --surface / --surface-2 are the light grounds the generic hover reaches
    // for. Either one here puts white text on a white button again.
    expect(body).not.toMatch(/background:\s*var\(--surface/)
  })

  it("restates the text colour, so the pair can never come apart again", () => {
    // The base rule's `color` survived the old hover by accident, not by
    // design. Pinning both ends in the hover rule is what makes the button
    // legible in dark mode too, where the palette inverts.
    expect(ruleBody(".btn.primary:hover:not(:disabled)")!).toMatch(
      /color:\s*var\(--nav-text-hover\)/,
    )
  })

  it("outranks the generic .btn hover it has to beat", () => {
    // (0,4,0) against (0,3,0). If someone ever simplifies this selector to
    // `.btn.primary:hover` — (0,3,0) — the two tie and source order decides,
    // which puts the bug back for any rule that lands later in the file.
    const generic = ruleBody(".btn:hover:not(:disabled)")
    expect(generic, "the rule this exists to outrank is gone").not.toBeNull()
    expect(generic).toMatch(/background:\s*var\(--surface-2\)/)
    expect(css).toContain(".btn.primary:hover:not(:disabled)")
  })
})
