/** Are two theme labels the same topic? Mirrors
 *  `backend/app/crucible/kg_themes.py`'s `normalize_label` / `content_tokens`
 *  / `same_topic` term for term.
 *
 *  A MIRROR, NOT A SECOND OPINION. The backend imports those functions
 *  directly and this file exists only because the panel cannot; if the rule in
 *  `kg_themes.py` changes, this changes with it, or the panel will offer a
 *  choice between two names for one topic that the exported document has
 *  already collapsed — the exact disagreement the two-renderer discipline in
 *  `goalMoscow.ts` exists to prevent. */

//: `kg_themes._STOPWORDS`.
const STOPWORDS = new Set(
  ["and", "the", "of", "for", "a", "to", "in", "on", "with"],
)

//: `kg_themes._MIN_SHARED_TOKENS`. Two shared content words is a subject; one
//: is a coincidence of vocabulary ('Prototype Agent' / 'Strategy Agent').
const MIN_SHARED_TOKENS = 2

//: `kg_themes._SEPARATORS` — '&', '/', dashes and friends flatten to a space,
//: so word ORDER cannot make one topic look like two.
const SEPARATORS = /[-&/+|_,‐-―]+/g

//: `kg_themes._PUNCT_EDGES`, as a pair of anchored classes.
const PUNCT_EDGES = "[ \\t\\n\\r.,;:!?'\"“”‘’()\\[\\]{}<>*\\-–—/&|_]"
const EDGE_TRIM = new RegExp(`^${PUNCT_EDGES}+|${PUNCT_EDGES}+$`, "g")

/** `kg_themes.normalize_label`: NFKC, separators flattened, whitespace
 *  collapsed, edge punctuation stripped, case-folded. NOT stemmed and NOT
 *  de-pluralised, deliberately — see that function's own docstring. */
export function normalizeLabel(label: string): string {
  const s = (label || "").normalize("NFKC").replace(SEPARATORS, " ")
  return s.split(/\s+/).filter(Boolean).join(" ")
    .replace(EDGE_TRIM, "")
    .toLowerCase()
    .trim()
}

/** `kg_themes.content_tokens`: the topic-bearing words of a label. */
export function contentTokens(label: string): Set<string> {
  return new Set(
    normalizeLabel(label).split(" ").filter((t) => t && !STOPWORDS.has(t)),
  )
}

/** `kg_themes.same_topic`. Two ways to qualify, both about the words
 *  themselves rather than a learned distance: SUBSET (one label says
 *  everything the other says and possibly more), or TWO SHARED CONTENT WORDS.
 *  An EMPTY set never matches, including against another empty set — it is
 *  vacuously a subset of everything, so admitting it would let a label made
 *  entirely of stopwords absorb the corpus. */
export function sameTopic(a: Set<string>, b: Set<string>): boolean {
  if (!a.size || !b.size) return false
  const subset = (x: Set<string>, y: Set<string>) =>
    [...x].every((t) => y.has(t))
  if (subset(a, b) || subset(b, a)) return true
  let shared = 0
  for (const t of a) if (b.has(t)) shared += 1
  return shared >= MIN_SHARED_TOKENS
}
