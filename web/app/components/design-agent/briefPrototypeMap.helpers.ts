import type { BriefPrototypeMapEntry } from "../../lib/api"

export type InsightPrototypeState = {
  hasPrd: boolean
  prdId: number | null
  prototypeReady: boolean
  previewImageUrl: string | null
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
    }
  }

  if (entry.prototype === null) {
    return {
      hasPrd: true,
      prdId: entry.prd_id,
      prototypeReady: false,
      previewImageUrl: null,
    }
  }

  return {
    hasPrd: true,
    prdId: entry.prd_id,
    prototypeReady: entry.prototype.ready,
    previewImageUrl: entry.prototype.preview_image_url,
  }
}
