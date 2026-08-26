// What the editor is allowed to produce — and why it is exactly this list.
//
// THE EDITOR'S SCHEMA AND THE SERVER'S SANITIZER ARE ONE CONTRACT WITH TWO
// ENDS. `backend/app/custom_artifact_html.py` applies an allowlist on every
// write: an unknown tag is unwrapped (its text survives, the tag does not) and
// an unknown style property is dropped. So anything the toolbar can create but
// the sanitizer does not keep is not a cosmetic mismatch — it is formatting
// that vanishes when the user saves, with no error and no way to tell why.
//
// That failure is silent, which is what makes it worth a file. The two lists
// are kept side by side here so a future change to either one is made looking
// at the other.
//
//   SERVER KEEPS (tags):  p br hr h1 h2 h3 h4 strong b em i u s strike sub sup
//                         blockquote ul ol li code pre a span mark
//                         table thead tbody tr th td
//   SERVER KEEPS (css):   font-family font-size font-weight font-style color
//                         background-color text-align text-decoration
//
// The two deliberate narrowings:
//
//   * HEADINGS STOP AT 4. TipTap's StarterKit defaults to h1-h6, and the
//     sanitizer keeps only h1-h4 — so an h5 would be UNWRAPPED on save and the
//     user's heading would come back as plain text. Configured, not left to
//     the default.
//   * NO IMAGES. `<img>` is not on the server's list (it fetches, and a
//     `data:` image would balloon the stored body past its ceiling), so the
//     toolbar does not offer one. Better a missing button than a picture that
//     disappears on save.

/** Heading levels the toolbar offers AND the schema allows. Must stay a subset
 *  of the server's h1-h4 — see the note above. */
export const HEADING_LEVELS = [1, 2, 3, 4] as const

/** Fonts offered in the family picker.
 *
 *  Stacks rather than single families, so a document keeps its intended LOOK
 *  (serif / sans / mono) on a machine that lacks the first choice. The stored
 *  value is whatever is chosen here, and it survives the sanitizer as a
 *  `font-family` declaration on a span. */
export const FONT_FAMILIES: { label: string; value: string }[] = [
  { label: "Default", value: "" },
  { label: "Sans serif", value: "Inter, Helvetica, Arial, sans-serif" },
  { label: "Serif", value: "Georgia, 'Times New Roman', serif" },
  { label: "Mono", value: "'SF Mono', Menlo, Consolas, monospace" },
]

/** Sizes offered in the size picker, in px because that is what `font-size`
 *  stores and what the sanitizer passes through unchanged. */
export const FONT_SIZES: { label: string; value: string }[] = [
  { label: "Default", value: "" },
  { label: "Small", value: "13px" },
  { label: "Normal", value: "15px" },
  { label: "Large", value: "19px" },
  { label: "Huge", value: "24px" },
]

/** The colour picker's grid — the same forty a document tool is expected to
 *  offer, laid out the way everyone already reads one: a row of greys, then
 *  ten hues at full strength, light, and dark.
 *
 *  It replaced a five-item list. A list is fine for fonts, where the options
 *  ARE the vocabulary, and wrong for colour, where the option someone wants is
 *  a point in a space and naming five of them just means the other one is
 *  unreachable. A custom picker sits under the grid for that case (see
 *  `ColorGrid`), so nothing is unreachable at all.
 *
 *  Every value is a hex literal, which is what the sanitizer's
 *  `background-color` / `color` allowlist keeps and what `_CSS_VALUE_BANNED`
 *  cannot mistake for a fetch. */
const GREYS = [
  ["Black", "#000000"], ["Grey 1", "#434343"], ["Grey 2", "#666666"],
  ["Grey 3", "#999999"], ["Grey 4", "#B7B7B7"], ["Grey 5", "#CCCCCC"],
  ["Grey 6", "#D9D9D9"], ["Grey 7", "#EFEFEF"], ["Grey 8", "#F3F3F3"],
  ["White", "#FFFFFF"],
] as const

const HUES = [
  "Berry", "Red", "Orange", "Yellow", "Green", "Cyan", "Cornflower", "Blue",
  "Purple", "Magenta",
] as const

const HUE_ROWS: { suffix: string; hexes: string[] }[] = [
  {
    suffix: "",
    hexes: ["#980000", "#FF0000", "#FF9900", "#FFD966", "#00A550", "#00BCD4",
            "#4A86E8", "#1155CC", "#9900FF", "#E91E8C"],
  },
  {
    suffix: " light",
    hexes: ["#E6B8AF", "#F4CCCC", "#FCE5CD", "#FFF2CC", "#D9EAD3", "#D0E0E3",
            "#C9DAF8", "#CFE2F3", "#D9D2E9", "#EAD1DC"],
  },
  {
    suffix: " dark",
    hexes: ["#A61C00", "#CC0000", "#E69138", "#BF9000", "#38761D", "#134F5C",
            "#1C4587", "#073763", "#674EA7", "#A64D79"],
  },
]

export type Swatch = { label: string; value: string }

export const COLOR_SWATCHES: Swatch[][] = [
  GREYS.map(([label, value]) => ({ label, value })),
  ...HUE_ROWS.map((row) =>
    row.hexes.map((value, i) => ({ label: `${HUES[i]}${row.suffix}`, value })),
  ),
]

/** Link hrefs the editor will accept, mirroring the server's `_SAFE_URL`.
 *
 *  Checked HERE as well as there because the two do different jobs: the server
 *  makes the stored document safe, while this makes the user's mistake VISIBLE
 *  at the moment they make it. A `javascript:` link silently losing its href on
 *  save is a worse experience than being told the link was not accepted. */
const SAFE_HREF = /^(?:https?:|mailto:|tel:|#|\/|\.{0,2}\/)/i

export function isSafeHref(href: string): boolean {
  return SAFE_HREF.test(href.trim())
}

/** Normalize what a person types into a link box.
 *
 *  "sprntly.ai" is a URL to a human and a relative path to a browser, so a
 *  bare domain gets https://. Returns null for anything that is still not
 *  safe, which the caller reports rather than storing. */
export function normalizeHref(raw: string): string | null {
  const href = raw.trim()
  if (!href) return null
  if (isSafeHref(href)) return href
  // No scheme and not obviously a path → treat as a bare domain.
  if (!href.includes(":") && /^[\w-]+(\.[\w-]+)+/.test(href)) return `https://${href}`
  return null
}
