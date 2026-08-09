/**
 * Docs content model. Every document is HARDCODED as a TypeScript object in
 * `web/app/docs/content/<slug>.ts` and registered in `content/index.ts`.
 * Nothing here is fetched from a database — the whole docs site is baked into
 * the static export at build time.
 *
 * A document is a list of sections. Each section's `body` is a Markdown string
 * (GFM: tables, lists, links). Section titles become the left-nav / "On this
 * page" table of contents, and each section is individually searchable.
 */
export type DocSection = {
  /** URL hash anchor + scroll-spy id. Kebab-case, unique within the doc. */
  id: string
  /** Rendered as an <h2> and shown in the table of contents. */
  title: string
  /** Markdown (remark-gfm). Blockquotes (`> …`) render as green callouts. */
  body: string
}

export type Doc = {
  /** URL slug: /docs/<slug>. Must be unique across all docs. */
  slug: string
  /** Document title (H1 + browser tab). */
  title: string
  /** One-line description shown on the docs home cards + meta description. */
  description: string
  /** Grouping label for the docs home + sidebar (e.g. "Guides"). */
  category: string
  /** Human version label, e.g. "1.0". Optional. */
  version?: string
  /** Human "last updated" label, e.g. "July 2026". Optional. */
  updated?: string
  /** Ordered sections. First section renders directly under the doc header. */
  sections: DocSection[]
}
