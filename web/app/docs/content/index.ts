import type { Doc, DocSection } from "./types"
import { sprntlyHowToGuide } from "./sprntly-how-to-guide"

/**
 * The docs registry. To add a new document:
 *   1. Create `content/<slug>.ts` exporting a `Doc` (see sprntly-how-to-guide.ts).
 *   2. Import it here and add it to the `DOCS` array below.
 * Everything else — routing, the docs home, the sidebar, and search — picks it
 * up automatically. All content is hardcoded; nothing is read from a database.
 */
export const DOCS: Doc[] = [sprntlyHowToGuide]

export type { Doc, DocSection }

export function getDoc(slug: string): Doc | undefined {
  return DOCS.find((d) => d.slug === slug)
}

/** All docs grouped by their `category`, preserving registry order. */
export function docsByCategory(): { category: string; docs: Doc[] }[] {
  const groups: { category: string; docs: Doc[] }[] = []
  for (const doc of DOCS) {
    let group = groups.find((g) => g.category === doc.category)
    if (!group) {
      group = { category: doc.category, docs: [] }
      groups.push(group)
    }
    group.docs.push(doc)
  }
  return groups
}

/** Strip Markdown to plain text so section bodies are searchable. */
export function stripMarkdown(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, " ") // fenced code
    .replace(/`([^`]+)`/g, "$1") // inline code
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ") // images
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1") // links -> text
    .replace(/^\s*\|.*\|\s*$/gm, (row) => row.replace(/\|/g, " ")) // table pipes
    .replace(/[#>*_~`]/g, " ") // md punctuation
    .replace(/\s+/g, " ")
    .trim()
}

export type SearchResult = {
  slug: string
  docTitle: string
  sectionId: string
  sectionTitle: string
  snippet: string
}

/** Flat, precomputed search index over every section of every doc. */
type IndexEntry = {
  slug: string
  docTitle: string
  section: DocSection
  haystack: string
  plain: string
}

const INDEX: IndexEntry[] = DOCS.flatMap((doc) =>
  doc.sections.map((section) => {
    const plain = stripMarkdown(section.body)
    return {
      slug: doc.slug,
      docTitle: doc.title,
      section,
      plain,
      haystack: `${doc.title} ${section.title} ${plain}`.toLowerCase(),
    }
  }),
)

/** Build a short snippet centered on the first match of `query`. */
function buildSnippet(plain: string, query: string): string {
  const idx = plain.toLowerCase().indexOf(query.toLowerCase())
  if (idx === -1) return plain.slice(0, 140).trim()
  const start = Math.max(0, idx - 60)
  const end = Math.min(plain.length, idx + query.length + 80)
  const prefix = start > 0 ? "…" : ""
  const suffix = end < plain.length ? "…" : ""
  return `${prefix}${plain.slice(start, end).trim()}${suffix}`
}

/** Case-insensitive AND-of-terms search across all doc sections. */
export function searchDocs(query: string, limit = 12): SearchResult[] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const terms = q.split(/\s+/).filter(Boolean)
  const results: SearchResult[] = []
  for (const entry of INDEX) {
    if (!terms.every((t) => entry.haystack.includes(t))) continue
    results.push({
      slug: entry.slug,
      docTitle: entry.docTitle,
      sectionId: entry.section.id,
      sectionTitle: entry.section.title,
      snippet: buildSnippet(entry.plain, terms[0]),
    })
    if (results.length >= limit) break
  }
  return results
}
