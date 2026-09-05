// The plan gate's stylesheet, checked against the tokens that actually exist.
//
// WHY THIS FILE EXISTS. Two defects shipped in the first version of that module
// and neither was visible in a test, a typecheck or a review:
//
//   1. It leaned on `var(--border, #ececec)` seven times. `--border` is not
//      declared anywhere the web app loads — only in `ds-agent/` and
//      `prototype-runtime/` — so every one of them silently rendered the
//      hardcoded fallback, in a warmer grey than the house rule colour. A
//      token that never resolves is a hardcoded value wearing a variable's
//      name, and the fallback is what makes it invisible.
//
//   2. It used `var(--accent-muted, var(--green-s, #e9f4ec))`. `--green-s` is
//      scoped to a screen this card never renders inside, so that literal was
//      reachable too — a mint fill in a product whose brand had just become
//      black, white and grey.
//
// Both are the same failure: CSS has no undefined-variable error, so the only
// thing standing between a typo and a wrong colour is something that reads the
// declarations. That is what this does.
//
// It is deliberately scoped to this one module rather than swept across the
// app: the older `.ga-*` rules in `globals.css` carry the identical `--border`
// bug, and fixing those is a separate change with a much wider blast radius.
// This stops the pattern spreading.
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it } from "vitest"

const RAW = readFileSync(
  join(__dirname, "..", "GoalAnalysisPlan.module.css"), "utf8")
const GLOBALS = readFileSync(
  join(__dirname, "..", "..", "..", "globals.css"), "utf8")

/** The stylesheet with its comments removed.
 *
 *  EVERY CHECK BELOW READS DECLARATIONS, NOT PROSE. The first run of this file
 *  failed six of its own assertions against a stylesheet that satisfies all of
 *  them, because that stylesheet documents the tokens it must not use and
 *  names the literals it replaced — so a ban on `--border` tripped on the
 *  sentence explaining the ban. A rule that cannot be written down next to its
 *  own reason is not a rule anybody will keep. */
const CSS = RAW.replace(/\/\*[\s\S]*?\*\//g, "")

/** Every custom property the app declares at `:root`.
 *
 *  Read from `:root` specifically, not from the whole file: `globals.css` also
 *  declares tokens inside scoped blocks (`.tkv2` has its own `--green-s`, for
 *  instance) and those are exactly the ones a shared component must not reach
 *  for, because it does not render inside them. */
const rootTokens = (): Set<string> => {
  const start = GLOBALS.indexOf(":root")
  const end = GLOBALS.indexOf("\n  }", start)
  const block = GLOBALS.slice(start, end === -1 ? undefined : end)
  return new Set(
    [...block.matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]),
  )
}

/** Every `var(--x)` the module reads, at any nesting depth. */
const referenced = (): string[] =>
  [...CSS.matchAll(/var\(\s*(--[a-z0-9-]+)/gi)].map((m) => m[1])

describe("the plan gate's stylesheet names tokens that exist", () => {
  it("references no custom property the app does not declare", () => {
    const declared = rootTokens()
    // `--font-serif` is a font stack with a real generic fallback chain, not a
    // colour, and is intentionally optional.
    const optional = new Set(["--font-serif"])
    const missing = [...new Set(referenced())]
      .filter((t) => !declared.has(t) && !optional.has(t))
    expect(missing).toEqual([])
  })

  it("never reaches for the token that started this", () => {
    // `--border` resolves nowhere in this app. Pinned by name because the
    // pattern is copied from the older rules in `globals.css` and would come
    // back the same way.
    expect(CSS).not.toContain("--border")
  })

  it("carries no colour literal at all", () => {
    // A fallback is a safety net, not a colour choice — and every one that
    // shipped here was being used as the latter. The ladder and the rule
    // colours are the decision; a hex beside them is a second, unreviewed one.
    const literals = CSS.match(/#[0-9a-f]{3,8}\b/gi) ?? []
    expect(literals).toEqual([])
  })
})

describe("green means 'it worked' and nothing else", () => {
  it("uses no success token, because this screen has no success state", () => {
    // A plan gate states what a run WILL do. Nothing on it has succeeded yet,
    // so the one green in the system has no business here.
    expect(CSS).not.toMatch(/--success/)
    expect(CSS).not.toMatch(/--green/)
  })

  it("spends its one accent on the thing the reader chose", () => {
    // `--accent` is the brand near-black now, not a colour accent: it marks
    // emphasis and selection. Hairlines, ticks and decoration belong on the
    // neutral scale, and a section heading is not an emphasis.
    const accentRules = CSS.split("}")
      // EXACT, because `--accent-soft` is the neutral grey tint (#EEEFF0)
      // and not the accent itself — a boundary match would conflate them.
      .filter((rule) => /var\(\s*--accent(?![a-z-])/.test(rule))
      .map((rule) => rule.split("{")[0].trim())
    expect(accentRules).toEqual([".optionOn"])
  })
})

describe("text sits on the measured ink ladder, never on an opacity", () => {
  it("dims nothing with opacity", () => {
    // Measured against `--surface`, this file's own opacity values landed at
    // 3.33:1, 2.66:1, 2.15:1 and 3.90:1 — four failures of AA, two of them on
    // sentences. An opacity produces a ratio nobody chose.
    expect(CSS).not.toMatch(/^\s*opacity\s*:/m)
  })

  it("keeps --ink-4 off every sentence", () => {
    // The brand states the rule outright: `--ink-4` is "decoration, disabled,
    // icon strokes; never a sentence". It measures 3.2:1 on `--surface`, which
    // is under AA by design.
    const ink4Rules = CSS.split("}")
      .filter((rule) => /var\(\s*--ink-4\b/.test(rule))
      .map((rule) => rule.split("{")[0].trim())
    expect(ink4Rules).toEqual([".tickOff"])
  })

  it("draws every rule in the house line colours", () => {
    const borders = [...CSS.matchAll(/border[a-z-]*:\s*[^;]*;/gi)]
      .map((m) => m[0])
      .filter((d) => /\d+px\s+(solid|dashed)/.test(d))
    expect(borders.length).toBeGreaterThan(0)
    for (const decl of borders) {
      expect(decl).toMatch(/var\(\s*--(line|line-strong|accent|ink-3)\b/)
    }
  })
})
