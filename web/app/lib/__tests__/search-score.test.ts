// Unit tests for the ⌘K palette's pure scoring/grouping layer (lib/search/score.ts):
// field-weight ordering (exact > prefix > word-prefix > substring > keyword >
// crumb), multi-token AND semantics, highlight-range collection/merging,
// per-group caps, fixed group ordering, and empty-query behavior.
import { describe, expect, it } from "vitest"

import {
  GROUP_ORDER,
  mergeRanges,
  normalize,
  scoreItem,
  searchItems,
} from "../search/score"
import type { SearchItem } from "../search/types"

function item(overrides: Partial<SearchItem> & { id: string; title: string }): SearchItem {
  return {
    group: "pages",
    breadcrumb: [],
    keywords: [],
    iconId: "doc",
    action: { kind: "path", path: "/x" },
    ...overrides,
  }
}

describe("normalize", () => {
  it("lowercases, trims, and collapses whitespace", () => {
    expect(normalize("  Weekly   BRIEF ")).toBe("weekly brief")
  })
})

describe("mergeRanges", () => {
  it("merges overlapping and adjacent ranges", () => {
    expect(
      mergeRanges([
        { start: 4, end: 8 },
        { start: 0, end: 5 },
        { start: 8, end: 10 },
        { start: 20, end: 22 },
      ]),
    ).toEqual([
      { start: 0, end: 10 },
      { start: 20, end: 22 },
    ])
  })
})

describe("scoreItem", () => {
  it("ranks exact > prefix > word-prefix > substring on the title", () => {
    const exact = scoreItem("settings", item({ id: "a", title: "Settings" }))!
    const prefix = scoreItem("sett", item({ id: "b", title: "Settings" }))!
    const wordPrefix = scoreItem("sett", item({ id: "c", title: "App Settings" }))!
    const substring = scoreItem("ett", item({ id: "d", title: "Settings" }))!
    expect(exact.score).toBeGreaterThan(prefix.score)
    expect(prefix.score).toBeGreaterThan(wordPrefix.score)
    expect(wordPrefix.score).toBeGreaterThan(substring.score)
  })

  it("returns null when any token fails to match (AND semantics)", () => {
    const it1 = item({ id: "a", title: "Team & roles", breadcrumb: ["Settings"] })
    expect(scoreItem("team settings", it1)).not.toBeNull()
    expect(scoreItem("team zebra", it1)).toBeNull()
    expect(scoreItem("", it1)).toBeNull()
    expect(scoreItem("   ", it1)).toBeNull()
  })

  it("matches via keywords and breadcrumb without title highlight ranges", () => {
    const scored = scoreItem(
      "integrations",
      item({ id: "a", title: "Connectors", keywords: ["integrations"] }),
    )!
    expect(scored.score).toBeGreaterThan(0)
    expect(scored.titleRanges).toEqual([])

    const viaCrumb = scoreItem(
      "settings",
      item({ id: "b", title: "Connectors", breadcrumb: ["Settings", "Data"] }),
    )!
    expect(viaCrumb.score).toBeGreaterThan(0)
  })

  it("collects highlight ranges for title matches", () => {
    const scored = scoreItem("week brief", item({ id: "a", title: "Weekly brief" }))!
    expect(scored.titleRanges).toEqual([
      { start: 0, end: 4 },
      { start: 7, end: 12 },
    ])
  })
})

describe("searchItems", () => {
  const corpus: SearchItem[] = [
    item({ id: "page:settings", title: "Settings", group: "pages" }),
    item({
      id: "settings:team",
      title: "Team & roles",
      group: "settings",
      breadcrumb: ["Settings", "Workspace"],
    }),
    item({
      id: "settings:connectors",
      title: "Connectors",
      group: "settings",
      breadcrumb: ["Settings", "Data & Integrations"],
    }),
    item({ id: "skill:journey", title: "Journey map", group: "skills" }),
    item({ id: "chat:1", title: "Settings discussion", group: "chats" }),
  ]

  it("groups results in fixed GROUP_ORDER and drops empty groups", () => {
    const groups = searchItems("settings", corpus)
    const order = groups.map((g) => g.group)
    expect(order).toEqual(["pages", "settings", "chats"])
    // Every settings pane surfaces via its breadcrumb trail.
    const settingsGroup = groups.find((g) => g.group === "settings")!
    expect(settingsGroup.items.map((s) => s.item.id).sort()).toEqual([
      "settings:connectors",
      "settings:team",
    ])
  })

  it("caps results per group", () => {
    const many = Array.from({ length: 12 }, (_, i) =>
      item({ id: `chat:${i}`, title: `Chat about pricing ${i}`, group: "chats" }),
    )
    const groups = searchItems("pricing", many, { perGroupCap: 5 })
    expect(groups).toHaveLength(1)
    expect(groups[0].items).toHaveLength(5)
  })

  it("returns nothing for an empty query", () => {
    expect(searchItems("", corpus)).toEqual([])
  })

  it("keeps GROUP_ORDER exhaustive over every group used", () => {
    expect(GROUP_ORDER).toContain("recent")
    expect(new Set(GROUP_ORDER).size).toBe(GROUP_ORDER.length)
  })
})
