import { describe, expect, it } from "vitest"
import { buildHomeChips } from "../homeChips"
import type { ChatHomeCard } from "../../types/content"

const home = (id: string): ChatHomeCard => ({
  id,
  icon: "sparkle",
  title: id,
  desc: "",
  target: "brief",
})
const starter = (id: string): ChatHomeCard => ({
  id,
  icon: "diamond",
  title: id,
  desc: "",
  target: "ondemand",
  prompt: id,
})

describe("buildHomeChips", () => {
  it("uses curated home cards only and does not pad with Ask starters", () => {
    const out = buildHomeChips([home("brief"), home("feedback")], [starter("q3"), starter("prd")])
    expect(out).toEqual([
      { kind: "home", card: home("brief") },
      { kind: "home", card: home("feedback") },
    ])
    // Q3 (an Ask starter) must never surface alongside curated home cards.
    expect(out.some((c) => c.card.id === "q3")).toBe(false)
  })

  it("falls back to Ask starters only when there are no home cards", () => {
    const out = buildHomeChips([], [starter("q3"), starter("prd")])
    expect(out).toEqual([
      { kind: "starter", card: starter("q3") },
      { kind: "starter", card: starter("prd") },
    ])
  })

  it("caps the row at 4 chips", () => {
    const out = buildHomeChips([home("a"), home("b"), home("c"), home("d"), home("e")], [])
    expect(out).toHaveLength(4)
    expect(out.map((c) => c.card.id)).toEqual(["a", "b", "c", "d"])
  })

  it("returns an empty row when both sources are empty", () => {
    expect(buildHomeChips([], [])).toEqual([])
  })
})
