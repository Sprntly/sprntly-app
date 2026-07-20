import type {
  MatchRange,
  ResultGroup,
  ScoredItem,
  SearchGroup,
  SearchItem,
} from "./types"

// ── Scoring + grouping for the ⌘K palette ────────────────────────────────────
//
// Hand-rolled on purpose: the corpus is small (dozens of static items + a few
// hundred workspace entities), and DocSearch-style highlighting needs EXACT
// match ranges — a substring/word-prefix scorer gives predictable ranking and
// precise ranges where a fuzzy library would produce confusing highlights.
// Pure module, zero React — mirrors routes.ts and is unit-tested directly.

export const GROUP_ORDER: SearchGroup[] = [
  "recent",
  "pages",
  "settings",
  "actions",
  "skills",
  "chats",
  "artifacts",
  "documents",
  "connectors",
  "team",
  "workspaces",
]

export const GROUP_LABELS: Record<SearchGroup, string> = {
  recent: "Recent",
  pages: "Pages",
  settings: "Settings",
  actions: "Actions",
  skills: "Skills",
  chats: "Chats",
  artifacts: "Artifacts",
  documents: "Documents",
  connectors: "Connectors",
  team: "Team",
  workspaces: "Workspaces",
}

/** Lowercase, trim, collapse runs of whitespace. */
export function normalize(s: string): string {
  return s.toLowerCase().trim().replace(/\s+/g, " ")
}

/** True when the char before `idx` is not a letter/digit (word boundary). */
function atWordBoundary(text: string, idx: number): boolean {
  if (idx === 0) return true
  return !/[a-z0-9]/i.test(text[idx - 1])
}

/** Merge overlapping/adjacent ranges so highlights never nest or collide. */
export function mergeRanges(ranges: MatchRange[]): MatchRange[] {
  if (ranges.length <= 1) return ranges
  const sorted = [...ranges].sort((a, b) => a.start - b.start)
  const out: MatchRange[] = [sorted[0]]
  for (let i = 1; i < sorted.length; i++) {
    const last = out[out.length - 1]
    const cur = sorted[i]
    if (cur.start <= last.end) {
      last.end = Math.max(last.end, cur.end)
    } else {
      out.push({ ...cur })
    }
  }
  return out
}

// Per-token field scores. Title matches dominate; keywords carry aliases;
// subtitle/breadcrumb are weak catch-alls (they make "settings" surface every
// settings pane via the trail without out-ranking the Settings page itself).
const TITLE_EXACT = 100
const TITLE_PREFIX = 80
const TITLE_WORD_PREFIX = 70
const TITLE_SUBSTRING = 55
const KEYWORD_EXACT = 50
const KEYWORD_PREFIX = 40
const KEYWORD_SUBSTRING = 25
const SUBTITLE_SUBSTRING = 22
const CRUMB_SUBSTRING = 20
/** Small nudge so navigation targets win ties against same-named entities. */
const NAV_GROUP_BONUS = 5

/**
 * Score one item against a query. Multi-token AND: every whitespace-separated
 * token must match SOMEWHERE (title, keyword, subtitle, or breadcrumb) or the
 * item is out. Returns null on no match / empty query. Only title matches
 * produce highlight ranges (keyword-only matches render the title plain).
 */
export function scoreItem(query: string, item: SearchItem): ScoredItem | null {
  const tokens = normalize(query).split(" ").filter(Boolean)
  if (tokens.length === 0) return null

  const title = item.title.toLowerCase()
  const keywords = item.keywords.map((k) => k.toLowerCase())
  const subtitle = (item.subtitle ?? "").toLowerCase()
  const crumb = item.breadcrumb.join(" ").toLowerCase()

  let total = 0
  const ranges: MatchRange[] = []

  for (const tok of tokens) {
    let best = 0

    const idx = title.indexOf(tok)
    if (idx >= 0) {
      if (title === tok) best = TITLE_EXACT
      else if (idx === 0) best = TITLE_PREFIX
      else if (atWordBoundary(title, idx)) best = TITLE_WORD_PREFIX
      else best = TITLE_SUBSTRING
      ranges.push({ start: idx, end: idx + tok.length })
    }

    for (const kw of keywords) {
      if (kw === tok) best = Math.max(best, KEYWORD_EXACT)
      else if (kw.startsWith(tok)) best = Math.max(best, KEYWORD_PREFIX)
      else if (kw.includes(tok)) best = Math.max(best, KEYWORD_SUBSTRING)
    }

    if (best < SUBTITLE_SUBSTRING && subtitle.includes(tok)) best = SUBTITLE_SUBSTRING
    if (best < CRUMB_SUBSTRING && crumb.includes(tok)) best = CRUMB_SUBSTRING

    if (best === 0) return null // AND semantics — one dead token kills the item
    total += best
  }

  if (item.group === "pages" || item.group === "settings" || item.group === "actions") {
    total += NAV_GROUP_BONUS
  }

  return { item, score: total, titleRanges: mergeRanges(ranges) }
}

/** Per-group result caps. Settings is effectively uncapped — typing
 *  "settings" must list EVERY pane (the palette's core promise), and the
 *  corpus tops out at ~14. Noisy entity groups stay tight. */
const DEFAULT_GROUP_CAP = 5
const GROUP_CAPS: Partial<Record<SearchGroup, number>> = {
  settings: 50,
  pages: 10,
}

/**
 * Score `items` against `query`, bucket by group in GROUP_ORDER, sort within
 * each group by score (desc, title asc tiebreak), cap per group, drop empties.
 */
export function searchItems(
  query: string,
  items: SearchItem[],
  opts?: { perGroupCap?: number },
): ResultGroup[] {
  const byGroup = new Map<SearchGroup, ScoredItem[]>()

  for (const item of items) {
    const scored = scoreItem(query, item)
    if (!scored) continue
    const bucket = byGroup.get(item.group)
    if (bucket) bucket.push(scored)
    else byGroup.set(item.group, [scored])
  }

  const out: ResultGroup[] = []
  for (const group of GROUP_ORDER) {
    const bucket = byGroup.get(group)
    if (!bucket || bucket.length === 0) continue
    bucket.sort(
      (a, b) => b.score - a.score || a.item.title.localeCompare(b.item.title),
    )
    const cap = opts?.perGroupCap ?? GROUP_CAPS[group] ?? DEFAULT_GROUP_CAP
    out.push({ group, label: GROUP_LABELS[group], items: bucket.slice(0, cap) })
  }
  return out
}
