import { readFileSync } from "node:fs"
import { dirname, join } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

/**
 * The palette's text steps, measured.
 *
 * The brand went black/white/grey, and the whole risk of a grey scale is that
 * every step looks fine to the eye that picked it. The first pass of this
 * palette had `--ink-3` at 4.48:1 on `--surface` — under WCAG AA by two
 * hundredths, and completely invisible without arithmetic. That is how a
 * design system acquires text nobody can read.
 *
 * Read out of globals.css rather than duplicated here, so editing the token is
 * what this checks — a copy would pass forever while the app went grey.
 */
const CSS = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), "..", "..", "globals.css"),
  "utf8",
)

function token(name: string): string {
  const m = new RegExp(`--${name}:\\s*(#[0-9A-Fa-f]{6})\\s*;`).exec(CSS)
  if (!m) throw new Error(`--${name} is not a plain hex in globals.css`)
  return m[1]
}

function luminance(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
  const lin = c.map((x) => (x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4))
  return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

describe("the text scale clears WCAG AA", () => {
  // Against `--surface`, not white: the off-white is the darker of the two
  // backgrounds these sit on, so passing here passes everywhere.
  const surface = token("surface")

  it.each([
    ["ink", 4.5],
    ["ink-2", 4.5],
    ["ink-3", 4.5],
  ])("--%s is readable as body text", (name, need) => {
    expect(contrast(token(name), surface)).toBeGreaterThanOrEqual(need)
  })

  it("--ink-4 clears the 3:1 floor for the things it IS used for", () => {
    // Decoration, disabled states and icon strokes — never a sentence, which
    // is why it is held to the non-text threshold and not to 4.5.
    expect(contrast(token("ink-4"), surface)).toBeGreaterThanOrEqual(3)
  })
})

describe("the brand's own pairs", () => {
  it("a primary button's label is readable on it", () => {
    expect(contrast("#FFFFFF", token("accent"))).toBeGreaterThanOrEqual(4.5)
  })

  it("the nav rail's text is readable on the rail, and on a hovered row", () => {
    expect(contrast(token("nav-text"), token("nav"))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(token("nav-text"), token("nav-2"))).toBeGreaterThanOrEqual(4.5)
  })

  it("the rail carries its own INVERTED accent, or its buttons vanish", () => {
    // The page accent is black and the rail is black. A New chat button
    // painted `--accent` there measured 1.1:1 against its own background —
    // painted, and invisible. `--nav-accent` is white for exactly this.
    expect(contrast(token("nav-accent"), token("nav"))).toBeGreaterThanOrEqual(4.5)
    expect(contrast(token("nav-accent-ink"), token("nav-accent"))).toBeGreaterThanOrEqual(4.5)
  })

  it("a hover on the rail is a step you can SEE", () => {
    // "Every hover grey" only means something if the grey registers. Two
    // earlier values (#1C1F22, #2B2F32) sat 1.2:1 and 1.4:1 off the rail —
    // a hover you have to look for is not a hover.
    expect(contrast(token("nav-2"), token("nav"))).toBeGreaterThanOrEqual(1.6)
  })

  it("success still reads on its own tint", () => {
    // Green survives as the one STATE colour. If it ever stops being legible
    // on its own badge, the thing it exists to signal is gone.
    expect(contrast(token("success-ink"), token("success-soft"))).toBeGreaterThanOrEqual(4.5)
  })
})

describe("the brand is neutral", () => {
  it("the accent family carries no hue", () => {
    // A "grey" whose channels drift apart is a tint, and the whole point of
    // this palette is that it does not have one. The old accent was #179463 —
    // a 125-point spread between its channels.
    for (const name of ["accent", "accent-hover", "accent-ink", "surface", "ink"]) {
      const hex = token(name)
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16))
      const spread = Math.max(r, g, b) - Math.min(r, g, b)
      expect(spread, `--${name} (${hex}) is tinted, not neutral`).toBeLessThanOrEqual(8)
    }
  })

  it("keeps ONE green, and only for status", () => {
    // Success is deliberately exempt: green used to mean both "brand" and
    // "this worked", and repainting the status colour too would have left
    // Ready and Failed distinguishable only by their words.
    expect(token("success")).toBe("#179463")
  })
})
