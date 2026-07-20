// @vitest-environment jsdom
//
// Recents storage for the ⌘K palette (lib/search/recents.ts): per-workspace
// key isolation, dedupe/move-to-front, the MAX_RECENTS cap, and corrupt-JSON
// resilience.
import { beforeEach, describe, expect, it } from "vitest"

import { MAX_RECENTS, getRecents, pushRecent } from "../search/recents"
import type { SearchItem } from "../search/types"

function item(id: string): SearchItem {
  return {
    id,
    group: "pages",
    title: `Item ${id}`,
    breadcrumb: [],
    keywords: [],
    iconId: "doc",
    action: { kind: "path", path: `/x/${id}` },
  }
}

beforeEach(() => {
  localStorage.clear()
})

describe("recents", () => {
  it("stores and returns items, most recent first", () => {
    pushRecent("ws1", item("a"))
    pushRecent("ws1", item("b"))
    expect(getRecents("ws1").map((r) => r.id)).toEqual(["b", "a"])
  })

  it("isolates recents per workspace", () => {
    pushRecent("ws1", item("a"))
    pushRecent("ws2", item("b"))
    expect(getRecents("ws1").map((r) => r.id)).toEqual(["a"])
    expect(getRecents("ws2").map((r) => r.id)).toEqual(["b"])
  })

  it("dedupes by id and moves the repeat to the front", () => {
    pushRecent("ws1", item("a"))
    pushRecent("ws1", item("b"))
    pushRecent("ws1", item("a"))
    expect(getRecents("ws1").map((r) => r.id)).toEqual(["a", "b"])
  })

  it("caps the list at MAX_RECENTS", () => {
    for (let i = 0; i < MAX_RECENTS + 4; i++) pushRecent("ws1", item(String(i)))
    const got = getRecents("ws1")
    expect(got).toHaveLength(MAX_RECENTS)
    expect(got[0].id).toBe(String(MAX_RECENTS + 3))
  })

  it("returns [] on corrupt or non-array storage", () => {
    localStorage.setItem("sprntly_palette_recents:ws1", "{not json")
    expect(getRecents("ws1")).toEqual([])
    localStorage.setItem("sprntly_palette_recents:ws1", JSON.stringify({ nope: 1 }))
    expect(getRecents("ws1")).toEqual([])
    // Malformed entries inside an otherwise-valid array are filtered out.
    localStorage.setItem(
      "sprntly_palette_recents:ws1",
      JSON.stringify([item("ok"), { id: 42 }, null]),
    )
    expect(getRecents("ws1").map((r) => r.id)).toEqual(["ok"])
  })
})
