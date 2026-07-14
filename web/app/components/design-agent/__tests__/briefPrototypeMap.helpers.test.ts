import { describe, expect, it } from "vitest"
import { prototypeStateForInsight } from "../briefPrototypeMap.helpers"
import type { BriefPrototypeMapEntry } from "../../../lib/api"

describe("prototypeStateForInsight", () => {
  it("returns hasPrd:false for an insight with no entry in the map", () => {
    const map = new Map<number, BriefPrototypeMapEntry>()
    expect(prototypeStateForInsight(map, 0)).toEqual({
      hasPrd: false,
      prdId: null,
      prototypeReady: false,
      previewImageUrl: null,
      prdTitle: null,
      prototypePrdId: null,
    })
  })

  it("returns hasPrd:true + prototypeReady:false when entry has no prototype", () => {
    const map = new Map<number, BriefPrototypeMapEntry>([
      [2, { insight_index: 2, prd_id: 42, prd_title: "Patient discharge flow", prototype: null }],
    ])
    expect(prototypeStateForInsight(map, 2)).toEqual({
      hasPrd: true,
      prdId: 42,
      prototypeReady: false,
      previewImageUrl: null,
      prdTitle: "Patient discharge flow",
      prototypePrdId: null,
    })
  })

  it("returns prototypeReady:true + previewImageUrl when prototype is ready", () => {
    const map = new Map<number, BriefPrototypeMapEntry>([
      [
        5,
        {
          insight_index: 5,
          prd_id: 99,
          prd_title: "My Cool Prototype",
          prototype: {
            ready: true,
            preview_image_url: "https://cdn.example.com/thumb.png",
          },
        },
      ],
    ])
    expect(prototypeStateForInsight(map, 5)).toEqual({
      hasPrd: true,
      prdId: 99,
      prototypeReady: true,
      previewImageUrl: "https://cdn.example.com/thumb.png",
      prdTitle: "My Cool Prototype",
      // No prototype.prd_id in the entry → falls back to the entry's prd_id.
      prototypePrdId: 99,
    })
  })

  it("surfaces prototype.prd_id when the prototype lives on an OLDER PRD", () => {
    const map = new Map<number, BriefPrototypeMapEntry>([
      [
        3,
        {
          insight_index: 3,
          prd_id: 120, // the regenerated (newest) PRD
          prd_title: "Regenerated PRD",
          prototype: { ready: true, preview_image_url: null, prd_id: 101 },
        },
      ],
    ])
    const result = prototypeStateForInsight(map, 3)
    expect(result.prdId).toBe(120)
    expect(result.prototypePrdId).toBe(101)
  })

  it("returns previewImageUrl:null when prototype is ready but no thumbnail was captured", () => {
    const map = new Map<number, BriefPrototypeMapEntry>([
      [
        1,
        {
          insight_index: 1,
          prd_id: 7,
          prd_title: "Handoff latency PRD",
          prototype: { ready: true, preview_image_url: null },
        },
      ],
    ])
    const result = prototypeStateForInsight(map, 1)
    expect(result.prototypeReady).toBe(true)
    expect(result.previewImageUrl).toBeNull()
    expect(result.prdTitle).toBe("Handoff latency PRD")
  })
})
