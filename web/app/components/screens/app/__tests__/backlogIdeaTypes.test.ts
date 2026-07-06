import { describe, expect, it } from "vitest"
import { canHaveUiPrototype, type IdeaType } from "../backlogIdeaTypes"

describe("canHaveUiPrototype", () => {
  it("allows UI-capable idea types (the prototype CTA shows)", () => {
    expect(canHaveUiPrototype("UI")).toBe(true)
    expect(canHaveUiPrototype("New initiative")).toBe(true)
    expect(canHaveUiPrototype("Bug")).toBe(true)
  })

  it("hides the CTA for non-UI idea types (Infra / Research)", () => {
    expect(canHaveUiPrototype("Infra")).toBe(false)
    expect(canHaveUiPrototype("Research")).toBe(false)
  })

  it("covers the full IdeaType union (no unhandled type)", () => {
    const all: IdeaType[] = ["New initiative", "UI", "Infra", "Bug", "Research"]
    // Exactly the three UI-capable types qualify.
    expect(all.filter(canHaveUiPrototype)).toEqual(["New initiative", "UI", "Bug"])
  })
})
