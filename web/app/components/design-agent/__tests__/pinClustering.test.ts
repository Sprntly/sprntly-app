// Node-env vitest (no DOM) — pinClustering is a PURE strategy module, so the
// suite exercises it directly: activation threshold, radius grouping +
// centroid math, determinism / input-order independence / non-mutation, and
// opts overrides. The strategy is the swappable contract (a future design
// pass lands as a new strategy function + a call-site swap), so these tests
// pin the DEFAULT strategy's behaviour — a deliberate one-file update if the
// default ever changes.
import { describe, expect, it } from "vitest"

import { clusterPins, type PinPoint } from "../pinClustering"

const pt = (n: number, xPct: number, yPct: number): PinPoint => ({ n, xPct, yPct })

/** Deterministic pseudo-shuffle (fixed rotation + interleave) — no RNG so a
 *  failure is reproducible run-to-run. */
function reorder<T>(arr: T[]): T[] {
  const out: T[] = []
  for (let i = arr.length - 1; i >= 0; i -= 2) out.push(arr[i]!)
  for (let i = arr.length % 2 === 0 ? arr.length - 2 : arr.length - 3; i >= 0; i -= 2) out.push(arr[i]!)
  return out
}

describe("clusterPins — activation threshold", () => {
  it("test_cluster_below_activation_returns_all_singles", () => {
    // 11 coincident points — spatially they would all group, but the count is
    // below the default activateAt (12), so clustering never engages.
    const points = Array.from({ length: 11 }, (_, i) => pt(i + 1, 50, 50))
    const result = clusterPins(points)
    expect(result.clusters).toEqual([])
    expect(result.singles).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
  })
})

describe("clusterPins — grouping", () => {
  it("test_cluster_groups_within_radius_and_counts_members", () => {
    // 10 tight points around (50, 50) + 2 far-apart outliers = 12 total
    // (>= activateAt). The tight ten group into ONE cluster whose centroid is
    // the member mean; the outliers come back as singles.
    const tight = Array.from({ length: 10 }, (_, i) => pt(i + 1, 49 + (i % 3), 50))
    const far = [pt(11, 5, 5), pt(12, 95, 95)]
    const result = clusterPins([...tight, ...far])
    expect(result.clusters).toHaveLength(1)
    const cluster = result.clusters[0]!
    expect(cluster.members).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    const meanX = tight.reduce((s, p) => s + p.xPct, 0) / tight.length
    const meanY = tight.reduce((s, p) => s + p.yPct, 0) / tight.length
    expect(cluster.xPct).toBeCloseTo(meanX, 6)
    expect(cluster.yPct).toBeCloseTo(meanY, 6)
    expect(result.singles).toEqual([11, 12])
  })
})

describe("clusterPins — purity", () => {
  it("test_cluster_deterministic_and_input_unmutated", () => {
    const points = [
      ...Array.from({ length: 9 }, (_, i) => pt(i + 1, 20 + i * 0.5, 30)),
      pt(10, 70, 70),
      pt(11, 71, 70.5),
      pt(12, 8, 90),
    ]
    // Freeze the array AND every point: any mutation attempt throws in strict
    // mode (vitest runs ESM strict), so a green run proves non-mutation.
    const frozen = Object.freeze(points.map((p) => Object.freeze({ ...p })))
    const first = clusterPins(frozen as unknown as PinPoint[])
    const second = clusterPins(frozen as unknown as PinPoint[])
    // Same input → same output (determinism, no hidden state between calls).
    expect(second).toEqual(first)
    // Input ORDER independence: a reordered copy yields the identical result.
    const reordered = reorder([...points])
    expect(clusterPins(reordered)).toEqual(first)
    // And the reordered source array's order was not disturbed (no in-place sort).
    expect(reordered.map((p) => p.n)).toEqual(reorder([...points]).map((p) => p.n))
  })
})

describe("clusterPins — opts", () => {
  it("test_cluster_opts_respected", () => {
    // activateAt override: 3 coincident points cluster once the threshold drops.
    const three = [pt(1, 50, 50), pt(2, 50.5, 50), pt(3, 50, 50.5)]
    expect(clusterPins(three).clusters).toEqual([]) // default activateAt=12
    const activated = clusterPins(three, { activateAt: 3 })
    expect(activated.clusters).toHaveLength(1)
    expect(activated.clusters[0]!.members).toEqual([1, 2, 3])
    // radiusPct override: 2%-spaced points group at the default radius (5) but
    // fall apart at a 0.5 radius.
    const spaced = Array.from({ length: 12 }, (_, i) => pt(i + 1, 40 + i * 2, 50))
    const wide = clusterPins(spaced)
    expect(wide.clusters.length).toBeGreaterThan(0)
    const narrow = clusterPins(spaced, { radiusPct: 0.5 })
    expect(narrow.clusters).toEqual([])
    expect(narrow.singles).toHaveLength(12)
  })
})
