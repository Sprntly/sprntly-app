// The editor schema is one half of a contract whose other half is in Python.
//
// `backend/app/custom_artifact_html.py` applies an allowlist on every write:
// an unknown tag is UNWRAPPED (text survives, tag does not) and an unknown
// style property is DROPPED. So anything the toolbar can produce that the
// sanitizer does not keep is not a cosmetic mismatch — it is formatting that
// disappears when the user saves, with no error and nothing to tell them why.
//
// These tests pin the narrow places where the two could drift apart. They
// cannot import the Python allowlist, so they encode the same facts as
// literals and name the file that must agree — which is the point: a change to
// either side should fail here and send the author to look at the other.
import { describe, expect, it } from "vitest"
import {
  FONT_FAMILIES,
  FONT_SIZES,
  HEADING_LEVELS,
  COLOR_SWATCHES,
  isSafeHref,
  normalizeHref,
} from "../editorSchema"

/** Tags kept by `custom_artifact_html._ALLOWED`, transcribed. */
const SERVER_KEEPS_TAGS = new Set([
  "p", "br", "hr", "h1", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "s",
  "strike", "sub", "sup", "blockquote", "ul", "ol", "li", "code", "pre", "a",
  "span", "mark", "table", "thead", "tbody", "tr", "th", "td",
])

/** CSS properties kept by `custom_artifact_html._ALLOWED_CSS`, transcribed. */
const SERVER_KEEPS_CSS = new Set([
  "font-family", "font-size", "font-weight", "font-style", "color",
  "background-color", "text-align", "text-decoration",
])

describe("headings", () => {
  it("stops at 4, because the sanitizer keeps only h1-h4", () => {
    // An h5 would be UNWRAPPED on save: the user's heading comes back as plain
    // text. TipTap's StarterKit defaults to h1-h6, so this must be configured
    // rather than inherited — see DocumentEditor's StarterKit.configure.
    expect([...HEADING_LEVELS]).toEqual([1, 2, 3, 4])
    for (const level of HEADING_LEVELS) {
      expect(SERVER_KEEPS_TAGS.has(`h${level}`)).toBe(true)
    }
    expect(SERVER_KEEPS_TAGS.has("h5")).toBe(false)
  })
})

describe("the pickers only produce CSS the server keeps", () => {
  it("font family maps to font-family", () => {
    expect(SERVER_KEEPS_CSS.has("font-family")).toBe(true)
    // Every non-default option must be a usable stack, not an empty string
    // that would write `font-family: ` and be dropped as an empty declaration.
    for (const f of FONT_FAMILIES.filter((f) => f.value)) {
      expect(f.value.trim().length).toBeGreaterThan(0)
    }
  })

  it("font size maps to font-size, in a unit the sanitizer passes through", () => {
    expect(SERVER_KEEPS_CSS.has("font-size")).toBe(true)
    for (const f of FONT_SIZES.filter((f) => f.value)) {
      expect(f.value).toMatch(/^\d+(\.\d+)?px$/)
    }
  })

  it("colours map to color / background-color", () => {
    expect(SERVER_KEEPS_CSS.has("color")).toBe(true)
    expect(SERVER_KEEPS_CSS.has("background-color")).toBe(true)
    for (const swatch of COLOR_SWATCHES.flat()) {
      expect(swatch.value, `${swatch.label} is not a hex literal`).toMatch(
        /^#[0-9A-Fa-f]{6}$/,
      )
    }
  })

  it("offers a grid, not a shortlist — every row the same width", () => {
    // The picker replaced a five-entry list. Ten per row is what makes it read
    // as a palette rather than a menu, and a ragged row reads as a bug.
    expect(COLOR_SWATCHES.length).toBeGreaterThanOrEqual(4)
    for (const row of COLOR_SWATCHES) expect(row.length).toBe(10)
  })

  it("names every swatch, because a hex is not a name", () => {
    // The label is the accessible name and the tooltip. "#4A86E8" tells a
    // screen-reader user nothing they can act on.
    const labels = COLOR_SWATCHES.flat().map((s) => s.label)
    expect(new Set(labels).size, "two swatches share a name").toBe(labels.length)
    for (const label of labels) expect(label).not.toMatch(/^#/)
  })

  it("no picker value can reach the network", () => {
    // `url(...)` in a CSS value is stripped server-side (`_CSS_VALUE_BANNED`),
    // so a picker offering one would produce a style that silently vanishes —
    // and, worse, would be a fetch if it ever did survive.
    for (const v of [...FONT_FAMILIES, ...FONT_SIZES, ...COLOR_SWATCHES.flat()]) {
      expect(v.value.toLowerCase()).not.toContain("url(")
      expect(v.value.toLowerCase()).not.toContain("javascript:")
    }
  })

  it("every picker offers a way back to the default", () => {
    // Without an explicit "none" option, a span applied by accident can only be
    // removed by clearing ALL formatting.
    // The colour picker's way back is its own "Default" / "None" control
    // rather than a row in the list — see `ColorGrid`, which is asserted where
    // it is rendered.
    for (const list of [FONT_FAMILIES, FONT_SIZES]) {
      expect(list.some((o: { value: string }) => o.value === "")).toBe(true)
    }
  })
})

describe("link hrefs", () => {
  it.each([
    "https://sprntly.ai",
    "http://example.test/x?y=1",
    "mailto:someone@example.test",
    "tel:+15550100",
    "/artifacts",
    "#section",
    "./relative",
  ])("accepts %s", (href) => {
    expect(isSafeHref(href)).toBe(true)
  })

  it.each([
    "javascript:alert(1)",
    "vbscript:msgbox(1)",
    "data:text/html,<script>alert(1)</script>",
    "file:///etc/passwd",
  ])("refuses %s", (href) => {
    expect(isSafeHref(href)).toBe(false)
    expect(normalizeHref(href)).toBeNull()
  })

  it("turns a bare domain into https, because that is what a person means", () => {
    expect(normalizeHref("sprntly.ai")).toBe("https://sprntly.ai")
    expect(normalizeHref("  docs.example.test/guide  ")).toBe("https://docs.example.test/guide")
  })

  it("returns null for empty input rather than a bare https://", () => {
    expect(normalizeHref("")).toBeNull()
    expect(normalizeHref("   ")).toBeNull()
  })

  it("does not invent a scheme for something with a colon it does not know", () => {
    // The dangerous direction: prefixing https:// onto `javascript:alert(1)`
    // would produce a link that LOOKS safe and is not.
    expect(normalizeHref("javascript:alert(1)")).toBeNull()
  })
})
