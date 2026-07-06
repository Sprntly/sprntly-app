import type { ChatHomeCard } from "../types/content"

export type HomeChipItem = { kind: "home" | "starter"; card: ChatHomeCard }

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
