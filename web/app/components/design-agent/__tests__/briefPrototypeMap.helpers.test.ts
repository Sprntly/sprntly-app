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
    })
  })

  it("returns hasPrd:true + prototypeReady:false when entry has no prototype", () => {
    const map = new Map<number, BriefPrototypeMapEntry>([
      [2, { insight_index: 2, prd_id: 42, prototype: null }],
    ])
    expect(prototypeStateForInsight(map, 2)).toEqual({
      hasPrd: true,
      prdId: 42,
      prototypeReady: false,
      previewImageUrl: null,
    })
  })

  it("returns prototypeReady:true + previewImageUrl when prototype is ready", () => {
    const map = new Map<number, BriefPrototypeMapEntry>([
      [
        5,
        {
          insight_index: 5,
          prd_id: 99,
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
    })
  })

  it("returns previewImageUrl:null when prototype is ready but no thumbnail was captured", () => {
    const map = new Map<number, BriefPrototypeMapEntry>([
      [
        1,
        {
          insight_index: 1,
          prd_id: 7,
          prototype: { ready: true, preview_image_url: null },
        },
      ],
    ])
    const result = prototypeStateForInsight(map, 1)
    expect(result.prototypeReady).toBe(true)
    expect(result.previewImageUrl).toBeNull()
  })
})
