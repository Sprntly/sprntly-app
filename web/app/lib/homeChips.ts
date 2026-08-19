import type { ChatHomeCard } from "../types/content"

export type HomeChipItem = { kind: "home" | "starter"; card: ChatHomeCard }

/**
 * The curated fallback chip row shown on the chat landing when no API-driven
 * home/starter cards are available. Shared so surfaces (main chat, project
 * chat) draw the same landing chips from one source instead of duplicating the
 * definitions.
 */
export const DEFAULT_HOME_CHIPS: HomeChipItem[] = [
  { kind: "home", card: { id: "def-brief", icon: "sparkle", title: "View Top Insights brief", desc: "", target: "brief" } },
  { kind: "starter", card: { id: "def-analyze", icon: "chart", title: "Analyze data", desc: "", target: "ondemand", prompt: "Analyze our key product metrics and identify the top opportunities." } },
  { kind: "starter", card: { id: "def-draft", icon: "document", title: "Draft quarterly report", desc: "", target: "ondemand", prompt: "Draft a quarterly product report with key metrics, wins, and next steps." } },
  { kind: "starter", card: { id: "def-proto", icon: "rocket", title: "Prototype", desc: "", target: "ondemand", prompt: "Help me prototype the top feature in our product roadmap." } },
]

/**
 * Build the chat-home suggestion row (max 4 chips).
 *
 * The row is a curated set of `home` cards. We pad from the Ask-page
 * `starterList` solely when there are no home cards to show, so the row never
 * silently re-surfaces Ask starters (e.g. Q3 strategy) alongside curated chips.
 */
export function buildHomeChips(home: ChatHomeCard[], starterList: ChatHomeCard[]): HomeChipItem[] {
  const out: HomeChipItem[] = []
  for (const card of home) {
    if (out.length >= 4) break
    out.push({ kind: "home", card })
  }
  if (out.length === 0) {
    for (const card of starterList) {
      if (out.length >= 4) break
      out.push({ kind: "starter", card })
    }
  }
  return out
}
