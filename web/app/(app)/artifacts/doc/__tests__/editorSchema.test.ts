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
  HIGHLIGHT_COLORS,
  TEXT_COLORS,
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
    for (const c of [...TEXT_COLORS, ...HIGHLIGHT_COLORS].filter((c) => c.value)) {
      expect(c.value).toMatch(/^#[0-9A-Fa-f]{3,8}$/)
    }
  })

  it("no picker value can reach the network", () => {
    // `url(...)` in a CSS value is stripped server-side (`_CSS_VALUE_BANNED`),
    // so a picker offering one would produce a style that silently vanishes —
    // and, worse, would be a fetch if it ever did survive.
    for (const v of [...FONT_FAMILIES, ...FONT_SIZES, ...TEXT_COLORS, ...HIGHLIGHT_COLORS]) {
      expect(v.value.toLowerCase()).not.toContain("url(")
      expect(v.value.toLowerCase()).not.toContain("javascript:")
    }
  })

  it("every picker offers a way back to the default", () => {
    // Without an explicit "none" option, a span applied by accident can only be
    // removed by clearing ALL formatting.
    for (const list of [FONT_FAMILIES, FONT_SIZES, TEXT_COLORS, HIGHLIGHT_COLORS]) {
      expect(list.some((o) => o.value === "")).toBe(true)
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
