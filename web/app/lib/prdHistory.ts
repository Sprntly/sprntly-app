// Unified PRD version history: merges in-place edit *snapshots* (prd_versions,
// restorable) with prior *generations* (separate prds rows from regeneration,
// openable) into one time-sorted list for the Version History dropdown.

export type PrdSnapshot = {
  id: number
  prd_id: number
  version_number: number
  title: string
  payload_md: string
  saved_by: string
  saved_at: string
}

export type PrdGeneration = {
  id: number
  title: string
  status: string
  generated_at: string
  insight_index: number | null
}

export type HistoryEntry =
  | { kind: "snapshot"; ts: number; snapshot: PrdSnapshot }
  | { kind: "generation"; ts: number; generation: PrdGeneration; isCurrent: boolean }

/**
 * Merge edit-snapshots and prior generations into one history, newest first.
 * The generation matching `currentPrdId` is flagged `isCurrent` (it's the PRD
 * you're viewing) rather than dropped, so the list reads as a full timeline.
 */
export function mergeHistory(
  versions: PrdSnapshot[],
  generations: PrdGeneration[],
  currentPrdId: number,
): HistoryEntry[] {
  const entries: HistoryEntry[] = [
    ...versions.map((s): HistoryEntry => ({
      kind: "snapshot",
      ts: Date.parse(s.saved_at) || 0,
      snapshot: s,
    })),
    ...generations.map((g): HistoryEntry => ({
      kind: "generation",
      ts: Date.parse(g.generated_at) || 0,
      generation: g,
      isCurrent: g.id === currentPrdId,
    })),
  ]
  entries.sort((a, b) => b.ts - a.ts)
  return entries
}
