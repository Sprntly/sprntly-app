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

/** Text colours. Kept short on purpose: a full picker invites a document that
 *  looks like a ransom note, and every value here has to read on the white
 *  page the document is rendered on. */
export const TEXT_COLORS: { label: string; value: string }[] = [
  { label: "Default", value: "" },
  { label: "Muted", value: "#5A5853" },
  { label: "Red", value: "#B42318" },
  { label: "Green", value: "#0E6E49" },
  { label: "Blue", value: "#1E40AF" },
]

/** Highlight (background) colours. Stored as `background-color` on a span,
 *  which the sanitizer keeps. */
export const HIGHLIGHT_COLORS: { label: string; value: string }[] = [
  { label: "None", value: "" },
  { label: "Yellow", value: "#FEF3C7" },
  { label: "Green", value: "#DBF1E7" },
  { label: "Blue", value: "#DBEAFE" },
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
