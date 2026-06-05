import { describe, expect, it } from "vitest"
import {
  canLaunchWorkspace,
  COWORKER_SLOTS,
  COWORKERS,
  emptyCoworkerNames,
  normalizeCoworkerNames,
} from "../onboarding/coworkersApi"

describe("coworker catalog", () => {
  it("has the four v4 slots in display order", () => {
    expect(COWORKER_SLOTS).toEqual(["pm", "pd", "ds", "admin"])
  })

  it("each coworker has a label, blurb, and placeholder handle", () => {
    for (const c of COWORKERS) {
      expect(c.label).toMatch(/coworker/i)
      expect(c.blurb.length).toBeGreaterThan(0)
      expect(c.placeholder).toMatch(/^name_/)
    }
  })
})

describe("normalizeCoworkerNames", () => {
  it("trims every slot", () => {
    expect(
      normalizeCoworkerNames({ pm: "  Atlas ", pd: "Juno", ds: " Vera", admin: "Ada " }),
    ).toEqual({ pm: "Atlas", pd: "Juno", ds: "Vera", admin: "Ada" })
  })
})

describe("canLaunchWorkspace", () => {
  it("requires all four coworkers named", () => {
    expect(canLaunchWorkspace(emptyCoworkerNames())).toBe(false)
    expect(
      canLaunchWorkspace({ pm: "Atlas", pd: "Juno", ds: "Vera", admin: "" }),
    ).toBe(false)
    expect(
      canLaunchWorkspace({ pm: "Atlas", pd: "Juno", ds: "Vera", admin: "Ada" }),
    ).toBe(true)
  })

  it("treats whitespace-only names as unset", () => {
    expect(
      canLaunchWorkspace({ pm: "Atlas", pd: "Juno", ds: "Vera", admin: "   " }),
    ).toBe(false)
  })
})
