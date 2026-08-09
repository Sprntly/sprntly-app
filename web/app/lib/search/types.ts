import type { ScreenId } from "../../types"

// ── Global search (⌘K command palette) — shared types ────────────────────────
//
// A SearchItem is one row the palette can surface: a static page, a settings
// pane, or a dynamic workspace entity (skill, chat, artifact, …). Items are
// SERIALIZABLE BY DESIGN — recents round-trip localStorage — so anything the
// palette needs to act on rides in the `action` union rather than closures,
// and icons are string ids resolved to components inside the palette.

/** Result buckets, in display order (see GROUP_ORDER in score.ts). */
export type SearchGroup =
  | "recent"
  | "pages"
  | "settings"
  | "actions"
  | "skills"
  | "chats"
  | "artifacts"
  | "documents"
  | "connectors"
  | "team"
  | "workspaces"

/** What selecting an item DOES. Discriminated + serializable (recents). */
export type SearchItemAction =
  /** Plain navigation — query-param deep links included (`/settings?section=…`). */
  | { kind: "path"; path: string }
  /** Navigate via the app's screen map (NavigationContext.goTo). */
  | { kind: "screen"; screen: ScreenId }
  /** Start a fresh chat (NavigationContext.goToNewChat). */
  | { kind: "new-chat" }
  /** Resume a saved conversation — the ChatsScreen handoff pattern:
   *  write `sprntly_resume_conv` then land on the chat surface. */
  | { kind: "resume-chat"; dbId: number; title: string; prdId: number | null }
  /** Open a generated PRD as a chat tab + panel (NavigationContext.openPrdTab). */
  | { kind: "prd-tab"; prdId: number; title: string; briefId: number | null; insightIndex: number | null }
  /** Switch the active workspace (WorkspaceContext.setActiveWorkspace). */
  | { kind: "switch-workspace"; workspaceId: string }

export type SearchItem = {
  /** Stable id — "page:/skills", "settings:connectors", "skill:<id>", "chat:<dbId>". */
  id: string
  group: SearchGroup
  title: string
  /** Secondary line under the title (description / preview / email). */
  subtitle?: string
  /** DocSearch-style trail, e.g. ["Settings", "Data & Integrations"]. */
  breadcrumb: string[]
  /** Display URL shown on the row; omitted for stateful actions (chat resume…). */
  url?: string
  /** Extra matchable strings (aliases, trigger, category, email…). */
  keywords: string[]
  /** Key into the palette's icon map (keeps items serializable). */
  iconId: string
  action: SearchItemAction
}

/** Half-open [start, end) span of the TITLE that matched — for highlighting. */
export type MatchRange = { start: number; end: number }

export type ScoredItem = {
  item: SearchItem
  score: number
  titleRanges: MatchRange[]
}

export type ResultGroup = {
  group: SearchGroup
  label: string
  items: ScoredItem[]
}
