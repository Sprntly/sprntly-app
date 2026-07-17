// Spatial grouping for canvas pin markers — a PURE strategy module (no DOM,
// no React, no mutation), unit-testable in node-env vitest.
//
// THE STRATEGY IS THE SWAPPABLE CONTRACT: consumers bind a `ClusterStrategy`
// value (the public viewer chrome imports `clusterPins`, the locked spatial
// default below). When the grouping design is refined, land the refinement as
// a NEW strategy function (or new default opts) in this module plus a
// one-line binding/opts change at the call-site — the pin layer's rendering
// contract (`PinCluster` in, markers out) does not change.

/** One pin marker as the strategy sees it: its badge number (`n`, the join
 *  key across the pin layer's inputs) + its effective viewport-% position. */
export type PinPoint = { n: number; xPct: number; yPct: number }

/** A grouped marker: centroid (viewport %) + the member pin numbers. */
export type PinCluster = { xPct: number; yPct: number; members: number[] }

export type ClusterResult = { clusters: PinCluster[]; singles: number[] }

export type ClusterStrategy = (
  points: PinPoint[],
  opts?: { radiusPct?: number; activateAt?: number },
) => ClusterResult

/**
 * Locked default: greedy deterministic SPATIAL clustering.
 *
 * - Activation: clustering engages only when `points.length >= activateAt`
 *   (default 12). Below that everything returns as singles — small pin counts
 *   read fine unclustered; the 100+-comment prototype is the target.
 * - Grouping: points are walked in ascending-`n` order (a sorted COPY — the
 *   input array and its points are never mutated, and input order therefore
 *   never affects the result). A point within `radiusPct` (default 5,
 *   Euclidean in viewport-% space) of an existing group's centroid joins that
 *   group and the centroid is recomputed; otherwise it seeds a new group.
 * - Output: groups with >= 2 members become `clusters` (centroid + member
 *   `n`s, ascending); one-member groups come back as `singles`.
 */
export const clusterPins: ClusterStrategy = (points, opts) => {
  const radiusPct = opts?.radiusPct ?? 5
  const activateAt = opts?.activateAt ?? 12
  const sorted = [...points].sort((a, b) => a.n - b.n)
  if (sorted.length < activateAt) {
    return { clusters: [], singles: sorted.map((p) => p.n) }
  }
  type Group = { sumX: number; sumY: number; members: PinPoint[] }
  const groups: Group[] = []
  for (const p of sorted) {
    let joined = false
    for (const g of groups) {
      const cx = g.sumX / g.members.length
      const cy = g.sumY / g.members.length
      const dx = p.xPct - cx
      const dy = p.yPct - cy
      if (Math.sqrt(dx * dx + dy * dy) <= radiusPct) {
        g.members.push(p)
        g.sumX += p.xPct
        g.sumY += p.yPct
        joined = true
        break
      }
    }
    if (!joined) groups.push({ sumX: p.xPct, sumY: p.yPct, members: [p] })
  }
  const clusters: PinCluster[] = []
  const singles: number[] = []
  for (const g of groups) {
    if (g.members.length === 1) {
      singles.push(g.members[0]!.n)
    } else {
      clusters.push({
        xPct: g.sumX / g.members.length,
        yPct: g.sumY / g.members.length,
        members: g.members.map((m) => m.n),
      })
    }
  }
  return { clusters, singles }
}
