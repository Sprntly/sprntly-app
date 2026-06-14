import { describe, expect, it } from "vitest"
import {
  canLaunchWorkspace,
  COWORKER_SLOTS,
  COWORKERS,
  emptyCoworkerNames,
  normalizeCoworkerNames,
  VISIBLE_COWORKER_SLOTS,
  VISIBLE_COWORKERS,
} from "../onboarding/coworkersApi"

describe("coworker catalog", () => {
  it("keeps the four v4 slots in display order (backend contract shape)", () => {
    // The full catalog is unchanged so the GET/PUT /v1/company/coworkers
    // payload still round-trips all four slots.
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

describe("visible coworker subset", () => {
  it("surfaces only the Product (pm) coworker in the teammate UI", () => {
    expect(VISIBLE_COWORKER_SLOTS).toEqual(["pm"])
    expect(VISIBLE_COWORKERS).toHaveLength(1)
    expect(VISIBLE_COWORKERS[0].slot).toBe("pm")
    expect(VISIBLE_COWORKERS[0].label).toBe("Product coworker")
  })

  it("does not surface the design / data-science / admin coworkers", () => {
    const slots = VISIBLE_COWORKERS.map((c) => c.slot)
    expect(slots).not.toContain("pd")
    expect(slots).not.toContain("ds")
    expect(slots).not.toContain("admin")
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
  it("requires only the visible Product coworker named", () => {
    expect(canLaunchWorkspace(emptyCoworkerNames())).toBe(false)
    // Hidden slots empty: launch is still allowed once Product is named.
    expect(
      canLaunchWorkspace({ pm: "Atlas", pd: "", ds: "", admin: "" }),
    ).toBe(true)
  })

  it("does NOT gate launch on the hidden slots", () => {
    // pd / ds / admin unnamed must not block launch.
    expect(
      canLaunchWorkspace({ pm: "Atlas", pd: "", ds: "", admin: "" }),
    ).toBe(true)
    // …but a missing Product name still blocks it.
    expect(
      canLaunchWorkspace({ pm: "", pd: "Juno", ds: "Vera", admin: "Ada" }),
    ).toBe(false)
  })

  it("treats a whitespace-only Product name as unset", () => {
    expect(
      canLaunchWorkspace({ pm: "   ", pd: "", ds: "", admin: "" }),
    ).toBe(false)
  })
})
