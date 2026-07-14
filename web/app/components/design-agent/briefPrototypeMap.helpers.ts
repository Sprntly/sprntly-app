import type { BriefPrototypeMapEntry } from "../../lib/api"

export type InsightPrototypeState = {
  hasPrd: boolean
  prdId: number | null
  prototypeReady: boolean
  previewImageUrl: string | null
  prdTitle: string | null
  /** The PRD id to open the READY prototype through (may be an OLDER PRD than
   *  `prdId` when the PRD was regenerated after the prototype was built).
   *  Null unless `prototypeReady`. */
  prototypePrdId: number | null
}

/**
 * Resolve the prototype readiness state for a single insight card.
 *
 * Three cases:
 *  1. No entry in the map → no PRD has been created for this insight.
 *  2. Entry present but prototype is null → PRD exists; generation not yet
 *     started (or was invalidated and not re-run).
 *  3. Entry present and prototype.ready is true → generation complete;
 *     preview_image_url may be null if no thumbnail was captured.
 */
export function prototypeStateForInsight(
  entriesByInsight: Map<number, BriefPrototypeMapEntry>,
  insightIndex: number,
): InsightPrototypeState {
  const entry = entriesByInsight.get(insightIndex)

  if (entry === undefined) {
    return {
      hasPrd: false,
      prdId: null,
      prototypeReady: false,
      previewImageUrl: null,
      prdTitle: null,
      prototypePrdId: null,
    }
  }

  if (entry.prototype === null) {
    return {
      hasPrd: true,
      prdId: entry.prd_id,
      prototypeReady: false,
      previewImageUrl: null,
      prdTitle: entry.prd_title || null,
      prototypePrdId: null,
    }
  }

  return {
    hasPrd: true,
    prdId: entry.prd_id,
    prototypeReady: entry.prototype.ready,
    previewImageUrl: entry.prototype.preview_image_url,
    prdTitle: entry.prd_title || null,
    // Older backends omit prototype.prd_id — fall back to the entry's PRD.
    prototypePrdId: entry.prototype.prd_id ?? entry.prd_id,
  }
}
